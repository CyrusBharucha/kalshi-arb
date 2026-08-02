"""
database/performance.py
Phase 16: Database performance optimization.

Creates all performance-critical indexes and runs ANALYZE.
Safe to run multiple times (uses IF NOT EXISTS).

Usage:
    python database/performance.py
"""
from __future__ import annotations

import logging
from typing import List, Tuple

from sqlalchemy import text

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.repository import session_scope, get_engine

logger = logging.getLogger(__name__)

# (index_name, table, columns, unique, partial_condition)
INDEXES: List[Tuple] = [
    # Markets - keyset pagination by status + event_ticker
    ("idx_markets_status_event", "markets", "(status, event_ticker)", False, None),
    # Markets - look up by ticker
    ("idx_markets_ticker", "markets", "(ticker)", False, None),
    # Markets - active-only partial index
    ("idx_markets_active", "markets", "(event_ticker)", False, "WHERE status = 'active'"),

    # Trades - by market + time (OHLC resample queries)
    ("idx_trades_market_ts", "trades", "(market_id, trade_ts)", False, None),
    # Trades - by time for bulk queries
    ("idx_trades_ts", "trades", "(trade_ts)", False, None),

    # Candlesticks - market + period + time (the main lookup pattern)
    ("idx_candles_market_period_ts", "candlesticks",
     "(market_id, period_interval, period_end_ts)", False, None),

    # Contract relationships - scan by type
    ("idx_cr_type", "contract_relationships", "(relationship_type)", False, None),
    # Contract relationships - look up by market_id_1
    ("idx_cr_mid1", "contract_relationships", "(market_id_1)", False, None),
    # Contract relationships - look up by market_id_2
    ("idx_cr_mid2", "contract_relationships", "(market_id_2)", False, None),
    # Contract relationships - non-complement (most common query pattern)
    ("idx_cr_noncomplement", "contract_relationships", "(market_id_1, market_id_2)",
     False, "WHERE relationship_type != 'complement'"),

    # Arbitrage opportunities - by status + detected_at (dashboard queries)
    ("idx_arbopp_status_ts", "arbitrage_opportunities", "(status, detected_at)", False, None),
    # Arbitrage opportunities - by strategy type
    ("idx_arbopp_strategy", "arbitrage_opportunities", "(strategy_type)", False, None),

    # Market snapshots - by market + time
    ("idx_msnap_market_ts", "market_snapshots", "(market_id, snapshot_ts)", False, None),

    # Backtest trades - by run_id (metrics aggregation)
    ("idx_bt_run_id", "backtest_trades", "(run_id)", False, None),
]

TABLES_TO_ANALYZE = [
    "markets", "trades", "candlesticks", "contract_relationships",
    "arbitrage_opportunities", "market_snapshots", "backtest_trades",
]


def create_indexes(concurrent: bool = True) -> dict:
    """Create all performance indexes concurrently."""
    created = 0
    skipped = 0
    errors  = 0

    for (name, table, cols, unique, condition) in INDEXES:
        unique_kw = "UNIQUE " if unique else ""
        concur_kw = "CONCURRENTLY " if concurrent else ""
        where_kw  = f" WHERE {condition}" if condition else ""
        sql = (
            f"CREATE {unique_kw}INDEX {concur_kw}IF NOT EXISTS {name} "
            f"ON {table} {cols}{where_kw}"
        )
        try:
            # CONCURRENTLY cannot run inside a transaction — use AUTOCOMMIT connection
            engine = get_engine()
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text(sql))
            logger.info("[idx] %-45s  OK", name)
            created += 1
        except Exception as exc:
            err_str = str(exc).lower()
            if "already exists" in err_str:
                logger.debug("[idx] %-45s  already exists", name)
                skipped += 1
            else:
                logger.warning("[idx] %-45s  ERROR: %s", name, exc)
                errors += 1

    return {"created": created, "skipped": skipped, "errors": errors}


def run_analyze() -> None:
    """Update table statistics for the query planner."""
    for table in TABLES_TO_ANALYZE:
        try:
            with session_scope() as s:
                s.execute(text(f"ANALYZE {table}"))
            logger.info("[analyze] %s", table)
        except Exception as exc:
            logger.warning("[analyze] %s failed: %s", table, exc)


def run_performance_optimization() -> dict:
    """Full Phase 16 performance pass."""
    logger.info("=== PHASE 16: PERFORMANCE OPTIMIZATION ===")
    result = create_indexes()
    run_analyze()
    result["analyze_tables"] = TABLES_TO_ANALYZE
    logger.info("Done: %s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    import json
    r = run_performance_optimization()
    print(json.dumps(r, indent=2))
