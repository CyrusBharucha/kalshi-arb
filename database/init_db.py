"""
database/init_db.py
===================
Standalone database initialisation script.

Connects to PostgreSQL using DATABASE_URL from .env, then:
  1. Runs schema.sql  — creates all tables, constraints, views
  2. Runs indexes.sql — covering indexes for hot query paths
  3. Creates / refreshes materialized views from views.sql
  4. Reports row counts for every table

Usage:
    python database/init_db.py
    python run.py init_db
"""

from __future__ import annotations

import os
import sys
import time
import logging

# Allow import from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
import sqlalchemy
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)

_DB_DIR = Path(__file__).resolve().parent

# SQL files to run in order (schema must come first)
_SQL_FILES = [
    _DB_DIR / "schema.sql",
    _DB_DIR / "indexes.sql",
    _DB_DIR / "views.sql",
]

# Materialized views to refresh after creation
_MATVIEWS = [
    "mv_daily_market_stats",
    "mv_table_sizes",
    "mv_market_summary",
    "mv_daily_arb_stats",
    "mv_canadian_markets",
    "mv_l2_quality",
]

# Tables to count for the summary report
_TABLES = [
    "events",
    "markets",
    "market_snapshots",
    "candlesticks",
    "trades",
    "order_book_snapshots",
    "l2_snapshots",
    "contract_relationships",
    "arbitrage_opportunities",
    "backtest_trades",
    "external_market_data",
    "cross_asset_spreads",
    "macro_events",
    "ingestion_log",
    "data_quality_log",
    "backtest_results",
    "cross_asset_live",
    "cross_asset_model_spreads",
]


def _get_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL not set. Add it to your .env file:\n"
            "  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kalshi_arb"
        )
    return url


def _strip_sql_comments(sql: str) -> str:
    """Remove `--` line comments, leaving string literals intact.

    Splitting a SQL file on ';' only works once comments are gone: a comment
    such as `-- refresh with: REFRESH MATERIALIZED VIEW mv_foo;` otherwise
    splits mid-sentence and the trailing prose is sent to the server as a
    statement.
    """
    out: list[str] = []
    for line in sql.splitlines():
        in_single = False
        in_double = False
        cut = None
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif (
                ch == "-"
                and not in_single
                and not in_double
                and i + 1 < len(line)
                and line[i + 1] == "-"
            ):
                cut = i
                break
            i += 1
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


def _split_statements(sql: str) -> list[str]:
    """Split a comment-stripped SQL script into executable statements.

    Semicolons inside string literals and inside $$-quoted bodies do not
    terminate a statement.
    """
    stmts: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    in_dollar = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if not in_single and not in_double and sql.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        if not in_dollar:
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == ";" and not in_single and not in_double:
                stmt = "".join(buf).strip()
                if stmt:
                    stmts.append(stmt)
                buf = []
                i += 1
                continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def _run_sql_file(engine, path: Path) -> int:
    """Execute a SQL file statement by statement.

    Each statement runs in its own transaction so that one failure (for
    example an index on a column that does not exist yet) cannot poison the
    rest of the file with `InFailedSqlTransaction`.
    Returns the number of statements executed.
    """
    sql = _strip_sql_comments(path.read_text(encoding="utf-8"))
    executed = 0
    for stmt in _split_statements(sql):
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            executed += 1
        except sqlalchemy.exc.ProgrammingError as exc:
            err = str(exc.orig)
            # Ignore "already exists" and CONCURRENTLY-in-transaction errors
            if any(kw in err for kw in ("already exists", "CONCURRENTLY")):
                continue
            logger.warning("  [WARN] %s", err.split("\n")[0])
        except Exception as exc:
            logger.warning("  [WARN] Stmt skipped: %s", str(exc).split("\n")[0])
    return executed


def _refresh_matviews(engine) -> None:
    """Refresh every materialized view.

    REFRESH ... CONCURRENTLY cannot run inside a transaction block and
    requires the view to already hold data, so we use an AUTOCOMMIT
    connection and fall back to a plain refresh for first population.
    """
    ac_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    for mv in _MATVIEWS:
        with ac_engine.connect() as conn:
            try:
                conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}"))
                logger.info("  Refreshed materialized view: %s", mv)
                continue
            except Exception:
                pass
            try:
                conn.execute(text(f"REFRESH MATERIALIZED VIEW {mv}"))
                logger.info("  Refreshed (non-concurrent): %s", mv)
            except Exception as exc2:
                logger.warning("  Could not refresh %s: %s", mv, str(exc2).split("\n")[0])


def _table_counts(conn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tbl in _TABLES:
        try:
            row = conn.execute(
                text(f"SELECT COUNT(*) FROM {tbl}")  # noqa: S608
            ).fetchone()
            counts[tbl] = int(row[0]) if row else 0
        except Exception:
            counts[tbl] = -1  # table doesn't exist yet
    return counts


def init_database() -> None:
    """Full initialisation sequence — idempotent, safe to re-run."""
    url = _get_url()
    logger.info("Connecting to: %s", url.split("@")[-1])  # hide creds

    engine = create_engine(url, pool_pre_ping=True)

    t0 = time.perf_counter()

    # ── Phase 1: Schema + indexes ───────────────────────────────────────────
    logger.info("=== PHASE 1: SCHEMA & INDEXES ===")
    for sql_path in _SQL_FILES:
        if not sql_path.exists():
            logger.warning("  SQL file not found, skipping: %s", sql_path)
            continue
        logger.info("  Applying: %s", sql_path.name)
        n = _run_sql_file(engine, sql_path)
        logger.info("    -> %d statements executed", n)

    # ── Phase 2: Materialized views ─────────────────────────────────────────
    logger.info("=== PHASE 2: MATERIALIZED VIEWS ===")
    _refresh_matviews(engine)

    # ── Phase 3: Table row count report ────────────────────────────────────
    logger.info("=== PHASE 3: TABLE SUMMARY ===")
    with engine.connect() as conn:
        counts = _table_counts(conn)

    elapsed = time.perf_counter() - t0
    logger.info("")
    logger.info("  %-35s %s", "TABLE", "ROWS")
    logger.info("  " + "-" * 46)
    for tbl, n in counts.items():
        if n == -1:
            logger.info("  %-35s (not found)", tbl)
        else:
            logger.info("  %-35s %d", tbl, n)

    logger.info("")
    logger.info("Database initialisation complete in %.1fs.", elapsed)
    logger.info("Run 'python run.py ingest' to start historical data ingestion.")


if __name__ == "__main__":
    init_database()
