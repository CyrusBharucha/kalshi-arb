"""
data/synthesis_live.py
======================
Synthesis API -> Kalshi live orderbook WebSocket client.

Provides real-time best-bid/ask AND full L2 depth for every active Kalshi
market without requiring a Kalshi account.  Streams deltas from:
  wss://synthesis.trade/api/v1/orderbook/ws

Architecture
------------
- L2 orderbook cache: market_id -> L2Book (full depth, per data/orderbook_l2.py)
- Backward-compatible TopOfBook dataclass kept for test compatibility
- Relationship lookup pre-loaded from DB at startup
- After each delta, check whether the updated market is in a relationship pair
- If a fee-adjusted net edge exists, persist to arbitrage_opportunities
- VWAP / slippage available from L2Book for realistic execution modelling

Data quality classification
---------------------------
Opportunities detected here are Class A - live bid/ask from the order-book.
This is the highest evidential class (Rule 5 / executable arbitrage).

Never print or log the API secret key.
Do not place real trades; this is a research scanner.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import websocket
from dotenv import load_dotenv
from sqlalchemy import text

from feeds.orderbook_l2 import L2Book, L2Cache

load_dotenv()
logger = logging.getLogger(__name__)

# -- Config ---------------------------------------------------------------------
WS_URL         = "wss://synthesis.trade/api/v1/orderbook/ws"
KALSHI_FEE_PCT = 0.07          # 7% of payout; see execution/fees.py
MAX_FEE        = 0.035         # $0.035 per contract cap
RECONNECT_DELAY_BASE = 5       # seconds, doubles on each retry up to MAX
RECONNECT_DELAY_MAX  = 120
HEARTBEAT_INTERVAL   = 30      # seconds between pings


@dataclass
class TopOfBook:
    """Best bid/ask for both YES and NO sides."""
    market_id:    str
    yes_bid:      float = 0.0
    yes_ask:      float = 1.0
    no_bid:       float = 0.0
    no_ask:       float = 1.0
    sequence:     int   = 0
    updated_at:   float = field(default_factory=time.time)

    def midpoint_yes(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2.0

    def executable_cost_yes(self) -> float:
        """Cost to BUY YES at the ask."""
        return self.yes_ask

    def executable_cost_no(self) -> float:
        """Cost to BUY NO at the ask."""
        return self.no_ask


def _kalshi_fee(prob: float) -> float:
    """Kalshi taker fee per contract: ceil(0.07 * P * (1-P) * 100) / 100."""
    raw = KALSHI_FEE_PCT * prob * (1.0 - prob)
    return min(math.ceil(raw * 100) / 100, MAX_FEE)


class LiveOrderbookCache:
    """Thread-safe in-memory orderbook state for all Kalshi markets."""

    def __init__(self):
        self._books: Dict[str, TopOfBook] = {}
        self._lock = threading.Lock()
        self._update_callbacks: List[callable] = []

    def register_callback(self, fn: callable):
        """fn(book: TopOfBook) called after each update."""
        self._update_callbacks.append(fn)

    def apply_delta(self, delta: Dict[str, Any]) -> Optional[TopOfBook]:
        """Apply one WebSocket delta message to the cache."""
        mid = delta.get("market_id")
        if not mid:
            return None

        seq = int(delta.get("sequence", 0))

        with self._lock:
            existing = self._books.get(mid)
            # Sequence guard: ignore stale deltas
            if existing and existing.sequence > seq:
                return None

            book = TopOfBook(
                market_id  = mid,
                yes_bid    = float(delta.get("yes_best_bid", 0)),
                yes_ask    = float(delta.get("yes_best_ask", 1)),
                no_bid     = float(delta.get("no_best_bid",  0)),
                no_ask     = float(delta.get("no_best_ask",  1)),
                sequence   = seq,
                updated_at = time.time(),
            )
            self._books[mid] = book

        for fn in self._update_callbacks:
            try:
                fn(book)
            except Exception as exc:
                logger.warning("Callback error: %s", exc)

        return book

    def get(self, market_id: str) -> Optional[TopOfBook]:
        with self._lock:
            return self._books.get(market_id)

    def snapshot(self) -> Dict[str, TopOfBook]:
        with self._lock:
            return dict(self._books)

    def __len__(self):
        with self._lock:
            return len(self._books)


class LiveArbScanner:
    """
    Evaluates arbitrage opportunities in real-time against live bid/ask prices.

    Accepts an L2Cache; uses top_of_book() for price signal and L2Book.vwap_*
    methods for slippage-adjusted execution sizing.

    For each relationship type:
      complement           - YES ask + NO ask < $1 on the same contract → buy both.
      mutually_exclusive   - Exactly one of N outcomes resolves YES; buy all N NOs.
                             Arb condition: Σ NO_asks < N−1 (cost < guaranteed payout).
                             Gross edge: (N−1) − Σ NO_asks.
      collectively_exhaustive - Exactly one outcome MUST resolve YES; buy all N YESes.
                             Arb condition: Σ YES_asks < $1 (cost < guaranteed payout).
      threshold_order / superset - m1 more likely; buy YES on m1 if yes_ask_m1 < yes_bid_m2
    """

    def __init__(self, cache: "L2Cache", relationships: List[Dict]):
        self.cache = cache
        # Index relationships by market_id for O(1) lookup
        self._by_market: Dict[str, List[Dict]] = {}
        for rel in relationships:
            for mid in (rel["market_id_1"], rel["market_id_2"]):
                self._by_market.setdefault(mid, []).append(rel)
        logger.info("LiveArbScanner loaded %d relationships, %d market index entries",
                    len(relationships), len(self._by_market))

    def on_update(self, book: "L2Book"):
        """Called after every orderbook update. Checks all relationships for this market."""
        rels = self._by_market.get(book.market_id, [])
        for rel in rels:
            opp = self._evaluate(rel)
            if opp:
                yield opp

    def _evaluate(self, rel: Dict) -> Optional[Dict]:
        id1   = rel["market_id_1"]
        id2   = rel["market_id_2"]
        rtype = rel["relationship_type"]

        # L2Cache: get full-depth books and top-of-book price dicts
        book1 = self.cache.get(id1)
        book2 = self.cache.get(id2)
        if not book1 or not book2:
            return None

        tob1 = self.cache.top_of_book(id1)
        tob2 = self.cache.top_of_book(id2)
        if not tob1 or not tob2:
            return None

        opp = None
        if rtype == "mutually_exclusive":
            opp = self._check_me(rel, tob1, tob2, id1, id2)
        elif rtype == "collectively_exhaustive":
            opp = self._check_ce(rel, tob1, tob2, id1, id2)
        elif rtype in ("threshold_order", "superset"):
            opp = self._check_threshold(rel, tob1, tob2, id1, id2)

        if opp:
            # Augment with L2 depth information
            opp["depth"] = {
                id1: book1.depth(),
                id2: book2.depth(),
            }
            # VWAP for $100 notional (≈100 contracts at ~$1 each)
            VWAP_QTY = 100.0
            vw1 = book1.vwap_yes_ask(VWAP_QTY)
            vw2 = book2.vwap_yes_ask(VWAP_QTY)
            opp["vwap_yes_ask"] = {
                id1: round(vw1[0], 4) if vw1 else None,
                id2: round(vw2[0], 4) if vw2 else None,
            }
            opp["max_qty"] = {
                id1: round(book1.yes.total_ask_qty(), 2),
                id2: round(book2.yes.total_ask_qty(), 2),
            }

        return opp

    def _check_me(self, rel, tob1, tob2, id1, id2) -> Optional[Dict]:
        """
        ME short: sell YES on both when YES_bid_1 + YES_bid_2 > $1.
        Since both can't resolve YES, one YES is overpriced. Selling both
        and collecting >$1 guarantees a profit regardless of outcome.
        Gross edge = (YES_bid_1 + YES_bid_2) − $1.00.
        """
        yes_bid_sum = tob1["yes_bid"] + tob2["yes_bid"]
        gross_edge = max(yes_bid_sum - 1.0, 0.0)
        if gross_edge <= 0:
            return None

        fee1 = _kalshi_fee(tob1["yes_bid"])
        fee2 = _kalshi_fee(tob2["yes_bid"])
        net_edge = gross_edge - fee1 - fee2
        if net_edge <= 0:
            return None

        return {
            "relationship_type": "mutually_exclusive",
            "market_id_1":  id1,
            "market_id_2":  id2,
            "strategy":     "sell_yes_both_me",
            "gross_edge":   round(gross_edge, 4),
            "fees":         round(fee1 + fee2, 4),
            "net_edge":     round(net_edge, 4),
            "prices": {
                id1: dict(tob1),
                id2: dict(tob2),
            },
            "classification": "A",
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }

    def _check_ce(self, rel, tob1, tob2, id1, id2) -> Optional[Dict]:
        """CE: buying YES on both should cost < $1 (guaranteed payout)."""
        cost = tob1["yes_ask"] + tob2["yes_ask"]
        gross_edge = 1.0 - cost
        if gross_edge <= 0:
            return None

        fee1 = _kalshi_fee(tob1["yes_ask"])
        fee2 = _kalshi_fee(tob2["yes_ask"])
        net_edge = gross_edge - fee1 - fee2
        if net_edge <= 0:
            return None

        return {
            "relationship_type": "collectively_exhaustive",
            "market_id_1": id1,
            "market_id_2": id2,
            "strategy":    "buy_yes_both_ce",
            "gross_edge":  round(gross_edge, 4),
            "fees":        round(fee1 + fee2, 4),
            "net_edge":    round(net_edge, 4),
            "prices": {
                id1: dict(tob1),
                id2: dict(tob2),
            },
            "classification": "A",
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }

    def _check_threshold(self, rel, tob1, tob2, id1, id2) -> Optional[Dict]:
        """
        Threshold/superset: m1 more likely -> yes_bid_1 should > yes_ask_2.
        Arb: buy YES on m2 (cheaper, underpriced) and sell YES on m1 (expensive).
        Violation: yes_bid_m1 < yes_ask_m2 means m1 is wrongly priced cheaper.
        """
        if tob1["yes_bid"] < tob2["yes_ask"]:
            gross_edge = tob2["yes_ask"] - tob1["yes_bid"]
            if gross_edge <= 0:
                return None
            fee1 = _kalshi_fee(tob1["yes_bid"])
            fee2 = _kalshi_fee(tob2["yes_ask"])
            net_edge = gross_edge - fee1 - fee2
            if net_edge <= 0:
                return None
            return {
                "relationship_type": rel["relationship_type"],
                "market_id_1": id1,
                "market_id_2": id2,
                "strategy":    "threshold_spread",
                "gross_edge":  round(gross_edge, 4),
                "fees":        round(fee1 + fee2, 4),
                "net_edge":    round(net_edge, 4),
                "prices": {
                    id1: dict(tob1),
                    id2: dict(tob2),
                },
                "classification": "A",
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
        return None


class SynthesisWebSocketClient:
    """
    Manages the Synthesis WebSocket connection with:
    - Reconnection with exponential backoff
    - Heartbeat / ping
    - Sequence tracking
    - Graceful shutdown
    """

    def __init__(
        self,
        api_secret: str,
        cache: "L2Cache",
        venue: str = "kalshi",
        markets: Optional[List[str]] = None,
    ):
        self._secret  = api_secret
        self._cache   = cache
        self._venue   = venue
        # Synthesis' orderbook WS only streams data for tickers named
        # explicitly here. A bare venue-only subscribe is accepted by the
        # server but never emits a snapshot or delta (verified live) - so
        # callers MUST pass a market list to get any data at all.
        self._markets = list(markets) if markets else []
        self._running = threading.Event()
        self._ws: Optional[websocket.WebSocketApp] = None
        self._reconnect_delay = RECONNECT_DELAY_BASE
        self._stats = {
            "total_messages": 0,
            "total_deltas":   0,
            "snapshots":      0,
            "reconnects":     0,
            "started_at":     None,
        }

    # -- Public interface --------------------------------------------------------

    def start(self):
        """Start client in a background thread."""
        self._running.set()
        self._stats["started_at"] = time.time()
        t = threading.Thread(target=self._run_loop, daemon=True, name="synthesis-ws")
        t.start()
        logger.info("SynthesisWebSocketClient started (venue=%s)", self._venue)
        return t

    def stop(self):
        """Signal graceful shutdown."""
        logger.info("SynthesisWebSocketClient stopping...")
        self._running.clear()
        if self._ws:
            self._ws.close()

    @property
    def stats(self) -> Dict:
        return dict(self._stats)

    # -- Connection loop ---------------------------------------------------------

    def _run_loop(self):
        while self._running.is_set():
            try:
                self._connect_once()
            except Exception as exc:
                logger.error("WebSocket loop error: %s", exc, exc_info=True)

            if not self._running.is_set():
                break

            delay = self._reconnect_delay
            logger.info("Reconnecting in %ds (attempt %d)...",
                        delay, self._stats["reconnects"])
            time.sleep(delay)
            self._reconnect_delay = min(delay * 2, RECONNECT_DELAY_MAX)
            self._stats["reconnects"] += 1

    def _connect_once(self):
        logger.info("Connecting to %s", WS_URL)

        self._ws = websocket.WebSocketApp(
            WS_URL,
            header={"X-PROJECT-API-KEY": self._secret},
            on_open    = self._on_open,
            on_message = self._on_message,
            on_error   = self._on_error,
            on_close   = self._on_close,
        )
        # run_forever blocks until connection closes.
        # ping_interval=0 disables the websocket-library auto-ping: Synthesis's
        # server does not respond to WebSocket-level PINGs and silently drops them,
        # causing run_forever to disconnect after ping_timeout seconds on every
        # cycle.  Our ws_bridge.py zombie watchdog (120s silence → reconnect) is
        # a higher-level and more reliable liveness check.
        self._ws.run_forever(ping_interval=0)

    def _on_open(self, ws):
        self._reconnect_delay = RECONNECT_DELAY_BASE  # reset on success

        if not self._markets:
            # No explicit tickers -> venue-only subscribe. Kept as a
            # last-resort fallback; in practice Synthesis sends nothing
            # for this form, so callers should always supply `markets`.
            logger.warning(
                "WebSocket connected - no markets list provided, "
                "subscribing venue-only (venue=%s); this typically "
                "yields NO data - see market_discovery.py", self._venue,
            )
            ws.send(json.dumps({"type": "subscribe", "venue": self._venue}))
            return

        # Synthesis caps `markets` at 1000 entries per message - chunk.
        CHUNK = 1000
        for i in range(0, len(self._markets), CHUNK):
            chunk = self._markets[i:i + CHUNK]
            ws.send(json.dumps({
                "type": "subscribe",
                "venue": self._venue,
                "markets": chunk,
            }))
        logger.info(
            "WebSocket connected - subscribed to %d markets (venue=%s)",
            len(self._markets), self._venue,
        )

    def _on_message(self, ws, raw: str):
        self._stats["total_messages"] += 1
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Non-JSON message: %s", raw[:80])
            return

        resp = msg.get("response", {})

        if "delta" in resp:
            self._stats["total_deltas"] += 1
            d = resp["delta"]
            # Synthesis delta may be bare flat dict or wrapped in {"orderbook": {...}}
            if "orderbook" in d:
                self._apply_snapshot_entry(d)
            else:
                self._cache.apply_delta(d)

        elif "orderbooks" in resp:
            self._stats["snapshots"] += 1
            books = resp.get("orderbooks") or []
            for b in books:
                self._apply_snapshot_entry(b)
            logger.debug("Snapshot: %d books", len(books))

    def _apply_snapshot_entry(self, entry: Dict[str, Any]) -> None:
        """
        Apply one Synthesis snapshot/delta entry in the nested format:
          {"venue": "kalshi", "orderbook": {"market_id": ...,
            "yes": {"bids": {price: size, ...}, "asks": {...}},
            "no":  {"bids": {...}, "asks": {...}}}}

        Converts to per-level apply_delta() calls on the L2Cache.
        """
        ob = entry.get("orderbook") or entry
        market_id = ob.get("market_id")
        if not market_id:
            return

        seq = int(ob.get("sequence", 0))
        yes_data = ob.get("yes", {})
        no_data  = ob.get("no",  {})

        yes_bids_raw = yes_data.get("bids", {}) if isinstance(yes_data, dict) else {}
        yes_asks_raw = yes_data.get("asks", {}) if isinstance(yes_data, dict) else {}
        no_bids_raw  = no_data.get("bids",  {}) if isinstance(no_data,  dict) else {}
        no_asks_raw  = no_data.get("asks",  {}) if isinstance(no_data,  dict) else {}

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        yes_best_bid = max((_f(k) for k in yes_bids_raw if _f(k) is not None), default=None)
        yes_best_ask = min((_f(k) for k in yes_asks_raw if _f(k) is not None), default=None)
        no_best_bid  = max((_f(k) for k in no_bids_raw  if _f(k) is not None), default=None)
        no_best_ask  = min((_f(k) for k in no_asks_raw  if _f(k) is not None), default=None)

        base = {
            "market_id":    market_id,
            "sequence":     seq,
            "yes_best_bid": yes_best_bid,
            "yes_best_ask": yes_best_ask,
            "no_best_bid":  no_best_bid,
            "no_best_ask":  no_best_ask,
        }

        for price_str, size_str in yes_bids_raw.items():
            p, s = _f(price_str), _f(size_str)
            if p is not None and s is not None:
                self._cache.apply_delta({**base, "side": "yes", "price": p, "amount": s})
        for price_str, size_str in yes_asks_raw.items():
            p, s = _f(price_str), _f(size_str)
            if p is not None and s is not None:
                self._cache.apply_delta({**base, "side": "yes", "price": p, "amount": s})
        for price_str, size_str in no_bids_raw.items():
            p, s = _f(price_str), _f(size_str)
            if p is not None and s is not None:
                self._cache.apply_delta({**base, "side": "no", "price": p, "amount": s})
        for price_str, size_str in no_asks_raw.items():
            p, s = _f(price_str), _f(size_str)
            if p is not None and s is not None:
                self._cache.apply_delta({**base, "side": "no", "price": p, "amount": s})

        # A full snapshot is authoritative — clear any gap flag so the book
        # is eligible for scanning again after a reconnect.
        book = self._cache.get(market_id)
        if book is not None:
            book.clear_gap()

    def _on_error(self, ws, error):
        logger.warning("WebSocket error: %s", error)

    def _on_close(self, ws, code, msg):
        logger.info("WebSocket closed (code=%s msg=%s)", code, msg)


def load_relationships_from_db(session) -> List[Dict]:
    """Load all non-complement relationships from the DB for the live scanner."""
    rows = session.execute(text("""
        SELECT market_id_1, market_id_2, relationship_type,
               implied_inequality, confidence
        FROM contract_relationships
        WHERE relationship_type != 'complement'
          AND confidence >= 0.85
        LIMIT 500000
    """)).fetchall()
    return [
        {
            "market_id_1":       r[0],
            "market_id_2":       r[1],
            "relationship_type": r[2],
            "implied_inequality": r[3],
            "confidence":        float(r[4]),
        }
        for r in rows
    ]


def persist_opportunity(session, opp: Dict):
    """Write a live arbitrage opportunity to arbitrage_opportunities."""
    import uuid
    session.execute(text("""
        INSERT INTO arbitrage_opportunities (
            opportunity_id, detected_at, strategy_type, classification,
            markets_involved, prices_json,
            gross_edge, total_fees, net_edge, status, notes
        ) VALUES (
            :oid, :det, :stype, :cls,
            :mkts, :prices::jsonb,
            :gross, :fees, :net, 'open', :notes
        )
        ON CONFLICT DO NOTHING
    """), {
        "oid":    str(uuid.uuid4()),
        "det":    opp["detected_at"],
        "stype":  opp["strategy"],
        "cls":    opp["classification"],
        "mkts":   [opp["market_id_1"], opp["market_id_2"]],
        "prices": json.dumps(opp["prices"]),
        "gross":  opp["gross_edge"],
        "fees":   opp["fees"],
        "net":    opp["net_edge"],
        "notes":  f"Live bid/ask: {opp['relationship_type']}",
    })


def run_live_scanner(duration_seconds: Optional[int] = None):
    """
    Entry point: connect to Synthesis, load relationships, scan for opportunities.
    Runs indefinitely (or for duration_seconds if set).
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from database.repository import session_scope

    secret = os.environ.get("SYNTHESIS_SECRET_KEY")
    if not secret:
        raise RuntimeError("SYNTHESIS_SECRET_KEY not set in environment")

    # Load relationships from DB
    logger.info("Loading relationships from DB...")
    with session_scope() as s:
        relationships = load_relationships_from_db(s)
    logger.info("Loaded %d relationships", len(relationships))

    # Set up components - L2Cache maintains full depth per market
    cache   = L2Cache()
    scanner = LiveArbScanner(cache, relationships)

    opportunities_found = 0

    def on_book_update(book: L2Book):
        nonlocal opportunities_found
        for opp in scanner.on_update(book):
            opportunities_found += 1
            depth_info = opp.get("depth", {})
            logger.info(
                "OPPORTUNITY #%d | %s | %s vs %s | net=%.4f | "
                "depth=%s/%s",
                opportunities_found,
                opp["relationship_type"],
                opp["market_id_1"],
                opp["market_id_2"],
                opp["net_edge"],
                depth_info.get(opp["market_id_1"], {}).get("total", "?"),
                depth_info.get(opp["market_id_2"], {}).get("total", "?"),
            )
            try:
                with session_scope() as s:
                    persist_opportunity(s, opp)
            except Exception as exc:
                logger.warning("Failed to persist opportunity: %s", exc)

    cache.register_callback(on_book_update)

    client = SynthesisWebSocketClient(secret, cache)
    ws_thread = client.start()

    start = time.time()
    try:
        while True:
            elapsed = time.time() - start
            ws_stats = client.stats
            cache_stats = cache.stats()
            # Average depth across all tracked markets
            snap = cache.snapshot()
            total_levels = sum(
                b.yes.levels() + b.no.levels()
                for b in snap.values()
            )
            avg_depth = total_levels / max(len(snap), 1)
            logger.info(
                "Status: elapsed=%.0fs  markets=%d  msgs=%d  deltas=%d  "
                "stale=%d  opps=%d  avg_depth=%.1f  reconnects=%d",
                elapsed,
                len(cache),
                ws_stats["total_messages"],
                ws_stats["total_deltas"],
                cache_stats["stale_discarded"],
                opportunities_found,
                avg_depth,
                ws_stats["reconnects"],
            )
            if duration_seconds and elapsed >= duration_seconds:
                break
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Interrupted - shutting down")
    finally:
        client.stop()

    # Final depth summary
    snap = cache.snapshot()
    total_levels = sum(b.yes.levels() + b.no.levels() for b in snap.values())
    return {
        "duration_seconds":    time.time() - start,
        "markets_cached":      len(cache),
        "total_messages":      client.stats["total_messages"],
        "opportunities_found": opportunities_found,
        "reconnects":          client.stats["reconnects"],
        "total_price_levels":  total_levels,
        "avg_depth_per_market": round(total_levels / max(len(snap), 1), 1),
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    result = run_live_scanner(duration_seconds=120)
    print(f"\n=== LIVE SCANNER RESULT: {result} ===")
