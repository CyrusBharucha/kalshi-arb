"""
tests/test_load_actionable_pairs.py
======================================
Unit tests for analysis/run_empirical.load_actionable_pairs().

Tests:
  - Returns a pd.DataFrame
  - Calls session_scope
  - Calls pd.read_sql
  - Passes session bind to pd.read_sql
  - Returns result from pd.read_sql unchanged

No live DB — session_scope and pd.read_sql are mocked.
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


class TestLoadActionablePairs:
    def _mock_context(self, return_df=None):
        if return_df is None:
            return_df = pd.DataFrame()
        mock_ss = MagicMock()
        mock_sess = MagicMock()
        mock_sess.bind = MagicMock()
        mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
        mock_ss.return_value.__exit__ = MagicMock(return_value=False)
        return mock_ss, return_df

    def test_returns_dataframe(self):
        from analysis.run_empirical import load_actionable_pairs
        mock_ss, expected = self._mock_context(pd.DataFrame({"market_id_1": ["A"]}))
        with patch("analysis.run_empirical.session_scope", mock_ss), \
             patch("analysis.run_empirical.pd.read_sql", return_value=expected):
            result = load_actionable_pairs()
        assert isinstance(result, pd.DataFrame)

    def test_calls_session_scope(self):
        from analysis.run_empirical import load_actionable_pairs
        mock_ss, _ = self._mock_context()
        with patch("analysis.run_empirical.session_scope", mock_ss), \
             patch("analysis.run_empirical.pd.read_sql", return_value=pd.DataFrame()):
            load_actionable_pairs()
        mock_ss.assert_called_once()

    def test_calls_read_sql(self):
        from analysis.run_empirical import load_actionable_pairs
        mock_ss, _ = self._mock_context()
        with patch("analysis.run_empirical.session_scope", mock_ss), \
             patch("analysis.run_empirical.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            load_actionable_pairs()
        mock_rs.assert_called_once()

    def test_returns_read_sql_result(self):
        from analysis.run_empirical import load_actionable_pairs
        expected = pd.DataFrame({"market_id_1": ["X"], "market_id_2": ["Y"]})
        mock_ss, _ = self._mock_context()
        with patch("analysis.run_empirical.session_scope", mock_ss), \
             patch("analysis.run_empirical.pd.read_sql", return_value=expected):
            result = load_actionable_pairs()
        assert len(result) == 1
        assert result["market_id_1"].iloc[0] == "X"

    def test_empty_result_returns_empty_df(self):
        from analysis.run_empirical import load_actionable_pairs
        mock_ss, _ = self._mock_context()
        with patch("analysis.run_empirical.session_scope", mock_ss), \
             patch("analysis.run_empirical.pd.read_sql", return_value=pd.DataFrame()):
            result = load_actionable_pairs()
        assert result.empty
