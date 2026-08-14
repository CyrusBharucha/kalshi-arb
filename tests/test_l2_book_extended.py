"""
tests/test_l2_book_extended.py
================================
Extended unit tests for data/orderbook_l2.py — methods not covered in
test_l2_reconstruction.py:

  - L2Book.needs_snapshot: reflects gap_detected flag
  - L2Book.yes_bids / yes_asks: max_levels slicing, correct side
  - L2Book.no_bids / no_asks: from NO side
  - L2Book.vwap_yes_bid: VWAP walking bids (sell YES)
  - L2Book.vwap_no_ask: VWAP on NO ask side

No DB, no network.
"""
from __future__ import annotations

import pytest

from feeds.orderbook_l2 import L2Book, SideBook, PriceLevel


# ---------------------------------------------------------------------------
# Helper to build an L2Book with pre-populated sides
# ---------------------------------------------------------------------------

def _make_book(market_id="MKT-001") -> L2Book:
    return L2Book(market_id=market_id)


def _add_yes_bid(book: L2Book, price: float, qty: float) -> None:
    book.yes.apply_level(price, qty, best_bid=price + 0.02, best_ask=price + 0.04)


def _add_yes_ask(book: L2Book, price: float, qty: float) -> None:
    book.yes.apply_level(price, qty, best_bid=price - 0.04, best_ask=price - 0.02)


def _add_no_bid(book: L2Book, price: float, qty: float) -> None:
    book.no.apply_level(price, qty, best_bid=price + 0.02, best_ask=price + 0.04)


def _add_no_ask(book: L2Book, price: float, qty: float) -> None:
    book.no.apply_level(price, qty, best_bid=price - 0.04, best_ask=price - 0.02)


# ---------------------------------------------------------------------------
# needs_snapshot
# ---------------------------------------------------------------------------

class TestNeedsSnapshot:
    """needs_snapshot reflects the gap_detected flag."""

    def test_no_gap_initially(self):
        book = _make_book()
        assert book.needs_snapshot is False

    def test_needs_snapshot_after_gap_detected(self):
        book = _make_book()
        book.gap_detected = True
        assert book.needs_snapshot is True

    def test_clear_gap_resets_needs_snapshot(self):
        book = _make_book()
        book.gap_detected = True
        book.clear_gap()
        assert book.needs_snapshot is False

    def test_gap_via_apply_delta_sequence_jump(self):
        """Applying a delta with a sequence jump triggers gap_detected."""
        book = _make_book()
        # First delta sets sequence = 1
        book.apply_delta({"sequence": 1, "side": "yes",
                          "price": 0.45, "amount": 100,
                          "yes_best_bid": 0.44, "yes_best_ask": 0.46})
        # Skip to 5 (gap of 3)
        book.apply_delta({"sequence": 5, "side": "yes",
                          "price": 0.46, "amount": 100,
                          "yes_best_bid": 0.45, "yes_best_ask": 0.47})
        assert book.needs_snapshot is True


# ---------------------------------------------------------------------------
# yes_bids / yes_asks
# ---------------------------------------------------------------------------

class TestYesBidsAsks:
    """yes_bids() returns bid levels from YES side; yes_asks() returns ask levels."""

    @pytest.fixture(autouse=True)
    def _book(self):
        self.book = _make_book()

    def test_yes_bids_empty_initially(self):
        assert self.book.yes_bids() == []

    def test_yes_asks_empty_initially(self):
        assert self.book.yes_asks() == []

    def test_yes_bids_contains_bid_levels(self):
        # apply at price <= best_bid → goes to bids
        self.book.yes.apply_level(0.40, 50, best_bid=0.42, best_ask=0.44)
        bids = self.book.yes_bids()
        assert len(bids) == 1
        assert bids[0].price == 0.40

    def test_yes_asks_contains_ask_levels(self):
        # apply at price >= best_ask → goes to asks
        self.book.yes.apply_level(0.45, 80, best_bid=0.42, best_ask=0.44)
        asks = self.book.yes_asks()
        assert len(asks) == 1
        assert asks[0].price == 0.45

    def test_yes_bids_sorted_descending(self):
        self.book.yes.apply_level(0.38, 10, 0.42, 0.44)
        self.book.yes.apply_level(0.40, 20, 0.42, 0.44)
        self.book.yes.apply_level(0.36, 30, 0.42, 0.44)
        bids = self.book.yes_bids()
        prices = [b.price for b in bids]
        assert prices == sorted(prices, reverse=True)

    def test_yes_asks_sorted_ascending(self):
        self.book.yes.apply_level(0.48, 10, 0.42, 0.44)
        self.book.yes.apply_level(0.46, 20, 0.42, 0.44)
        self.book.yes.apply_level(0.50, 30, 0.42, 0.44)
        asks = self.book.yes_asks()
        prices = [a.price for a in asks]
        assert prices == sorted(prices)

    def test_yes_bids_max_levels_limits(self):
        for i in range(10):
            self.book.yes.apply_level(0.40 - i * 0.01, 10, 0.45, 0.47)
        bids = self.book.yes_bids(max_levels=3)
        assert len(bids) == 3

    def test_yes_asks_max_levels_limits(self):
        for i in range(10):
            self.book.yes.apply_level(0.50 + i * 0.01, 10, 0.45, 0.47)
        asks = self.book.yes_asks(max_levels=4)
        assert len(asks) == 4


# ---------------------------------------------------------------------------
# no_bids / no_asks
# ---------------------------------------------------------------------------

class TestNoBidsAsks:
    """no_bids() / no_asks() operate on the NO side."""

    @pytest.fixture(autouse=True)
    def _book(self):
        self.book = _make_book()

    def test_no_bids_empty_initially(self):
        assert self.book.no_bids() == []

    def test_no_asks_empty_initially(self):
        assert self.book.no_asks() == []

    def test_no_bids_only_from_no_side(self):
        # Add to YES side - should NOT appear in no_bids
        self.book.yes.apply_level(0.40, 50, 0.42, 0.44)
        assert self.book.no_bids() == []

    def test_no_bids_populated(self):
        self.book.no.apply_level(0.55, 100, best_bid=0.56, best_ask=0.58)
        bids = self.book.no_bids()
        assert len(bids) == 1
        assert bids[0].price == 0.55

    def test_no_asks_populated(self):
        self.book.no.apply_level(0.60, 75, best_bid=0.55, best_ask=0.58)
        asks = self.book.no_asks()
        assert len(asks) == 1
        assert asks[0].price == 0.60

    def test_no_bids_max_levels_limits(self):
        for i in range(8):
            self.book.no.apply_level(0.55 - i * 0.01, 10, 0.60, 0.62)
        bids = self.book.no_bids(max_levels=3)
        assert len(bids) == 3

    def test_no_asks_max_levels_limits(self):
        for i in range(8):
            self.book.no.apply_level(0.62 + i * 0.01, 10, 0.58, 0.60)
        asks = self.book.no_asks(max_levels=2)
        assert len(asks) == 2


# ---------------------------------------------------------------------------
# vwap_yes_bid (VWAP to SELL YES, walking bids)
# ---------------------------------------------------------------------------

class TestVwapYesBid:
    """vwap_yes_bid() walks YES bids to fill a sell order."""

    @pytest.fixture(autouse=True)
    def _book(self):
        self.book = _make_book()

    def test_empty_bids_returns_none(self):
        assert self.book.vwap_yes_bid(100.0) is None

    def test_single_level_full_fill(self):
        # 200 qty available at 0.40
        self.book.yes.apply_level(0.40, 200, 0.42, 0.44)
        result = self.book.vwap_yes_bid(100.0)
        assert result is not None
        vwap, filled = result
        assert abs(vwap - 0.40) < 1e-6
        assert abs(filled - 100.0) < 1e-6

    def test_single_level_partial_fill(self):
        # Only 50 available, target=100
        self.book.yes.apply_level(0.40, 50, 0.42, 0.44)
        result = self.book.vwap_yes_bid(100.0)
        assert result is not None
        vwap, filled = result
        assert abs(filled - 50.0) < 1e-6

    def test_two_level_walk(self):
        # Best bid = 0.45 (50 qty), then 0.40 (100 qty)
        self.book.yes.apply_level(0.45, 50, 0.46, 0.48)
        self.book.yes.apply_level(0.40, 100, 0.46, 0.48)
        result = self.book.vwap_yes_bid(60.0)
        assert result is not None
        vwap, filled = result
        # Uses 50@0.45 + 10@0.40 = (50*0.45 + 10*0.40) / 60
        expected = (50 * 0.45 + 10 * 0.40) / 60
        assert abs(vwap - expected) < 1e-6
        assert abs(filled - 60.0) < 1e-6

    def test_zero_target_returns_none(self):
        self.book.yes.apply_level(0.40, 100, 0.42, 0.44)
        assert self.book.vwap_yes_bid(0.0) is None


# ---------------------------------------------------------------------------
# vwap_no_ask (VWAP to BUY NO)
# ---------------------------------------------------------------------------

class TestVwapNoAsk:
    """vwap_no_ask() walks NO asks to fill a buy order."""

    @pytest.fixture(autouse=True)
    def _book(self):
        self.book = _make_book()

    def test_empty_no_asks_returns_none(self):
        assert self.book.vwap_no_ask(50.0) is None

    def test_single_level_no_ask_full_fill(self):
        # NO ask at 0.60, qty=200
        self.book.no.apply_level(0.60, 200, best_bid=0.55, best_ask=0.58)
        result = self.book.vwap_no_ask(100.0)
        assert result is not None
        vwap, filled = result
        assert abs(vwap - 0.60) < 1e-6
        assert abs(filled - 100.0) < 1e-6

    def test_single_level_no_ask_partial(self):
        self.book.no.apply_level(0.60, 30, best_bid=0.55, best_ask=0.58)
        result = self.book.vwap_no_ask(100.0)
        assert result is not None
        _, filled = result
        assert abs(filled - 30.0) < 1e-6

    def test_two_level_no_ask_walk(self):
        # NO asks: 0.58 (50 qty), 0.60 (100 qty)
        self.book.no.apply_level(0.58, 50, best_bid=0.55, best_ask=0.57)
        self.book.no.apply_level(0.60, 100, best_bid=0.55, best_ask=0.57)
        result = self.book.vwap_no_ask(70.0)
        assert result is not None
        vwap, filled = result
        expected = (50 * 0.58 + 20 * 0.60) / 70
        assert abs(vwap - expected) < 1e-6
        assert abs(filled - 70.0) < 1e-6
