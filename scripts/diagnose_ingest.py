"""
scripts/diagnose_ingest.py
Runs after `python run.py ingest` - reports exactly what was downloaded:
  - API endpoints called and their HTTP status
  - Row counts per PostgreSQL table
  - Date ranges of data
  - Any errors encountered

Usage:
    python scripts/diagnose_ingest.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
from datetime import datetime, timezone

from sqlalchemy import text
from database.repository import session_scope, get_engine

TABLES = [
    "events",
    "markets",
    "market_snapshots",
    "candlesticks",
    "order_book_snapshots",
    "trades",
    "contract_relationships",
    "arbitrage_opportunities",
    "backtest_trades",
    "external_market_data",
    "macro_events",
    "cross_asset_spreads",
    "ingestion_log",
]

def row_count(session, table: str) -> int:
    try:
        r = session.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()
        return int(r[0]) if r else 0
    except Exception as e:
        return f"ERROR: {e}"

def table_date_range(session, table: str, ts_col: str):
    try:
        r = session.execute(
            text(f"SELECT MIN({ts_col}), MAX({ts_col}) FROM {table}")
        ).fetchone()
        if r and r[0]:
            return str(r[0])[:10], str(r[1])[:10]
    except Exception:
        pass
    return None, None

def main():
    print("=" * 65)
    print("KALSHI ARB ENGINE - DATABASE DIAGNOSTIC")
    print(f"Run at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 65)

    with session_scope() as s:
        print("\n-- TABLE ROW COUNTS ------------------------------------------")
        print(f"{'Table':<35} {'Rows':>10}")
        print("-" * 47)
        for t in TABLES:
            n = row_count(s, t)
            print(f"{t:<35} {str(n):>10}")

        print("\n-- DATE RANGES -----------------------------------------------")
        ranges = [
            ("markets",           "created_at"),
            ("candlesticks",      "period_end_ts"),
            ("trades",            "trade_ts"),
            ("market_snapshots",  "snapshot_ts"),
            ("external_market_data", "price_ts"),
            ("ingestion_log",     "run_ts"),
        ]
        for table, ts_col in ranges:
            start, end = table_date_range(s, table, ts_col)
            if start:
                print(f"  {table:<30} {start}  ->  {end}")

        print("\n-- INGESTION LOG (last 20 runs) ------------------------------")
        try:
            rows = s.execute(text("""
                SELECT job_type, target, rows_inserted, rows_skipped, status,
                       error_message, run_ts
                FROM ingestion_log
                ORDER BY run_ts DESC
                LIMIT 20
            """)).fetchall()
            if rows:
                print(f"{'Job':<25} {'Target':<20} {'Inserted':>9} {'Skipped':>8} {'Status':<10} {'Error'}")
                print("-" * 90)
                for r in rows:
                    err = (str(r[4]) or "")[:40] if r[5] else ""
                    print(f"{str(r[0]):<25} {str(r[1] or ''):<20} {str(r[2]):>9} {str(r[3]):>8} {str(r[4]):<10} {err}")
            else:
                print("  (no ingestion log entries yet)")
        except Exception as e:
            print(f"  Could not read ingestion_log: {e}")

        print("\n-- MARKET CATEGORY BREAKDOWN ---------------------------------")
        try:
            cats = s.execute(text("""
                SELECT category, COUNT(*) as n, status
                FROM markets
                GROUP BY category, status
                ORDER BY n DESC
                LIMIT 20
            """)).fetchall()
            if cats:
                print(f"{'Category':<30} {'Status':<12} {'Count':>8}")
                print("-" * 52)
                for r in cats:
                    print(f"{str(r[0] or 'NULL'):<30} {str(r[2]):<12} {str(r[1]):>8}")
        except Exception as e:
            print(f"  {e}")

        print("\n-- CANDLESTICK PERIODS IN DB ---------------------------------")
        try:
            periods = s.execute(text("""
                SELECT period_interval, COUNT(*) as n,
                       MIN(period_end_ts)::date as earliest,
                       MAX(period_end_ts)::date as latest
                FROM candlesticks
                GROUP BY period_interval
                ORDER BY period_interval
            """)).fetchall()
            if periods:
                for r in periods:
                    mins = r[0]
                    label = {1: "1-min", 60: "1-hour", 1440: "1-day"}.get(mins, f"{mins}m")
                    print(f"  {label:<10} {r[1]:>9} candles  {r[2]} -> {r[3]}")
            else:
                print("  (no candlestick data yet)")
        except Exception as e:
            print(f"  {e}")

        print("\n-- CANADIAN-RELEVANT MARKETS (score ≥ 5) --------------------")
        try:
            ca = s.execute(text("""
                SELECT ticker, title, canadian_relevance, status
                FROM markets
                WHERE canadian_relevance >= 5
                ORDER BY canadian_relevance DESC
                LIMIT 20
            """)).fetchall()
            if ca:
                for r in ca:
                    print(f"  [{r[2]:>2}] {str(r[0]):<40} {str(r[3]):<10} {str(r[1])[:50]}")
            else:
                print("  (none found yet - classifier may not have run)")
        except Exception as e:
            print(f"  {e}")

    print("\n" + "=" * 65)
    print("Diagnostic complete.")

if __name__ == "__main__":
    main()
