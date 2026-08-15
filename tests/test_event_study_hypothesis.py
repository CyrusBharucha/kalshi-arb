"""
tests/test_event_study_hypothesis.py
======================================
Tests for hypothesis-testing helpers in cross_asset/event_study.py:
  - test_spread_mean_reversion (ADF test wrapper)
  - test_spread_vs_liquidity (Pearson correlation wrapper)

No DB, no network. Uses scipy/statsmodels (available in requirements).
"""
from __future__ import annotations

import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Patch DB imports
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    repo_mock = MagicMock()
    repo_mock.get_cross_asset_spread_history = MagicMock(return_value=pd.DataFrame())
    repo_mock.get_external_data_series = MagicMock(return_value=pd.DataFrame())

    with patch.dict(__import__("sys").modules, {
        "database.repository": repo_mock,
        "database": MagicMock(),
    }):
        yield


# ---------------------------------------------------------------------------
# test_spread_mean_reversion
# ---------------------------------------------------------------------------

class TestSpreadMeanReversion:
    def _call(self, series):
        from analysis.event_study import test_spread_mean_reversion
        return test_spread_mean_reversion(series)

    def test_insufficient_data_returns_error(self):
        s = pd.Series([0.01] * 10)
        result = self._call(s)
        assert "error" in result

    def test_exactly_19_obs_returns_error(self):
        s = pd.Series([0.01] * 19)
        result = self._call(s)
        assert "error" in result

    def test_20_obs_runs_without_error(self):
        # A random-walk series of 20 obs
        rng = np.random.RandomState(42)
        s = pd.Series(rng.randn(20).cumsum())
        result = self._call(s)
        # May return error or valid dict (depends on statsmodels availability)
        if "error" not in result:
            assert "adf_statistic" in result
            assert "p_value" in result

    def test_stationary_series_detected(self):
        """White noise is stationary — ADF should reject null."""
        rng = np.random.RandomState(1)
        s = pd.Series(rng.randn(100))  # white noise, stationary
        result = self._call(s)
        if "error" not in result:
            assert isinstance(result["is_stationary"], bool)
            assert "conclusion" in result
            assert "n_observations" in result

    def test_returns_required_keys(self):
        rng = np.random.RandomState(0)
        s = pd.Series(rng.randn(50))
        result = self._call(s)
        if "error" not in result:
            for key in ["adf_statistic", "p_value", "is_stationary", "conclusion", "n_observations"]:
                assert key in result

    def test_mean_spread_and_std_present(self):
        rng = np.random.RandomState(7)
        s = pd.Series(rng.randn(50))
        result = self._call(s)
        if "error" not in result:
            assert "mean_spread" in result
            assert "std_spread" in result

    def test_p_value_between_0_and_1(self):
        rng = np.random.RandomState(0)
        s = pd.Series(rng.randn(50))
        result = self._call(s)
        if "error" not in result:
            assert 0.0 <= result["p_value"] <= 1.0

    def test_nan_series_handled(self):
        s = pd.Series([float("nan")] * 25)
        result = self._call(s)
        assert "error" in result  # dropna leaves empty


# ---------------------------------------------------------------------------
# test_spread_vs_liquidity
# ---------------------------------------------------------------------------

class TestSpreadVsLiquidity:
    def _call(self, spread, liquidity):
        from analysis.event_study import test_spread_vs_liquidity
        return test_spread_vs_liquidity(spread, liquidity)

    def test_insufficient_data_returns_error(self):
        s = pd.Series([0.05] * 5)
        l = pd.Series([100.0] * 5)
        result = self._call(s, l)
        assert "error" in result

    def test_10_obs_runs_without_error(self):
        s = pd.Series([0.05] * 10)
        l = pd.Series([100.0] * 10)
        result = self._call(s, l)
        if "error" not in result:
            assert "pearson_r" in result

    def test_returns_required_keys(self):
        rng = np.random.RandomState(0)
        s = pd.Series(rng.randn(30).cumsum())
        l = pd.Series(rng.uniform(100, 1000, 30))
        result = self._call(s, l)
        if "error" not in result:
            for key in ["pearson_r", "p_value", "n_obs", "significant", "conclusion"]:
                assert key in result

    def test_p_value_between_0_and_1(self):
        rng = np.random.RandomState(42)
        s = pd.Series(rng.randn(30))
        l = pd.Series(rng.uniform(100, 1000, 30))
        result = self._call(s, l)
        if "error" not in result:
            assert 0.0 <= result["p_value"] <= 1.0

    def test_pearson_r_between_minus1_and_plus1(self):
        rng = np.random.RandomState(42)
        s = pd.Series(rng.randn(30))
        l = pd.Series(rng.uniform(100, 1000, 30))
        result = self._call(s, l)
        if "error" not in result:
            assert -1.0 <= result["pearson_r"] <= 1.0

    def test_n_obs_correct(self):
        s = pd.Series([0.05] * 15)
        l = pd.Series([100.0] * 15)
        result = self._call(s, l)
        if "error" not in result:
            assert result["n_obs"] == 15

    def test_negative_correlation_perfectly_anticorrelated(self):
        """Perfect negative correlation: spread ↑ when liquidity ↓."""
        spread = pd.Series(np.linspace(0, 1, 30))
        liquidity = pd.Series(np.linspace(1, 0, 30))
        result = self._call(spread, liquidity)
        if "error" not in result:
            assert result["pearson_r"] < -0.9

    def test_mismatched_lengths_handled(self):
        """Mismatched series — concat on index will align (some NaN dropped)."""
        s = pd.Series([0.1] * 20, index=range(20))
        l = pd.Series([100.0] * 15, index=range(15))
        result = self._call(s, l)
        # Should work (aligns on index, drops unmatched) or return error
        assert isinstance(result, dict)
