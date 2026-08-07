"""
cross_asset/live_data.py
=========================
Live cross-asset data fetcher — no paid API keys required for core functionality.

Data sources (all free / no key):
  - Bank of Canada VALET API: CORRA, policy rate, government bond yields
  - Yahoo Finance (yfinance): CAD/USD FX, TSX, WTI crude, VIX
  - Statistics Canada (statscan): CPI, GDP (quarterly)

Optional (requires .env keys):
  - FRED API: US rates, US CPI (FRED_API_KEY)
  - EIA API: WTI official (EIA_API_KEY)
  - Alpha Vantage: intraday FX (ALPHA_VANTAGE_API_KEY)

Usage:
    from analysis.live_data import get_boc_rates, get_fx_rates, get_all_live

    rates = get_boc_rates()        # {"corra": 0.0275, "policy_rate": 0.0275, ...}
    fx    = get_fx_rates()         # {"cadusd": 0.731, "cadusd_1d_change": -0.002, ...}
    all_  = get_all_live()         # combined dict of all available data
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import requests

log = logging.getLogger(__name__)

_CACHE: Dict[str, tuple] = {}   # key -> (value, expires_ts)
_CACHE_TTL = {
    "boc":   3600,    # BOC VALET updates daily
    "fx":    300,     # FX updates every 5 min
    "wti":   3600,
    "fred":  3600,
}


def _cached(key: str, ttl_key: str, fn, *args, **kwargs):
    now = time.monotonic()
    if key in _CACHE:
        val, exp = _CACHE[key]
        if now < exp:
            return val
    try:
        val = fn(*args, **kwargs)
        _CACHE[key] = (val, now + _CACHE_TTL.get(ttl_key, 3600))
        return val
    except Exception as e:
        log.warning("Data fetch failed for %s: %s", key, e)
        if key in _CACHE:
            return _CACHE[key][0]   # return stale data rather than failing
        return {}


# ---------------------------------------------------------------------------
# Bank of Canada VALET API (free, no key)
# ---------------------------------------------------------------------------

BOC_VALET = "https://www.bankofcanada.ca/valet"

_BOC_SERIES = {
    "corra":           "AVG.INTWO",            # Canadian Overnight Repo Rate Average (CORRA) (%)
    "policy_rate":     "V39079",               # Bank of Canada policy interest rate (target)
    "gbond_2y":        "BD.CDN.2YR.DQ.YLD",   # 2-year gov bond yield
    "gbond_5y":        "BD.CDN.5YR.DQ.YLD",   # 5-year gov bond yield
    "gbond_10y":       "BD.CDN.10YR.DQ.YLD",  # 10-year gov bond yield
}


def _boc_fetch(series_id: str, days_back: int = 30) -> Optional[float]:
    """Fetch latest value for a BOC VALET series."""
    end   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    url   = f"{BOC_VALET}/observations/{series_id}/json"
    # NOTE: BOC VALET does not support 'limit' param — fetch range and take last
    params = {"start_date": start, "end_date": end, "order_dir": "desc"}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    obs = data.get("observations", [])
    if not obs:
        return None
    val = obs[0].get(series_id, {}).get("v")
    return float(val) / 100.0 if val else None   # convert % to decimal


def _boc_history(series_id: str, days_back: int = 365) -> list[tuple[str, float]]:
    """Fetch historical time-series from BOC VALET."""
    end   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    url   = f"{BOC_VALET}/observations/{series_id}/json"
    params = {"start_date": start, "end_date": end, "order_dir": "asc"}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    obs  = data.get("observations", [])
    result = []
    for o in obs:
        d   = o.get("d")
        val = o.get(series_id, {}).get("v")
        if d and val:
            try:
                result.append((d, float(val) / 100.0))
            except (ValueError, TypeError):
                pass
    return result


def get_boc_rates(days_back: int = 30) -> Dict[str, Any]:
    """Fetch current BOC rates from VALET API."""
    def _fetch():
        out: Dict[str, Any] = {"source": "Bank of Canada VALET", "as_of": None}
        for name, series_id in _BOC_SERIES.items():
            try:
                val = _boc_fetch(series_id, days_back)
                out[name] = val
                if val is not None:
                    out["as_of"] = datetime.now(timezone.utc).isoformat()
            except Exception as e:
                log.warning("BOC series %s failed: %s", series_id, e)
                out[name] = None
        return out

    return _cached("boc_rates", "boc", _fetch)


def get_boc_history(series: str = "corra", days_back: int = 365) -> list:
    """Return [(date_str, rate_decimal), ...] for a BOC series."""
    series_id = _BOC_SERIES.get(series, series)
    return _cached(f"boc_hist_{series}_{days_back}", "boc", _boc_history, series_id, days_back)


# ---------------------------------------------------------------------------
# Yahoo Finance — FX, equities, commodities (free, no key)
# ---------------------------------------------------------------------------

def get_fx_rates() -> Dict[str, Any]:
    """Fetch live FX rates via yfinance."""
    def _fetch():
        import yfinance as yf
        out: Dict[str, Any] = {"source": "Yahoo Finance", "as_of": None}

        tickers_map = {
            "cadusd":  "CADUSD=X",
            "usdcad":  "USDCAD=X",
            "eurusd":  "EURUSD=X",
        }
        for name, yt in tickers_map.items():
            try:
                t = yf.Ticker(yt)
                hist = t.history(period="2d", interval="1h")
                if not hist.empty:
                    out[name] = float(hist["Close"].iloc[-1])
                    if len(hist) > 1:
                        prev = float(hist["Close"].iloc[-2])
                        out[f"{name}_chg"] = round(out[name] - prev, 6)
            except Exception as e:
                log.warning("FX %s failed: %s", name, e)
                out[name] = None

        out["as_of"] = datetime.now(timezone.utc).isoformat()
        return out

    return _cached("fx_rates", "fx", _fetch)


def get_equity_data() -> Dict[str, Any]:
    """Fetch TSX, S&P 500, VIX from Yahoo Finance."""
    def _fetch():
        import yfinance as yf
        out: Dict[str, Any] = {"source": "Yahoo Finance"}
        tickers_map = {
            "tsx":   "^GSPTSE",
            "sp500": "^GSPC",
            "vix":   "^VIX",
            "wti":   "CL=F",
        }
        for name, yt in tickers_map.items():
            try:
                t = yf.Ticker(yt)
                hist = t.history(period="2d", interval="1d")
                if not hist.empty:
                    out[name] = float(hist["Close"].iloc[-1])
                    if len(hist) > 1:
                        prev = float(hist["Close"].iloc[-2])
                        out[f"{name}_1d_chg_pct"] = round((out[name] - prev) / prev * 100, 4)
            except Exception as e:
                log.warning("Equity %s failed: %s", name, e)
                out[name] = None
        out["as_of"] = datetime.now(timezone.utc).isoformat()
        return out

    return _cached("equity", "fx", _fetch)


# ---------------------------------------------------------------------------
# Combined snapshot
# ---------------------------------------------------------------------------

def get_all_live() -> Dict[str, Any]:
    """Return a combined snapshot of all live cross-asset data."""
    boc = get_boc_rates()
    fx  = get_fx_rates()
    eq  = get_equity_data()
    return {
        "boc": boc,
        "fx":  fx,
        "equity": eq,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print("Fetching live cross-asset data...")
    data = get_all_live()
    print(json.dumps(data, indent=2, default=str))
