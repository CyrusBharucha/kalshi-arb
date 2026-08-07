"""
backtest/metrics.py
Performance metric calculations for backtests.
Used by both the Kalshi internal backtest and the cross-asset backtest.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def compute_performance_metrics(
    pnl_series: pd.Series,
    periods_per_year: float = 252.0,
    risk_free_rate: float = 0.04,
) -> Dict[str, Any]:
    """
    Compute comprehensive performance metrics from a P&L time series.

    Args:
        pnl_series:         Period-level P&L values (NOT cumulative).
        periods_per_year:   252 for daily, 52 for weekly, 12 for monthly.
        risk_free_rate:     Annual risk-free rate for Sharpe calculation.

    Returns:
        Dict of metrics.
    """
    if pnl_series is None or len(pnl_series) < 2:
        return {"error": "Insufficient data"}

    pnl = pnl_series.dropna()
    n   = len(pnl)
    cum = pnl.cumsum()

    # Basic statistics
    total_pnl  = float(pnl.sum())
    avg_pnl    = float(pnl.mean())
    std_pnl    = float(pnl.std(ddof=1)) if n > 1 else 0.0
    n_pos      = int((pnl > 0).sum())
    n_neg      = int((pnl < 0).sum())
    n_zero     = int((pnl == 0).sum())

    win_rate   = n_pos / n if n > 0 else 0.0
    avg_win    = float(pnl[pnl > 0].mean()) if n_pos > 0 else 0.0
    avg_loss   = float(pnl[pnl < 0].mean()) if n_neg > 0 else 0.0
    profit_factor = (
        abs(pnl[pnl > 0].sum() / pnl[pnl < 0].sum())
        if n_neg > 0 and pnl[pnl < 0].sum() != 0 else None
    )

    # Annualised Sharpe
    rf_per_period = risk_free_rate / periods_per_year
    excess_pnl = pnl - rf_per_period
    sharpe = (
        float(excess_pnl.mean() / excess_pnl.std(ddof=1) * math.sqrt(periods_per_year))
        if excess_pnl.std(ddof=1) > 0 else None
    )

    # Sortino (downside deviation)
    neg_excess = excess_pnl[excess_pnl < 0]
    sortino = (
        float(excess_pnl.mean() / neg_excess.std(ddof=1) * math.sqrt(periods_per_year))
        if len(neg_excess) > 1 and neg_excess.std(ddof=1) > 0 else None
    )

    # Max drawdown
    roll_max = cum.cummax()
    drawdown = cum - roll_max
    max_dd   = float(drawdown.min())

    # Max drawdown duration
    dd_duration = 0
    in_dd = False
    current_dd_len = 0
    for val in drawdown:
        if val < 0:
            in_dd = True
            current_dd_len += 1
            dd_duration = max(dd_duration, current_dd_len)
        else:
            in_dd = False
            current_dd_len = 0

    # Calmar ratio (annualised return / max drawdown)
    if max_dd < 0:
        ann_return = avg_pnl * periods_per_year
        calmar = ann_return / abs(max_dd)
    else:
        calmar = None

    return {
        "n_trades":           n,
        "total_pnl":          round(total_pnl, 4),
        "avg_pnl_per_trade":  round(avg_pnl, 6),
        "std_pnl":            round(std_pnl, 6),
        "n_winning":          n_pos,
        "n_losing":           n_neg,
        "n_breakeven":        n_zero,
        "win_rate":           round(win_rate, 4),
        "avg_win":            round(avg_win, 4) if avg_win else None,
        "avg_loss":           round(avg_loss, 4) if avg_loss else None,
        "profit_factor":      round(profit_factor, 4) if profit_factor else None,
        "sharpe_ratio":       round(sharpe, 4) if sharpe else None,
        "sortino_ratio":      round(sortino, 4) if sortino else None,
        "calmar_ratio":       round(calmar, 4) if calmar else None,
        "max_drawdown":       round(max_dd, 4),
        "max_drawdown_duration": dd_duration,
        "cumulative_pnl":     round(float(cum.iloc[-1]), 4) if len(cum) else 0.0,
    }


def opportunity_frequency_stats(
    opportunities_df: pd.DataFrame,
    timestamp_col: str = "detected_at",
) -> Dict[str, Any]:
    """
    Analyse how often opportunities appear and their characteristics.
    """
    if opportunities_df.empty:
        return {}

    df = opportunities_df.copy()
    df[timestamp_col] = pd.to_datetime(df[timestamp_col], utc=True)
    df = df.set_index(timestamp_col).sort_index()

    edges = df.get("net_edge", pd.Series(dtype=float))

    return {
        "total_opportunities":    len(df),
        "date_range_start":       str(df.index.min()),
        "date_range_end":         str(df.index.max()),
        "avg_net_edge":           float(edges.mean()) if len(edges) else None,
        "median_net_edge":        float(edges.median()) if len(edges) else None,
        "max_net_edge":           float(edges.max()) if len(edges) else None,
        "pct_class_A":            (df["classification"] == "A").mean() if "classification" in df else None,
        "pct_class_B":            (df["classification"] == "B").mean() if "classification" in df else None,
        "strategy_breakdown":     df.get("strategy_type", pd.Series()).value_counts().to_dict(),
    }
