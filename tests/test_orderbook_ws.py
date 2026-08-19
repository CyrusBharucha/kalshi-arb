"""
tests/test_orderbook_ws.py
===========================
Unit tests for the in-memory OrderBook and LivePriceCache classes
in data/websocket_client.py.

No live WebSocket, no database, no API keys needed.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import patch


# Patch DB imports so the module loads without a real DB connection
@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    with (
        patch("feeds.websocket_client.session_scope"),
        patch("feeds.websocket_client.insert_snapshot"),
        patch("feeds.websocket_client.insert_order_book"),
        patch("feeds.websocket_client.upsert_trade"),
    ):
        yield


from feeds.websocket_client import OrderBook, LivePriceCache, _cents


# ---------------------------------------------------------------------------
# _cents helper
# ---------------------------------------------------------------------------

class TestCentsHelper:
    def test_int_cents_converted(self):
        assert abs(_cents(50) - 0.50) < 1e-9

    def test_float_already_decimal(self):
        """Values <= 1 should be returned as-is."""
        assert abs(_cents(0.50) - 0.50) < 1e-9

    def test_one_cent_as_int(self):
        """Integer 1 is NOT treated as 1 cent (1 is not > 1.0 as float).
        To pass 1 cent, use 100 (which _cents converts: 100/100 = 1.0)
        or the value 0.01 directly."""
        # _cents(1.0) returns 1.0 (treated as decimal, not cents)
        assert _cents(1.0) == 1.0
        # For actual 1 cent, pass the integer 100 * 0.01 = 1: caller uses > 1.0 check
        # e.g. _cents(2) = 2/100 = 0.02
        assert abs(_cents(2) - 0.02) < 1e-9

    def test_99_cents(self):
        assert abs(_cents(99) - 0.99) < 1e-9

    def test_boundary_one(self):
        """Exact 1.0 is treated as 1 cent (f > 1.0 is False) -> returns 1.0."""
        assert _cents(1.0) == 1.0  # 1.0 / 100 won't happen; returned as-is


# ---------------------------------------------------------------------------
# OrderBook — initialisation
# ---------------------------------------------------------------------------

class TestOrderBookInit:
    def test_market_id_stored(self):
        ob = OrderBook("KXTEST-01")
        assert ob.market_id == "KXTEST-01"

    def test_empty_on_init(self):
        ob = OrderBook("KXTEST-01")
        assert ob.best_bid() is None
        assert ob.best_ask() is None

    def test_seq_minus_one_on_init(self):
        ob = OrderBook("X")
        assert ob.seq == -1


# ---------------------------------------------------------------------------
# OrderBook — apply_snapshot
# ---------------------------------------------------------------------------

class TestOrderBookSnapshot:
    def _make_ob(self):
        ob = OrderBook("TICKER-A")
        ob.apply_snapshot({
            "yes": [[50, 100], [49, 200]],
            "no":  [[47, 150]],          # no bid at 47c -> yes ask at 53c
        })
        return ob

    def test_best_bid_after_snapshot(self):
        ob = self._make_ob()
        # Yes bids at 50c and 49c -> best bid = 50c
        assert abs(ob.best_bid() - 0.50) < 1e-6

    def test_best_ask_after_snapshot(self):
        ob = self._make_ob()
        # No side: bid=47 → yes_ask = 1 - 0.47 = 0.53
        assert abs(ob.best_ask() - 0.53) < 1e-6

    def test_spread_positive(self):
        ob = self._make_ob()
        s = ob.spread()
        assert s is not None
        assert s > 0

    def test_mid_between_bid_and_ask(self):
        ob = self._make_ob()
        m = ob.mid()
        assert ob.best_bid() < m < ob.best_ask()

    def test_zero_qty_not_inserted(self):
        ob = OrderBook("X")
        ob.apply_snapshot({"yes": [[50, 0]], "no": [[45, 100]]})
        assert ob.best_bid() is None  # 0 qty should be skipped

    def test_snapshot_clears_old_data(self):
        ob = OrderBook("X")
        ob.apply_snapshot({"yes": [[50, 100]], "no": []})
        ob.apply_snapshot({"yes": [[40, 200]], "no": []})
        assert abs(ob.best_bid() - 0.40) < 1e-6  # old bid at 50 gone

    def test_to_ticker_snap_returns_bid_ask(self):
        ob = self._make_ob()
        snap = ob.to_ticker_snap(datetime.now(timezone.utc))
        assert snap["yes_bid"] is not None
        assert snap["yes_ask"] is not None

    def test_to_ticker_snap_no_bid_is_complement(self):
        ob = self._make_ob()
        snap = ob.to_ticker_snap(datetime.now(timezone.utc))
        # no_bid = 1 - yes_ask
        assert abs(snap["no_bid"] - (1.0 - snap["yes_ask"])) < 1e-6


# ---------------------------------------------------------------------------
# OrderBook — apply_delta
# ---------------------------------------------------------------------------

class TestOrderBookDelta:
    def _base_ob(self):
        ob = OrderBook("X")
        ob.apply_snapshot({"yes": [[50, 100]], "no": [[45, 200]]})
        return ob

    def test_add_qty_to_existing_level(self):
        ob = self._base_ob()
        ob.apply_delta({"side": "yes", "price": 50, "delta": 50})
        # 100 + 50 = 150 at 50c
        assert ob._yes_bids[0.50] == 150.0

    def test_remove_qty_from_level(self):
        ob = self._base_ob()
        ob.apply_delta({"side": "yes", "price": 50, "delta": -50})
        assert ob._yes_bids[0.50] == 50.0

    def test_remove_all_qty_drops_level(self):
        ob = self._base_ob()
        ob.apply_delta({"side": "yes", "price": 50, "delta": -100})
        assert 0.50 not in ob._yes_bids

    def test_negative_qty_drops_level(self):
        ob = self._base_ob()
        ob.apply_delta({"side": "yes", "price": 50, "delta": -200})  # over-remove
        assert 0.50 not in ob._yes_bids

    def test_new_price_level_added(self):
        ob = self._base_ob()
        ob.apply_delta({"side": "yes", "price": 48, "delta": 75})
        assert abs(ob._yes_bids.get(0.48, 0) - 75.0) < 1e-6

    def test_zero_delta_no_op(self):
        ob = self._base_ob()
        bids_before = dict(ob._yes_bids)
        ob.apply_delta({"side": "yes", "price": 50, "delta": 0})
        assert ob._yes_bids == bids_before


# ---------------------------------------------------------------------------
# OrderBook — reset
# ---------------------------------------------------------------------------

class TestOrderBookReset:
    def test_reset_clears_bids(self):
        ob = OrderBook("X")
        ob.apply_snapshot({"yes": [[50, 100]], "no": []})
        ob.reset()
        assert ob.best_bid() is None

    def test_reset_clears_asks(self):
        ob = OrderBook("X")
        ob.apply_snapshot({"yes": [], "no": [[45, 100]]})
        ob.reset()
        assert ob.best_ask() is None

    def test_reset_seq_minus_one(self):
        ob = OrderBook("X")
        ob.seq = 42
        ob.reset()
        assert ob.seq == -1


# ---------------------------------------------------------------------------
# LivePriceCache
# ---------------------------------------------------------------------------

class TestLivePriceCache:
    def test_update_and_get(self):
        cache = LivePriceCache()
        snap = {"yes_bid": 0.45, "yes_ask": 0.50, "source": "ws"}
        cache.update("TICKER-A", snap)
        result = cache.get("TICKER-A")
        assert result["yes_bid"] == 0.45

    def test_get_missing_returns_none(self):
        cache = LivePriceCache()
        assert cache.get("MISSING") is None

    def test_all_returns_all_entries(self):
        cache = LivePriceCache()
        cache.update("A", {"yes_bid": 0.4})
        cache.update("B", {"yes_bid": 0.6})
        all_entries = cache.all()
        assert "A" in all_entries and "B" in all_entries

    def test_update_overwrites(self):
        cache = LivePriceCache()
        cache.update("A", {"yes_bid": 0.4})
        cache.update("A", {"yes_bid": 0.5})
        assert cache.get("A")["yes_bid"] == 0.5

    def test_len_reflects_count(self):
        cache = LivePriceCache()
        cache.update("A", {})
        cache.update("B", {})
        assert len(cache) == 2
