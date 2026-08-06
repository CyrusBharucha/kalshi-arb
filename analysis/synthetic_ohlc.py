"""
analysis/synthetic_ohlc.py
Build synthetic OHLC candlesticks from raw trade data.

Since Kalshi's candlestick API requires authentication, we construct
candlestick data from the 1.97M public trade records we already have.
Tick-level trade data -> hourly/daily OHLC stored in candlesticks table.

Coverage: any market that has trades in the trades table.
Classification: Class B (historical - no live order-book depth).

Usage:
    python analysis/synthetic_ohlc.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Any, List

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.repository import session_scope
from database.models import Candlestick

logger = logging.getLogger(__name__)

PERIODS = [60, 1440]   # minutes: hourly + daily


def build_synthetic_ohlc(period_minutes: int = 60) -> Dict[str, Any]:
    """
    Aggregate trades into OHLC bars and store in candlesticks table.

    Returns summary dict.
    """
    logger.info("Building synthetic OHLC (period=%dm)...", period_minutes)

    # Load all trades into pandas
    with session_scope() as s:
        df = pd.read_sql(
            text("""
                SELECT market_id, trade_ts, price, quantity
                FROM trades
                ORDER BY market_id, trade_ts
            """),
            s.bind,
        )

    if df.empty:
        logger.warning("No trades found - cannot build synthetic OHLC")
        return {"rows_written": 0}

    logger.info("Loaded %d trade rows across %d markets",
                len(df), df["market_id"].nunique())

    df["trade_ts"] = pd.to_datetime(df["trade_ts"], utc=True)
    df["price"]    = pd.to_numeric(df["price"],    errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(1)

    # Resample per market
    freq = f"{period_minutes}min"
    all_rows: List[Dict] = []

    for market_id, grp in df.groupby("market_id"):
        grp = grp.set_index("trade_ts").sort_index()

        ohlc = grp["price"].resample(freq).ohlc()
        vol  = grp["quantity"].resample(freq).sum()

        merged = ohlc.join(vol.rename("volume"), how="left")
        merged.dropna(subset=["open"], inplace=True)

        for ts, row in merged.iterrows():
            # period_end_ts = end of the bar
            end_ts = ts + pd.Timedelta(minutes=period_minutes)
            all_rows.append({
                "market_id":       market_id,
                "period_interval": period_minutes,
                "period_end_ts":   end_ts.to_pydatetime(),
                "price_open":      float(row["open"]),
                "price_high":      float(row["high"]),
                "price_low":       float(row["low"]),
                "price_close":     float(row["close"]),
                "price_mean":      float(row["close"]),   # approximation
                "volume":          float(row["volume"]) if pd.notna(row["volume"]) else None,
                # No bid/ask in trade data - these are last-price only
                "yes_bid_close":   None,
                "yes_ask_close":   None,
            })

        if len(all_rows) >= 10_000:
            _flush(all_rows)
            all_rows.clear()

    written = _flush(all_rows)
    logger.info("Synthetic OHLC complete: period=%dm  rows=%d", period_minutes, written)
    return {
        "period_minutes": period_minutes,
        "rows_written":   written,
        "markets":        df["market_id"].nunique(),
    }


def _flush(rows: List[Dict]) -> int:
    if not rows:
        return 0
    try:
        with session_scope() as s:
            stmt = pg_insert(Candlestick).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["market_id", "period_interval", "period_end_ts"],
                set_={
                    "price_close":  stmt.excluded.price_close,
                    "volume":       stmt.excluded.volume,
                    "price_high":   stmt.excluded.price_high,
                    "price_low":    stmt.excluded.price_low,
                },
            )
            s.execute(stmt)
        return len(rows)
    except Exception as exc:
        logger.warning("Flush failed: %s", exc)
        return 0


def run_all_periods() -> Dict[str, Any]:
    total = 0
    for p in PERIODS:
        r = build_synthetic_ohlc(p)
        total += r.get("rows_written", 0)
    return {"total_rows": total, "periods": PERIODS}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    result = run_all_periods()
    print(f"\n=== SYNTHETIC OHLC COMPLETE: {result} ===")
