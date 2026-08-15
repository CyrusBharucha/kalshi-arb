"""
tests/test_ws_orderbook.py
============================
Unit tests for data/websocket_client.py OrderBook class.

OrderBook maintains a local order book from Kalshi WS snapshots + deltas.
Tests cover:
  - reset(): clears bids, asks, seq
  - apply_snapshot(): yes side as bids, no side as asks (complement price)
  - apply_delta(): additive delta, removal on qty <= 0
  - best_bid() / best_ask(): max/min price, None when empty
  - mid(): average of best_bid + best_ask; None when either missing
  - spread(): ask - bid; None when either missing

No DB, no network.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    """Patch DB imports so websocket_client can be imported."""
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(session_scope=mock_ss),
        "database.models": MagicMock(),
    }):
        with patch("database.repository.session_scope", return_value=mock_ss):
            yield


def _make_book(market_id="KXTEST-01"):
    from feeds.websocket_client import OrderBook
    return OrderBook(market_id)


class TestOrderBookInit:
    def test_initial_market_id(self):
        b = _make_book("MKT-X")
        assert b.market_id == "MKT-X"

    def test_initial_seq_minus_one(self):
        b = _make_book()
        assert b.seq == -1

    def test_initial_bids_empty(self):
        b = _make_book()
        assert b._yes_bids == {}

    def test_initial_asks_empty(self):
        b = _make_book()
        assert b._yes_asks == {}


class TestOrderBookReset:
    def test_reset_clears_bids(self):
        b = _make_book()
        b._yes_bids[0.45] = 10
        b.reset()
        assert b._yes_bids == {}

    def test_reset_clears_asks(self):
        b = _make_book()
        b._yes_asks[0.55] = 5
        b.reset()
        assert b._yes_asks == {}

    def test_reset_sets_seq_to_minus_one(self):
        b = _make_book()
        b.seq = 42
        b.reset()
        assert b.seq == -1


class TestOrderBookApplySnapshot:
    """apply_snapshot() loads yes bids and NO bids as yes asks (complement)."""

    def test_yes_side_populates_bids(self):
        b = _make_book()
        b.apply_snapshot({"yes": [[45, 10], [40, 5]], "no": []})
        assert 0.45 in b._yes_bids
        assert b._yes_bids[0.45] == 10.0

    def test_no_side_populates_asks_as_complement(self):
        """No bid at price 60 → yes_ask at 1.0 - 0.60 = 0.40."""
        b = _make_book()
        b.apply_snapshot({"yes": [], "no": [[60, 8]]})
        # no price 60 cents → 0.60; complement → 1.0 - 0.60 = 0.40
        assert round(0.40, 6) in b._yes_asks

    def test_zero_qty_yes_not_stored(self):
        b = _make_book()
        b.apply_snapshot({"yes": [[45, 0]], "no": []})
        assert 0.45 not in b._yes_bids

    def test_zero_qty_no_not_stored(self):
        b = _make_book()
        b.apply_snapshot({"yes": [], "no": [[55, 0]]})
        assert b._yes_asks == {}

    def test_snapshot_replaces_existing(self):
        b = _make_book()
        b._yes_bids[0.30] = 100
        b.apply_snapshot({"yes": [[45, 10]], "no": []})
        # Old bid gone, new one present
        assert 0.30 not in b._yes_bids
        assert 0.45 in b._yes_bids

    def test_empty_snapshot_clears_book(self):
        b = _make_book()
        b._yes_bids[0.45] = 10
        b.apply_snapshot({"yes": [], "no": []})
        assert b._yes_bids == {}

    def test_cent_prices_normalized(self):
        """Price sent as integer cents (45 → 0.45)."""
        b = _make_book()
        b.apply_snapshot({"yes": [[45, 10]], "no": []})
        assert 0.45 in b._yes_bids


class TestOrderBookApplyDelta:
    """apply_delta() applies incremental updates."""

    def test_positive_delta_adds_to_bid(self):
        b = _make_book()
        b._yes_bids[0.45] = 5.0
        b.apply_delta({"side": "yes", "price": 45, "delta": 3})
        assert b._yes_bids[0.45] == 8.0

    def test_negative_delta_reduces_bid(self):
        b = _make_book()
        b._yes_bids[0.45] = 10.0
        b.apply_delta({"side": "yes", "price": 45, "delta": -4})
        assert b._yes_bids[0.45] == 6.0

    def test_delta_to_zero_removes_level(self):
        b = _make_book()
        b._yes_bids[0.45] = 5.0
        b.apply_delta({"side": "yes", "price": 45, "delta": -5})
        assert 0.45 not in b._yes_bids

    def test_delta_below_zero_removes_level(self):
        b = _make_book()
        b._yes_bids[0.45] = 3.0
        b.apply_delta({"side": "yes", "price": 45, "delta": -10})
        assert 0.45 not in b._yes_bids

    def test_zero_delta_is_noop(self):
        b = _make_book()
        b._yes_bids[0.45] = 5.0
        b.apply_delta({"side": "yes", "price": 45, "delta": 0})
        assert b._yes_bids[0.45] == 5.0

    def test_ask_side_delta(self):
        b = _make_book()
        b._yes_asks[0.55] = 3.0
        b.apply_delta({"side": "no", "price": 55, "delta": 2})
        assert b._yes_asks[0.55] == 5.0

    def test_new_level_created_from_delta(self):
        b = _make_book()
        b.apply_delta({"side": "yes", "price": 42, "delta": 7})
        assert b._yes_bids[0.42] == 7.0


class TestOrderBookDerivedQuotes:
    """best_bid, best_ask, mid, spread."""

    def _populate(self, b):
        b._yes_bids = {0.44: 5, 0.42: 10}   # best bid = 0.44
        b._yes_asks = {0.46: 5, 0.48: 10}   # best ask = 0.46

    def test_best_bid_max_price(self):
        b = _make_book()
        self._populate(b)
        assert b.best_bid() == pytest.approx(0.44)

    def test_best_ask_min_price(self):
        b = _make_book()
        self._populate(b)
        assert b.best_ask() == pytest.approx(0.46)

    def test_best_bid_none_when_empty(self):
        b = _make_book()
        assert b.best_bid() is None

    def test_best_ask_none_when_empty(self):
        b = _make_book()
        assert b.best_ask() is None

    def test_mid_average_of_bid_ask(self):
        b = _make_book()
        self._populate(b)
        assert b.mid() == pytest.approx(0.45)

    def test_mid_none_when_no_bids(self):
        b = _make_book()
        b._yes_asks = {0.55: 5}
        assert b.mid() is None

    def test_mid_none_when_no_asks(self):
        b = _make_book()
        b._yes_bids = {0.45: 5}
        assert b.mid() is None

    def test_spread_ask_minus_bid(self):
        b = _make_book()
        self._populate(b)
        assert b.spread() == pytest.approx(0.02)

    def test_spread_none_when_empty(self):
        b = _make_book()
        assert b.spread() is None
