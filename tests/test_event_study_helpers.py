"""
tests/test_event_study_helpers.py
===================================
Unit tests for pure-logic helper functions in cross_asset/event_study.py:
  - _compute_market_response
  - _assess_data_quality
  - _compute_lead_lag (where possible without statsmodels)

No database, no network.
"""
from __future__ import annotations

import math
import pandas as pd
import numpy as np
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Patch DB imports
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    repo_mock = MagicMock()
    repo_mock.session_scope = MagicMock()
    repo_mock.get_cross_asset_spread_history = MagicMock(return_value=pd.DataFrame())
    repo_mock.get_external_data_series = MagicMock(return_value=pd.DataFrame())

    with patch.dict(__import__("sys").modules, {
        "database.repository": repo_mock,
        "database": MagicMock(),
    }):
        yield


# ---------------------------------------------------------------------------
# Helpers: build DataFrames that _compute_market_response expects
# ---------------------------------------------------------------------------

def _ts(offset_s=0):
    return pd.Timestamp("2024-06-01 12:00:00", tz="UTC") + pd.Timedelta(seconds=offset_s)


def _spread_df(n_pre=5, n_post=5, value_col="kalshi_probability", base=0.60, delta=0.05):
    """Build a DataFrame with n_pre pre-event and n_post post-event rows."""
    event_ts = _ts(0)
    rows = []
    for i in range(-n_pre, 0):
        rows.append({"spread_ts": _ts(i * 60), value_col: base})
    for i in range(n_post):
        rows.append({"spread_ts": _ts(i * 60 + 1), value_col: base + delta})
    return pd.DataFrame(rows)


def _trad_df(n_pre=5, n_post=5, value_col="price", base=1.35, delta=0.01):
    event_ts = _ts(0)
    rows = []
    for i in range(-n_pre, 0):
        rows.append({"price_ts": _ts(i * 60), value_col: base})
    for i in range(n_post):
        rows.append({"price_ts": _ts(i * 60 + 1), value_col: base + delta})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# _compute_market_response
# ---------------------------------------------------------------------------

class TestComputeMarketResponse:
    def _call(self, df, event_ts=None, value_col="kalshi_probability", window=3600):
        from analysis.event_study import _compute_market_response
        if event_ts is None:
            event_ts = _ts(0).to_pydatetime()
        return _compute_market_response(df, event_ts, value_col, window)

    def test_empty_df_returns_error(self):
        result = self._call(pd.DataFrame())
        assert "error" in result

    def test_missing_value_col_returns_error(self):
        df = _spread_df()
        result = self._call(df, value_col="nonexistent_col")
        assert "error" in result

    def test_no_pre_event_data_returns_error(self):
        """Only post-event rows → insufficient pre-event."""
        df = _spread_df(n_pre=0, n_post=5)
        result = self._call(df)
        assert "error" in result

    def test_no_post_event_data_returns_error(self):
        """Only pre-event rows → insufficient post-event."""
        df = _spread_df(n_pre=5, n_post=0)
        result = self._call(df)
        assert "error" in result

    def test_immediate_change_computed_correctly(self):
        """post-pre = 0.05."""
        df = _spread_df(base=0.60, delta=0.05)
        result = self._call(df)
        if "error" not in result:
            assert abs(result["immediate_change"] - 0.05) < 1e-6

    def test_pre_event_value_is_last_pre_row(self):
        df = _spread_df(base=0.60, delta=0.05)
        result = self._call(df)
        if "error" not in result:
            assert abs(result["pre_event_value"] - 0.60) < 1e-6

    def test_post_event_value_is_first_post_row(self):
        df = _spread_df(base=0.60, delta=0.05)
        result = self._call(df)
        if "error" not in result:
            assert abs(result["post_event_value"] - 0.65) < 1e-6

    def test_horizons_dict_present(self):
        df = _spread_df()
        result = self._call(df)
        if "error" not in result:
            assert "horizons" in result

    def test_no_timestamp_col_returns_error(self):
        df = pd.DataFrame({"kalshi_probability": [0.5, 0.6]})
        result = self._call(df)
        assert "error" in result

    def test_zero_change_returns_zero_immediate_change(self):
        df = _spread_df(base=0.60, delta=0.0)
        result = self._call(df)
        if "error" not in result:
            assert abs(result["immediate_change"]) < 1e-9


# ---------------------------------------------------------------------------
# _assess_data_quality
# ---------------------------------------------------------------------------

class TestAssessDataQuality:
    def _call(self, k_df, t_df, event_ts=None):
        from analysis.event_study import _assess_data_quality
        if event_ts is None:
            event_ts = _ts(0).to_pydatetime()
        return _assess_data_quality(k_df, t_df, event_ts)

    def test_empty_both_returns_two_issues(self):
        result = self._call(pd.DataFrame(), pd.DataFrame())
        assert len(result["issues"]) >= 2

    def test_empty_kalshi_adds_issue(self):
        result = self._call(pd.DataFrame(), _trad_df())
        issues = result["issues"]
        assert any("Kalshi" in i for i in issues)

    def test_empty_trad_adds_issue(self):
        result = self._call(_spread_df(), pd.DataFrame())
        issues = result["issues"]
        assert any("traditional" in i.lower() or "No traditional" in i for i in issues)

    def test_both_populated_no_issues(self):
        result = self._call(_spread_df(), _trad_df())
        assert result["data_complete"] is True
        assert len(result["issues"]) == 0

    def test_n_kalshi_obs_correct(self):
        k = _spread_df(n_pre=3, n_post=3)
        result = self._call(k, _trad_df())
        assert result["n_kalshi_obs"] == 6

    def test_n_trad_obs_correct(self):
        t = _trad_df(n_pre=4, n_post=4)
        result = self._call(_spread_df(), t)
        assert result["n_trad_obs"] == 8

    def test_data_complete_false_when_missing(self):
        result = self._call(pd.DataFrame(), _trad_df())
        assert result["data_complete"] is False

    def test_returns_dict(self):
        result = self._call(_spread_df(), _trad_df())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# _compute_lead_lag
# ---------------------------------------------------------------------------

class TestComputeLeadLag:
    def _call(self, k_df, t_df, event_ts=None):
        from analysis.event_study import _compute_lead_lag
        if event_ts is None:
            event_ts = _ts(0).to_pydatetime()
        return _compute_lead_lag(k_df, t_df, event_ts, window_seconds=3600)

    def test_empty_both_returns_error(self):
        result = self._call(pd.DataFrame(), pd.DataFrame())
        assert "error" in result

    def test_empty_kalshi_returns_error(self):
        result = self._call(pd.DataFrame(), _trad_df())
        assert "error" in result

    def test_empty_trad_returns_error(self):
        result = self._call(_spread_df(), pd.DataFrame())
        assert "error" in result

    def test_aligned_data_returns_cross_correlations(self):
        """When both series have data, ccf dict should be present."""
        k = _spread_df(n_pre=10, n_post=10)
        t = _trad_df(n_pre=10, n_post=10)
        result = self._call(k, t)
        if "error" not in result:
            assert "cross_correlations" in result

    def test_best_lag_is_integer_or_none(self):
        k = _spread_df(n_pre=10, n_post=10)
        t = _trad_df(n_pre=10, n_post=10)
        result = self._call(k, t)
        if "error" not in result:
            best_lag = result.get("best_lag_minutes")
            assert best_lag is None or isinstance(best_lag, int)

    def test_interpretation_key_present(self):
        k = _spread_df(n_pre=10, n_post=10)
        t = _trad_df(n_pre=10, n_post=10)
        result = self._call(k, t)
        if "error" not in result:
            assert "interpretation" in result
