"""
cross_asset/historical_data.py
================================
Download 2 years of daily historical data from:
  - Bank of Canada VALET API: CORRA, policy rate, 2y/5y/10y bond yields
  - Yahoo Finance (yfinance): CADUSD, TSX, S&P 500, VIX, WTI crude oil

Output files (parquet):
  cross_asset/data/boc_history.parquet     — BOC VALET series
  cross_asset/data/market_history.parquet  — yfinance series

Usage:
    python cross_asset/historical_data.py
    python run.py cross_asset_history
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_DATA_DIR = _HERE / "data"
_BOC_PATH = _DATA_DIR / "boc_history.parquet"
_MKT_PATH = _DATA_DIR / "market_history.parquet"

# ---------------------------------------------------------------------------
# BOC VALET configuration
# ---------------------------------------------------------------------------
_BOC_BASE = "https://www.bankofcanada.ca/valet/observations"
_BOC_SERIES: Dict[str, str] = {
    "CORRA":        "AVG.INTWO",            # CORRA overnight rate (verified working)
    "POLICY_RATE":  "V39079",               # BoC overnight rate target (verified working)
    "CA_2Y_YIELD":  "BD.CDN.2YR.DQ.YLD",   # 2-year GoC bond yield (verified working)
    "CA_5Y_YIELD":  "BD.CDN.5YR.DQ.YLD",   # 5-year GoC bond yield
    "CA_10Y_YIELD": "BD.CDN.10YR.DQ.YLD",  # 10-year GoC bond yield
}

# Fallback series IDs
_BOC_SERIES_FALLBACK: Dict[str, str] = {
    "CORRA":        "AVG.INTWO",
    "POLICY_RATE":  "V39079",
    "CA_2Y_YIELD":  "BD.CDN.2YR.DQ.YLD",
    "CA_5Y_YIELD":  "BD.CDN.5YR.DQ.YLD",
    "CA_10Y_YIELD": "BD.CDN.10YR.DQ.YLD",
}

# ---------------------------------------------------------------------------
# yfinance tickers
# ---------------------------------------------------------------------------
_YF_TICKERS: Dict[str, str] = {
    "CADUSD":  "CADUSD=X",
    "TSX":     "^GSPTSE",   # Toronto Stock Exchange composite (correct ticker)
    "SP500":   "^GSPC",
    "VIX":     "^VIX",
    "WTI":     "CL=F",
}


# ---------------------------------------------------------------------------
# BOC VALET fetcher
# ---------------------------------------------------------------------------

def _fetch_boc_series(series_id: str, start: str, end: str) -> Optional[pd.Series]:
    """Fetch one BOC VALET series; returns a pd.Series indexed by date."""
    url = f"{_BOC_BASE}/{series_id}/json"
    params = {"start_date": start, "end_date": end, "order_dir": "asc"}
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("BOC VALET fetch failed (%s): %s", series_id, exc)
        return None

    obs = data.get("observations", [])
    if not obs:
        return None

    dates, values = [], []
    for o in obs:
        d = o.get("d")
        # Each observation has a key matching the series id
        for k, v in o.items():
            if k == "d":
                continue
            val = v.get("v") if isinstance(v, dict) else v
            try:
                dates.append(pd.Timestamp(d))
                values.append(float(val))
                break
            except (TypeError, ValueError):
                break

    if not dates:
        return None
    return pd.Series(values, index=pd.DatetimeIndex(dates), name=series_id)


def download_boc_history(years: int = 2) -> pd.DataFrame:
    """
    Download BOC VALET data for all configured series.
    Returns a DataFrame with columns = series names, index = date.
    """
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=years * 365)
    start = start_dt.strftime("%Y-%m-%d")
    end = end_dt.strftime("%Y-%m-%d")

    logger.info("BOC VALET download: %s to %s", start, end)
    frames: Dict[str, pd.Series] = {}

    for name, series_id in _BOC_SERIES.items():
        logger.info("  Fetching %s (%s)...", name, series_id)
        s = _fetch_boc_series(series_id, start, end)

        # Try fallback if primary fails
        if s is None or s.empty:
            fallback = _BOC_SERIES_FALLBACK.get(name)
            if fallback and fallback != series_id:
                logger.info("  Retrying with fallback ID: %s", fallback)
                s = _fetch_boc_series(fallback, start, end)

        if s is not None and not s.empty:
            frames[name] = s
            logger.info("    -> %d observations", len(s))
        else:
            logger.warning("    -> No data for %s", name)

        time.sleep(0.3)  # polite rate limiting

    if not frames:
        logger.error("No BOC data retrieved. Check network / series IDs.")
        return pd.DataFrame()

    df = pd.DataFrame(frames)
    df.index.name = "date"
    df = df.sort_index()
    return df


# ---------------------------------------------------------------------------
# yfinance fetcher
# ---------------------------------------------------------------------------

def download_market_history(years: int = 2) -> pd.DataFrame:
    """
    Download yfinance daily close prices for CADUSD, TSX, SP500, VIX, WTI.
    Returns a DataFrame with columns = asset names, index = date.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        return pd.DataFrame()

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=years * 365)
    start = start_dt.strftime("%Y-%m-%d")
    end = end_dt.strftime("%Y-%m-%d")

    logger.info("yfinance download: %s to %s", start, end)

    frames: Dict[str, pd.Series] = {}
    for name, ticker in _YF_TICKERS.items():
        logger.info("  Fetching %s (%s)...", name, ticker)
        try:
            t = yf.Ticker(ticker)
            hist = t.history(start=start, end=end, interval="1d", auto_adjust=True)
            if hist.empty:
                logger.warning("    -> No data for %s", ticker)
                continue
            s = hist["Close"].rename(name)
            s.index = s.index.tz_localize(None).normalize()  # strip tz, date only
            frames[name] = s
            logger.info("    -> %d trading days", len(s))
        except Exception as exc:
            logger.warning("    -> Error fetching %s: %s", ticker, exc)
        time.sleep(0.5)

    if not frames:
        logger.error("No yfinance data retrieved.")
        return pd.DataFrame()

    df = pd.DataFrame(frames)
    df.index = pd.DatetimeIndex(df.index)
    df.index.name = "date"
    df = df.sort_index()
    return df


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_historical_download(years: int = 2) -> Dict[str, str]:
    """
    Download BOC + yfinance history and save as parquet.
    Returns a summary dict with file paths and row counts.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, str] = {}

    # -- BOC VALET --
    boc_df = download_boc_history(years=years)
    if not boc_df.empty:
        boc_df.to_parquet(_BOC_PATH, index=True, engine="pyarrow")
        logger.info("Saved BOC history: %s (%d rows x %d cols)", _BOC_PATH, *boc_df.shape)
        summary["boc_path"] = str(_BOC_PATH)
        summary["boc_rows"] = str(len(boc_df))
        summary["boc_cols"] = ", ".join(boc_df.columns.tolist())
    else:
        summary["boc_status"] = "no_data"

    # -- yfinance --
    mkt_df = download_market_history(years=years)
    if not mkt_df.empty:
        mkt_df.to_parquet(_MKT_PATH, index=True, engine="pyarrow")
        logger.info("Saved market history: %s (%d rows x %d cols)", _MKT_PATH, *mkt_df.shape)
        summary["market_path"] = str(_MKT_PATH)
        summary["market_rows"] = str(len(mkt_df))
        summary["market_cols"] = ", ".join(mkt_df.columns.tolist())
    else:
        summary["market_status"] = "no_data"

    # -- Print summary --
    print("\n" + "=" * 60)
    print("CROSS-ASSET HISTORICAL DATA DOWNLOAD COMPLETE")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<20} {v}")

    if not boc_df.empty:
        print(f"\nBOC series date range: {boc_df.index[0].date()} → {boc_df.index[-1].date()}")
        print(boc_df.describe().round(4).to_string())

    if not mkt_df.empty:
        print(f"\nMarket data date range: {mkt_df.index[0].date()} → {mkt_df.index[-1].date()}")
        print(mkt_df.describe().round(4).to_string())

    return summary


# ---------------------------------------------------------------------------
# Load helpers (for dashboard / analysis use)
# ---------------------------------------------------------------------------

def load_boc_history() -> pd.DataFrame:
    """Load the saved BOC history parquet. Returns empty DataFrame if file missing."""
    if _BOC_PATH.exists():
        return pd.read_parquet(_BOC_PATH)
    logger.warning("BOC history not found at %s. Run: python cross_asset/historical_data.py", _BOC_PATH)
    return pd.DataFrame()


def load_market_history() -> pd.DataFrame:
    """Load the saved market history parquet. Returns empty DataFrame if file missing."""
    if _MKT_PATH.exists():
        return pd.read_parquet(_MKT_PATH)
    logger.warning("Market history not found at %s. Run: python cross_asset/historical_data.py", _MKT_PATH)
    return pd.DataFrame()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    run_historical_download(years=2)
