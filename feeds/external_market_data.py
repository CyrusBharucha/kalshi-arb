"""
data/external_market_data.py
Fetches traditional financial market data for the Canadian cross-asset engine.

Sources:
  1. Bank of Canada VALET API        - CORRA, overnight rate, bond yields (free, no key)
  2. FRED (St. Louis Fed)            - CAD/USD, macro indicators (free key)
  3. Alpha Vantage                   - WTI spot, TSX, FX (free tier)
  4. EIA (Energy Info Administration)- WTI price series (free key)

All data is upserted into external_market_data table.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import requests
import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from database.repository import session_scope, upsert_external_data, log_ingestion

logger = logging.getLogger(__name__)


# -- HTTP helper ----------------------------------------------------------------

_session = requests.Session()
_session.headers["User-Agent"] = "KalshiArbEngine/1.0 research"


def _get_json(url: str, params: Dict = None, retries: int = 3) -> Any:
    for attempt in range(retries):
        try:
            resp = _session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Bank of Canada VALET API
# Docs: https://www.bankofcanada.ca/valet/docs
# No API key required.
# ══════════════════════════════════════════════════════════════════════════════

BOC_SERIES = {
    # Overnight / policy rate
    "CORRA":   "V39079",    # Canadian Overnight Repo Rate Average
    "BOC_RATE": "V122514",  # Bank of Canada target overnight rate
    # Government bond yields
    "CAGB_2Y":  "V122531",  # GoC 2-year bond yield
    "CAGB_5Y":  "V122533",  # GoC 5-year bond yield
    "CAGB_10Y": "V122538",  # GoC 10-year bond yield
    # Inflation
    "CPI_TOTAL": "V41690914", # Canada CPI total (monthly)
}


def fetch_boc_series(
    series_id: str,
    asset_name: str,
    start_date: str = "2020-01-01",
    end_date: Optional[str] = None,
) -> int:
    """
    Fetch a Bank of Canada VALET series and upsert to DB.
    Returns number of rows inserted.
    """
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    url = f"{config.BOC_VALET_URL}/observations/{series_id}/json"
    params = {
        "start_date": start_date,
        "end_date":   end_date,
        "order_dir":  "asc",
    }
    data = _get_json(url, params)
    observations = data.get("observations", [])

    inserted = 0
    with session_scope() as s:
        for obs in observations:
            date_str = obs.get("d", "")
            val_raw  = (obs.get(series_id) or {}).get("v")
            if not date_str or val_raw is None:
                continue
            try:
                ts = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                price = float(val_raw) / 100.0   # yields come as percent - convert to decimal
                row = {
                    "price_ts":         ts,
                    "asset":            asset_name,
                    "instrument":       "rate",
                    "tenor":            None,
                    "price":            price,
                    "bid":              None,
                    "ask":              None,
                    "volume":           None,
                    "source":           "boc_valet",
                    "source_series_id": series_id,
                }
                upsert_external_data(s, row)
                inserted += 1
            except Exception as exc:
                logger.debug("BOC row skip %s %s: %s", series_id, date_str, exc)

    logger.info("BOC %s (%s): %d rows inserted", asset_name, series_id, inserted)
    return inserted


def fetch_all_boc_series(start_date: str = "2020-01-01") -> None:
    """Fetch every configured BOC series."""
    for asset_name, series_id in BOC_SERIES.items():
        try:
            fetch_boc_series(series_id, asset_name, start_date=start_date)
            with session_scope() as s:
                log_ingestion(s, job_type="boc_valet", target=series_id, status="success")
        except Exception as exc:
            logger.error("BOC series %s failed: %s", series_id, exc)
            with session_scope() as s:
                log_ingestion(s, job_type="boc_valet", target=series_id,
                               status="error", error_message=str(exc))
        time.sleep(0.5)


# ══════════════════════════════════════════════════════════════════════════════
# 2. FRED (Federal Reserve Economic Data)
# Docs: https://fred.stlouisfed.org/docs/api/fred/
# Free API key required.
# ══════════════════════════════════════════════════════════════════════════════

FRED_SERIES = {
    # CAD/USD
    "CADUSD":  ("DEXCAUS",  "fx",       None,   "daily"),   # USD per CAD
    # Canadian macro
    "CA_CPI":  ("CPALTT01CAM657N", "macro", None, "monthly"), # CPI
    "CA_UNEMP": ("LRUNTTTTCAM156S", "macro", None, "monthly"), # Unemployment
    # WTI (backup)
    "WTI_SPOT": ("DCOILWTICO", "futures", "spot", "daily"),
    # US-Canada rate spread
    "US_FEDFUNDS": ("FEDFUNDS", "rate", "overnight", "monthly"),
}


def fetch_fred_series(
    series_id: str,
    asset_name: str,
    instrument: str,
    tenor: Optional[str],
    start_date: str = "2020-01-01",
) -> int:
    """Fetch a FRED series and upsert to DB."""
    if not config.FRED_API_KEY:
        logger.warning("FRED_API_KEY not set - skipping %s", series_id)
        return 0

    url  = f"{config.FRED_BASE_URL}/series/observations"
    params = {
        "series_id":         series_id,
        "api_key":           config.FRED_API_KEY,
        "file_type":         "json",
        "observation_start": start_date,
        "sort_order":        "asc",
    }
    data = _get_json(url, params)
    observations = data.get("observations", [])

    inserted = 0
    with session_scope() as s:
        for obs in observations:
            date_str = obs.get("date", "")
            val_raw  = obs.get("value", ".")
            if val_raw == "." or not date_str:
                continue
            try:
                ts    = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                price = float(val_raw)
                row = {
                    "price_ts":         ts,
                    "asset":            asset_name,
                    "instrument":       instrument,
                    "tenor":            tenor,
                    "price":            price,
                    "bid":              None,
                    "ask":              None,
                    "volume":           None,
                    "source":           "fred",
                    "source_series_id": series_id,
                }
                upsert_external_data(s, row)
                inserted += 1
            except Exception as exc:
                logger.debug("FRED row skip %s %s: %s", series_id, date_str, exc)

    logger.info("FRED %s (%s): %d rows", asset_name, series_id, inserted)
    return inserted


def fetch_all_fred_series(start_date: str = "2020-01-01") -> None:
    for asset_name, (series_id, instrument, tenor, _freq) in FRED_SERIES.items():
        try:
            fetch_fred_series(series_id, asset_name, instrument, tenor, start_date)
        except Exception as exc:
            logger.error("FRED series %s failed: %s", series_id, exc)
        time.sleep(0.3)


# ══════════════════════════════════════════════════════════════════════════════
# 3. EIA (Energy Information Administration)
# WTI crude oil spot price
# ══════════════════════════════════════════════════════════════════════════════

def fetch_eia_wti(start_date: str = "2020-01-01") -> int:
    """Fetch WTI weekly spot price from EIA OPEN DATA v2."""
    if not config.EIA_API_KEY:
        logger.warning("EIA_API_KEY not set - skipping WTI fetch")
        return 0

    url = f"{config.EIA_BASE_URL}/petroleum/pri/spt/data"
    params = {
        "api_key": config.EIA_API_KEY,
        "frequency": "weekly",
        "data[0]": "value",
        "facets[series][]": "RWTC",   # WTI spot price, dollars per barrel
        "start": start_date,
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": 5000,
        "offset": 0,
    }
    data = _get_json(url, params)
    series = (data.get("response") or {}).get("data", [])

    inserted = 0
    with session_scope() as s:
        for obs in series:
            date_str = obs.get("period", "")
            val_raw  = obs.get("value")
            if val_raw is None or not date_str:
                continue
            try:
                ts = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                row = {
                    "price_ts":         ts,
                    "asset":            "WTI_SPOT",
                    "instrument":       "spot",
                    "tenor":            None,
                    "price":            float(val_raw),
                    "bid":              None,
                    "ask":              None,
                    "volume":           None,
                    "source":           "eia",
                    "source_series_id": "RWTC",
                }
                upsert_external_data(s, row)
                inserted += 1
            except Exception as exc:
                logger.debug("EIA row skip: %s", exc)

    logger.info("EIA WTI: %d rows inserted", inserted)
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# 4. Alpha Vantage (FX spot, TSX proxy)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_alpha_vantage_fx(
    from_ccy: str = "CAD",
    to_ccy: str = "USD",
    asset_name: str = "CADUSD",
    outputsize: str = "full",
) -> int:
    """Fetch daily FX from Alpha Vantage."""
    if not config.ALPHA_VANTAGE_KEY:
        logger.warning("ALPHA_VANTAGE_KEY not set - skipping FX")
        return 0

    url = config.ALPHA_VANTAGE_URL
    params = {
        "function":   "FX_DAILY",
        "from_symbol": from_ccy,
        "to_symbol":   to_ccy,
        "outputsize":  outputsize,
        "apikey":      config.ALPHA_VANTAGE_KEY,
    }
    data = _get_json(url, params)
    ts_data = data.get("Time Series FX (Daily)", {})

    inserted = 0
    with session_scope() as s:
        for date_str, ohlc in sorted(ts_data.items()):
            try:
                ts = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                row = {
                    "price_ts":         ts,
                    "asset":            asset_name,
                    "instrument":       "spot",
                    "tenor":            None,
                    "price":            float(ohlc.get("4. close", 0)),
                    "bid":              None,
                    "ask":              None,
                    "volume":           None,
                    "source":           "alpha_vantage",
                    "source_series_id": f"{from_ccy}{to_ccy}",
                }
                upsert_external_data(s, row)
                inserted += 1
            except Exception as exc:
                logger.debug("AV row skip %s: %s", date_str, exc)

    logger.info("Alpha Vantage %s/%s: %d rows", from_ccy, to_ccy, inserted)
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# 5. OIS / Implied Policy Rate from CORRA / Overnight Rate
#    Derives implied BOC rate probabilities from the swap market.
#    Uses BOC VALET data - CORRA is the nearest available proxy.
# ══════════════════════════════════════════════════════════════════════════════

def compute_implied_boc_probabilities(
    meeting_date: datetime,
    possible_rates: List[float],
    current_ois_rate: float,
) -> Dict[str, float]:
    """
    Given an OIS/CORRA rate and a meeting date, compute the probability
    distribution across possible policy outcomes using a simple linear model.

    This is a simplified version - proper implementation uses the OIS strip
    to back out the expected rate at each meeting and then distributes
    probability across strikes using the normal distribution.

    Args:
        meeting_date:   Next BOC decision date
        possible_rates: List of possible target rates (e.g. [4.25, 4.50, 4.75])
        current_ois_rate: Current overnight rate from CORRA/swap market

    Returns:
        {rate_str: probability} dict
    """
    import numpy as np

    rates_arr = sorted(possible_rates)
    mean = current_ois_rate
    std  = 0.25   # rough assumption - 25bp uncertainty

    # Implied probability for each discrete outcome using normal CDF
    from scipy.stats import norm
    probs = {}
    n = len(rates_arr)
    for i, rate in enumerate(rates_arr):
        if i == 0:
            p = norm.cdf(rate + 0.125, loc=mean, scale=std)
        elif i == n - 1:
            p = 1.0 - norm.cdf(rate - 0.125, loc=mean, scale=std)
        else:
            p = (norm.cdf(rate + 0.125, loc=mean, scale=std) -
                 norm.cdf(rate - 0.125, loc=mean, scale=std))
        probs[f"{rate:.2f}%"] = max(0.0, min(1.0, p))

    # Normalize
    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}
    return probs


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrator
# ══════════════════════════════════════════════════════════════════════════════

def run_external_data_ingest(start_date: str = "2022-01-01") -> None:
    """Pull all external market data sources."""
    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s: %(message)s")
    logger.info("=== External Market Data Ingest ===")

    logger.info("Fetching Bank of Canada series...")
    fetch_all_boc_series(start_date=start_date)

    logger.info("Fetching FRED series...")
    fetch_all_fred_series(start_date=start_date)

    logger.info("Fetching EIA WTI...")
    fetch_eia_wti(start_date=start_date)

    logger.info("Fetching Alpha Vantage FX (CAD/USD)...")
    fetch_alpha_vantage_fx()

    logger.info("=== External data ingest complete ===")


if __name__ == "__main__":
    run_external_data_ingest()
