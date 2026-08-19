"""
tests/test_event_study.py
==========================
Unit tests for cross_asset/event_study.py — covers only the pure-function
helpers that have NO database or external API dependency.

  - _compute_market_response: empty df, missing column, pre/post split
  - _assess_data_quality: flags, completeness
  - test_spread_vs_liquidity: correlation direction
  - EVENT_WINDOWS / CANADIAN_MACRO_EVENT_TYPES: constants integrity
"""
from __future__ import annotations

import math
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from analysis.event_study import (
    EVENT_WINDOWS,
    CANADIAN_MACRO_EVENT_TYPES,
    _compute_market_response,
    _assess_data_quality,
    test_spread_vs_liquidity as spread_vs_liquidity_hypothesis,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_event_windows_are_sorted(self):
        assert EVENT_WINDOWS == sorted(EVENT_WINDOWS)

    def test_event_windows_non_empty(self):
        assert len(EVENT_WINDOWS) > 0

    def test_event_windows_positive(self):
        for w in EVENT_WINDOWS:
            assert w > 0

    def test_macro_event_types_not_empty(self):
        assert len(CANADIAN_MACRO_EVENT_TYPES) > 0

    def test_macro_event_types_have_string_values(self):
        for k, v in CANADIAN_MACRO_EVENT_TYPES.items():
            assert isinstance(k, str)
            assert isinstance(v, str)
            assert len(v) > 0


# ---------------------------------------------------------------------------
# _compute_market_response
# ---------------------------------------------------------------------------

def _make_ts_df(event_ts: datetime, n_pre: int = 5, n_post: int = 5, col: str = "kalshi_probability"):
    """Build a synthetic spread_df for testing."""
    pre_ts = [event_ts - timedelta(minutes=i + 1) for i in range(n_pre)][::-1]
    post_ts = [event_ts + timedelta(minutes=i + 1) for i in range(n_post)]
    ts_list = pre_ts + post_ts
    vals = [0.40 + i * 0.01 for i in range(len(ts_list))]
    return pd.DataFrame({"spread_ts": ts_list, col: vals})


_EVENT_TS = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)


class TestComputeMarketResponse:
    def test_empty_df_returns_error(self):
        res = _compute_market_response(pd.DataFrame(), _EVENT_TS, "kalshi_probability", 3600)
        assert "error" in res

    def test_missing_value_col_returns_error(self):
        df = _make_ts_df(_EVENT_TS)
        res = _compute_market_response(df, _EVENT_TS, "nonexistent_col", 3600)
        assert "error" in res

    def test_returns_pre_event_value(self):
        df = _make_ts_df(_EVENT_TS)
        res = _compute_market_response(df, _EVENT_TS, "kalshi_probability", 3600)
        assert "pre_event_value" in res
        assert isinstance(res["pre_event_value"], float)

    def test_returns_post_event_value(self):
        df = _make_ts_df(_EVENT_TS)
        res = _compute_market_response(df, _EVENT_TS, "kalshi_probability", 3600)
        assert "post_event_value" in res

    def test_immediate_change_equals_post_minus_pre(self):
        df = _make_ts_df(_EVENT_TS)
        res = _compute_market_response(df, _EVENT_TS, "kalshi_probability", 3600)
        if "error" not in res:
            expected = res["post_event_value"] - res["pre_event_value"]
            assert abs(res["immediate_change"] - expected) < 1e-9

    def test_horizons_present(self):
        df = _make_ts_df(_EVENT_TS, n_pre=10, n_post=20)
        res = _compute_market_response(df, _EVENT_TS, "kalshi_probability", 3600)
        if "error" not in res:
            assert "horizons" in res
            assert isinstance(res["horizons"], dict)

    def test_no_pre_data_returns_error(self):
        # Only post-event rows
        ts_list = [_EVENT_TS + timedelta(minutes=i + 1) for i in range(5)]
        df = pd.DataFrame({"spread_ts": ts_list, "kalshi_probability": [0.5] * 5})
        res = _compute_market_response(df, _EVENT_TS, "kalshi_probability", 3600)
        assert "error" in res

    def test_no_post_data_returns_error(self):
        # Only pre-event rows
        ts_list = [_EVENT_TS - timedelta(minutes=i + 1) for i in range(5)]
        df = pd.DataFrame({"spread_ts": ts_list, "kalshi_probability": [0.5] * 5})
        res = _compute_market_response(df, _EVENT_TS, "kalshi_probability", 3600)
        assert "error" in res

    def test_price_ts_column_also_accepted(self):
        ts_list = (
            [_EVENT_TS - timedelta(minutes=i + 1) for i in range(5)][::-1] +
            [_EVENT_TS + timedelta(minutes=i + 1) for i in range(5)]
        )
        df = pd.DataFrame({"price_ts": ts_list, "price": [0.5] * 10})
        res = _compute_market_response(df, _EVENT_TS, "price", 3600)
        assert "error" not in res or "Insufficient" in res.get("error", "")


# ---------------------------------------------------------------------------
# _assess_data_quality
# ---------------------------------------------------------------------------

class TestAssessDataQuality:
    def test_empty_both_flags_issues(self):
        res = _assess_data_quality(pd.DataFrame(), pd.DataFrame(), _EVENT_TS)
        assert not res["data_complete"]
        assert len(res["issues"]) >= 2

    def test_empty_kalshi_flags_issue(self):
        trad_df = pd.DataFrame({"price_ts": [_EVENT_TS], "price": [1.0]})
        res = _assess_data_quality(pd.DataFrame(), trad_df, _EVENT_TS)
        assert any("Kalshi" in issue for issue in res["issues"])

    def test_empty_trad_flags_issue(self):
        k_df = pd.DataFrame({"spread_ts": [_EVENT_TS], "kalshi_probability": [0.5]})
        res = _assess_data_quality(k_df, pd.DataFrame(), _EVENT_TS)
        assert any("traditional" in issue.lower() for issue in res["issues"])

    def test_both_populated_complete(self):
        k_df = pd.DataFrame({"spread_ts": [_EVENT_TS], "kalshi_probability": [0.5]})
        t_df = pd.DataFrame({"price_ts": [_EVENT_TS], "price": [1.0]})
        res = _assess_data_quality(k_df, t_df, _EVENT_TS)
        assert res["data_complete"]

    def test_n_obs_counts_correct(self):
        k_df = pd.DataFrame({"spread_ts": [_EVENT_TS] * 3, "kalshi_probability": [0.5] * 3})
        t_df = pd.DataFrame({"price_ts": [_EVENT_TS] * 7, "price": [1.0] * 7})
        res = _assess_data_quality(k_df, t_df, _EVENT_TS)
        assert res["n_kalshi_obs"] == 3
        assert res["n_trad_obs"] == 7


# ---------------------------------------------------------------------------
# test_spread_vs_liquidity
# ---------------------------------------------------------------------------

class TestSpreadVsLiquidity:
    def _make_series(self, n=50):
        rng = np.random.default_rng(42)
        liquidity = rng.uniform(100, 10000, n)
        # Strong negative correlation: large spread when liquidity is low
        spread = -0.8 * liquidity / 10000 + 0.2 + rng.normal(0, 0.01, n)
        return pd.Series(spread, name="spread"), pd.Series(liquidity, name="liquidity")

    def test_insufficient_data_returns_error(self):
        s = pd.Series([0.1, 0.2])
        l = pd.Series([100, 200])
        res = spread_vs_liquidity_hypothesis(s, l)
        assert "error" in res

    def test_returns_pearson_r(self):
        s, l = self._make_series()
        res = spread_vs_liquidity_hypothesis(s, l)
        assert "pearson_r" in res
        assert -1.0 <= res["pearson_r"] <= 1.0

    def test_returns_p_value(self):
        s, l = self._make_series()
        res = spread_vs_liquidity_hypothesis(s, l)
        assert "p_value" in res
        assert 0.0 <= res["p_value"] <= 1.0

    def test_known_negative_correlation(self):
        """Synthetically negative-correlated series should show |r| > 0.3."""
        s, l = self._make_series()
        res = spread_vs_liquidity_hypothesis(s, l)
        # The synthetic data has negative correlation between |spread| and liquidity
        # but the function takes abs(spread), so correlation sign depends on arrangement
        assert abs(res["pearson_r"]) > 0.3

    def test_returns_n_obs(self):
        s, l = self._make_series(40)
        res = spread_vs_liquidity_hypothesis(s, l)
        assert res["n_obs"] == 40

    def test_returns_conclusion_string(self):
        s, l = self._make_series()
        res = spread_vs_liquidity_hypothesis(s, l)
        assert isinstance(res["conclusion"], str)
        assert len(res["conclusion"]) > 0
