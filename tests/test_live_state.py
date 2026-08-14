"""
tests/test_live_state.py
=========================
Unit tests for dashboard/live_state.py:
  - MarketQuote: age_seconds, is_stale, l2_available
  - FeedStats: default values and field types
  - LiveState: state mutations, get_stats shape, thread safety smoke test

No DB, no WebSocket — pure in-memory tests.
"""
from __future__ import annotations

import time
import threading
from dataclasses import fields as dc_fields
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# MarketQuote tests
# ---------------------------------------------------------------------------

class TestMarketQuote:
    """MarketQuote dataclass and its computed properties."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.live_state import MarketQuote
        self.MarketQuote = MarketQuote

    def _make(self, **kwargs):
        defaults = {"ticker": "TEST-YES", "updated_at": time.time()}
        defaults.update(kwargs)
        return self.MarketQuote(**defaults)

    def test_instantiates(self):
        mq = self._make()
        assert mq.ticker == "TEST-YES"

    def test_age_seconds_is_float(self):
        mq = self._make()
        assert isinstance(mq.age_seconds, float)

    def test_age_seconds_near_zero_for_fresh_quote(self):
        mq = self._make(updated_at=time.time())
        assert mq.age_seconds < 1.0

    def test_age_seconds_grows_with_older_timestamp(self):
        mq = self._make(updated_at=time.time() - 5.0)
        assert mq.age_seconds >= 4.9

    def test_is_stale_false_for_fresh(self):
        mq = self._make(updated_at=time.time())
        assert not mq.is_stale

    def test_is_stale_true_after_300s(self):
        mq = self._make(updated_at=time.time() - 301.0)
        assert mq.is_stale

    def test_is_stale_threshold_is_300s(self):
        mq_fresh = self._make(updated_at=time.time() - 299.0)
        mq_stale = self._make(updated_at=time.time() - 301.0)
        assert not mq_fresh.is_stale
        assert mq_stale.is_stale

    def test_l2_available_false_for_empty_book(self):
        mq = self._make()
        # default yes_bids and yes_asks are empty lists
        assert not mq.l2_available

    def test_l2_available_false_for_single_level(self):
        mq = self._make(yes_bids=[[0.5, 100]], yes_asks=[])
        assert not mq.l2_available

    def test_l2_available_true_for_multiple_bid_levels(self):
        mq = self._make(yes_bids=[[0.5, 100], [0.49, 200]], yes_asks=[])
        assert mq.l2_available

    def test_l2_available_true_for_multiple_ask_levels(self):
        mq = self._make(yes_bids=[], yes_asks=[[0.51, 100], [0.52, 200]])
        assert mq.l2_available

    def test_default_spread_is_1(self):
        mq = self._make()
        assert mq.spread == 1.0

    def test_default_mid_is_half(self):
        mq = self._make()
        assert mq.mid == 0.5


# ---------------------------------------------------------------------------
# FeedStats tests
# ---------------------------------------------------------------------------

class TestFeedStats:
    """FeedStats default values."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.live_state import FeedStats
        self.FeedStats = FeedStats

    def test_connected_defaults_false(self):
        fs = self.FeedStats()
        assert fs.connected is False

    def test_messages_total_defaults_zero(self):
        assert self.FeedStats().messages_total == 0

    def test_sequence_errors_defaults_zero(self):
        assert self.FeedStats().sequence_errors == 0

    def test_reconnects_defaults_zero(self):
        assert self.FeedStats().reconnects == 0

    def test_latency_ms_defaults_none(self):
        assert self.FeedStats().latency_ms is None

    def test_last_message_ts_defaults_none(self):
        assert self.FeedStats().last_message_ts is None


# ---------------------------------------------------------------------------
# LiveState tests
# ---------------------------------------------------------------------------

class TestLiveState:
    """LiveState singleton and state mutation methods."""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        """Reset the singleton before each test for isolation."""
        from dashboard.live_state import LiveState
        LiveState._instance = None
        self.LiveState = LiveState
        yield
        LiveState._instance = None

    def _state(self):
        return self.LiveState()  # fresh instance (singleton reset above)

    def test_instantiates(self):
        state = self._state()
        assert state is not None

    def test_singleton_pattern(self):
        s1 = self.LiveState.instance()
        s2 = self.LiveState.instance()
        assert s1 is s2

    def test_market_count_starts_at_zero(self):
        state = self._state()
        assert state.market_count() == 0

    def test_record_sequence_error_increments_count(self):
        state = self._state()
        state.record_sequence_error()
        state.record_sequence_error()
        stats = state.get_stats()
        assert stats["sequence_errors"] == 2

    def test_record_dropped_increments_count(self):
        state = self._state()
        state.record_dropped()
        stats = state.get_stats()
        assert stats["dropped_messages"] == 1

    def test_set_connected_updates_stats(self):
        state = self._state()
        state.set_connected(True)
        assert state.get_stats()["connected"] is True

    def test_set_disconnected_updates_stats(self):
        state = self._state()
        state.set_connected(True)
        state.set_connected(False)
        assert state.get_stats()["connected"] is False

    def test_update_msg_rate(self):
        state = self._state()
        state.update_msg_rate(3400.0)
        stats = state.get_stats()
        assert abs(stats["messages_per_sec"] - 3400.0) < 0.1

    def test_add_opportunity_stored(self):
        state = self._state()
        state.add_opportunity({"net_edge": 0.05, "strategy_type": "yes_no_complement"})
        opps = state.get_recent_opportunities()
        assert len(opps) == 1

    def test_get_recent_opportunities_limit(self):
        state = self._state()
        for i in range(10):
            state.add_opportunity({"net_edge": float(i)})
        opps = state.get_recent_opportunities(limit=5)
        assert len(opps) == 5

    def test_get_stats_has_required_keys(self):
        state = self._state()
        stats = state.get_stats()
        required = {"connected", "messages_total", "sequence_errors", "markets_tracked"}
        for k in required:
            assert k in stats, f"get_stats() missing key '{k}'"

    def test_record_message_increments_total(self):
        state = self._state()
        state.record_message()
        state.record_message()
        assert state.get_stats()["messages_total"] == 2

    def test_snapshot_all_empty_initially(self):
        state = self._state()
        snap = state.snapshot_all()
        assert isinstance(snap, dict)
        assert len(snap) == 0

    def test_thread_safety_concurrent_record(self):
        """Multiple threads recording messages should not raise."""
        state = self._state()
        errors = []

        def worker():
            try:
                for _ in range(100):
                    state.record_message()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert state.get_stats()["messages_total"] == 500
