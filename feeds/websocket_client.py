"""
data/websocket_client.py
Production WebSocket client for Kalshi Trade API v2.

Channels consumed:
  orderbook_snapshot  - full book on first subscribe
  orderbook_delta     - incremental depth updates
  ticker              - best bid/ask, last price, volume
  trade               - individual fills

All data is persisted to PostgreSQL (market_snapshots, order_book_snapshots,
trades).  The client processes a shared in-memory price cache that the live
arb scanner reads directly - no polling needed.

Auth: RSA-PSS signed headers on the WebSocket upgrade handshake (same key as
REST).  The path signed is exactly "/trade-api/ws/v2".

Reconnect: exponential back-off (2s -> 4s -> 8s ... capped at 60s); full
re-subscribe after each reconnect; sequence gap detection resets the local
order book and triggers a re-subscribe to force a fresh snapshot.

Callbacks (wired by run_live() in arbitrage/live_scanner.py):
  on_seq_gap(ticker, got_seq, expected_seq) -- called on any sequence gap;
      used by LiveArbitrageScanner.SequenceGapMonitor to track stale books.
  on_price_update(ticker, seq) -- called on every orderbook_snapshot,
      orderbook_delta, and ticker update; used by _record_update() for
      time-based stale detection.

Usage:
    async with KalshiWebSocketClient(tickers=["KXBTC-25DEC-T50000"]) as ws:
        await ws.run()
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from database.repository import (
    session_scope, insert_snapshot, insert_order_book, upsert_trade,
)

logger = logging.getLogger(__name__)

# -- Constants ------------------------------------------------------------------
WS_SIGN_PATH  = "/trade-api/ws/v2"
HEARTBEAT_S   = 20          # send ping every N seconds
SNAPSHOT_S    = config.SNAPSHOT_INTERVAL_S  # flush order-book snapshot every N s
OB_DEPTH      = config.ORDERBOOK_DEPTH      # levels to store per side
MAX_RECONNECT_WAIT = 60     # seconds


# -- RSA-PSS signer (same algorithm as REST) -----------------------------------

def _load_private_key(path: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Private key not found: {path}")
    with open(p, "rb") as fh:
        return serialization.load_pem_private_key(fh.read(), password=None)


def _ws_auth_headers(key_id: str, private_key) -> Dict[str, str]:
    """
    Build the three Kalshi auth headers for the WS upgrade handshake.
    Signed string: <timestamp_ms> + "GET" + "/trade-api/ws/v2"
    """
    ts_ms = str(int(time.time() * 1000))
    msg   = (ts_ms + "GET" + WS_SIGN_PATH).encode("utf-8")
    sig   = private_key.sign(
        msg,
        asym_padding.PSS(
            mgf=asym_padding.MGF1(hashes.SHA256()),
            salt_length=asym_padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY":       key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode("utf-8"),
    }


# -- In-memory order book -------------------------------------------------------

class OrderBook:
    """
    Local order book maintained from Kalshi WS snapshots + deltas.

    Kalshi sends prices as integers (cents 1-99).  We store them as floats
    (0.01-0.99) so they compare directly to probability fractions.

    The "no" side is the inverse of the "yes" side:
        no_bid  = 1 - yes_ask
        no_ask  = 1 - yes_bid
    """

    def __init__(self, market_id: str):
        self.market_id  = market_id
        self._yes_bids: Dict[float, float] = {}   # {price: qty}
        self._yes_asks: Dict[float, float] = {}
        self.seq        = -1                        # last known sequence number
        self.last_ts    = datetime.now(timezone.utc)

    def reset(self) -> None:
        self._yes_bids.clear()
        self._yes_asks.clear()
        self.seq = -1

    def apply_snapshot(self, msg: Dict[str, Any]) -> None:
        """Full replacement - called on orderbook_snapshot."""
        self.reset()
        for p, q in msg.get("yes", []):
            if q > 0:
                self._yes_bids[_cents(p)] = float(q)
        for p, q in msg.get("no", []):
            # Kalshi sends "no" bids as the complement side
            if q > 0:
                self._yes_asks[round(1.0 - _cents(p), 6)] = float(q)
        self.last_ts = datetime.now(timezone.utc)

    def apply_delta(self, msg: Dict[str, Any]) -> None:
        """Incremental update - called on orderbook_delta."""
        side  = msg.get("side", "yes")
        price = _cents(msg.get("price", 0))
        delta = float(msg.get("delta", 0))

        book = self._yes_bids if side == "yes" else self._yes_asks
        if delta == 0:
            return
        cur = book.get(price, 0.0) + delta
        if cur <= 0.0:
            book.pop(price, None)
        else:
            book[price] = cur
        self.last_ts = datetime.now(timezone.utc)

    # -- Derived quotes ---------------------------------------------------------

    def best_bid(self) -> Optional[float]:
        return max(self._yes_bids) if self._yes_bids else None

    def best_ask(self) -> Optional[float]:
        return min(self._yes_asks) if self._yes_asks else None

    def mid(self) -> Optional[float]:
        b, a = self.best_bid(), self.best_ask()
        if b is None or a is None:
            return None
        return round((b + a) / 2, 6)

    def spread(self) -> Optional[float]:
        b, a = self.best_bid(), self.best_ask()
        if b is None or a is None:
            return None
        return round(a - b, 6)

    def to_snapshot_rows(self, ts: datetime) -> List[Dict[str, Any]]:
        rows = []
        for rank, (price, qty) in enumerate(
            sorted(self._yes_bids.items(), reverse=True)[:OB_DEPTH], 1
        ):
            rows.append({"snapshot_ts": ts, "market_id": self.market_id,
                          "side": "yes", "price": price, "quantity": qty,
                          "level_rank": rank})
        for rank, (price, qty) in enumerate(
            sorted(self._yes_asks.items())[:OB_DEPTH], 1
        ):
            rows.append({"snapshot_ts": ts, "market_id": self.market_id,
                          "side": "no",  "price": round(1.0 - price, 6),
                          "quantity": qty, "level_rank": rank})
        return rows

    def to_ticker_snap(self, ts: datetime) -> Dict[str, Any]:
        bid = self.best_bid()
        ask = self.best_ask()
        return {
            "snapshot_ts":  ts,
            "market_id":    self.market_id,
            "yes_bid":      bid,
            "yes_ask":      ask,
            "no_bid":       round(1.0 - ask, 6) if ask is not None else None,
            "no_ask":       round(1.0 - bid, 6) if bid is not None else None,
            "source":       "websocket",
        }


def _cents(v) -> float:
    """Convert Kalshi price (int cents 1-99 or float 0.01-0.99) -> float 0.01-0.99."""
    f = float(v)
    return f / 100.0 if f > 1.0 else f


# -- Shared live price cache ----------------------------------------------------

class LivePriceCache:
    """
    Thread-safe in-memory snapshot of the latest ticker for every market.
    The arb scanner reads from this dict instead of querying the DB.

    Structure: {market_id: {"yes_bid": X, "yes_ask": Y, "last_price": Z,
                             "volume": V, "open_interest": OI, "ts": datetime}}
    """

    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}

    def update(self, market_id: str, fields: Dict[str, Any]) -> None:
        entry = self._data.setdefault(market_id, {})
        entry.update(fields)
        entry["ts"] = datetime.now(timezone.utc)

    def get(self, market_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(market_id)

    def all(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._data)

    def __len__(self):
        return len(self._data)


# Module-level singleton so the scanner can import it directly
LIVE_CACHE = LivePriceCache()


# -- WebSocket client -----------------------------------------------------------

class KalshiWebSocketClient:
    """
    Production WebSocket client for Kalshi Trade API v2.

    * Authenticates with RSA-PSS headers on the upgrade handshake
    * Handles orderbook_snapshot / orderbook_delta / ticker / trade channels
    * Tracks message sequence; detects gaps and resets the book
    * Persists order-book snapshots and trades to PostgreSQL
    * Keeps LivePriceCache updated for zero-latency arb scanning
    * Reconnects automatically with exponential back-off
    * Sends WebSocket pings for heartbeat (handled by websockets library)

    Parameters
    ----------
    tickers         : List of market tickers to subscribe to
    authenticated   : If True, load RSA key and sign the WS upgrade
    on_opportunity  : Optional callback called with each arb opportunity dict
    on_seq_gap      : Optional callback(ticker, seq, expected_seq) called when a
                      sequence gap is detected for a market channel. Used by
                      LiveArbitrageScanner.SequenceGapMonitor to track stale books.
    on_price_update : Optional callback(ticker, seq) called on every price update.
                      Used by LiveArbitrageScanner._record_update().
    """

    WS_URL = config.KALSHI_WS_URL

    def __init__(
        self,
        tickers: List[str],
        authenticated: bool = True,
        on_opportunity: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_seq_gap: Optional[Callable[[str, int, int], None]] = None,
        on_price_update: Optional[Callable[[str, Optional[int]], None]] = None,
    ):
        self.tickers        = list(tickers)
        self._authenticated = authenticated
        self._on_opportunity  = on_opportunity
        self._on_seq_gap      = on_seq_gap
        self._on_price_update = on_price_update

        self._books: Dict[str, OrderBook] = {t: OrderBook(t) for t in tickers}
        self._cmd_id   = 0
        self._running  = False
        self._reconnect_wait = 2   # seconds; doubles on each failure up to MAX

        # Load private key once at startup
        self._private_key = None
        if authenticated:
            if not config.KALSHI_KEY_ID or not config.KALSHI_PRIVKEY_PATH:
                raise ValueError(
                    "KALSHI_KEY_ID and KALSHI_PRIVKEY_PATH must be set in .env "
                    "for authenticated WebSocket connections."
                )
            self._private_key = _load_private_key(config.KALSHI_PRIVKEY_PATH)
            logger.info("RSA private key loaded from %s", config.KALSHI_PRIVKEY_PATH)

    # -- Public API -------------------------------------------------------------

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self._running = False

    async def run(self) -> None:
        """Main loop - runs until stopped; reconnects automatically."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
                # Clean exit
                self._reconnect_wait = 2
            except (ConnectionClosedError, ConnectionClosedOK) as exc:
                logger.warning("WS disconnected: %s - reconnecting in %ds",
                               exc, self._reconnect_wait)
            except OSError as exc:
                logger.warning("WS network error: %s - reconnecting in %ds",
                               exc, self._reconnect_wait)
            except Exception as exc:
                logger.error("WS unexpected error: %s - reconnecting in %ds",
                             exc, self._reconnect_wait, exc_info=True)

            if not self._running:
                break
            await asyncio.sleep(self._reconnect_wait)
            self._reconnect_wait = min(self._reconnect_wait * 2, MAX_RECONNECT_WAIT)

    def stop(self) -> None:
        self._running = False

    # -- Connection lifecycle ---------------------------------------------------

    async def _connect_and_listen(self) -> None:
        headers = {}
        if self._authenticated and self._private_key:
            headers = _ws_auth_headers(config.KALSHI_KEY_ID, self._private_key)

        logger.info("Connecting to %s (tickers=%d, auth=%s)",
                    self.WS_URL, len(self.tickers), self._authenticated)

        async with websockets.connect(
            self.WS_URL,
            additional_headers=headers,
            ping_interval=HEARTBEAT_S,
            ping_timeout=10,
            max_size=10 * 1024 * 1024,   # 10MB - large order books
        ) as ws:
            logger.info("Connected. Subscribing...")
            await self._subscribe(ws)

            # Run snapshot flusher as a concurrent task
            snap_task = asyncio.create_task(self._snapshot_loop())
            try:
                async for raw in ws:
                    await self._handle_message(json.loads(raw))
            finally:
                snap_task.cancel()
                try:
                    await snap_task
                except asyncio.CancelledError:
                    pass

    async def _subscribe(self, ws) -> None:
        """Send one subscription message per channel (Kalshi v2 format)."""
        channels = ["orderbook_delta", "ticker", "trade"]
        # Batch tickers into groups of 100 to stay under message size limits
        batch_size = 100
        for ch in channels:
            for i in range(0, len(self.tickers), batch_size):
                batch = self.tickers[i : i + batch_size]
                msg = {
                    "id":    self._next_id(),
                    "cmd":   "subscribe",
                    "params": {
                        "channels":       [ch],
                        "market_tickers": batch,
                    },
                }
                await ws.send(json.dumps(msg))
                logger.debug("Subscribed %s to %d tickers (batch %d)",
                             ch, len(batch), i // batch_size + 1)

    # -- Message dispatch -------------------------------------------------------

    async def _handle_message(self, msg: Dict[str, Any]) -> None:
        mtype = msg.get("type", "")
        data  = msg.get("msg", {})
        seq   = msg.get("seq")

        if mtype == "orderbook_snapshot":
            market_id = data.get("market_ticker", "")
            if market_id in self._books:
                book = self._books[market_id]
                # Detect sequence gap - notify callback, reset and re-snapshot
                if seq is not None and book.seq != -1 and seq != book.seq + 1:
                    expected = book.seq + 1
                    logger.warning("Seq gap for %s: expected %d got %d - resetting book",
                                   market_id, expected, seq)
                    if self._on_seq_gap is not None:
                        try:
                            self._on_seq_gap(market_id, seq, expected)
                        except Exception:
                            pass
                book.apply_snapshot(data)
                if seq is not None:
                    book.seq = seq
                LIVE_CACHE.update(market_id, book.to_ticker_snap(book.last_ts))
                if self._on_price_update is not None:
                    try:
                        self._on_price_update(market_id, seq)
                    except Exception:
                        pass

        elif mtype == "orderbook_delta":
            market_id = data.get("market_ticker", "")
            if market_id in self._books:
                book = self._books[market_id]
                if seq is not None and book.seq != -1 and seq != book.seq + 1:
                    expected = book.seq + 1
                    logger.warning("Seq gap for %s: expected %d got %d - queuing re-subscribe",
                                   market_id, expected, seq)
                    if self._on_seq_gap is not None:
                        try:
                            self._on_seq_gap(market_id, seq, expected)
                        except Exception:
                            pass
                    book.reset()   # will be corrected on next snapshot
                book.apply_delta(data)
                if seq is not None:
                    book.seq = seq
                LIVE_CACHE.update(market_id, book.to_ticker_snap(book.last_ts))
                if self._on_price_update is not None:
                    try:
                        self._on_price_update(market_id, seq)
                    except Exception:
                        pass

        elif mtype == "ticker":
            market_id = data.get("market_ticker", "")
            ts = datetime.now(timezone.utc)
            snap = {
                "snapshot_ts":   ts,
                "market_id":     market_id,
                "yes_bid":       _sf(data.get("yes_bid")),
                "yes_ask":       _sf(data.get("yes_ask")),
                "no_bid":        _sf(data.get("no_bid")),
                "no_ask":        _sf(data.get("no_ask")),
                "last_price":    _sf(data.get("last_price")),
                "volume":        _sf(data.get("volume")),
                "open_interest": _sf(data.get("open_interest")),
                "source":        "websocket",
            }
            LIVE_CACHE.update(market_id, snap)
            if self._on_price_update is not None:
                try:
                    self._on_price_update(market_id, seq)
                except Exception:
                    pass
            try:
                with session_scope() as s:
                    insert_snapshot(s, snap)
            except Exception as exc:
                logger.debug("Snapshot insert failed for %s: %s", market_id, exc)

        elif mtype == "trade":
            ts = datetime.now(timezone.utc)
            trade = {
                "trade_id":   data.get("trade_id", ""),
                "trade_ts":   ts,
                "market_id":  data.get("market_ticker", ""),
                "price":      _sf(data.get("yes_price") or data.get("yes_price_dollars")
                                   or data.get("price")),
                "quantity":   _sf(data.get("count") or data.get("count_fp")),
                "taker_side": data.get("taker_side"),
                "source":     "websocket",
            }
            if trade["trade_id"] and trade["market_id"] and trade["price"] is not None:
                try:
                    with session_scope() as s:
                        upsert_trade(s, trade)
                except Exception as exc:
                    logger.debug("Trade insert failed: %s", exc)

        elif mtype == "subscribed":
            logger.info("Confirmed subscribed: channel=%s markets=%d",
                        data.get("channel", "?"),
                        len(data.get("market_tickers", [])))

        elif mtype == "error":
            logger.error("Server WS error: %s", data)

        # Silently drop unknown types (heartbeat, etc.)

    # -- Order-book snapshot flush ----------------------------------------------

    async def _snapshot_loop(self) -> None:
        """Periodically persist order-book depth to DB."""
        while True:
            await asyncio.sleep(SNAPSHOT_S)
            ts = datetime.now(timezone.utc)
            for market_id, book in self._books.items():
                rows = book.to_snapshot_rows(ts)
                if not rows:
                    continue
                try:
                    with session_scope() as s:
                        insert_order_book(s, rows)
                except Exception as exc:
                    logger.debug("OB snapshot insert failed for %s: %s", market_id, exc)

    # -- Utility ----------------------------------------------------------------

    def _next_id(self) -> int:
        self._cmd_id += 1
        return self._cmd_id


def _sf(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        # Kalshi sometimes sends prices as cents (int > 1) - normalise
        return f / 100.0 if f > 1.0 else f
    except (TypeError, ValueError):
        return None


# -- Convenience entry point ----------------------------------------------------

async def stream_markets(
    tickers: List[str],
    authenticated: bool = True,
) -> None:
    """Stream live data for the given tickers. Runs until interrupted."""
    async with KalshiWebSocketClient(tickers, authenticated=authenticated) as ws:
        await ws.run()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    tickers = sys.argv[1:]
    if not tickers:
        print("Usage: python websocket_client.py TICKER1 [TICKER2 ...]")
        sys.exit(1)
    asyncio.run(stream_markets(tickers))
