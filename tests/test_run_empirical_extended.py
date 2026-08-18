"""
tests/test_run_empirical_extended.py
======================================
Extended unit tests for analysis/run_empirical.py:
  - persist_violations([]): returns 0 without touching DB
  - scan_violations(empty df): returns []
  - MIN_NET_EDGE filter in persist_violations
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest
import pandas as pd


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


class TestPersistViolationsEmpty:
    """persist_violations([]): returns 0 without opening DB."""

    def test_empty_list_returns_zero(self):
        from analysis.run_empirical import persist_violations
        mock_ss = MagicMock()
        with patch("analysis.run_empirical.session_scope", return_value=mock_ss):
            result = persist_violations([])
        assert result == 0

    def test_empty_does_not_open_session(self):
        from analysis.run_empirical import persist_violations
        mock_ss = MagicMock()
        with patch("analysis.run_empirical.session_scope", return_value=mock_ss):
            persist_violations([])
        mock_ss.__enter__.assert_not_called()


class TestScanViolationsEmpty:
    """scan_violations() on empty OHLC returns empty list."""

    def test_empty_df_returns_empty(self):
        from analysis.run_empirical import scan_violations
        result = scan_violations(
            pd.DataFrame(),
            {},
        )
        assert result == []


class TestPersistViolationsNetEdgeFilter:
    """persist_violations filters out rows below MIN_NET_EDGE."""

    def test_below_min_edge_not_persisted(self):
        from analysis.run_empirical import persist_violations, MIN_NET_EDGE
        import pandas as pd
        from datetime import datetime, timezone

        # Create violation just barely below threshold
        bad_violation = {
            "net_edge": MIN_NET_EDGE - 0.0001,
            "ts": pd.Timestamp("2026-01-01", tz="UTC"),
            "rtype": "mutually_exclusive",
            "market_id_1": "A", "market_id_2": "B",
            "gross_edge": 0.05, "total_fees": 0.03,
            "p1": 0.55, "p2": 0.50, "direction": "sell",
        }
        mock_ss = MagicMock()
        with patch("analysis.run_empirical.session_scope", return_value=mock_ss):
            result = persist_violations([bad_violation])
        assert result == 0
        mock_ss.__enter__.assert_not_called()
