"""
run.py
Main entry point for the Kalshi Arbitrage Engine.

Usage:
  python run.py init_db         # Initialise database schema
  python run.py dashboard       # Start Streamlit dashboard
  python run.py synthesis       # Synthesis WebSocket live feed (no Kalshi account needed)
  python run.py synthesis_test  # Test Synthesis connection for 60 seconds
  python run.py l2_scan         # Historical arb scan over l2_snapshots table
  python run.py l2_scan_canadian # L2 scan for Canadian-relevant markets only
  python run.py scan            # Live arbitrage scanner (continuous polling)
  python run.py scan_once       # Single scan cycle
  python run.py hist_scan       # Historical arb scan (requires candles in DB)
  python run.py candles         # Build synthetic OHLC from trades
  python run.py external        # External market data (BOC VALET + yfinance, no keys)
  python run.py backtest <tick> # Backtest a market ticker (complement arb)
  python run.py backtest_full   # Full historical backtest (all strategies)
  python run.py canadian        # Identify Canadian-relevant markets
  python run.py relationships   # Detect all market relationships
  python run.py live            # WebSocket live scanner (needs Kalshi API keys)
  python run.py integrity       # Data integrity audit
  python run.py refresh_views   # Refresh PostgreSQL materialized views

Prerequisites:
  1. PostgreSQL running and credentials in .env
  2. SYNTHESIS_SECRET_KEY in .env (for live L2 data via Synthesis WebSocket)
  3. pip install -r requirements.txt
"""

from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def cmd_ingest():
    """Historical data ingestion (deprecated — Predexon data source retired)."""
    logger.warning(
        "The 'ingest' command relied on Predexon's historical L2 API, which has been retired. "
        "Live market data is now streamed directly via the Synthesis WebSocket. "
        "Use 'python run.py synthesis' to start the live feed, or "
        "'python scripts/synthesis_l2_writer.py' to snapshot L2 data to Postgres."
    )


def cmd_loader():
    """Load .jsonl.gz dump files into Postgres (deprecated — Predexon files no longer produced)."""
    logger.warning(
        "The 'loader' command loaded JSONL files produced by the retired Predexon pull. "
        "No new JSONL files are produced. Use 'python run.py l2_scan' to scan the existing "
        "l2_snapshots table, or 'python run.py synthesis' to stream new live data."
    )


def cmd_load_trades():
    """Load trade dump files into Postgres (deprecated — see 'loader')."""
    cmd_loader()


def cmd_load_l2():
    """Load L2 dump files into Postgres (deprecated — see 'loader')."""
    cmd_loader()


def cmd_l2_scan_canadian():
    """
    Historical arb scan for Canadian-relevant markets only.
    Loads Canadian tickers from the DB (events.canadian_relevance >= 1)
    then runs the L2 historical scanner on that subset.
    """
    import json
    from database.repository import get_engine
    from sqlalchemy import text
    from engine.historical_scanner import run_historical_scan

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT m.ticker
            FROM markets m
            JOIN events e ON e.event_ticker = m.event_ticker
            WHERE e.canadian_relevance >= 1
              AND m.status IN ('open', 'settled', 'closed')
        """)).fetchall()
    tickers = [r[0] for r in rows]
    logger.info("Canadian markets: %d tickers to scan", len(tickers))

    result = run_historical_scan(tickers=tickers or None)
    logger.info("Canadian L2 scan: %s", json.dumps(result, default=str))


def cmd_l2_scan(since: str = None, until: str = None, tickers: list = None):
    """
    Historical arb scan over l2_snapshots table.
    Detects complement arb windows with full lifecycle tracking.
    Stores results in arbitrage_opportunities.
    """
    import json
    from datetime import datetime, timezone
    from engine.historical_scanner import run_historical_scan

    def _parse(s):
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        raise ValueError(f"Cannot parse date: {s!r}")

    result = run_historical_scan(
        tickers=tickers or None,
        since=_parse(since),
        until=_parse(until),
    )
    logger.info("L2 scan complete: %s", json.dumps(result, default=str))


def cmd_refresh_views():
    """
    Refresh all materialized views:
      - mv_daily_market_stats (VWAP / volume per market per day from trades)
      - mv_table_sizes (pg_stat row counts and sizes)
    """
    from database.repository import get_engine
    from sqlalchemy import text
    engine = get_engine()
    views = ["mv_daily_market_stats", "mv_table_sizes"]
    with engine.begin() as conn:
        for v in views:
            try:
                conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {v}"))
                logger.info("Refreshed: %s", v)
            except Exception as exc:
                # mv_daily_market_stats requires CONCURRENTLY index; fall back.
                try:
                    conn.execute(text(f"REFRESH MATERIALIZED VIEW {v}"))
                    logger.info("Refreshed (non-concurrent): %s", v)
                except Exception as exc2:
                    logger.warning("Could not refresh %s: %s", v, exc2)
    logger.info("Materialized views refresh complete.")


def cmd_scan_once():
    """Single scan cycle."""
    from engine.scanner import ArbitrageScanner
    scanner = ArbitrageScanner()
    summary = scanner.run_once()
    logger.info("Summary: %s", summary)


def cmd_scan():
    """Continuous scan loop."""
    import config
    from engine.scanner import ArbitrageScanner
    scanner = ArbitrageScanner()
    scanner.run_continuous(interval_s=60)


def cmd_external():
    """Pull external market data (BOC VALET + yfinance — no API keys required)."""
    from analysis.market_data import run_refresh
    run_refresh(days_back=365)


def cmd_backtest(ticker: str):
    """Backtest complement arb for a specific market ticker."""
    from backtest.engine import BacktestEngine
    import json

    engine = BacktestEngine()
    df = engine.backtest_complement_arb(ticker, period_interval=60)
    if df.empty:
        logger.warning("No backtest data for ticker: %s", ticker)
        return

    metrics = engine.compute_metrics(df)
    n_saved = engine.save_trades_to_db()

    logger.info("Backtest complete. Run ID: %s", engine.run_id)
    logger.info("Metrics:\n%s", json.dumps(metrics, indent=2, default=str))
    logger.info("Saved %d trades to DB.", n_saved)


def cmd_init_db():
    """Create all database tables."""
    from database.repository import init_db
    init_db()
    logger.info("Database initialised.")


def cmd_dashboard():
    """Launch Streamlit dashboard."""
    import subprocess
    import os
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard", "app.py")
    subprocess.run(["streamlit", "run", dashboard_path], check=True)


def cmd_relationships():
    """
    Run the relationship detector across all open markets.
    Populates contract_relationships and reports candidate count.
    """
    from analysis.relationship_runner import run_relationship_detector, get_candidate_summary
    stats, candidates = run_relationship_detector(status_filter="active")
    summary = get_candidate_summary()
    logger.info("=== RELATIONSHIP DETECTION COMPLETE ===")
    logger.info("  Total relationships : %d", summary["total_relationships"])
    for rtype, n in summary["by_type"].items():
        logger.info("    %-30s %d", rtype, n)
    logger.info("  Candidate markets   : %d", summary["candidate_markets"])
    logger.info("  Sample tickers      : %s", candidates[:5])


def cmd_candles():
    """Phase 3b: Build synthetic OHLC from raw trade data."""
    import json
    from analysis.synthetic_ohlc import run_all_periods
    result = run_all_periods()
    logger.info("Synthetic OHLC complete: %s", json.dumps(result, default=str))


def cmd_hist_scan():
    """Phase 5-7: Detect historical arb opportunities from candlestick data."""
    import json
    from analysis.historical_arb_scanner import run_historical_arb_scan, produce_research_report
    stats = run_historical_arb_scan()
    logger.info("Scan complete: %s", json.dumps(stats, default=str))
    report = produce_research_report()
    logger.info("Research report: %s", json.dumps(report, indent=2, default=str))


def cmd_backtest_full():
    """Phase 11: Full historical backtest across all detected opportunities."""
    import json
    from backtest.full_backtest import run_full_backtest
    metrics = run_full_backtest()
    logger.info("Backtest results:\n%s", json.dumps(metrics, indent=2, default=str))


def cmd_canadian():
    """Phase 8: Identify and classify Canadian-relevant Kalshi markets."""
    import json
    from analysis.canadian_markets import run_canadian_analysis
    result = run_canadian_analysis()
    logger.info("Canadian analysis:\n%s", json.dumps(result, indent=2, default=str))


def cmd_integrity():
    """Phase 15: Run data integrity audit across all tables."""
    import json
    from analysis.integrity_audit import run_integrity_audit
    report = run_integrity_audit()
    logger.info("Integrity audit:\n%s", json.dumps(report, indent=2, default=str))


def cmd_synthesis():
    """
    Synthesis live feed: connect to Kalshi orderbook via Synthesis API.
    No Kalshi account required. Runs until Ctrl-C.
    Requires SYNTHESIS_SECRET_KEY in .env.
    """
    from feeds.synthesis_live import run_live_scanner
    result = run_live_scanner(duration_seconds=None)  # runs indefinitely
    logger.info("Synthesis scanner result: %s", result)


def cmd_synthesis_test():
    """Test Synthesis connection for 60 seconds and report."""
    import json
    from feeds.synthesis_live import run_live_scanner
    result = run_live_scanner(duration_seconds=60)
    print(json.dumps(result, indent=2, default=str))


def cmd_canadian_filter():
    """Run Canadian market classifier against all DB markets."""
    import json
    from analysis.canadian_filter import classify_all_from_db
    markets = classify_all_from_db()
    print(f"Found {len(markets)} Canadian-relevant markets")
    for m in markets[:20]:
        print(f"  {m['market_id']:<45} {m['categories']}")


def cmd_live():
    """
    Start the WebSocket live arb scanner.
    Subscribes to all candidate markets from contract_relationships.
    Requires KALSHI_KEY_ID and KALSHI_PRIVKEY_PATH in .env.
    """
    import asyncio
    from engine.live_scanner import run_live
    asyncio.run(run_live())


def cmd_cross_asset_history():
    """Download 2 years of BOC VALET + yfinance daily history to parquet."""
    from analysis.historical_data import run_historical_download
    run_historical_download(years=2)


def cmd_cross_asset():
    """Start the cross-asset live feed daemon (polls BOC + yfinance every 5 min)."""
    from analysis.live_feed import _poll_loop, _get_engine, _ensure_table
    engine = _get_engine()
    if engine is not None:
        try:
            _ensure_table(engine)
        except Exception as exc:
            logger.warning("Table setup failed: %s", exc)
    _poll_loop(engine=engine)


def cmd_cross_asset_once():
    """Fetch one cross-asset snapshot, write it to cross_asset_live, and exit.

    The `cross_asset` daemon polls forever; this one-shot form is what CI and
    the "seed the table" path need.
    """
    from analysis.live_feed import _fetch_live_snapshot, _write_to_db, _get_engine, _ensure_table
    engine = _get_engine()
    if engine is not None:
        _ensure_table(engine)
    snapshot = _fetch_live_snapshot()
    if engine is not None:
        _write_to_db(engine, snapshot)
        logger.info("Wrote %d cross-asset metrics to cross_asset_live", len(snapshot))
    else:
        logger.warning("No DB engine available; snapshot not persisted")
    logger.info("Snapshot: %s", snapshot)


def cmd_cross_asset_scan():
    """Run one cross-asset model scan, writing to cross_asset_model_spreads."""
    from analysis.arb_scanner import _get_conn, _ensure_schema, scan_once, _persist_results, _print_opportunities
    conn = _get_conn()
    try:
        _ensure_schema(conn)
        records = scan_once(conn)
        _persist_results(conn, records)
        _print_opportunities(records)
    finally:
        conn.close()


def cmd_cross_asset_backfill():
    """Back-fill historical model spreads from trades + history parquet."""
    from analysis.arb_scanner import _get_conn, _ensure_schema, historical_backfill
    conn = _get_conn()
    try:
        _ensure_schema(conn)
        historical_backfill(conn)
    finally:
        conn.close()


def cmd_trade_scan():
    """Task 6: detect historical ME violations from the executed trade tape.

    Writes Class C opportunities -- trade-derived, never book-confirmed. See
    arbitrage/trade_derived_scanner.py for why these must not be mixed with
    Class A/B results.
    """
    import json
    from engine.trade_derived_scanner import run_trade_derived_scan
    stats = run_trade_derived_scan()
    logger.info("Trade-derived scan:\n%s", json.dumps(stats, indent=2, default=str))


def cmd_quality():
    """Run data quality checks across all tables."""
    from analysis.integrity_audit import run_integrity_audit
    import json
    report = run_integrity_audit()
    logger.info("Quality report:\n%s", json.dumps(report, indent=2, default=str))


# -- CLI dispatch ---------------------------------------------------------------

COMMANDS = {
    "ingest":          cmd_ingest,
    "loader":              cmd_loader,
    "load_trades":         cmd_load_trades,
    "load_l2":             cmd_load_l2,
    "l2_scan":             cmd_l2_scan,
    "l2_scan_canadian":    cmd_l2_scan_canadian,
    "refresh_views":       cmd_refresh_views,
    "candles":         cmd_candles,
    "scan":            cmd_scan,
    "scan_once":       cmd_scan_once,
    "hist_scan":       cmd_hist_scan,
    "external":        cmd_external,
    "init_db":         cmd_init_db,
    "db_init":         cmd_init_db,   # alias -- both spellings are documented
    "dashboard":       cmd_dashboard,
    "relationships":   cmd_relationships,
    "live":            cmd_live,
    "synthesis":       cmd_synthesis,
    "synthesis_test":  cmd_synthesis_test,
    "backtest_full":   cmd_backtest_full,
    "canadian":        cmd_canadian,
    "canadian_filter":      cmd_canadian_filter,
    "integrity":            cmd_integrity,
    "cross_asset_history":  cmd_cross_asset_history,
    "cross_asset":          cmd_cross_asset,
    "cross_asset_once":     cmd_cross_asset_once,
    "cross_asset_scan":     cmd_cross_asset_scan,
    "cross_asset_backfill": cmd_cross_asset_backfill,
    "quality":              cmd_quality,
    "trade_scan":           cmd_trade_scan,
}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        # "backtest" is dispatched separately below because it takes a <TICKER>
        # argument, so it is not in COMMANDS -- list it explicitly in the help.
        print("Commands:", ", ".join(sorted(list(COMMANDS.keys()) + ["backtest <TICKER>"])))
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "backtest":
        if len(sys.argv) < 3:
            print("Usage: python run.py backtest <TICKER>")
            sys.exit(1)
        cmd_backtest(sys.argv[2])
    elif cmd == "l2_scan":
        # Optional: --since DATE --until DATE --tickers TICK1 TICK2 ...
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("l2_scan")
        ap.add_argument("--since",   default=None)
        ap.add_argument("--until",   default=None)
        ap.add_argument("--tickers", nargs="*", default=None)
        args = ap.parse_args()
        cmd_l2_scan(since=args.since, until=args.until, tickers=args.tickers)
    elif cmd in COMMANDS:
        COMMANDS[cmd]()
    else:
        print(f"Unknown command: {cmd}")
        print("Available:", ", ".join(COMMANDS.keys()))
        sys.exit(1)
