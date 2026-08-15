"""
tests/test_relationship_runner.py
===================================
Unit tests for analysis/relationship_runner.py:
  - Constants: MKT_PAGE, REL_BATCH_SIZE, LOG_EVERY (positive ints)
  - RunStats dataclass: defaults, elapsed(), summary() string format

No DB required — these are pure-Python dataclass and constant tests.
"""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


class TestConstants:
    """Module-level tuning constants."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.relationship_runner import MKT_PAGE, REL_BATCH_SIZE, LOG_EVERY
        self.mkt_page      = MKT_PAGE
        self.rel_batch     = REL_BATCH_SIZE
        self.log_every     = LOG_EVERY

    def test_mkt_page_is_positive_int(self):
        assert isinstance(self.mkt_page, int) and self.mkt_page > 0

    def test_rel_batch_size_is_positive_int(self):
        assert isinstance(self.rel_batch, int) and self.rel_batch > 0

    def test_log_every_is_positive_int(self):
        assert isinstance(self.log_every, int) and self.log_every > 0

    def test_mkt_page_at_least_1000(self):
        """Should page in large chunks to stay efficient."""
        assert self.mkt_page >= 1000

    def test_rel_batch_at_least_100(self):
        assert self.rel_batch >= 100


class TestRunStats:
    """RunStats dataclass: defaults, elapsed, summary."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.relationship_runner import RunStats
        self.RunStats = RunStats

    def test_instantiates_with_defaults(self):
        rs = self.RunStats()
        assert rs is not None

    def test_events_seen_default_zero(self):
        assert self.RunStats().events_seen == 0

    def test_events_with_multi_default_zero(self):
        assert self.RunStats().events_with_multi == 0

    def test_rels_found_default_zero(self):
        assert self.RunStats().rels_found == 0

    def test_rels_written_default_zero(self):
        assert self.RunStats().rels_written == 0

    def test_elapsed_is_float(self):
        rs = self.RunStats()
        assert isinstance(rs.elapsed(), float)

    def test_elapsed_near_zero_immediately(self):
        rs = self.RunStats()
        assert rs.elapsed() < 1.0

    def test_elapsed_grows_over_time(self):
        rs = self.RunStats()
        t0 = rs.elapsed()
        time.sleep(0.01)
        t1 = rs.elapsed()
        assert t1 > t0

    def test_summary_is_string(self):
        rs = self.RunStats()
        assert isinstance(rs.summary(), str)

    def test_summary_contains_events_seen(self):
        rs = self.RunStats(events_seen=1234)
        assert "1,234" in rs.summary() or "events_seen=1,234" in rs.summary()

    def test_summary_contains_rels_found(self):
        rs = self.RunStats(rels_found=999)
        assert "999" in rs.summary()

    def test_summary_contains_rels_written(self):
        rs = self.RunStats(rels_written=555)
        assert "555" in rs.summary()

    def test_summary_contains_elapsed_label(self):
        rs = self.RunStats()
        assert "elapsed" in rs.summary()

    def test_fields_mutable(self):
        rs = self.RunStats()
        rs.events_seen = 100
        rs.rels_found = 50
        rs.rels_written = 48
        assert rs.events_seen == 100
        assert rs.rels_found == 50
        assert rs.rels_written == 48
