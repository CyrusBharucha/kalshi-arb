"""
data/orderbook_l2.py
====================
Full L2 order-book reconstruction from Synthesis WebSocket deltas.

Each Synthesis delta contains:
  price  : a specific price level in the order book
  amount : absolute quantity at that price level (0 = level removed)
  side   : "yes" or "no" - which side of the market this level belongs to
  yes_best_bid / yes_best_ask : updated top-of-book (after this change)
  no_best_bid  / no_best_ask  : updated top-of-book (after this change)

Reconstruction rule:
  book[market_id][side][price] = amount   (delete if amount == 0)

Bid vs ask classification:
  For side="yes": price <= yes_best_bid  -> YES bid level
                  price >= yes_best_ask  -> YES ask level
  For side="no":  price <= no_best_bid   -> NO  bid level
                  price >= no_best_ask   -> NO  ask level
  Levels inside the spread are impossible in a valid book; we store them
  in a "mid" bucket and log a warning - they usually resolve on the next delta.

In Kalshi markets YES and NO are complementary:
  YES ask at P  <==>  NO bid at (1-P)   (buying YES at ask = selling NO at bid)
  NO  ask at P  <==>  YES bid at (1-P)
We expose both representations but store the native side label from Synthesis.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Tolerance for floating-point price comparisons
_PRICE_EPS = 1e-6


@dataclass
class PriceLevel:
    price:    float
    quantity: float


@dataclass
class SideBook:
    """One side (YES or NO) of the order book for a single market."""
    bids: Dict[float, float] = field(default_factory=dict)  # price -> qty
    asks: Dict[float, float] = field(default_factory=dict)  # price -> qty
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None

    def apply_level(
        self,
        price: float,
        quantity: float,
        best_bid: Optional[float],
        best_ask: Optional[float],
    ) -> str:
        """
        Apply one price-level update.
        Returns: "bid" | "ask" | "mid" (in spread, ambiguous)
        """
        self.best_bid = best_bid
        self.best_ask = best_ask

        if quantity == 0.0:
            # Level cleared - remove from whichever bucket it's in
            self.bids.pop(price, None)
            self.asks.pop(price, None)
            return "cleared"

        if best_bid is not None and price <= best_bid + _PRICE_EPS:
            self.bids[price] = quantity
            return "bid"
        elif best_ask is not None and price >= best_ask - _PRICE_EPS:
            self.asks[price] = quantity
            return "ask"
        else:
            # Inside spread or best not yet known - keep in bids as resting
            self.bids[price] = quantity
            return "mid"

    def sorted_bids(self) -> List[PriceLevel]:
        """Bids descending (best first)."""
        return [PriceLevel(p, q) for p, q in
                sorted(self.bids.items(), reverse=True) if q > 0]

    def sorted_asks(self) -> List[PriceLevel]:
        """Asks ascending (best first)."""
        return [PriceLevel(p, q) for p, q in
                sorted(self.asks.items()) if q > 0]

    def best_bid_level(self) -> Optional[PriceLevel]:
        bids = self.sorted_bids()
        return bids[0] if bids else None

    def best_ask_level(self) -> Optional[PriceLevel]:
        asks = self.sorted_asks()
        return asks[0] if asks else None

    def total_bid_qty(self) -> float:
        return sum(self.bids.values())

    def total_ask_qty(self) -> float:
        return sum(self.asks.values())

    def levels(self) -> int:
        return len(self.bids) + len(self.asks)


@dataclass
class L2Book:
    """Full L2 book for one Kalshi market (YES and NO sides)."""
    market_id:  str
    yes:        SideBook = field(default_factory=SideBook)
    no:         SideBook = field(default_factory=SideBook)
    sequence:   int      = 0
    updated_at: float    = field(default_factory=time.time)
    delta_count: int     = 0
    gap_detected: bool   = False   # True if a sequence gap was seen
    gap_count:    int    = 0       # total gaps seen for this market

    def __post_init__(self) -> None:
        # Per-book lock: protects all mutable state (yes/no SideBooks, sequence,
        # updated_at, delta_count, gap_detected, gap_count).  The WS writer
        # holds this lock while calling apply_delta(); readers (scanner thread)
        # hold it while calling yes_asks() / no_asks() / yes_bids() / no_bids().
        self._lock: threading.Lock = threading.Lock()

    @property
    def needs_snapshot(self) -> bool:
        """True when a sequence gap has been detected and the book may be stale."""
        return self.gap_detected

    def clear_gap(self) -> None:
        """Called after a full snapshot is received; clears the gap flag."""
        with self._lock:
            self.gap_detected = False

    def apply_delta(self, delta: Dict[str, Any]) -> bool:
        """
        Apply one Synthesis delta.  Returns True if applied, False if stale.

        Sequence gap detection:
          If seq > self.sequence + 1 (gap > 0), the book is marked stale
          (gap_detected=True, needs_snapshot=True) until a fresh snapshot
          is applied via clear_gap().
        """
        # Parse scalars before acquiring the lock so we minimise lock hold time.
        seq = int(delta.get("sequence", 0))
        side_str = (delta.get("side") or "").lower()
        try:
            price    = float(delta.get("price",  0))
            quantity = float(delta.get("amount", 0))
        except (TypeError, ValueError):
            return False

        ybb = _safe_float(delta.get("yes_best_bid"))
        yba = _safe_float(delta.get("yes_best_ask"))
        nbb = _safe_float(delta.get("no_best_bid"))
        nba = _safe_float(delta.get("no_best_ask"))

        with self._lock:
            if seq < self.sequence:
                return False                    # stale - discard

            # Detect sequence gap (skip first delta where sequence==0)
            if self.sequence > 0 and seq > self.sequence + 1:
                gap = seq - self.sequence - 1
                self.gap_detected = True
                self.gap_count += 1
                logger.warning(
                    "SEQ GAP market=%s prev=%d curr=%d gap=%d",
                    self.market_id, self.sequence, seq, gap,
                )

            if side_str == "yes":
                self.yes.apply_level(price, quantity, ybb, yba)
            elif side_str == "no":
                self.no.apply_level(price, quantity, nbb, nba)
            else:
                logger.debug("Unknown side %r for %s", side_str, self.market_id)

            self.sequence    = seq
            self.updated_at  = time.time()
            self.delta_count += 1
        return True

    # -- Derived views ----------------------------------------------------------
    # All public read methods acquire _lock so they are safe when called from
    # the scanner thread while the WS thread is inside apply_delta().

    def yes_bids(self, max_levels: int = 10) -> List[PriceLevel]:
        with self._lock:
            return self.yes.sorted_bids()[:max_levels]

    def yes_asks(self, max_levels: int = 10) -> List[PriceLevel]:
        with self._lock:
            return self.yes.sorted_asks()[:max_levels]

    def no_bids(self, max_levels: int = 10) -> List[PriceLevel]:
        with self._lock:
            return self.no.sorted_bids()[:max_levels]

    def no_asks(self, max_levels: int = 10) -> List[PriceLevel]:
        with self._lock:
            return self.no.sorted_asks()[:max_levels]

    def depth(self) -> Dict[str, int]:
        with self._lock:
            return {
                "yes_bids": len(self.yes.bids),
                "yes_asks": len(self.yes.asks),
                "no_bids":  len(self.no.bids),
                "no_asks":  len(self.no.asks),
                "total":    self.yes.levels() + self.no.levels(),
            }

    def vwap_yes_ask(self, target_qty: float) -> Optional[Tuple[float, float]]:
        """
        Volume-weighted average price to BUY target_qty YES contracts.
        Returns (vwap, actual_qty_filled) or None if insufficient liquidity.
        """
        with self._lock:
            return _vwap(self.yes.sorted_asks(), target_qty)

    def vwap_yes_bid(self, target_qty: float) -> Optional[Tuple[float, float]]:
        """VWAP to SELL target_qty YES contracts (walking down the bids)."""
        with self._lock:
            return _vwap(self.yes.sorted_bids(), target_qty)

    def vwap_no_ask(self, target_qty: float) -> Optional[Tuple[float, float]]:
        with self._lock:
            return _vwap(self.no.sorted_asks(), target_qty)

    def summary(self) -> str:
        with self._lock:
            yb = self.yes.best_bid_level()
            ya = self.yes.best_ask_level()
            nb = self.no.best_bid_level()
            na = self.no.best_ask_level()
            yb_n = len(self.yes.bids)
            ya_n = len(self.yes.asks)
            nb_n = len(self.no.bids)
            na_n = len(self.no.asks)
            seq  = self.sequence
            dc   = self.delta_count
        fmt = lambda lvl: f"{lvl.price:.2f}" if lvl else "?"
        return (
            f"{self.market_id}  "
            f"YES {fmt(yb)}/{fmt(ya)} "
            f"({yb_n}b/{ya_n}a)  "
            f"NO {fmt(nb)}/{fmt(na)} "
            f"({nb_n}b/{na_n}a)  "
            f"seq={seq:,} deltas={dc}"
        )


class L2Cache:
    """
    Thread-safe cache of L2Books for all active markets.
    Drop-in replacement for the existing LiveOrderbookCache - keeps
    backward-compatible top-of-book accessors while also maintaining
    full depth.
    """

    def __init__(self):
        self._books:     Dict[str, L2Book] = {}
        self._lock       = threading.Lock()
        self._callbacks: List[Any]         = []
        self._stats = {
            "total_deltas":   0,
            "stale_discarded": 0,
            "markets_seen":   0,
        }

    def register_callback(self, fn):
        self._callbacks.append(fn)

    def apply_delta(self, delta: Dict[str, Any]) -> Optional[L2Book]:
        mid = delta.get("market_id")
        if not mid:
            return None

        with self._lock:
            if mid not in self._books:
                self._books[mid] = L2Book(market_id=mid)
                self._stats["markets_seen"] += 1
            book = self._books[mid]

        applied = book.apply_delta(delta)

        with self._lock:
            self._stats["total_deltas"] += 1
            if not applied:
                self._stats["stale_discarded"] += 1
                return None

        for fn in self._callbacks:
            try:
                fn(book)
            except Exception as exc:
                logger.warning("L2 callback error: %s", exc)

        return book

    def get(self, market_id: str) -> Optional[L2Book]:
        with self._lock:
            return self._books.get(market_id)

    def snapshot(self) -> Dict[str, L2Book]:
        with self._lock:
            return dict(self._books)

    def stats(self) -> Dict:
        with self._lock:
            return dict(self._stats)

    # Hard cap: evict books not updated in the last N seconds, and enforce a
    # total size limit. Called from the arb-scanner eviction loop.
    _STALE_EVICT_S = 300   # 5 minutes — mirrors LiveState
    _BOOKS_CAP     = 2000  # mirrors LiveState._MARKETS_CAP

    def evict_stale(self) -> int:
        import time as _t
        cutoff = _t.time() - self._STALE_EVICT_S
        with self._lock:
            stale = [mid for mid, book in self._books.items()
                     if book.updated_at < cutoff]
            for mid in stale:
                del self._books[mid]
            if len(self._books) > self._BOOKS_CAP:
                sorted_books = sorted(
                    self._books.items(), key=lambda kv: kv[1].updated_at
                )
                excess = len(self._books) - self._BOOKS_CAP
                for mid, _ in sorted_books[:excess]:
                    del self._books[mid]
                stale.extend(mid for mid, _ in sorted_books[:excess])
            return len(stale)

    def __getitem__(self, market_id: str):
        with self._lock:
            return self._books[market_id]

    def __len__(self):
        with self._lock:
            return len(self._books)

    # -- Backward-compatible top-of-book accessor -------------------------------
    def top_of_book(self, market_id: str) -> Optional[Dict[str, float]]:
        """
        Returns {yes_bid, yes_ask, no_bid, no_ask} for backward compatibility
        with code that uses LiveOrderbookCache.
        """
        book = self.get(market_id)
        if not book:
            return None
        yb = book.yes.best_bid_level()
        ya = book.yes.best_ask_level()
        nb = book.no.best_bid_level()
        na = book.no.best_ask_level()
        return {
            "yes_bid": yb.price if yb else (book.yes.best_bid or 0.0),
            "yes_ask": ya.price if ya else (book.yes.best_ask or 1.0),
            "no_bid":  nb.price if nb else (book.no.best_bid  or 0.0),
            "no_ask":  na.price if na else (book.no.best_ask  or 1.0),
        }


# -- Helpers --------------------------------------------------------------------

def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _vwap(
    levels: List[PriceLevel],
    target_qty: float,
) -> Optional[Tuple[float, float]]:
    """
    Walk price levels to fill target_qty.
    Returns (vwap, actual_filled) - actual_filled may be < target if illiquid.
    Returns None if no levels at all.
    """
    if not levels:
        return None
    filled    = 0.0
    cost      = 0.0
    for lvl in levels:
        take = min(lvl.quantity, target_qty - filled)
        cost   += take * lvl.price
        filled += take
        if filled >= target_qty - _PRICE_EPS:
            break
    if filled < _PRICE_EPS:
        return None
    return (cost / filled, filled)
