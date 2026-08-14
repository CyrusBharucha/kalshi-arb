"""
tests/test_backtest_metrics_extended.py
=========================================
Additional tests for backtest/metrics.py:
  - opportunity_frequency_stats (untested function)
  - compute_performance_metrics: Sortino, Calmar, profit_factor,
    drawdown duration, and n_breakeven edge cases

No database, no network. Pure pandas arithmetic.
"""
from __future__ import annotations

import math
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from backtest.metrics import compute_performance_metrics, opportunity_frequency_stats


# ---------------------------------------------------------------------------
# opportunity_frequency_stats
# ---------------------------------------------------------------------------

def _opp_df(n=10, net_edge=0.05, has_class=True, has_strategy=True):
    """Build a mock opportunities DataFrame."""
    base_ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        row = {
            "detected_at": base_ts + timedelta(hours=i),
            "net_edge": net_edge + i * 0.001,
        }
        if has_class:
            row["classification"] = "A" if i % 3 == 0 else "B"
        if has_strategy:
            row["strategy_type"] = "yes_no_complement" if i % 2 == 0 else "mutually_exclusive"
        rows.append(row)
    return pd.DataFrame(rows)


class TestOpportunityFrequencyStats:
    def test_empty_df_returns_empty(self):
        result = opportunity_frequency_stats(pd.DataFrame())
        assert result == {}

    def test_returns_dict(self):
        result = opportunity_frequency_stats(_opp_df())
        assert isinstance(result, dict)

    def test_total_opportunities_correct(self):
        result = opportunity_frequency_stats(_opp_df(n=7))
        assert result["total_opportunities"] == 7

    def test_avg_net_edge_computed(self):
        df = _opp_df(net_edge=0.10, n=5)
        result = opportunity_frequency_stats(df)
        assert result["avg_net_edge"] is not None
        assert abs(result["avg_net_edge"] - df["net_edge"].mean()) < 1e-9

    def test_median_net_edge_computed(self):
        df = _opp_df(net_edge=0.10, n=5)
        result = opportunity_frequency_stats(df)
        assert result["median_net_edge"] is not None

    def test_max_net_edge_is_max(self):
        df = _opp_df(net_edge=0.10, n=5)
        result = opportunity_frequency_stats(df)
        assert abs(result["max_net_edge"] - df["net_edge"].max()) < 1e-9

    def test_date_range_start_and_end_present(self):
        result = opportunity_frequency_stats(_opp_df(n=5))
        assert "date_range_start" in result
        assert "date_range_end" in result

    def test_pct_class_a_is_fraction(self):
        df = _opp_df(n=9, has_class=True)
        result = opportunity_frequency_stats(df)
        pct_a = result.get("pct_class_A")
        assert pct_a is not None
        assert 0.0 <= pct_a <= 1.0

    def test_strategy_breakdown_is_dict(self):
        result = opportunity_frequency_stats(_opp_df(has_strategy=True))
        assert isinstance(result["strategy_breakdown"], dict)

    def test_no_classification_col_gives_none_pct(self):
        df = _opp_df(has_class=False)
        result = opportunity_frequency_stats(df)
        assert result.get("pct_class_A") is None

    def test_single_row_works(self):
        result = opportunity_frequency_stats(_opp_df(n=1))
        assert result["total_opportunities"] == 1


# ---------------------------------------------------------------------------
# compute_performance_metrics — additional metric edge cases
# ---------------------------------------------------------------------------

def _pnl(values):
    return pd.Series(values, dtype=float)


class TestComputeMetricsAdditional:
    def test_all_negative_no_profit_factor(self):
        result = compute_performance_metrics(_pnl([-0.1, -0.2, -0.3]))
        assert result.get("profit_factor") is None

    def test_all_positive_no_sortino(self):
        """All positive PnL → no negative excess returns → Sortino = None."""
        result = compute_performance_metrics(_pnl([0.1, 0.2, 0.3]))
        assert result.get("sortino_ratio") is None

    def test_breakeven_trades_counted(self):
        result = compute_performance_metrics(_pnl([0.1, 0.0, 0.0, -0.1, 0.2]))
        assert result["n_breakeven"] == 2

    def test_no_drawdown_calmar_none(self):
        """Monotonically increasing cumulative PnL → max_drawdown = 0 → calmar = None."""
        result = compute_performance_metrics(_pnl([0.1, 0.2, 0.3, 0.4]))
        assert result.get("calmar_ratio") is None

    def test_drawdown_duration_positive_when_in_drawdown(self):
        # Series: goes up then down for 3 periods, never recovers
        result = compute_performance_metrics(_pnl([0.5, -0.1, -0.1, -0.1]))
        assert result["max_drawdown_duration"] >= 3

    def test_cumulative_pnl_matches_sum(self):
        values = [0.1, -0.05, 0.2, -0.1]
        result = compute_performance_metrics(_pnl(values))
        assert abs(result["cumulative_pnl"] - sum(values)) < 1e-6

    def test_n_trades_is_length_of_series(self):
        result = compute_performance_metrics(_pnl([0.1, 0.2, 0.3]))
        assert result["n_trades"] == 3

    def test_win_rate_is_fraction(self):
        # win_rate is rounded to 4 decimal places
        result = compute_performance_metrics(_pnl([0.1, -0.1, 0.1]))
        assert abs(result["win_rate"] - 2/3) < 1e-4

    def test_all_same_pnl_sharpe_is_none(self):
        """Constant PnL → std = 0 → Sharpe = None."""
        result = compute_performance_metrics(_pnl([0.1, 0.1, 0.1]))
        assert result.get("sharpe_ratio") is None

    def test_return_dict_has_required_keys(self):
        result = compute_performance_metrics(_pnl([0.1, -0.1, 0.2]))
        required = [
            "n_trades", "total_pnl", "win_rate", "avg_pnl_per_trade",
            "max_drawdown", "cumulative_pnl",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_n_winning_plus_losing_plus_breakeven_equals_n(self):
        result = compute_performance_metrics(_pnl([0.1, -0.1, 0.0, 0.2, -0.05]))
        n = result["n_trades"]
        assert result["n_winning"] + result["n_losing"] + result["n_breakeven"] == n
