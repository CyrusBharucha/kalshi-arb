"""
arbitrage/live_scanner.py
WebSocket-driven live arbitrage scanner.

Instead of polling REST every 60 seconds, this scanner:
  1. Subscribes to live WS channels for all candidate markets
  2. Receives real-time price updates into LIVE_CACHE
  3. Re-scans affected events every time a price changes
  4. Persists detected opportunities to arbitrage_opportunities

The scanner runs as a separate asyncio task alongside the WS client.
Every price update triggers an immediate check of all relationships
in that event - latency from trade to arb signal is typically < 50ms.

Usage:
    python run.py live --tickers KXBTC-25DEC-T50000 KXBTC-25DEC-T55000
    # or let it auto-select candidates from contract_relationships:
    python run.py live
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from sqlalchemy import text

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from feeds.websocket_client import KalshiWebSocketClient, LIVE_CACHE
from database.repository import session_scope, insert_opportunity, get_engine
from engine.relationship_detector import check_logical_price_violations
from engine.yes_no import scan_complement_arb
from engine.lifecycle import OpportunityLifecycle
from engine.fees import taker_fee_per_contract as kalshi_fee

logger = logging.getLogger(__name__)

SCAN_DEBOUNCE_S   = 0.1    # re-scan at most 10× per second per event
STALE_BOOK_S      = 30.0   # mark a market stale if no update for this long
GAP_LOG_THRESHOLD = 5      # warn when a sequence gap is larger than this


# -- Sequence gap detection -------------------------------------------------------

class SequenceGapMonitor:
    """
    Tracks per-market WebSocket sequence numbers to detect gaps.

    Kalshi WebSocket messages carry an incrementing sequence number per market
    channel. A gap (seq[n+1] - seq[n] > 1) means messages were dropped, which
    may have left the local order book in a stale state.

    Usage:
        monitor = SequenceGapMonitor()
        # On each incoming message:
        gap_size = monitor.update(ticker, seq_num)
        if gap_size > 0:
            # Book may be stale for `ticker`
            ...
    """

    def __init__(self):
        self._last_seq: Dict[str, int] = {}
        self._gap_counts: Dict[str, int] = defaultdict(int)
        self._total_gaps = 0
        self._stale_markets: Set[str] = set()

    def update(self, ticker: str, seq: int) -> int:
        """
        Record a sequence number for a market.

        Returns the size of the gap (0 if no gap, >0 if messages were dropped).
        On a gap, the market is added to the stale set until reset() is called.
        """
        prev = self._last_seq.get(ticker)
        self._last_seq[ticker] = seq

        if prev is None:
            return 0   # first message - no gap possible

        gap = seq - prev - 1
        if gap > 0:
            self._gap_counts[ticker] += 1
            self._total_gaps += 1
            self._stale_markets.add(ticker)
            if gap >= GAP_LOG_THRESHOLD:
                logger.warning(
                    "SEQ GAP ticker=%s  prev=%d  curr=%d  gap=%d",
                    ticker, prev, seq, gap,
                )
            return gap
        return 0

    def is_stale(self, ticker: str) -> bool:
        """Return True if ticker has a known sequence gap (book may be incomplete)."""
        return ticker in self._stale_markets

    def reset(self, ticker: str) -> None:
        """Clear stale flag after a full book snapshot is received."""
        self._stale_markets.discard(ticker)
        self._last_seq.pop(ticker, None)

    def stats(self) -> Dict[str, Any]:
        return {
            "total_gaps":     self._total_gaps,
            "stale_markets":  len(self._stale_markets),
            "markets_tracked": len(self._last_seq),
            "gap_counts":     dict(self._gap_counts),
        }


# -- Helpers --------------------------------------------------------------------

def _load_relationships(tickers: List[str]) -> Dict[str, List[Dict]]:
    """
    Load all non-complement relationships for the given tickers from DB.
    Returns {event_ticker: [relationship, ...]}
    """
    if not tickers:
        return {}
    with session_scope() as s:
        rows = s.execute(text("""
            SELECT cr.market_id_1, cr.market_id_2, cr.relationship_type,
                   cr.logical_constraint, cr.implied_inequality, cr.confidence,
                   m.event_ticker
            FROM contract_relationships cr
            JOIN markets m ON m.market_id = cr.market_id_1
            WHERE cr.market_id_1 = ANY(:t)
              AND cr.relationship_type != 'complement'
        """), {"t": tickers}).fetchall()

    by_event: Dict[str, List] = defaultdict(list)
    for r in rows:
        by_event[r.event_ticker].append(dict(r._mapping))
    return dict(by_event)


def _load_candidate_tickers(limit: int = 2000) -> List[str]:
    """Pull market tickers from contract_relationships (non-complement, open status)."""
    with session_scope() as s:
        rows = s.execute(text("""
            SELECT DISTINCT m.ticker
            FROM contract_relationships cr
            JOIN markets m ON m.market_id = cr.market_id_1
            WHERE cr.relationship_type != 'complement'
              AND m.status IN ('open', 'active')
            LIMIT :lim
        """), {"lim": limit}).fetchall()
    return [r[0] for r in rows]


def _build_opp(
    rel: Dict, prices: Dict[str, Dict],
    violation: Dict,
) -> Optional[Dict[str, Any]]:
    """Convert a price-violation + relationship into an opportunity dict."""
    m1 = rel["market_id_1"]
    m2 = rel["market_id_2"]
    p1 = prices.get(m1, {})
    p2 = prices.get(m2, {})

    bid1  = p1.get("yes_bid")
    ask1  = p1.get("yes_ask")
    bid2  = p2.get("yes_bid")
    ask2  = p2.get("yes_ask")

    if None in (bid1, ask1, bid2, ask2):
        return None

    mag   = violation.get("magnitude", 0)
    if mag <= 0:
        return None

    # Rough net edge: magnitude minus fees on both legs
    fee1  = kalshi_fee(ask1) if ask1 else 0
    fee2  = kalshi_fee(bid2) if bid2 else 0
    net   = mag - fee1 - fee2

    return {
        "detected_at":    datetime.now(timezone.utc),
        "strategy_type":  rel["relationship_type"],
        "classification": "A",
        "markets_involved": [m1, m2],
        "prices_json":    {"market_1": p1, "market_2": p2},
        "gross_edge":     round(mag, 6),
        "total_fees":     round(fee1 + fee2, 6),
        "estimated_slippage": 0.002,
        "net_edge":       round(net, 6),
        "max_executable_contracts": 1,
        "status":         "open",
    }


# -- Live scanner ---------------------------------------------------------------

class LiveArbitrageScanner:
    """
    Continuously scans LIVE_CACHE for arb opportunities
    triggered by incoming WebSocket price updates.
    """

    def __init__(
        self,
        tickers: Optional[List[str]] = None,
        min_net_edge: float = config.MIN_NET_EDGE,
    ):
        self.tickers      = tickers  # None -> auto-load from DB
        self.min_net_edge = min_net_edge

        self._rels_by_event: Dict[str, List] = {}
        self._event_for_ticker: Dict[str, str] = {}
        self._last_scan: Dict[str, float] = defaultdict(float)
        self._last_update: Dict[str, float] = {}   # ticker -> monotonic ts of last price change
        self._opps_found  = 0
        self._opps_saved  = 0
        self._opps_closed = 0
        self._scans_run   = 0
        self._start       = time.monotonic()
        self._lifecycle   = OpportunityLifecycle(get_engine())
        self._seq_monitor = SequenceGapMonitor()

    def _setup(self, tickers: List[str]) -> None:
        """Load relationships and build index structures."""
        self._rels_by_event = _load_relationships(tickers)
        logger.info("Loaded relationships for %d events", len(self._rels_by_event))

        # Build ticker -> event index
        with session_scope() as s:
            rows = s.execute(text("""
                SELECT ticker, event_ticker FROM markets
                WHERE ticker = ANY(:t)
            """), {"t": tickers}).fetchall()
        self._event_for_ticker = {r.ticker: r.event_ticker for r in rows}

    async def run(self) -> None:
        """Main scan loop - call alongside the WS client."""
        # Auto-load tickers if not provided
        if not self.tickers:
            logger.info("No tickers specified - loading candidates from DB...")
            self.tickers = _load_candidate_tickers()
            if not self.tickers:
                logger.warning("No candidate tickers in contract_relationships. "
                               "Run `python run.py relationships` first.")
                return

        self._setup(self.tickers)
        logger.info("Live scanner active: %d tickers, %d events with relationships",
                    len(self.tickers), len(self._rels_by_event))

        prev_cache_size = 0
        while True:
            await asyncio.sleep(SCAN_DEBOUNCE_S)
            cache = LIVE_CACHE.all()
            if len(cache) == prev_cache_size:
                continue  # no new data
            prev_cache_size = len(cache)

            # Find events with fresh data
            changed_events: Set[str] = set()
            for ticker in cache:
                et = self._event_for_ticker.get(ticker)
                if et and et in self._rels_by_event:
                    changed_events.add(et)

            for event_ticker in changed_events:
                now = time.monotonic()
                if now - self._last_scan[event_ticker] < SCAN_DEBOUNCE_S:
                    continue
                self._last_scan[event_ticker] = now
                self._scan_event(event_ticker, cache)
                self._scans_run += 1

    def _scan_event(
        self,
        event_ticker: str,
        cache: Dict[str, Dict],
    ) -> None:
        rels   = self._rels_by_event.get(event_ticker, [])
        prices = {tid: snap for tid, snap in cache.items()
                  if self._event_for_ticker.get(tid) == event_ticker}

        if not prices:
            return

        violations = check_logical_price_violations(rels, prices)
        for v in violations:
            rel = v.get("relationship", {})
            opp = _build_opp(rel, prices, v)
            if opp and opp["net_edge"] >= self.min_net_edge:
                self._opps_found += 1
                logger.info(
                    "ARB: %s  markets=%s/%s  gross=%.4f  net=%.4f",
                    opp["strategy_type"],
                    opp["markets_involved"][0][-20:],
                    opp["markets_involved"][1][-20:],
                    opp["gross_edge"],
                    opp["net_edge"],
                )
                # Lifecycle reconcile for this single detection
                try:
                    lc = self._lifecycle.reconcile([opp])
                    self._opps_saved  += lc["opened"]
                    self._opps_closed += lc["closed"]
                except Exception as exc:
                    logger.debug("Lifecycle reconcile failed: %s", exc)

    def _record_update(self, ticker: str, seq: Optional[int] = None) -> None:
        """Track last-update time for stale-book detection; process seq gap if provided."""
        self._last_update[ticker] = time.monotonic()
        if seq is not None:
            self._seq_monitor.update(ticker, seq)

    def stale_books(self) -> List[str]:
        """
        Return list of tickers whose last update is older than STALE_BOOK_S
        or that have a known sequence gap.
        """
        now = time.monotonic()
        stale = []
        for ticker, last_ts in self._last_update.items():
            if now - last_ts > STALE_BOOK_S:
                stale.append(ticker)
            elif self._seq_monitor.is_stale(ticker):
                stale.append(ticker)
        return stale

    def status(self) -> Dict[str, Any]:
        elapsed = time.monotonic() - self._start
        seq_stats = self._seq_monitor.stats()
        return {
            "uptime_s":         round(elapsed, 1),
            "tickers_watched":  len(self.tickers or []),
            "events_with_rels": len(self._rels_by_event),
            "live_cache_size":  len(LIVE_CACHE),
            "scans_run":        self._scans_run,
            "opps_found":       self._opps_found,
            "opps_saved":       self._opps_saved,
            "opps_closed":      self._opps_closed,
            "stale_books":      len(self.stale_books()),
            "seq_gaps_total":   seq_stats["total_gaps"],
            "seq_stale_markets": seq_stats["stale_markets"],
        }


# -- Runner ---------------------------------------------------------------------

async def run_live(
    tickers: Optional[List[str]] = None,
    authenticated: bool = True,
) -> None:
    """
    Connect the WebSocket client and live scanner and run both concurrently.
    """
    # If no tickers given, load from DB
    if not tickers:
        tickers = _load_candidate_tickers(limit=500)

    if not tickers:
        logger.warning("No candidate tickers found. Run `python run.py relationships` first.")
        return

    scanner = LiveArbitrageScanner(tickers=tickers)

    # Wire scanner seq-gap and price-update callbacks so SequenceGapMonitor
    # and stale-book detection track real WS message flow.
    def _on_seq_gap_cb(ticker: str, got_seq: int, expected_seq: int) -> None:
        scanner._seq_monitor.update(ticker, got_seq)   # register the gap

    def _on_price_update_cb(ticker: str, seq: Optional[int]) -> None:
        scanner._record_update(ticker, seq)

    async with KalshiWebSocketClient(
        tickers=tickers,
        authenticated=authenticated,
        on_seq_gap=_on_seq_gap_cb,
        on_price_update=_on_price_update_cb,
    ) as ws_client:
        # Run WS client and scanner concurrently
        ws_task      = asyncio.create_task(ws_client.run())
        scanner_task = asyncio.create_task(scanner.run())
        status_task  = asyncio.create_task(_status_printer(scanner, ws_client))

        try:
            await asyncio.gather(ws_task, scanner_task, status_task)
        except KeyboardInterrupt:
            logger.info("Shutting down live scanner...")
        finally:
            ws_task.cancel()
            scanner_task.cancel()
            status_task.cancel()


async def _status_printer(
    scanner: LiveArbitrageScanner,
    ws_client: KalshiWebSocketClient,
    interval: int = 30,
) -> None:
    """Log a status line every N seconds."""
    while True:
        await asyncio.sleep(interval)
        s = scanner.status()
        logger.info(
            "[live] uptime=%ds  cache=%d  scans=%d  opps_found=%d  opps_saved=%d",
            s["uptime_s"], s["live_cache_size"],
            s["scans_run"], s["opps_found"], s["opps_saved"],
        )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    tickers_arg = sys.argv[1:] or None
    asyncio.run(run_live(tickers=tickers_arg))
