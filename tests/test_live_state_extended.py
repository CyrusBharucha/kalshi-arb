"""
tests/test_live_state_extended.py
====================================
Extended unit tests for dashboard/live_state.py.

Covers functions not tested in test_live_state.py:
  - LiveState.update_book(): updates market quote from raw delta dict
  - LiveState.get_book(): returns dict or None; correct fields
  - LiveState.market_count(): length of _markets
  - LiveState.top_by_volume(): sorted list of tickers
  - LiveState.get_recent_opportunities(): returns list
  - LiveState.add_opportunity(): respects _arb_max cap

No DB, no network.
"""
from __future__ import annotations

import pytest


def _make_state():
    from dashboard.live_state import LiveState
    return LiveState()


# ---------------------------------------------------------------------------
# LiveState.update_book  (raw delta dict path)
# ---------------------------------------------------------------------------

class TestUpdateBook:
    def test_creates_market_entry(self):
        state = _make_state()
        state.update_book("KXTEST-01", {"yes_bids": [[45, 10]], "yes_asks": [[55, 5]]})
        assert state.market_count() == 1

    def test_yes_bid_set_from_best_level(self):
        state = _make_state()
        state.update_book("KXTEST-01", {"yes_bids": [[45, 10]], "yes_asks": []})
        book = state.get_book("KXTEST-01")
        assert book["yes_bid"] == pytest.approx(0.45)

    def test_yes_ask_set_from_best_level(self):
        state = _make_state()
        state.update_book("KXTEST-01", {"yes_bids": [], "yes_asks": [[55, 5]]})
        book = state.get_book("KXTEST-01")
        assert book["yes_ask"] == pytest.approx(0.55)

    def test_no_bid_complement_of_yes_ask(self):
        state = _make_state()
        state.update_book("KXTEST-01", {"yes_bids": [[45, 10]], "yes_asks": [[55, 5]]})
        book = state.get_book("KXTEST-01")
        assert book["no_bid"] == pytest.approx(1.0 - 0.55)

    def test_mid_computed(self):
        state = _make_state()
        state.update_book("KXTEST-01", {"yes_bids": [[45, 10]], "yes_asks": [[55, 5]]})
        book = state.get_book("KXTEST-01")
        assert book["mid"] == pytest.approx(0.50)

    def test_spread_computed(self):
        state = _make_state()
        state.update_book("KXTEST-01", {"yes_bids": [[45, 10]], "yes_asks": [[55, 5]]})
        book = state.get_book("KXTEST-01")
        assert book["spread"] == pytest.approx(0.10)

    def test_sequence_updated(self):
        state = _make_state()
        state.update_book("KXTEST-01", {"yes_bids": [[45, 10]], "yes_asks": [], "sequence": 42})
        book = state.get_book("KXTEST-01")
        assert book["sequence"] == 42

    def test_yes_bids_stored_as_price_per_100(self):
        state = _make_state()
        state.update_book("KXTEST-01", {"yes_bids": [[45, 10], [40, 5]], "yes_asks": []})
        book = state.get_book("KXTEST-01")
        # yes_bids should be [[0.45, 10], [0.40, 5]]
        assert len(book["yes_bids"]) == 2
        assert book["yes_bids"][0][0] == pytest.approx(0.45)

    def test_update_merges_existing_market(self):
        state = _make_state()
        state.update_book("MKT-A", {"yes_bids": [[45, 10]], "yes_asks": []})
        state.update_book("MKT-A", {"yes_bids": [[46, 8]], "yes_asks": []})
        assert state.market_count() == 1  # still 1 market

    def test_empty_bids_does_not_update_yes_bid(self):
        state = _make_state()
        # No yes_bids → yes_bid stays at default 0.0
        state.update_book("MKT-A", {"yes_bids": [], "yes_asks": []})
        book = state.get_book("MKT-A")
        assert book["yes_bid"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# LiveState.get_book
# ---------------------------------------------------------------------------

class TestGetBook:
    def test_returns_none_for_missing_ticker(self):
        state = _make_state()
        assert state.get_book("NONEXISTENT") is None

    def test_returns_dict_for_known_ticker(self):
        state = _make_state()
        state.update_book("MKT-X", {"yes_bids": [[50, 5]], "yes_asks": [[60, 3]]})
        result = state.get_book("MKT-X")
        assert isinstance(result, dict)

    def test_source_is_synthesis_live(self):
        state = _make_state()
        state.update_book("MKT-X", {})
        book = state.get_book("MKT-X")
        assert book["source"] == "synthesis_live"

    def test_has_is_stale_field(self):
        state = _make_state()
        state.update_book("MKT-X", {})
        book = state.get_book("MKT-X")
        assert "is_stale" in book
        assert isinstance(book["is_stale"], bool)

    def test_has_l2_available_field(self):
        state = _make_state()
        state.update_book("MKT-X", {"yes_bids": [[45, 5], [40, 3]], "yes_asks": []})
        book = state.get_book("MKT-X")
        assert "l2_available" in book

    def test_l2_available_true_for_multiple_levels(self):
        state = _make_state()
        state.update_book("MKT-X", {"yes_bids": [[45, 5], [40, 3]], "yes_asks": []})
        book = state.get_book("MKT-X")
        assert book["l2_available"] is True

    def test_has_age_seconds_field(self):
        state = _make_state()
        state.update_book("MKT-X", {})
        book = state.get_book("MKT-X")
        assert "age_seconds" in book
        assert isinstance(book["age_seconds"], float)


# ---------------------------------------------------------------------------
# LiveState.market_count
# ---------------------------------------------------------------------------

class TestMarketCount:
    def test_starts_at_zero(self):
        state = _make_state()
        assert state.market_count() == 0

    def test_increments_with_new_markets(self):
        state = _make_state()
        state.update_book("MKT-A", {})
        state.update_book("MKT-B", {})
        assert state.market_count() == 2

    def test_same_market_twice_stays_at_one(self):
        state = _make_state()
        state.update_book("MKT-A", {})
        state.update_book("MKT-A", {})
        assert state.market_count() == 1


# ---------------------------------------------------------------------------
# LiveState.top_by_volume / snapshot_all
# ---------------------------------------------------------------------------

class TestTopByVolume:
    def test_returns_list(self):
        state = _make_state()
        result = state.top_by_volume()
        assert isinstance(result, list)

    def test_empty_state_returns_empty(self):
        state = _make_state()
        assert state.top_by_volume() == []

    def test_returns_n_markets(self):
        state = _make_state()
        for i in range(5):
            state.update_book(f"MKT-{i}", {})
        result = state.top_by_volume(n=3)
        assert len(result) <= 3

    def test_no_crash_with_n_larger_than_market_count(self):
        state = _make_state()
        state.update_book("MKT-A", {})
        result = state.top_by_volume(n=50)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# LiveState.add_opportunity cap
# ---------------------------------------------------------------------------

class TestAddOpportunityCap:
    def test_cap_enforced(self):
        state = _make_state()
        state._arb_max = 5
        for i in range(10):
            state.add_opportunity({"id": i, "edge": 0.05})
        # Only the last 5 should remain (most recent first)
        opps = state.get_recent_opportunities(limit=100)
        assert len(opps) == 5

    def test_most_recent_is_first(self):
        state = _make_state()
        state.add_opportunity({"id": 1})
        state.add_opportunity({"id": 2})
        opps = state.get_recent_opportunities(limit=10)
        assert opps[0]["id"] == 2  # newest inserted last, queue inserts at 0
