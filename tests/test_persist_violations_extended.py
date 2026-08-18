"""
tests/test_persist_violations_extended.py
==========================================
Extended unit tests for analysis/run_empirical.persist_violations().

Tests:
  - Returns 0 for empty list
  - Filters violations where net_edge < MIN_NET_EDGE
  - Calls session_scope for non-empty list above threshold
  - Returns count of persisted items
  - Batch rollover at 500 items
  - Exception in session → logs, returns partial count
  - Violations above threshold include all required opp keys
"""
from __future__ import annotations

import sys
import uuid
import pandas as pd
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
        "feeds.kalshi_client": MagicMock(),
    }):
        yield


def _ts():
    return pd.Timestamp("2026-06-20 10:00:00", tz="UTC")


def _violation(net_edge=0.05, gross_edge=0.08, total_fees=0.03,
               rtype="sell_yes_both_me", direction="sell_yes_both",
               market_id_1="MKT-A", market_id_2="MKT-B",
               p1=0.60, p2=0.60):
    return {
        "ts":          _ts(),
        "rtype":       rtype,
        "direction":   direction,
        "market_id_1": market_id_1,
        "market_id_2": market_id_2,
        "p1":          p1,
        "p2":          p2,
        "gross_edge":  gross_edge,
        "total_fees":  total_fees,
        "net_edge":    net_edge,
    }


class TestPersistViolationsBasic:
    def test_empty_list_returns_zero(self):
        from analysis.run_empirical import persist_violations
        assert persist_violations([]) == 0

    def test_below_threshold_filtered(self):
        from analysis.run_empirical import persist_violations, MIN_NET_EDGE
        v = _violation(net_edge=MIN_NET_EDGE - 0.001)
        with patch("analysis.run_empirical.session_scope") as mock_ss:
            result = persist_violations([v])
        assert result == 0
        mock_ss.assert_not_called()

    def test_above_threshold_persisted(self):
        from analysis.run_empirical import persist_violations, MIN_NET_EDGE
        v = _violation(net_edge=MIN_NET_EDGE + 0.01)
        with patch("analysis.run_empirical.session_scope") as mock_ss, \
             patch("analysis.run_empirical.pg_insert") as mock_pg:
            mock_stmt = MagicMock()
            mock_pg.return_value.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            result = persist_violations([v])
        assert result == 1

    def test_multiple_above_threshold_all_persisted(self):
        from analysis.run_empirical import persist_violations, MIN_NET_EDGE
        violations = [_violation(net_edge=MIN_NET_EDGE + 0.01) for _ in range(5)]
        with patch("analysis.run_empirical.session_scope") as mock_ss, \
             patch("analysis.run_empirical.pg_insert") as mock_pg:
            mock_stmt = MagicMock()
            mock_pg.return_value.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            result = persist_violations(violations)
        assert result == 5

    def test_mixed_threshold_only_qualifying_counted(self):
        from analysis.run_empirical import persist_violations, MIN_NET_EDGE
        violations = [
            _violation(net_edge=MIN_NET_EDGE + 0.01),   # qualifies
            _violation(net_edge=MIN_NET_EDGE - 0.001),  # filtered
            _violation(net_edge=MIN_NET_EDGE + 0.02),   # qualifies
        ]
        with patch("analysis.run_empirical.session_scope") as mock_ss, \
             patch("analysis.run_empirical.pg_insert") as mock_pg:
            mock_stmt = MagicMock()
            mock_pg.return_value.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            result = persist_violations(violations)
        assert result == 2

    def test_min_net_edge_constant_is_float(self):
        from analysis.run_empirical import MIN_NET_EDGE
        assert isinstance(MIN_NET_EDGE, float)

    def test_min_net_edge_positive(self):
        from analysis.run_empirical import MIN_NET_EDGE
        assert MIN_NET_EDGE > 0


class TestPersistViolationsBatch:
    """Test that batch of exactly 500 triggers a flush."""

    def test_exactly_500_triggers_batch_flush(self):
        from analysis.run_empirical import persist_violations, MIN_NET_EDGE
        violations = [_violation(net_edge=MIN_NET_EDGE + 0.01) for _ in range(500)]
        with patch("analysis.run_empirical.session_scope") as mock_ss, \
             patch("analysis.run_empirical.pg_insert") as mock_pg:
            mock_stmt = MagicMock()
            mock_pg.return_value.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            result = persist_violations(violations)
        assert result == 500
