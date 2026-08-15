"""
tests/test_relationship_runner_extended.py
============================================
Extended unit tests for analysis/relationship_runner.py beyond constants and RunStats:
  - _flush_rels([], stats): returns early, no session opened
  - MAX_ME_MARKETS constant: positive int
  - RunStats.summary() contains multi_market key

No DB, no network.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


class TestMaxMeMarkets:
    """MAX_ME_MARKETS constant: positive, reasonable upper bound."""

    def test_is_positive(self):
        from analysis.relationship_runner import MAX_ME_MARKETS
        assert MAX_ME_MARKETS > 0

    def test_is_int(self):
        from analysis.relationship_runner import MAX_ME_MARKETS
        assert isinstance(MAX_ME_MARKETS, int)

    def test_prevents_exponential_blowup(self):
        # If MAX_ME_MARKETS is too small, we skip legit events;
        # if too large, O(n^2) pairs become slow. 5..500 is reasonable.
        from analysis.relationship_runner import MAX_ME_MARKETS
        assert 5 <= MAX_ME_MARKETS <= 500


class TestFlushRelsEmpty:
    """_flush_rels([], stats): early return, no DB access."""

    def test_empty_batch_does_not_open_session(self):
        from analysis.relationship_runner import _flush_rels, RunStats
        stats = RunStats()
        mock_ss = MagicMock()
        with patch("analysis.relationship_runner.session_scope", return_value=mock_ss) as scope:
            _flush_rels([], stats)
        scope.assert_not_called()

    def test_empty_batch_does_not_change_rels_written(self):
        from analysis.relationship_runner import _flush_rels, RunStats
        stats = RunStats()
        with patch("analysis.relationship_runner.session_scope", MagicMock()):
            _flush_rels([], stats)
        assert stats.rels_written == 0

    def test_returns_none(self):
        from analysis.relationship_runner import _flush_rels, RunStats
        stats = RunStats()
        with patch("analysis.relationship_runner.session_scope", MagicMock()):
            result = _flush_rels([], stats)
        assert result is None


class TestRunStatsSummaryContents:
    """RunStats.summary() string: additional content checks."""

    def test_summary_contains_multi_market_events(self):
        from analysis.relationship_runner import RunStats
        stats = RunStats()
        stats.events_with_multi = 5
        assert "multi" in stats.summary().lower() or "5" in stats.summary()

    def test_summary_contains_rels_written_value(self):
        from analysis.relationship_runner import RunStats
        stats = RunStats()
        stats.rels_written = 42
        assert "42" in stats.summary()

    def test_summary_contains_events_seen_value(self):
        from analysis.relationship_runner import RunStats
        stats = RunStats()
        stats.events_seen = 100
        assert "100" in stats.summary()
