"""
analysis/targeted_candle_pull.py
Pull candlestick data ONLY for candidate markets (those in contract_relationships).

Strategy:
  1. Load markets that have trades AND are in contract_relationships (non-complement)
  2. For active markets  -> GET /markets/candlesticks (batch up to 20 tickers)
  3. For finalized markets -> GET /historical/markets/{ticker}/candlesticks
  4. Store in candlesticks table
  5. Respect 15 req/s rate limit

Limits:
  - Live endpoint covers ~90 days
  - Historical endpoint covers settled markets up to resolution

Usage:
    python analysis/targeted_candle_pull.py
    # or via run.py:
    python run.py candles
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from feeds.kalshi_client import KalshiClient
from database.repository import session_scope
from database.models import Candlestick

logger = logging.getLogger(__name__)

# Lookback for active-market candlesticks (days)
LOOKBACK_DAYS = 90
PERIOD_INTERVAL = 60     # 1-hour candles
LIVE_BATCH = 10          # tickers per /markets/candlesticks request (API limit ~20)
MAX_MARKETS = 2000       # cap so we don't run indefinitely


def _parse_candle(ticker: str, c: Dict[str, Any], period_interval: int) -> Optional[Dict]:
    """Parse a single candlestick dict from the API into a DB row."""
    ts_raw = c.get("end_period_ts") or c.get("period_end_ts") or c.get("end_ts")
    if ts_raw is None:
        return None
    # ts_raw may be epoch seconds (int) or ISO string
    if isinstance(ts_raw, (int, float)):
        ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
    else:
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))

    def _f(key):
        v = c.get(key)
        return float(v) if v is not None else None

    return {
        "market_id":       ticker,
        "period_interval": period_interval,
        "period_end_ts":   ts,
        "yes_bid_open":    _f("yes_bid_open"),
        "yes_bid_high":    _f("yes_bid_high"),
        "yes_bid_low":     _f("yes_bid_low"),
        "yes_bid_close":   _f("yes_bid_close"),
        "yes_ask_open":    _f("yes_ask_open"),
        "yes_ask_high":    _f("yes_ask_high"),
        "yes_ask_low":     _f("yes_ask_low"),
        "yes_ask_close":   _f("yes_ask_close"),
        "price_open":      _f("open"),
        "price_high":      _f("high"),
        "price_low":       _f("low"),
        "price_close":     _f("close") or _f("yes_price"),
        "price_mean":      _f("mean") or _f("vwap"),
        "price_previous":  _f("previous"),
        "volume":          _f("volume"),
        "open_interest":   _f("open_interest"),
    }


def _flush_candles(rows: List[Dict]) -> int:
    """Bulk-upsert a batch of candlestick rows. Returns rows written."""
    if not rows:
        return 0
    try:
        with session_scope() as s:
            stmt = pg_insert(Candlestick).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_candle_market_period_ts",
                set_={
                    "price_close":   stmt.excluded.price_close,
                    "volume":        stmt.excluded.volume,
                    "yes_bid_close": stmt.excluded.yes_bid_close,
                    "yes_ask_close": stmt.excluded.yes_ask_close,
                },
            )
            s.execute(stmt)
        return len(rows)
    except Exception as exc:
        logger.warning("Candle flush failed: %s - skipping %d rows", exc, len(rows))
        return 0


def _get_candidate_markets() -> List[Dict[str, Any]]:
    """
    Return markets that are in contract_relationships (non-complement)
    prioritised by: active markets first, then highest trade count.
    """
    with session_scope() as s:
        rows = s.execute(text("""
            SELECT m.market_id, m.ticker, m.status,
                   COUNT(t.trade_id) AS trade_count
            FROM markets m
            JOIN (
                SELECT DISTINCT market_id_1 AS mid FROM contract_relationships
                WHERE relationship_type != 'complement'
                UNION
                SELECT DISTINCT market_id_2 FROM contract_relationships
                WHERE relationship_type != 'complement'
            ) cands ON cands.mid = m.market_id
            LEFT JOIN trades t ON t.market_id = m.market_id
            GROUP BY m.market_id, m.ticker, m.status
            ORDER BY
                CASE m.status WHEN 'active' THEN 0 ELSE 1 END,
                COUNT(t.trade_id) DESC
            LIMIT :lim
        """), {"lim": MAX_MARKETS}).fetchall()
    return [dict(r._mapping) for r in rows]


def pull_active_candles(
    client: KalshiClient,
    tickers: List[str],
    start_ts: int,
    end_ts: int,
    period_interval: int = PERIOD_INTERVAL,
) -> int:
    """Pull candlesticks for active markets in batches. Returns rows written."""
    total = 0
    batch_buf: List[Dict] = []

    for i in range(0, len(tickers), LIVE_BATCH):
        batch = tickers[i : i + LIVE_BATCH]
        try:
            data = client.get_candlesticks(batch, period_interval, start_ts, end_ts)
            # Response shape: {"candlesticks": {"TICKER": [candle, ...]}}
            candles_map = data.get("candlesticks") or {}
            for ticker, clist in candles_map.items():
                for c in (clist or []):
                    row = _parse_candle(ticker, c, period_interval)
                    if row:
                        batch_buf.append(row)

            if len(batch_buf) >= 5_000:
                total += _flush_candles(batch_buf)
                batch_buf.clear()
        except Exception as exc:
            logger.warning("Live candle batch failed (tickers=%s): %s", batch[:3], exc)

    total += _flush_candles(batch_buf)
    return total


def pull_historical_candles(
    client: KalshiClient,
    ticker: str,
    start_ts: int,
    end_ts: int,
    period_interval: int = PERIOD_INTERVAL,
) -> int:
    """Pull candlesticks for a single finalized market. Returns rows written."""
    try:
        data = client.get_historical_candlesticks(ticker, period_interval, start_ts, end_ts)
        candles = data.get("candlesticks") or []
        rows = [r for c in candles for r in [_parse_candle(ticker, c, period_interval)] if r]
        return _flush_candles(rows)
    except Exception as exc:
        logger.debug("Historical candle fail %s: %s", ticker, exc)
        return 0


def run_targeted_candle_pull(
    period_interval: int = PERIOD_INTERVAL,
    lookback_days: int = LOOKBACK_DAYS,
    max_markets: int = MAX_MARKETS,
) -> Dict[str, Any]:
    """
    Main entry point: pull candlesticks for all candidate markets.
    Returns summary dict.
    """
    logger.info("Starting targeted candlestick pull (interval=%dm, lookback=%dd)",
                period_interval, lookback_days)

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=lookback_days)
    start_ts = int(start.timestamp())
    end_ts   = int(now.timestamp())

    client = KalshiClient(authenticated=False)
    markets = _get_candidate_markets()
    logger.info("Candidate markets: %d", len(markets))

    active_tickers   = [m["ticker"] for m in markets if m["status"] == "active"]
    finalized_tickers = [m["ticker"] for m in markets if m["status"] != "active"]

    logger.info("  Active: %d  |  Finalized: %d", len(active_tickers), len(finalized_tickers))

    total_written = 0

    # -- Active markets: batch pull ---------------------------------------------
    if active_tickers:
        logger.info("Pulling live candlesticks for %d active markets...", len(active_tickers))
        n = pull_active_candles(client, active_tickers, start_ts, end_ts, period_interval)
        logger.info("  Active candles written: %d", n)
        total_written += n

    # -- Finalized markets: one by one ------------------------------------------
    done = 0
    for ticker in finalized_tickers[:500]:   # cap historical at 500 (slow)
        n = pull_historical_candles(client, ticker, start_ts, end_ts, period_interval)
        total_written += n
        done += 1
        if done % 50 == 0:
            logger.info("  Historical progress: %d/%d  total_written=%d",
                        done, min(500, len(finalized_tickers)), total_written)

    logger.info("Targeted candle pull complete. Total rows: %d", total_written)
    return {
        "active_markets":    len(active_tickers),
        "finalized_markets": len(finalized_tickers),
        "total_candles":     total_written,
        "period_interval":   period_interval,
        "lookback_days":     lookback_days,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    result = run_targeted_candle_pull()
    print("\n=== CANDLE PULL COMPLETE ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
