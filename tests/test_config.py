"""
tests/test_config.py
=====================
Sanity-check tests for config.py constants and the SequenceGapMonitor
from arbitrage/live_scanner.py.

No database, no network.
"""
from __future__ import annotations

import pytest
import config
from engine.live_scanner import SequenceGapMonitor


# ---------------------------------------------------------------------------
# config.py constants
# ---------------------------------------------------------------------------

class TestConfigConstants:
    def test_fee_cap_is_3_5_cents(self):
        """Kalshi documented fee cap: $0.035 per contract."""
        assert abs(config.FEE_CAP - 0.035) < 1e-9

    def test_fee_alpha_is_7_percent(self):
        assert abs(config.FEE_ALPHA - 0.07) < 1e-9

    def test_min_gross_edge_positive(self):
        assert config.MIN_GROSS_EDGE > 0

    def test_min_net_edge_positive(self):
        assert config.MIN_NET_EDGE > 0

    def test_net_edge_less_than_gross_edge(self):
        assert config.MIN_NET_EDGE <= config.MIN_GROSS_EDGE

    def test_candle_periods_nonempty(self):
        assert len(config.KALSHI_CANDLE_PERIODS) > 0

    def test_candle_periods_all_positive(self):
        for p in config.KALSHI_CANDLE_PERIODS:
            assert p > 0

    def test_db_port_in_range(self):
        assert 1024 <= config.DB_PORT <= 65535

    def test_read_rps_positive(self):
        assert config.KALSHI_READ_RPS > 0

    def test_write_rps_positive(self):
        assert config.KALSHI_WRITE_RPS > 0

    def test_write_rps_less_than_read_rps(self):
        """Writes are more sensitive — write RPS should be <= read RPS."""
        assert config.KALSHI_WRITE_RPS <= config.KALSHI_READ_RPS

    def test_historical_lookback_at_least_30_days(self):
        assert config.HISTORICAL_LOOKBACK_DAYS >= 30

    def test_snapshot_interval_reasonable(self):
        assert 5 <= config.SNAPSHOT_INTERVAL_S <= 300

    def test_canadian_target_series_nonempty(self):
        assert len(config.CANADIAN_TARGET_SERIES) > 0

    def test_canadian_macro_events_nonempty(self):
        assert len(config.CANADIAN_MACRO_EVENTS) > 0


# ---------------------------------------------------------------------------
# SequenceGapMonitor
# ---------------------------------------------------------------------------

class TestSequenceGapMonitor:
    def test_init_no_gaps(self):
        m = SequenceGapMonitor()
        stats = m.stats()
        assert stats["total_gaps"] == 0

    def test_first_message_no_gap(self):
        m = SequenceGapMonitor()
        gap = m.update("TICKER-A", 1)
        assert gap == 0

    def test_consecutive_no_gap(self):
        m = SequenceGapMonitor()
        m.update("TICKER-A", 1)
        gap = m.update("TICKER-A", 2)
        assert gap == 0

    def test_gap_detected(self):
        m = SequenceGapMonitor()
        m.update("TICKER-A", 1)
        gap = m.update("TICKER-A", 5)
        assert gap == 3  # 5 - 1 - 1 = 3

    def test_gap_count_tracked(self):
        m = SequenceGapMonitor()
        m.update("TICKER-A", 1)
        m.update("TICKER-A", 5)   # gap 1
        m.update("TICKER-A", 6)
        m.update("TICKER-A", 10)  # gap 2
        assert m.stats()["total_gaps"] == 2

    def test_stale_market_after_gap(self):
        m = SequenceGapMonitor()
        m.update("TICKER-A", 1)
        m.update("TICKER-A", 5)
        assert m.is_stale("TICKER-A") is True

    def test_not_stale_after_consecutive(self):
        m = SequenceGapMonitor()
        m.update("TICKER-A", 1)
        m.update("TICKER-A", 2)
        assert m.is_stale("TICKER-A") is False

    def test_reset_clears_stale(self):
        m = SequenceGapMonitor()
        m.update("TICKER-A", 1)
        m.update("TICKER-A", 5)
        assert m.is_stale("TICKER-A") is True
        m.reset("TICKER-A")
        assert m.is_stale("TICKER-A") is False

    def test_reset_clears_seq(self):
        """After reset, first next message should not generate a gap."""
        m = SequenceGapMonitor()
        m.update("TICKER-A", 10)
        m.reset("TICKER-A")
        gap = m.update("TICKER-A", 100)  # large jump, but first after reset
        assert gap == 0

    def test_multiple_markets_independent(self):
        m = SequenceGapMonitor()
        m.update("TICKER-A", 1)
        m.update("TICKER-B", 1)
        m.update("TICKER-A", 5)   # gap in A
        assert m.is_stale("TICKER-A") is True
        assert m.is_stale("TICKER-B") is False

    def test_stats_contains_required_keys(self):
        m = SequenceGapMonitor()
        s = m.stats()
        for key in ("total_gaps", "stale_markets", "markets_tracked", "gap_counts"):
            assert key in s

    def test_markets_tracked_count(self):
        m = SequenceGapMonitor()
        m.update("A", 1)
        m.update("B", 1)
        m.update("C", 1)
        assert m.stats()["markets_tracked"] == 3

    def test_stale_markets_count_in_stats(self):
        m = SequenceGapMonitor()
        m.update("A", 1)
        m.update("A", 5)  # stale
        m.update("B", 1)
        assert m.stats()["stale_markets"] == 1
