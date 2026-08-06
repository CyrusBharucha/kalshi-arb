"""
cross_asset/market_data.py
==========================
Free market data fetcher for cross-asset analysis.

Sources (zero API keys required):
  1. Bank of Canada VALET API  — CORRA, policy rate, GoC bonds (built-in, free)
  2. yfinance                  — FX, equities, commodities, VIX

Fetches and caches data locally in PostgreSQL (ext_market_daily table).
All cross-asset pages read from the DB; this module populates it.

Run:
  python cross_asset/market_data.py                  # full refresh
  python cross_asset/market_data.py --days 7         # last 7 days only
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Asset definitions
# ---------------------------------------------------------------------------
YFINANCE_ASSETS = {
    # FX
    "CADUSD":  "CADUSD=X",     # CAD/USD exchange rate
    "USDCAD":  "CAD=X",        # USD/CAD (inverse, common quoting)
    # US equities / vol
    "SP500":   "^GSPC",        # S&P 500
    "NASDAQ":  "^IXIC",        # NASDAQ Composite
    "DJI":     "^DJI",         # Dow Jones Industrial Average
    "VIX":     "^VIX",         # CBOE Volatility Index
    # Canadian equities
    "TSX":     "^GSPTSE",      # S&P/TSX Composite
    # Commodities
    "WTI":     "CL=F",         # WTI Crude Oil front-month futures
    "GOLD":    "GC=F",         # Gold front-month futures
    "NATGAS":  "NG=F",         # Natural Gas front-month futures
    # US Treasury yields
    "TNX":     "^TNX",         # US 10-Year Treasury yield
    "FVX":     "^FVX",         # US 5-Year Treasury yield
    # Crypto (Kalshi crypto markets)
    "BTC":     "BTC-USD",      # Bitcoin
    "ETH":     "ETH-USD",      # Ethereum
    "DOGE":    "DOGE-USD",     # Dogecoin
    "XRP":     "XRP-USD",      # XRP / Ripple
    "BNB":     "BNB-USD",      # Binance Coin
}

BOC_SERIES = {
    "CORRA":      "AVG.INTWO",         # Canadian Overnight Repo Rate Average (daily)
    "BOC_RATE":   "V80691311",         # Bank of Canada overnight target rate
    "CAGB_2Y":    "V122514",           # 2-Year GoC benchmark bond yield
    "CAGB_5Y":    "BD.CDN.5YR.DQ.YLD",    # 5-Year GoC bond yield
    "CAGB_10Y":   "BD.CDN.10YR.DQ.YLD",   # 10-Year GoC bond yield
    "CA_CPI":     "V41690914",         # Canada CPI All-items (monthly)
}

BOC_VALET_URL = "https://www.bankofcanada.ca/valet"


# ---------------------------------------------------------------------------
# BOC VALET fetcher
# ---------------------------------------------------------------------------
def fetch_boc_series(series_name: str, start_date: str, end_date: str) -> List[Dict]:
    """
    Fetch a single BOC VALET time series.
    Returns list of {date, value, series}.
    """
    url = f"{BOC_VALET_URL}/observations/{series_name}/json"
    params = {"start_date": start_date, "end_date": end_date}
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            logger.warning("BOC VALET %s: HTTP %d", series_name, r.status_code)
            return []
        data = r.json()
        obs  = data.get("observations", [])
        rows = []
        for o in obs:
            date_str = o.get("d", "")
            # BOC VALET observation dict: {"d": "2026-01-02", "<series_id>": {"v": "2.25"}}
            # The value is under the series_name key, which is a dict with "v" field.
            val_dict = o.get(series_name)
            if val_dict is None:
                # Try finding any non-"d" key
                for k, vv in o.items():
                    if k != "d":
                        val_dict = vv
                        break
            if isinstance(val_dict, dict):
                v = val_dict.get("v")
            elif val_dict is not None:
                v = val_dict
            else:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            rows.append({
                "date":   date_str,
                "value":  v,
                "series": series_name,
            })
        return rows
    except Exception as exc:
        logger.warning("BOC VALET fetch failed for %s: %s", series_name, exc)
        return []


def fetch_all_boc(start_date: str, end_date: str) -> Dict[str, List[Dict]]:
    """Fetch all configured BOC series. Returns {asset_name: [rows]}."""
    results = {}
    for asset_name, series_id in BOC_SERIES.items():
        logger.info("Fetching BOC %s (%s)...", asset_name, series_id)
        rows = fetch_boc_series(series_id, start_date, end_date)
        results[asset_name] = rows
        logger.info("  %s: %d observations", asset_name, len(rows))
        time.sleep(0.2)  # be polite to BOC servers
    return results


# ---------------------------------------------------------------------------
# yfinance fetcher
# ---------------------------------------------------------------------------
def fetch_yfinance(symbol: str, asset_name: str, start_date: str, end_date: str) -> List[Dict]:
    """
    Fetch daily OHLCV from yfinance. Returns list of {date, open, high, low, close, volume}.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start_date, end=end_date, interval="1d", auto_adjust=True)
        if df.empty:
            logger.warning("yfinance %s: no data returned", symbol)
            return []
        df = df.reset_index()
        rows = []
        for _, row in df.iterrows():
            date_val = row.get("Date") or row.get("Datetime")
            if hasattr(date_val, "strftime"):
                date_str = date_val.strftime("%Y-%m-%d")
            else:
                date_str = str(date_val)[:10]
            rows.append({
                "date":   date_str,
                "open":   float(row.get("Open",  0) or 0),
                "high":   float(row.get("High",  0) or 0),
                "low":    float(row.get("Low",   0) or 0),
                "close":  float(row.get("Close", 0) or 0),
                "volume": float(row.get("Volume",0) or 0),
            })
        return rows
    except Exception as exc:
        logger.warning("yfinance %s (%s): %s", symbol, asset_name, exc)
        return []


def fetch_all_yfinance(start_date: str, end_date: str) -> Dict[str, List[Dict]]:
    """Fetch all configured yfinance assets. Returns {asset_name: [rows]}."""
    results = {}
    for asset_name, symbol in YFINANCE_ASSETS.items():
        logger.info("Fetching yfinance %s (%s)...", asset_name, symbol)
        rows = fetch_yfinance(symbol, asset_name, start_date, end_date)
        results[asset_name] = rows
        logger.info("  %s: %d daily bars", asset_name, len(rows))
        time.sleep(0.1)
    return results


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------
def ensure_external_data_table(engine) -> bool:
    """
    Ensure ext_market_daily table exists with OHLCV schema.
    The legacy external_market_data table has a different schema (price_ts, asset, price cols),
    so we use a dedicated ext_market_daily table for yfinance/BOC VALET daily bars.
    """
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS ext_market_daily (
                    id          BIGSERIAL PRIMARY KEY,
                    asset_name  TEXT NOT NULL,
                    source      TEXT NOT NULL,
                    obs_date    DATE NOT NULL,
                    open_val    DOUBLE PRECISION,
                    high_val    DOUBLE PRECISION,
                    low_val     DOUBLE PRECISION,
                    close_val   DOUBLE PRECISION,
                    volume_val  DOUBLE PRECISION,
                    fetched_at  TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (asset_name, obs_date)
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_extdaily_asset_date ON ext_market_daily(asset_name, obs_date)"
            ))
        return True
    except Exception as exc:
        logger.error("Failed to ensure ext_market_daily table: %s", exc)
        return False


def upsert_external_data(engine, asset_name: str, source: str, rows: List[Dict]) -> int:
    """
    Upsert rows into ext_market_daily.
    Handles both OHLCV rows (yfinance) and single-value rows (BOC).
    Returns rows inserted/updated.
    """
    if not rows:
        return 0
    from sqlalchemy import text
    inserted = 0
    batch = []
    for r in rows:
        if source == "yfinance":
            batch.append({
                "asset":   asset_name,
                "src":     source,
                "date":    r["date"],
                "open":    r.get("open"),
                "high":    r.get("high"),
                "low":     r.get("low"),
                "close":   r.get("close"),
                "vol":     r.get("volume"),
            })
        else:  # BOC / single-value
            batch.append({
                "asset":   asset_name,
                "src":     source,
                "date":    r["date"],
                "open":    None,
                "high":    None,
                "low":     None,
                "close":   r.get("value"),
                "vol":     None,
            })

    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO ext_market_daily
                    (asset_name, source, obs_date, open_val, high_val, low_val, close_val, volume_val)
                SELECT
                    :asset, :src, CAST(:date AS DATE),
                    :open, :high, :low, :close, :vol
                ON CONFLICT (asset_name, obs_date) DO UPDATE SET
                    close_val  = EXCLUDED.close_val,
                    open_val   = EXCLUDED.open_val,
                    high_val   = EXCLUDED.high_val,
                    low_val    = EXCLUDED.low_val,
                    volume_val = EXCLUDED.volume_val,
                    fetched_at = NOW()
            """), batch)
        inserted = len(batch)
    except Exception as exc:
        logger.error("upsert_external_data %s: %s", asset_name, exc)
    return inserted


# ---------------------------------------------------------------------------
# Macro events (BOC meetings, CPI releases etc.)
# ---------------------------------------------------------------------------
# Known Bank of Canada meeting dates (2026) — mark scheduled/decided
BOC_MEETINGS_2026 = [
    ("2026-01-29", "BOC_RATE_DECISION", "decided"),
    ("2026-03-12", "BOC_RATE_DECISION", "decided"),
    ("2026-04-16", "BOC_RATE_DECISION", "decided"),
    ("2026-06-04", "BOC_RATE_DECISION", "decided"),
    ("2026-07-30", "BOC_RATE_DECISION", "decided"),
    ("2026-09-10", "BOC_RATE_DECISION", "scheduled"),
    ("2026-10-29", "BOC_RATE_DECISION", "scheduled"),
    ("2026-12-10", "BOC_RATE_DECISION", "scheduled"),
]

def upsert_macro_events(engine) -> int:
    """
    Populate macro_events with BOC meeting dates.
    Adapts to whichever schema the table already has:
      - If table has event_date (our schema): use it
      - If table has event_ts (legacy schema): use CAST to timestamptz
    """
    from sqlalchemy import text
    rows = []
    for date_str, event_type, status in BOC_MEETINGS_2026:
        rows.append({
            "event_ts":    f"{date_str}T12:00:00+00:00",
            "event_type":  event_type,
            "description": f"Bank of Canada rate decision - {status}",
            "source":      "boc_calendar",
            "country":     "CA",
        })
    if not rows:
        return 0
    try:
        with engine.begin() as conn:
            # Use the existing macro_events schema (event_ts, country, event_type...)
            conn.execute(text("""
                INSERT INTO macro_events (event_ts, country, event_type, description, source)
                SELECT
                    CAST(:event_ts AS TIMESTAMPTZ),
                    CAST(:country AS CHAR(2)),
                    :event_type, :description, :source
                ON CONFLICT DO NOTHING
            """), rows)
        return len(rows)
    except Exception as exc:
        logger.error("upsert_macro_events: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Main refresh
# ---------------------------------------------------------------------------
def run_refresh(days_back: int = 365) -> Dict[str, int]:
    """
    Fetch all external market data and persist to PostgreSQL.
    Returns dict of {asset_name: rows_upserted}.
    """
    from database.repository import get_engine
    engine = get_engine()
    ensure_external_data_table(engine)

    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days_back)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str   = end_dt.strftime("%Y-%m-%d")

    logger.info("Refreshing external market data: %s → %s", start_str, end_str)
    counts: Dict[str, int] = {}

    # 1. BOC VALET (rates, CPI)
    boc_data = fetch_all_boc(start_str, end_str)
    for asset_name, rows in boc_data.items():
        n = upsert_external_data(engine, asset_name, "boc_valet", rows)
        counts[asset_name] = n
        logger.info("  Persisted %s: %d rows", asset_name, n)

    # 2. yfinance (FX, equities, commodities)
    yf_data = fetch_all_yfinance(start_str, end_str)
    for asset_name, rows in yf_data.items():
        n = upsert_external_data(engine, asset_name, "yfinance", rows)
        counts[asset_name] = n
        logger.info("  Persisted %s: %d rows", asset_name, n)

    # 3. Macro events
    n_macro = upsert_macro_events(engine)
    counts["macro_events"] = n_macro
    logger.info("  Macro events: %d rows", n_macro)

    total = sum(counts.values())
    logger.info("External data refresh complete — %d total rows across %d assets", total, len(counts))
    return counts


# ---------------------------------------------------------------------------
# Quick read API for dashboard
# ---------------------------------------------------------------------------
def get_asset_history(asset_name: str, days: int = 90) -> "pd.DataFrame":
    """
    Read historical data for one asset from PostgreSQL ext_market_daily.
    Returns DataFrame with columns: obs_date, close_val, open_val, high_val, low_val, volume_val.
    """
    try:
        import pandas as pd
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        with engine.connect() as conn:
            df = pd.read_sql(text("""
                SELECT obs_date, close_val, open_val, high_val, low_val, volume_val
                FROM ext_market_daily
                WHERE asset_name = :asset AND obs_date >= :cutoff
                ORDER BY obs_date
            """), conn, params={"asset": asset_name, "cutoff": cutoff})
        return df
    except Exception as exc:
        logger.warning("get_asset_history(%s): %s", asset_name, exc)
        import pandas as pd
        return pd.DataFrame()


def get_latest_prices() -> Dict[str, float]:
    """
    Return the most recent close_val for each asset in ext_market_daily.
    Returns {asset_name: float}.
    """
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT ON (asset_name)
                    asset_name, close_val, obs_date
                FROM ext_market_daily
                WHERE close_val IS NOT NULL
                ORDER BY asset_name, obs_date DESC
            """)).fetchall()
        return {r[0]: float(r[1]) for r in rows if r[1] is not None}
    except Exception as exc:
        logger.warning("get_latest_prices: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Fetch and store external market data")
    parser.add_argument("--days", type=int, default=365, help="Days of history to fetch")
    args = parser.parse_args()
    counts = run_refresh(days_back=args.days)
    print("\nSummary:")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v:,} rows")
