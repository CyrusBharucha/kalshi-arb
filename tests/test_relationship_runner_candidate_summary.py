"""
tests/test_relationship_runner_candidate_summary.py
=====================================================
Unit tests for get_candidate_summary() and _process_event() in
analysis/relationship_runner.py.

No live DB — all DB calls are mocked.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock, call
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


# ---------------------------------------------------------------------------
# get_candidate_summary()
# ---------------------------------------------------------------------------

class TestGetCandidateSummary:
    """get_candidate_summary() returns dict with required keys from DB."""

    def _make_session(self, rows, total=5, candidates=3):
        """Build a mock session returning controllable query results."""
        mock_session = MagicMock()

        # fetchall() for by_type rows
        fetchall_result = MagicMock()
        fetchall_result.fetchall.return_value = rows

        # scalar() calls: first = total, second = candidates
        scalars = iter([total, candidates])

        scalar_mock = MagicMock()
        scalar_mock.scalar.side_effect = lambda: next(scalars)

        mock_session.execute.side_effect = [
            fetchall_result,   # GROUP BY relationship_type
            scalar_mock,       # COUNT(*) total
            scalar_mock,       # COUNT DISTINCT candidate
        ]
        return mock_session

    def test_required_keys_present(self):
        from analysis.relationship_runner import get_candidate_summary
        mock_ctx = MagicMock()
        mock_session = MagicMock()
        # Mock execute to return consistent results
        rows = [("mutually_exclusive", 10), ("complement", 50)]
        mock_session.execute.return_value.fetchall.return_value = rows
        mock_session.execute.return_value.scalar.return_value = 60
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        with patch("analysis.relationship_runner.session_scope", return_value=mock_ctx):
            result = get_candidate_summary()
        for key in ("total_relationships", "by_type", "candidate_markets"):
            assert key in result, f"Missing key: {key}"

    def test_by_type_is_dict(self):
        from analysis.relationship_runner import get_candidate_summary
        mock_ctx = MagicMock()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("complement", 50), ("mutually_exclusive", 10)
        ]
        mock_session.execute.return_value.scalar.return_value = 60
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        with patch("analysis.relationship_runner.session_scope", return_value=mock_ctx):
            result = get_candidate_summary()
        assert isinstance(result["by_type"], dict)

    def test_empty_rows_gives_empty_by_type(self):
        from analysis.relationship_runner import get_candidate_summary
        mock_ctx = MagicMock()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = []
        mock_session.execute.return_value.scalar.return_value = 0
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        with patch("analysis.relationship_runner.session_scope", return_value=mock_ctx):
            result = get_candidate_summary()
        assert result["by_type"] == {}

    def test_by_type_maps_rtype_to_count(self):
        from analysis.relationship_runner import get_candidate_summary
        mock_ctx = MagicMock()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("complement", 7), ("threshold_order", 3)
        ]
        mock_session.execute.return_value.scalar.return_value = 10
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        with patch("analysis.relationship_runner.session_scope", return_value=mock_ctx):
            result = get_candidate_summary()
        assert result["by_type"].get("complement") == 7
        assert result["by_type"].get("threshold_order") == 3


# ---------------------------------------------------------------------------
# _process_event() — small event (< MAX_ME_MARKETS)
# ---------------------------------------------------------------------------

class TestProcessEventSmall:
    """_process_event() with small event calls detect_all_relationships_for_event."""

    def _make_markets(self, n=2):
        return [
            {"market_id": f"MKT-{i}", "event_ticker": "EVT-X",
             "ticker": f"T-{i}", "floor_strike": i * 10.0,
             "cap_strike": None, "market_type": "binary",
             "outcome_type": "YES_NO", "title": f"Market {i}"}
            for i in range(n)
        ]

    def test_empty_markets_does_nothing(self):
        from analysis.relationship_runner import _process_event, RunStats
        stats = RunStats()
        candidates = set()
        batch = []
        with patch("engine.relationship_detector.detect_all_relationships_for_event",
                   return_value=[]) as det:
            _process_event("EVT-X", [], stats, candidates, batch)
        # With 0 markets, detect might still be called (depending on impl)
        assert stats.rels_found == 0
        assert len(batch) == 0

    def test_complement_only_does_not_add_to_candidates(self):
        from analysis.relationship_runner import _process_event, RunStats
        stats = RunStats()
        candidates = set()
        batch = []
        markets = self._make_markets(2)
        fake_rels = [{"relationship_type": "complement", "market_id_1": "A",
                      "market_id_2": "B", "confidence": 1.0}]
        with patch("analysis.relationship_runner.detect_all_relationships_for_event",
                   return_value=fake_rels):
            _process_event("EVT-X", markets, stats, candidates, batch)
        # complement-only → no candidate event added
        assert "EVT-X" not in candidates

    def test_non_trivial_rel_adds_to_candidates(self):
        from analysis.relationship_runner import _process_event, RunStats
        stats = RunStats()
        candidates = set()
        batch = []
        markets = self._make_markets(2)
        fake_rels = [{"relationship_type": "mutually_exclusive",
                      "market_id_1": "A", "market_id_2": "B", "confidence": 0.9}]
        with patch("analysis.relationship_runner.detect_all_relationships_for_event",
                   return_value=fake_rels):
            _process_event("EVT-X", markets, stats, candidates, batch)
        assert "EVT-X" in candidates

    def test_rels_found_incremented(self):
        from analysis.relationship_runner import _process_event, RunStats
        stats = RunStats()
        candidates = set()
        batch = []
        markets = self._make_markets(2)
        fake_rels = [
            {"relationship_type": "complement", "market_id_1": "A",
             "market_id_2": "B", "confidence": 1.0},
            {"relationship_type": "mutually_exclusive", "market_id_1": "A",
             "market_id_2": "B", "confidence": 0.9},
        ]
        with patch("analysis.relationship_runner.detect_all_relationships_for_event",
                   return_value=fake_rels):
            _process_event("EVT-X", markets, stats, candidates, batch)
        assert stats.rels_found == 2

    def test_rels_appended_to_batch(self):
        from analysis.relationship_runner import _process_event, RunStats
        stats = RunStats()
        candidates = set()
        batch = []
        markets = self._make_markets(2)
        fake_rels = [{"relationship_type": "complement", "market_id_1": "A",
                      "market_id_2": "B", "confidence": 1.0}]
        with patch("analysis.relationship_runner.detect_all_relationships_for_event",
                   return_value=fake_rels):
            _process_event("EVT-X", markets, stats, candidates, batch)
        assert len(batch) == 1
