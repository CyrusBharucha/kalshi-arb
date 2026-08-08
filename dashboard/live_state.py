"""
dashboard/live_state.py
=======================
Shared in-process state for the live Synthesis WebSocket feed.

Architecture:
    SynthesisWebSocketClient (background thread)
          ↓  updates
    LiveState singleton (thread-safe dict)
          ↑  reads
    Streamlit pages (via get_live_state())

The WebSocket runs in its own thread, independent of Streamlit reruns.
Streamlit pages call get_live_state().snapshot() to get a point-in-time
copy of the current market data - zero blocking.

This avoids opening a new WebSocket connection on every Streamlit rerun.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from dashboard.live_arb_store import persist as _arb_persist
except Exception:
    _arb_persist = None  # type: ignore


@dataclass
class MarketQuote:
    """Current best-bid/ask for one market."""
    ticker: str
    yes_bid: float = 0.0
    yes_ask: float = 1.0
    no_bid:  float = 0.0
    no_ask:  float = 1.0
    mid:     float = 0.5
    spread:  float = 1.0
    # Full L2 depth (list of [price, size] pairs)
    yes_bids: List[List[float]] = field(default_factory=list)
    yes_asks: List[List[float]] = field(default_factory=list)
    sequence: int  = 0
    updated_at: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.updated_at

    @property
    def is_stale(self) -> bool:
        # 300s threshold: resting L2 book prices are valid until cancelled or
        # settled; a short WS silence doesn't invalidate existing order prices.
        return self.age_seconds > 300

    @property
    def l2_available(self) -> bool:
        return len(self.yes_bids) > 1 or len(self.yes_asks) > 1


@dataclass
class FeedStats:
    """WebSocket feed statistics."""
    connected: bool = False
    messages_total: int = 0
    messages_per_sec: float = 0.0
    markets_tracked: int = 0
    reconnects: int = 0
    sequence_errors: int = 0
    dropped_messages: int = 0
    last_message_ts: Optional[float] = None
    latency_ms: Optional[float] = None
    start_time: float = field(default_factory=time.time)


class LiveState:
    """
    Thread-safe singleton holding live market data.
    Updated by the WebSocket thread; read by Streamlit pages.
    """

    _instance: Optional["LiveState"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._data_lock  = threading.RLock()
        self._markets:    Dict[str, MarketQuote] = {}
        self._stats:      FeedStats = FeedStats()
        self._arb_queue:  List[Dict[str, Any]] = []  # recent live opportunities (capped)
        self._arb_max:    int = 200
        # Non-decaying session totals (not affected by queue cap)
        self._session_arbs_total:    int = 0
        self._session_arbs_ce:       int = 0
        self._session_arbs_comp:     int = 0
        self._session_best_edge:     float = 0.0
        self._session_last_arb_ts:   Optional[float] = None
        self._n_clean:               int = 0
        self._funnel:                Dict[str, Any] = {}

    # -- Singleton access ------------------------------------------------------

    @classmethod
    def instance(cls) -> "LiveState":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # -- Write methods (called from WebSocket thread) --------------------------

    # Maximum L2 depth levels to store per market (top-of-book is sufficient for arb scanning)
    _L2_DEPTH_CAP = 5
    # Evict markets not updated in this many seconds (likely closed/delisted)
    _STALE_EVICT_S = 3600  # 1 hour
    # Hard cap on total tracked markets to bound memory on Streamlit Cloud
    _MARKETS_CAP = 8000

    def update_from_book(self, book: Any) -> None:
        """
        Update market quote from an L2Book object (data/orderbook_l2.py).
        This is the primary bridge between the Synthesis WS client and the dashboard.
        """
        with self._data_lock:
            ticker = book.market_id
            q = self._markets.get(ticker) or MarketQuote(ticker=ticker)

            # Pull top-of-book from the L2Book
            yb = book.yes.best_bid_level()
            ya = book.yes.best_ask_level()
            nb = book.no.best_bid_level()
            na = book.no.best_ask_level()

            q.yes_bid = yb.price if yb else (book.yes.best_bid or 0.0)
            q.yes_ask = ya.price if ya else (book.yes.best_ask or 1.0)
            q.no_bid  = nb.price if nb else (book.no.best_bid  or 0.0)
            q.no_ask  = na.price if na else (book.no.best_ask  or 1.0)

            # Cap L2 depth to avoid unbounded memory growth from deep books
            q.yes_bids = [[lvl.price, lvl.quantity] for lvl in book.yes.sorted_bids()][:self._L2_DEPTH_CAP]
            q.yes_asks = [[lvl.price, lvl.quantity] for lvl in book.yes.sorted_asks()][:self._L2_DEPTH_CAP]

            q.mid     = round((q.yes_bid + q.yes_ask) / 2.0, 4)
            q.spread  = round(q.yes_ask - q.yes_bid, 4)
            q.sequence   = book.sequence
            q.updated_at = time.time()

            self._markets[ticker] = q
            self._stats.markets_tracked = len(self._markets)

    def update_book(self, ticker: str, update: Dict[str, Any]) -> None:
        """Update market quote from a raw delta dict (legacy / test path)."""
        with self._data_lock:
            q = self._markets.get(ticker) or MarketQuote(ticker=ticker)

            yes_bids = update.get("yes_bids", [])
            yes_asks = update.get("yes_asks", [])

            if yes_bids:
                q.yes_bid  = yes_bids[0][0] / 100.0
                q.yes_bids = [[p / 100.0, s] for p, s in yes_bids]
            if yes_asks:
                q.yes_ask  = yes_asks[0][0] / 100.0
                q.yes_asks = [[p / 100.0, s] for p, s in yes_asks]

            # NO side = complement
            q.no_bid = round(1.0 - q.yes_ask, 4)
            q.no_ask = round(1.0 - q.yes_bid, 4)
            q.mid    = round((q.yes_bid + q.yes_ask) / 2.0, 4)
            q.spread = round(q.yes_ask - q.yes_bid, 4)

            if "sequence" in update:
                q.sequence = int(update["sequence"])
            q.updated_at = time.time()

            self._markets[ticker] = q
            self._stats.markets_tracked = len(self._markets)

    def record_message(self, latency_ms: Optional[float] = None) -> None:
        with self._data_lock:
            self._stats.messages_total += 1
            self._stats.last_message_ts = time.time()
            if latency_ms is not None:
                self._stats.latency_ms = latency_ms

    def set_connected(self, connected: bool) -> None:
        with self._data_lock:
            if connected and not self._stats.connected:
                self._stats.reconnects += 1
            self._stats.connected = connected

    def add_opportunity(self, opp: Dict[str, Any]) -> None:
        with self._data_lock:
            # Always copy so caller mutations can't corrupt the queued entry.
            opp = dict(opp)
            # Stamp detection time if the scanner hasn't already set it
            if "detected_at_ts" not in opp:
                opp["detected_at_ts"] = time.time()
            self._arb_queue.insert(0, opp)
            if len(self._arb_queue) > self._arb_max:
                self._arb_queue = self._arb_queue[: self._arb_max]
            # Update non-decaying session totals
            self._session_arbs_total += 1
            if opp.get("net_edge_cents", 0) <= 50:
                self._n_clean += 1
            if opp.get("strategy") == "collectively_exhaustive":
                self._session_arbs_ce += 1
            else:
                self._session_arbs_comp += 1
            _edge = float(opp.get("net_edge_cents", 0))
            if _edge > self._session_best_edge:
                self._session_best_edge = _edge
            self._session_last_arb_ts = time.time()
        # Persist to live_arbs.db — outside the data lock to avoid contention
        if _arb_persist is not None:
            try:
                _arb_persist(opp)
            except Exception as _pe:
                logger.debug("live_state: persist failed: %s", _pe)

    def record_scanner_funnel(self, stats: dict) -> None:
        with self._data_lock:
            self._funnel = dict(stats)

    def get_scanner_funnel(self) -> dict:
        with self._data_lock:
            return dict(self._funnel)

    def get_session_stats(self) -> Dict[str, Any]:
        """Return non-decaying session-level arb scanner stats."""
        with self._data_lock:
            return {
                "total":        self._session_arbs_total,
                "complement":   self._session_arbs_comp,
                "ce":           self._session_arbs_ce,
                "best_edge":    self._session_best_edge,
                "last_arb_ts":  self._session_last_arb_ts,
                "last_ts":      self._session_last_arb_ts,  # alias for p01 compat
                "clean":        self._n_clean,
                "pre_fix":      self._session_arbs_total - self._n_clean,
            }

    def evict_stale_markets(self) -> int:
        """
        Remove markets not updated in _STALE_EVICT_S seconds.
        Also enforces _MARKETS_CAP by dropping the stalest entries if over limit.
        Returns the number of evicted entries.
        """
        now = time.time()
        with self._data_lock:
            cutoff = now - self._STALE_EVICT_S
            stale = [tk for tk, q in self._markets.items() if q.updated_at < cutoff]
            for tk in stale:
                del self._markets[tk]
            # If still over cap, drop the stalest remaining entries
            if len(self._markets) > self._MARKETS_CAP:
                sorted_by_age = sorted(self._markets.items(), key=lambda kv: kv[1].updated_at)
                excess = len(self._markets) - self._MARKETS_CAP
                for tk, _ in sorted_by_age[:excess]:
                    del self._markets[tk]
                stale.extend(tk for tk, _ in sorted_by_age[:excess])
            self._stats.markets_tracked = len(self._markets)
            return len(stale)

    def record_sequence_error(self) -> None:
        with self._data_lock:
            self._stats.sequence_errors += 1

    def record_dropped(self) -> None:
        with self._data_lock:
            self._stats.dropped_messages += 1

    def update_msg_rate(self, rate: float) -> None:
        with self._data_lock:
            self._stats.messages_per_sec = rate

    # -- Read methods (called from Streamlit) ----------------------------------

    def get_book(self, ticker: str) -> Optional[Dict[str, Any]]:
        with self._data_lock:
            q = self._markets.get(ticker)
            if q is None:
                return None
            return {
                "ticker":       q.ticker,
                "yes_bid":      q.yes_bid,
                "yes_ask":      q.yes_ask,
                "no_bid":       q.no_bid,
                "no_ask":       q.no_ask,
                "mid":          q.mid,
                "spread":       q.spread,
                "yes_bids":     q.yes_bids,
                "yes_asks":     q.yes_asks,
                "sequence":     q.sequence,
                "age_seconds":  q.age_seconds,
                "is_stale":     q.is_stale,
                "l2_available": q.l2_available,
                "source":       "synthesis_live",
            }

    def snapshot_all(self) -> Dict[str, MarketQuote]:
        """Return a shallow copy of all current market quotes."""
        with self._data_lock:
            return dict(self._markets)

    def get_stats(self) -> Dict[str, Any]:
        with self._data_lock:
            s = self._stats
            age = (
                round(time.time() - s.last_message_ts, 1)
                if s.last_message_ts else None
            )
            uptime = round(time.time() - s.start_time, 0)
            return {
                "connected":        s.connected,
                "messages_total":   s.messages_total,
                "messages_per_sec": round(s.messages_per_sec, 1),
                "markets_tracked":  s.markets_tracked,
                "reconnects":       s.reconnects,
                "sequence_errors":  s.sequence_errors,
                "dropped_messages": s.dropped_messages,
                "last_message_ts":  s.last_message_ts,
                "last_message_age_s": age,
                "latency_ms":       s.latency_ms,
                "uptime_s":         uptime,
            }

    def get_recent_opportunities(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._data_lock:
            return list(self._arb_queue[:limit])

    def arb_counts_by_strategy(self) -> Dict[str, int]:
        """Return session-lifetime per-strategy arb counters from ws_bridge."""
        try:
            from dashboard import ws_bridge as _wsb
            return _wsb.get_arb_counts()
        except Exception:
            return {"ync": 0, "ce": 0, "me": 0, "th": 0}

    def market_count(self) -> int:
        with self._data_lock:
            return len(self._markets)

    def top_by_volume(self, n: int = 20) -> List[MarketQuote]:
        """Return top N markets by spread tightness (proxy for liquidity)."""
        with self._data_lock:
            books = list(self._markets.values())
        return sorted(books, key=lambda q: q.spread)[:n]


def get_live_state() -> LiveState:
    """Public accessor - use this everywhere."""
    return LiveState.instance()
