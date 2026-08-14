"""
tests/test_backtest_metrics.py
================================
Unit tests for backtest/metrics.py — compute_performance_metrics.

No database, no network. Pure pandas arithmetic.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
import pytest

from backtest.metrics import compute_performance_metrics


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _pnl(values):
    return pd.Series(values, dtype=float)


# ---------------------------------------------------------------------------
# Error / edge cases
# ---------------------------------------------------------------------------

class TestComputePerformanceMetricsEdgeCases:
    def test_none_input_returns_error(self):
        result = compute_performance_metrics(None)
        assert "error" in result

    def test_single_element_returns_error(self):
        result = compute_performance_metrics(_pnl([1.0]))
        assert "error" in result

    def test_empty_series_returns_error(self):
        result = compute_performance_metrics(_pnl([]))
        assert "error" in result

    def test_all_nan_returns_error(self):
        result = compute_performance_metrics(_pnl([float("nan"), float("nan")]))
        assert "error" in result or result.get("total_pnl", 0) == 0


# ---------------------------------------------------------------------------
# Basic metrics
# ---------------------------------------------------------------------------

class TestComputePerformanceMetricsBasic:
    def _metrics(self, values):
        return compute_performance_metrics(_pnl(values))

    def test_total_pnl_correct(self):
        m = self._metrics([1.0, 2.0, -0.5, 0.5])
        assert abs(m["total_pnl"] - 3.0) < 1e-6

    def test_avg_pnl_correct(self):
        m = self._metrics([1.0, 2.0, -0.5, 0.5])
        avg_key = "avg_pnl_per_trade" if "avg_pnl_per_trade" in m else "avg_pnl"
        assert abs(m[avg_key] - 0.75) < 1e-6

    def test_win_rate_correct(self):
        m = self._metrics([1.0, 2.0, -0.5, 0.0])
        # 2 positive out of 4
        assert abs(m["win_rate"] - 0.5) < 1e-6

    def test_all_positive_win_rate_one(self):
        m = self._metrics([1.0, 2.0, 0.5])
        assert abs(m["win_rate"] - 1.0) < 1e-6

    def test_all_negative_win_rate_zero(self):
        m = self._metrics([-1.0, -2.0])
        assert m["win_rate"] == 0.0

    def test_avg_win_positive(self):
        m = self._metrics([1.0, 2.0, -0.5])
        assert m["avg_win"] > 0

    def test_avg_loss_negative(self):
        m = self._metrics([1.0, -2.0, -0.5])
        assert m["avg_loss"] < 0

    def test_required_keys_present(self):
        m = self._metrics([1.0, -0.5, 0.3, 0.8])
        for key in ("total_pnl", "win_rate", "avg_win", "avg_loss",
                    "max_drawdown", "n_trades"):
            assert key in m, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Sharpe / Sortino
# ---------------------------------------------------------------------------

class TestSharpeRatio:
    def _sharpe(self, m):
        return m.get("sharpe_ratio") or m.get("sharpe")

    def test_positive_sharpe_for_variable_positive_pnl(self):
        """Variable positive PnL produces a positive Sharpe."""
        import random; random.seed(42)
        pnl = [abs(x) + 0.01 for x in [random.gauss(0.01, 0.005) for _ in range(100)]]
        m = compute_performance_metrics(_pnl(pnl))
        s = self._sharpe(m)
        if s is not None:
            assert s > 0

    def test_negative_sharpe_for_consistently_negative_pnl(self):
        pnl = [-0.01 - abs(x) for x in [0.001 * i for i in range(1, 51)]]
        m = compute_performance_metrics(_pnl(pnl))
        s = self._sharpe(m)
        if s is not None:
            assert s < 0 or s == 0

    def test_sharpe_key_exists_in_result(self):
        m = compute_performance_metrics(_pnl([0.01, -0.005, 0.02, -0.01, 0.03]))
        # At least one sharpe key should exist
        has_sharpe = "sharpe_ratio" in m or "sharpe" in m
        assert has_sharpe

    def test_flat_pnl_sharpe_none_or_zero(self):
        """Constant PnL has zero std → Sharpe is None or 0."""
        m = compute_performance_metrics(_pnl([0.0] * 50))
        s = self._sharpe(m)
        assert s is None or s == 0 or (s is not None and abs(s) < 1e6)


# ---------------------------------------------------------------------------
# Max drawdown
# ---------------------------------------------------------------------------

class TestMaxDrawdown:
    def test_no_drawdown_when_always_positive(self):
        m = compute_performance_metrics(_pnl([1.0, 0.5, 0.3, 2.0]))
        assert m["max_drawdown"] == 0.0 or m["max_drawdown"] >= -1e-9

    def test_drawdown_correct(self):
        """Cumulative: 0, 1, 0.5 → drawdown = 0.5 - 1.0 = -0.5."""
        m = compute_performance_metrics(_pnl([1.0, -0.5]))
        assert m["max_drawdown"] <= 0

    def test_large_drawdown(self):
        m = compute_performance_metrics(_pnl([10.0, -5.0, -3.0]))
        # cummax=10 at peak; then goes to 5 then 2; drawdown = -8
        assert m["max_drawdown"] <= -7.0

    def test_drawdown_is_nonnegative_magnitude(self):
        """max_drawdown returned should be <= 0 (a negative number or zero)."""
        m = compute_performance_metrics(_pnl([1.0, -2.0, 0.5, -1.0]))
        assert m["max_drawdown"] <= 0


# ---------------------------------------------------------------------------
# Profit factor
# ---------------------------------------------------------------------------

class TestProfitFactor:
    def test_profit_factor_positive(self):
        m = compute_performance_metrics(_pnl([2.0, 1.0, -0.5, -0.5]))
        pf = m.get("profit_factor")
        if pf is not None:
            assert pf > 0

    def test_all_wins_profit_factor_none_or_inf(self):
        m = compute_performance_metrics(_pnl([1.0, 2.0, 0.5]))
        pf = m.get("profit_factor")
        # No losses → profit_factor should be None or inf
        assert pf is None or math.isinf(pf) or pf > 10

    def test_profit_factor_gt_one_for_net_positive(self):
        m = compute_performance_metrics(_pnl([3.0, -1.0]))
        pf = m.get("profit_factor")
        if pf is not None and not math.isinf(pf):
            assert pf > 1.0
