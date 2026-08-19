"""
cross_asset/spreads_pipeline.py
================================
Computes cross-asset spreads by joining:
  - Kalshi market snapshots (market_snapshots table)
  - External market data (ext_market_daily table)
  - Cross-asset probability model (probability_engine.py)

Writes results to:
  - cross_asset_spreads (PostgreSQL) — spread time series per (market_id, asset)
  - cross_asset_model_spreads (SQLite dashboard.db) — latest signals for offline dashboard

Run:
  python cross_asset/spreads_pipeline.py            # full scan
  python cross_asset/spreads_pipeline.py --days 7   # only last 7 days of snapshots
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# Asset pairs: (kalshi_ticker_prefix, asset_name, direction, threshold)
# direction: 'above' | 'below' | 'cut' | 'hold' | 'hike'
ASSET_PAIRS = [
    # BOC rate markets — compare to CORRA
    {"prefix": "KXBOC", "asset": "CORRA",   "model": "boc_rate"},
    {"prefix": "KXCB",  "asset": "CORRA",   "model": "boc_rate"},
    # CAD/USD threshold markets
    {"prefix": "KXCAD", "asset": "CADUSD",  "model": "fx_threshold"},
    # Oil threshold markets
    {"prefix": "KXWTI", "asset": "WTI",     "model": "commodity_threshold"},
    {"prefix": "KXOIL", "asset": "WTI",     "model": "commodity_threshold"},
]


def _boc_market_probability(ticker: str, corra: float, yes_mid: float) -> dict | None:
    """
    Extract BOC rate target from ticker (e.g. KXBOC-26SEP-T2.25) and
    compute model probability.  Returns None if can't parse.
    """
    try:
        from analysis.probability_engine import boc_rate_implied_probability
        # Parse target rate from ticker: T{rate} e.g. T2.25
        parts = ticker.upper().split("-")
        target_str = None
        for p in parts:
            if p.startswith("T") and len(p) > 1:
                target_str = p[1:]
                break
        if not target_str:
            return None
        target_rate = float(target_str) / 100.0
        ois_rate    = corra / 100.0  # CORRA stored as %, convert to decimal

        # Determine direction by comparing target to current rate
        if target_rate < ois_rate - 0.001:
            direction = "cut"
        elif target_rate > ois_rate + 0.001:
            direction = "hike"
        else:
            direction = "hold"

        prob = boc_rate_implied_probability(
            current_ois_rate=ois_rate,
            meeting_date_days_ahead=30,
            target_rate=target_rate,
            direction=direction,
            n_meetings=1,
        )
        spread = yes_mid - prob
        return {
            "model_prob": prob,
            "kalshi_midpoint": yes_mid,
            "spread": spread,
            "edge_pct": round(abs(spread) * 100, 2),
            "direction": "BUY_YES" if yes_mid < prob - 0.05 else ("SELL_YES" if yes_mid > prob + 0.05 else "NEUTRAL"),
            "is_flagged": abs(spread) > 0.10,
            "model_inputs": {"corra": round(corra, 4), "target_rate": target_str, "ois_rate": round(ois_rate, 4)},
        }
    except Exception as exc:
        logger.debug("_boc_market_probability(%s): %s", ticker, exc)
        return None


def run_pipeline(days_back: int = 30) -> Dict[str, int]:
    """
    Main pipeline: scan Kalshi market snapshots, match to external data, compute spreads.
    Returns {stage: count}.
    """
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        import pandas as pd
        import json
    except ImportError as exc:
        logger.error("Import error: %s", exc)
        return {}

    engine = get_engine()
    start  = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")

    counts = {"boc_signals": 0, "spreads_written": 0, "sqlite_signals": 0}

    # --- 1. Get latest CORRA from external_market_data ---
    corra_val = None
    try:
        with engine.connect() as conn:
            r = conn.execute(text("""
                SELECT close_val FROM ext_market_daily
                WHERE asset_name = 'CORRA' AND close_val IS NOT NULL
                ORDER BY obs_date DESC LIMIT 1
            """)).fetchone()
            if r:
                corra_val = float(r[0])
    except Exception as exc:
        logger.warning("Could not load CORRA: %s", exc)

    if corra_val is None:
        # Try BOC_RATE as fallback
        try:
            with engine.connect() as conn:
                r = conn.execute(text("""
                    SELECT close_val FROM ext_market_daily
                    WHERE asset_name = 'BOC_RATE' AND close_val IS NOT NULL
                    ORDER BY obs_date DESC LIMIT 1
                """)).fetchone()
                if r:
                    corra_val = float(r[0])
        except Exception:
            pass

    if corra_val is None:
        logger.warning("No CORRA/BOC_RATE data — using default 2.75%%")
        corra_val = 2.75  # fallback percent

    logger.info("Using CORRA = %.4f%%", corra_val)

    # --- 2. Get latest CADUSD ---
    cadusd_val = None
    try:
        with engine.connect() as conn:
            r = conn.execute(text("""
                SELECT close_val FROM ext_market_daily
                WHERE asset_name = 'CADUSD' AND close_val IS NOT NULL
                ORDER BY obs_date DESC LIMIT 1
            """)).fetchone()
            if r:
                cadusd_val = float(r[0])
    except Exception:
        logger.warning("CADUSD DB query failed — using stale fallback 0.735; FX model results may be inaccurate")
        cadusd_val = 0.735

    # --- 3. Get active BOC/CAD/WTI market prices ---
    # Priority: (a) recent market_snapshots  (b) candlestick last-close  (c) l2_snapshots
    snapshot_df = pd.DataFrame()
    try:
        with engine.connect() as conn:
            # Try market_snapshots first
            r_ct = conn.execute(text("SELECT COUNT(*) FROM market_snapshots")).fetchone()
            if r_ct and r_ct[0] > 0:
                snapshot_df = pd.read_sql(text("""
                    WITH latest_snaps AS (
                        SELECT DISTINCT ON (market_id)
                            market_id, yes_bid, yes_ask, snapshot_ts
                        FROM market_snapshots
                        WHERE snapshot_ts >= NOW() - INTERVAL '24 hours'
                        ORDER BY market_id, snapshot_ts DESC
                    )
                    SELECT
                        m.ticker, m.title, m.market_id, m.close_time,
                        s.yes_bid, s.yes_ask, s.snapshot_ts
                    FROM markets m
                    JOIN latest_snaps s ON s.market_id = m.market_id
                    WHERE m.status IN ('open','active')
                      AND (
                          m.ticker LIKE 'KXBOC%' OR m.ticker LIKE 'KXCB%'
                          OR m.ticker LIKE 'KXCAD%' OR m.ticker LIKE 'KXCORR%'
                      )
                    ORDER BY m.ticker
                    LIMIT 500
                """), conn)
            if snapshot_df.empty:
                # Fallback: use l2_snapshots (last bid/ask per ticker)
                snapshot_df = pd.read_sql(text("""
                    SELECT DISTINCT ON (ls.ticker)
                        ls.ticker, m.title, m.market_id, m.close_time,
                        CAST(ls.yes_bid AS DOUBLE PRECISION) / 100.0 AS yes_bid,
                        CAST(ls.yes_ask AS DOUBLE PRECISION) / 100.0 AS yes_ask,
                        ls.snapshot_ts
                    FROM l2_snapshots ls
                    JOIN markets m ON m.ticker = ls.ticker
                    WHERE m.status IN ('open','active')
                      AND (
                          ls.ticker LIKE 'KXBOC%' OR ls.ticker LIKE 'KXCB%'
                          OR ls.ticker LIKE 'KXCAD%'
                      )
                      AND ls.snapshot_ts >= NOW() - INTERVAL '90 days'
                    ORDER BY ls.ticker, ls.snapshot_ts DESC
                    LIMIT 500
                """), conn)
                if not snapshot_df.empty:
                    logger.info("Used l2_snapshots fallback (%d rows)", len(snapshot_df))
    except Exception as exc:
        logger.warning("Could not load market prices: %s", exc)

    # Last-resort fallback: use SQLite candlestick data for signal generation
    if snapshot_df.empty:
        try:
            import sqlite3
            db_path = Path(__file__).resolve().parent.parent / "dashboard" / "dashboard.db"
            if db_path.exists():
                sq = sqlite3.connect(str(db_path))
                # Use price_close as mid; if yes_bid/ask present use those
                cs_rows = sq.execute("""
                    SELECT market_id AS ticker,
                           '' AS title,
                           '' AS market_id_col,
                           '' AS close_time,
                           COALESCE(CAST(yes_bid_close AS REAL), CAST(price_close AS REAL) * 0.98) / 100.0 AS yes_bid,
                           COALESCE(CAST(yes_ask_close AS REAL), CAST(price_close AS REAL) * 1.02) / 100.0 AS yes_ask,
                           period_end_ts AS snapshot_ts
                    FROM candlesticks
                    WHERE (market_id LIKE 'KXBOC%' OR market_id LIKE 'KXCB%')
                      AND price_close IS NOT NULL
                      AND period_end_ts = (
                          SELECT MAX(period_end_ts) FROM candlesticks AS c2
                          WHERE c2.market_id = candlesticks.market_id
                      )
                    LIMIT 200
                """).fetchall()
                sq.close()
                if cs_rows:
                    snapshot_df = pd.DataFrame(cs_rows, columns=[
                        "ticker", "title", "market_id", "close_time",
                        "yes_bid", "yes_ask", "snapshot_ts"
                    ])
                    logger.info("Used SQLite candlestick fallback (%d rows)", len(snapshot_df))
        except Exception as exc2:
            logger.debug("SQLite fallback: %s", exc2)

    # --- 4. Compute BOC model signals ---
    sqlite_signals = []

    for _, row in snapshot_df.iterrows():
        try:
            ticker  = row["ticker"]
            yes_bid = float(row.get("yes_bid") or 0)
            yes_ask = float(row.get("yes_ask") or 0)
            if yes_bid <= 0 or yes_ask <= 0:
                continue
            yes_mid = (yes_bid + yes_ask) / 2.0
            result = _boc_market_probability(ticker, corra_val, yes_mid)
            if result is None:
                continue
            counts["boc_signals"] += 1
            sqlite_signals.append({
                "ticker":          ticker,
                "title":           str(row.get("title", ""))[:120],
                "asset":           "CORRA",
                "model_prob":      round(result["model_prob"], 4),
                "kalshi_midpoint": round(yes_mid, 4),
                "edge_pct":        result["edge_pct"],
                "direction":       result["direction"],
                "is_flagged":      result["is_flagged"],
                "model_inputs":    json.dumps({
                    **result["model_inputs"],
                    "cadusd": round(cadusd_val, 4) if cadusd_val else None,
                }),
                "scanned_at":      datetime.now(timezone.utc).isoformat(),
            })

            # Write to cross_asset_spreads in PostgreSQL
            try:
                with engine.begin() as conn2:
                    conn2.execute(text("""
                        INSERT INTO cross_asset_spreads
                            (market_id, asset, spread_ts, kalshi_probability,
                             trad_probability, trad_price, trad_instrument, kalshi_liquidity)
                        VALUES (
                            :mid, 'CORRA', NOW(),
                            :kp, :tp, :tv, 'CORRA', :liq
                        )
                        ON CONFLICT DO NOTHING
                    """), {
                        "mid": row["market_id"],
                        "kp":  round(yes_mid, 4),
                        "tp":  round(result["model_prob"], 4),
                        "tv":  round(corra_val, 4),
                        "liq": round(yes_ask - yes_bid, 4),
                    })
                counts["spreads_written"] += 1
            except Exception as exc2:
                logger.debug("cross_asset_spreads insert: %s", exc2)

        except Exception as exc:
            logger.debug("Signal computation error: %s", exc)

    # --- 5. Write signals to SQLite (for offline dashboard) ---
    if sqlite_signals:
        try:
            import sqlite3
            db_path = Path(__file__).resolve().parent.parent / "dashboard" / "dashboard.db"
            conn_sq = sqlite3.connect(str(db_path))
            c = conn_sq.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS cross_asset_model_spreads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT,
                    title TEXT,
                    asset TEXT,
                    model_prob REAL,
                    kalshi_midpoint REAL,
                    edge_pct REAL,
                    direction TEXT,
                    is_flagged INTEGER,
                    model_inputs TEXT,
                    scanned_at TEXT
                )
            """)
            # Clear old signals before inserting new ones
            c.execute("DELETE FROM cross_asset_model_spreads")
            for sig in sqlite_signals:
                c.execute("""
                    INSERT INTO cross_asset_model_spreads
                        (ticker, title, asset, model_prob, kalshi_midpoint,
                         edge_pct, direction, is_flagged, model_inputs, scanned_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    sig["ticker"], sig["title"], sig["asset"],
                    sig["model_prob"], sig["kalshi_midpoint"],
                    sig["edge_pct"], sig["direction"],
                    1 if sig["is_flagged"] else 0,
                    sig["model_inputs"], sig["scanned_at"],
                ))
            conn_sq.commit()
            conn_sq.close()
            counts["sqlite_signals"] = len(sqlite_signals)
            logger.info("Wrote %d signals to SQLite cross_asset_model_spreads", len(sqlite_signals))
        except Exception as exc:
            logger.error("SQLite write error: %s", exc)

    return counts


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Days of snapshots to scan")
    args = parser.parse_args()
    counts = run_pipeline(days_back=args.days)
    print("\nSummary:")
    for k, v in counts.items():
        print(f"  {k}: {v:,}")
