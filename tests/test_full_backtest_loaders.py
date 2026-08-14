"""
tests/test_full_backtest_loaders.py
=====================================
Unit tests for backtest/full_backtest:
  - load_opportunities(): calls session_scope + pd.read_sql, returns DataFrame
  - load_settlement_values(): calls session_scope + execute, returns dict
  - run_full_backtest(): returns error key when no opps found

All DB calls mocked.
"""
from __future__ import annotations

import sys
import pandas as pd
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


def _session_ctx():
    mock_ss = MagicMock()
    mock_sess = MagicMock()
    mock_sess.bind = MagicMock()
    mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
    mock_ss.return_value.__exit__ = MagicMock(return_value=False)
    return mock_ss, mock_sess


# ---------------------------------------------------------------------------
# load_opportunities
# ---------------------------------------------------------------------------

class TestLoadOpportunities:
    def _opps_df(self):
        return pd.DataFrame({
            "opportunity_id": ["OPP-1", "OPP-2"],
            "detected_at": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True),
            "strategy_type": ["mutually_exclusive", "threshold_order"],
            "classification": ["B", "B"],
            "markets_involved": [["M1", "M2"], ["M3", "M4"]],
            "prices_json": [{"p1": 0.6, "p2": 0.5}, {"p1": 0.4, "p2": 0.7}],
            "gross_edge": [0.1, 0.1],
            "total_fees": [0.02, 0.02],
            "estimated_slippage": [0.001, 0.001],
            "net_edge": [0.079, 0.079],
        })

    def test_returns_dataframe(self):
        from backtest.full_backtest import load_opportunities
        mock_ss, _ = _session_ctx()
        with patch("backtest.full_backtest.session_scope", mock_ss), \
             patch("backtest.full_backtest.pd.read_sql", return_value=self._opps_df()):
            result = load_opportunities()
        assert isinstance(result, pd.DataFrame)

    def test_calls_session_scope(self):
        from backtest.full_backtest import load_opportunities
        mock_ss, _ = _session_ctx()
        with patch("backtest.full_backtest.session_scope", mock_ss), \
             patch("backtest.full_backtest.pd.read_sql", return_value=pd.DataFrame()):
            load_opportunities()
        mock_ss.assert_called_once()

    def test_calls_read_sql(self):
        from backtest.full_backtest import load_opportunities
        mock_ss, _ = _session_ctx()
        with patch("backtest.full_backtest.session_scope", mock_ss), \
             patch("backtest.full_backtest.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            load_opportunities()
        mock_rs.assert_called_once()

    def test_empty_returns_empty_df(self):
        from backtest.full_backtest import load_opportunities
        mock_ss, _ = _session_ctx()
        with patch("backtest.full_backtest.session_scope", mock_ss), \
             patch("backtest.full_backtest.pd.read_sql", return_value=pd.DataFrame()):
            result = load_opportunities()
        assert result.empty

    def test_returns_read_sql_result(self):
        from backtest.full_backtest import load_opportunities
        mock_ss, _ = _session_ctx()
        expected = self._opps_df()
        with patch("backtest.full_backtest.session_scope", mock_ss), \
             patch("backtest.full_backtest.pd.read_sql", return_value=expected):
            result = load_opportunities()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# load_settlement_values
# ---------------------------------------------------------------------------

class TestLoadSettlementValues:
    def test_returns_dict(self):
        from backtest.full_backtest import load_settlement_values
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("backtest.full_backtest.session_scope", mock_ss):
            result = load_settlement_values()
        assert isinstance(result, dict)

    def test_empty_rows_returns_empty_dict(self):
        from backtest.full_backtest import load_settlement_values
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("backtest.full_backtest.session_scope", mock_ss):
            result = load_settlement_values()
        assert result == {}

    def test_calls_session_scope(self):
        from backtest.full_backtest import load_settlement_values
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("backtest.full_backtest.session_scope", mock_ss):
            load_settlement_values()
        mock_ss.assert_called_once()

    def test_single_row_mapped_correctly(self):
        from backtest.full_backtest import load_settlement_values
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = [("MKT-A", 1.0)]
        with patch("backtest.full_backtest.session_scope", mock_ss):
            result = load_settlement_values()
        assert result["MKT-A"] == 1.0

    def test_multiple_rows_all_mapped(self):
        from backtest.full_backtest import load_settlement_values
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = [
            ("MKT-A", 1.0), ("MKT-B", 0.0), ("MKT-C", 1.0)
        ]
        with patch("backtest.full_backtest.session_scope", mock_ss):
            result = load_settlement_values()
        assert len(result) == 3
        assert result["MKT-B"] == 0.0

    def test_none_settlement_value_mapped_to_none(self):
        from backtest.full_backtest import load_settlement_values
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = [("MKT-X", None)]
        with patch("backtest.full_backtest.session_scope", mock_ss):
            result = load_settlement_values()
        assert result["MKT-X"] is None

    def test_settlement_value_is_float(self):
        from backtest.full_backtest import load_settlement_values
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = [("MKT-A", 1)]
        with patch("backtest.full_backtest.session_scope", mock_ss):
            result = load_settlement_values()
        assert isinstance(result["MKT-A"], float)


# ---------------------------------------------------------------------------
# run_full_backtest (empty path)
# ---------------------------------------------------------------------------

class TestRunFullBacktestEmpty:
    def test_no_opps_returns_error_key(self):
        from backtest.full_backtest import run_full_backtest
        with patch("backtest.full_backtest.load_opportunities",
                   return_value=pd.DataFrame()):
            result = run_full_backtest()
        assert "error" in result

    def test_error_value_is_no_opportunities(self):
        from backtest.full_backtest import run_full_backtest
        with patch("backtest.full_backtest.load_opportunities",
                   return_value=pd.DataFrame()):
            result = run_full_backtest()
        assert result["error"] == "no_opportunities"

    def test_run_id_present_in_error_result(self):
        from backtest.full_backtest import run_full_backtest
        with patch("backtest.full_backtest.load_opportunities",
                   return_value=pd.DataFrame()):
            result = run_full_backtest()
        assert "run_id" in result

    def test_run_id_starts_with_bt(self):
        from backtest.full_backtest import run_full_backtest
        with patch("backtest.full_backtest.load_opportunities",
                   return_value=pd.DataFrame()):
            result = run_full_backtest()
        assert result["run_id"].startswith("bt_")
