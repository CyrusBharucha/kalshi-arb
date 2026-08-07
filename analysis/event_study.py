"""
cross_asset/event_study.py
Canadian macro event-study and information-diffusion analysis.

For each major Canadian macro event (BOC decision, CPI, employment, GDP),
measures the response of:
  - Kalshi contract prices
  - CAD/USD spot rate
  - Canadian overnight rates
  - WTI crude oil

and tests hypotheses about lead/lag relationships and information diffusion.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.repository import (
    session_scope, get_cross_asset_spread_history, get_external_data_series,
)

logger = logging.getLogger(__name__)


# -- Event window configuration -------------------------------------------------

EVENT_WINDOWS = [1, 5, 30, 60, 300, 900, 3600]  # seconds before/after event

CANADIAN_MACRO_EVENT_TYPES = {
    "boc_rate_decision":   "Bank of Canada rate decision",
    "canada_cpi":          "Canadian CPI release",
    "canada_employment":   "Canadian Labour Force Survey",
    "canada_gdp":          "Canadian GDP release",
    "wti_shock":           "Major WTI price movement",
}


# -- Event study framework ------------------------------------------------------

def run_event_study(
    event_ts: datetime,
    event_type: str,
    kalshi_market_id: str,
    trad_asset: str,
    window_seconds: int = 3600,
    n_windows: int = 10,
) -> Dict[str, Any]:
    """
    Measure the response of Kalshi and traditional markets around a macro event.

    Args:
        event_ts:          Exact timestamp of the event
        event_type:        Type label (e.g. 'canada_cpi')
        kalshi_market_id:  Kalshi market to measure
        trad_asset:        Traditional asset name (e.g. 'CADUSD', 'CORRA')
        window_seconds:    Length of event window (pre and post) in seconds
        n_windows:         Number of sub-windows to measure

    Returns:
        Dict with pre/post response metrics for both markets.
    """
    pre_start  = event_ts - timedelta(seconds=window_seconds)
    post_end   = event_ts + timedelta(seconds=window_seconds)

    # Pull Kalshi cross-asset spread history
    with session_scope() as s:
        spread_df = get_cross_asset_spread_history(
            s, kalshi_market_id, trad_asset,
            start_ts=pre_start, end_ts=post_end
        )
        trad_df = get_external_data_series(
            s, trad_asset, "spot",
            start_ts=pre_start, end_ts=post_end
        )

    results = {
        "event_ts":            event_ts.isoformat(),
        "event_type":          event_type,
        "kalshi_market_id":    kalshi_market_id,
        "trad_asset":          trad_asset,
        "window_seconds":      window_seconds,
        "kalshi_response":     _compute_market_response(spread_df, event_ts,
                                                          "kalshi_probability",
                                                          window_seconds),
        "trad_response":       _compute_market_response(trad_df, event_ts,
                                                          "price", window_seconds),
        "lead_lag":            _compute_lead_lag(spread_df, trad_df, event_ts,
                                                    window_seconds),
        "n_kalshi_obs":        len(spread_df),
        "n_trad_obs":          len(trad_df),
        "data_quality":        _assess_data_quality(spread_df, trad_df, event_ts),
    }
    return results


def _compute_market_response(
    df: pd.DataFrame,
    event_ts: datetime,
    value_col: str,
    window_seconds: int,
) -> Dict[str, Any]:
    """Compute price/probability response around event."""
    if df.empty or value_col not in df.columns:
        return {"error": "No data"}

    # Find pre-event level (last observation before event)
    if "spread_ts" in df.columns:
        ts_col = "spread_ts"
    elif "price_ts" in df.columns:
        ts_col = "price_ts"
    else:
        return {"error": "No timestamp column"}

    df = df.copy()
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    event_ts_aware = pd.Timestamp(event_ts).tz_localize("UTC") if event_ts.tzinfo is None else event_ts

    pre_df  = df[df[ts_col] < event_ts_aware].sort_values(ts_col)
    post_df = df[df[ts_col] >= event_ts_aware].sort_values(ts_col)

    if pre_df.empty or post_df.empty:
        return {"error": "Insufficient pre/post observations"}

    pre_val  = float(pre_df[value_col].iloc[-1])
    post_val = float(post_df[value_col].iloc[0])  # first obs after event

    # Compute response at various horizons
    horizons = {}
    for sec in EVENT_WINDOWS:
        horizon_end = event_ts_aware + timedelta(seconds=sec)
        horizon_df  = post_df[post_df[ts_col] <= horizon_end]
        if not horizon_df.empty:
            horizons[f"t+{sec}s"] = float(horizon_df[value_col].iloc[-1]) - pre_val

    return {
        "pre_event_value":  pre_val,
        "post_event_value": post_val,
        "immediate_change": post_val - pre_val,
        "horizons":         horizons,
    }


def _compute_lead_lag(
    kalshi_df: pd.DataFrame,
    trad_df: pd.DataFrame,
    event_ts: datetime,
    window_seconds: int,
    max_lag_obs: int = 20,
) -> Dict[str, Any]:
    """
    Test whether Kalshi leads or lags the traditional market.
    Uses cross-correlation of changes around the event.
    """
    if kalshi_df.empty or trad_df.empty:
        return {"error": "Insufficient data for lead-lag analysis"}

    # Align both series to a common frequency using forward-fill
    try:
        k_col = "kalshi_probability" if "kalshi_probability" in kalshi_df.columns else None
        t_col = "price" if "price" in trad_df.columns else None

        if k_col is None or t_col is None:
            return {"error": "Required columns not found"}

        k_ts_col = "spread_ts" if "spread_ts" in kalshi_df.columns else "price_ts"
        t_ts_col = "price_ts"

        k = kalshi_df[[k_ts_col, k_col]].copy()
        t = trad_df[[t_ts_col, t_col]].copy()

        k[k_ts_col] = pd.to_datetime(k[k_ts_col], utc=True)
        t[t_ts_col] = pd.to_datetime(t[t_ts_col], utc=True)

        k = k.set_index(k_ts_col).sort_index()
        t = t.set_index(t_ts_col).sort_index()

        # Resample to 1-minute frequency for cross-correlation
        k_1m = k.resample("1min").last().ffill()
        t_1m = t.resample("1min").last().ffill()

        # Align on common index
        aligned = pd.concat([k_1m, t_1m], axis=1, join="inner").dropna()
        if len(aligned) < 5:
            return {"error": "Too few aligned observations"}

        k_chg = aligned[k_col].diff().dropna()
        t_chg = aligned[t_col].diff().dropna()

        # Cross-correlations at lags 0, ±1, ±2, ... minutes
        ccf = {}
        for lag in range(-max_lag_obs, max_lag_obs + 1):
            if lag >= 0:
                a, b = k_chg[lag:], t_chg[:len(k_chg)-lag]
            else:
                a, b = k_chg[:lag], t_chg[-lag:]
            if a.std() == 0 or b.std() == 0:
                corr = float("nan")
            else:
                corr = a.corr(b)
            ccf[lag] = float(corr) if not np.isnan(corr) else None

        # Find lag at which correlation is maximised
        valid_ccf = {k: v for k, v in ccf.items() if v is not None}
        best_lag  = max(valid_ccf, key=lambda l: abs(valid_ccf[l])) if valid_ccf else None

        return {
            "cross_correlations":       ccf,
            "best_lag_minutes":         best_lag,
            "best_correlation":         valid_ccf.get(best_lag),
            "n_aligned_observations":   len(aligned),
            "interpretation": (
                "Kalshi leads by {:d} min".format(-best_lag) if best_lag and best_lag < 0
                else "Trad market leads by {:d} min".format(best_lag) if best_lag and best_lag > 0
                else "Simultaneous" if best_lag == 0
                else "Unknown"
            ),
        }

    except Exception as exc:
        logger.warning("Lead-lag computation failed: %s", exc)
        return {"error": str(exc)}


def _assess_data_quality(
    kalshi_df: pd.DataFrame,
    trad_df: pd.DataFrame,
    event_ts: datetime,
) -> Dict[str, Any]:
    """Flag data quality issues around the event."""
    issues = []

    if kalshi_df.empty:
        issues.append("No Kalshi data")
    if trad_df.empty:
        issues.append("No traditional market data")

    event_ts_aware = pd.Timestamp(event_ts)
    if event_ts_aware.tzinfo is None:
        event_ts_aware = event_ts_aware.tz_localize("UTC")

    return {
        "n_kalshi_obs": len(kalshi_df),
        "n_trad_obs":   len(trad_df),
        "issues":       issues,
        "data_complete": len(issues) == 0,
    }


# -- Hypothesis tests -----------------------------------------------------------

def test_spread_mean_reversion(
    spread_series: pd.Series,
    significance: float = 0.05,
) -> Dict[str, Any]:
    """
    H1: Large Kalshi/traditional probability spreads mean revert.
    Tests using ADF test for stationarity of the spread.
    """
    try:
        from statsmodels.tsa.stattools import adfuller
    except ImportError:
        return {"error": "statsmodels required"}

    if len(spread_series.dropna()) < 20:
        return {"error": "Insufficient data (need ≥20 obs)"}

    s = spread_series.dropna()
    adf_result = adfuller(s, autolag="AIC")

    adf_stat  = float(adf_result[0])
    p_value   = float(adf_result[1])
    is_stationary = p_value < significance

    return {
        "hypothesis":       "H1: Spread is stationary (mean-reverting)",
        "adf_statistic":    adf_stat,
        "p_value":          p_value,
        "is_stationary":    is_stationary,
        "conclusion": (
            "REJECT: Spread appears mean-reverting" if is_stationary
            else "FAIL TO REJECT: No evidence of mean reversion"
        ),
        "n_observations":   len(s),
        "mean_spread":      float(s.mean()),
        "std_spread":       float(s.std()),
    }


def test_spread_vs_liquidity(
    spread_series: pd.Series,
    liquidity_series: pd.Series,
) -> Dict[str, Any]:
    """
    H3: Large spreads are more likely when Kalshi liquidity is low.
    Tests correlation between |spread| and open interest.
    """
    aligned = pd.concat([spread_series.abs(), liquidity_series], axis=1).dropna()
    if len(aligned) < 10:
        return {"error": "Insufficient data"}

    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", stats.ConstantInputWarning)
            corr, p_value = stats.pearsonr(aligned.iloc[:, 0], aligned.iloc[:, 1])
    except (stats.ConstantInputWarning, Exception):
        corr, p_value = 0.0, 1.0

    return {
        "hypothesis": "H3: |spread| negatively correlated with Kalshi liquidity",
        "pearson_r":  float(corr),
        "p_value":    float(p_value),
        "n_obs":      len(aligned),
        "significant": p_value < 0.05,
        "conclusion": (
            "SUPPORTED: Large spreads associated with low liquidity" if corr < -0.1 and p_value < 0.05
            else "NOT SUPPORTED"
        ),
    }
