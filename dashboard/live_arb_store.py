"""
dashboard/live_arb_store.py
============================
Thread-safe persistence for live-detected arbitrage opportunities.

Write path (dual):
  1. SQLite  — always written; used locally and as an in-process cache.
  2. PostgreSQL — written when DATABASE_URL / DB_* env vars are set (Neon).
     Uses the existing `arbitrage_opportunities` table; auto-creates it on first
     write if it doesn't exist so no manual migration is needed.

Read path:
  query() returns PostgreSQL rows when Postgres is available, else SQLite.
  This means on Streamlit Cloud (with Neon wired up) the full history survives
  restarts; locally you still get the SQLite file.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQLite setup (always-available local store)
# ---------------------------------------------------------------------------
_STORE_PATH = Path(__file__).parent / "live_arbs.db"
_sqlite_lock = threading.Lock()
_sqlite_conn: Optional[sqlite3.Connection] = None


def _get_sqlite() -> sqlite3.Connection:
    global _sqlite_conn
    if _sqlite_conn is not None:
        return _sqlite_conn
    with _sqlite_lock:
        if _sqlite_conn is not None:
            return _sqlite_conn
        conn = sqlite3.connect(str(_STORE_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS live_arbs (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at          TEXT    NOT NULL,
                strategy_type        TEXT    NOT NULL,
                ticker               TEXT    NOT NULL,
                legs                 TEXT,
                yes_ask              REAL,
                no_ask               REAL,
                gross_edge_cents     REAL    NOT NULL,
                fees_cents           REAL    NOT NULL,
                net_edge_cents       REAL    NOT NULL,
                executable_contracts INTEGER,
                classification       TEXT    NOT NULL DEFAULT 'A',
                confidence           TEXT    NOT NULL DEFAULT 'MED',
                prices_json          TEXT,
                notes                TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_arbs_detected ON live_arbs(detected_at)"
        )
        # Add columns added after initial schema creation (idempotent)
        for _col_ddl in [
            "ALTER TABLE live_arbs ADD COLUMN confidence TEXT NOT NULL DEFAULT 'MED'",
        ]:
            try:
                conn.execute(_col_ddl)
            except Exception:
                pass  # column already exists
        conn.commit()
        _sqlite_conn = conn
        logger.info("live_arb_store: SQLite opened at %s", _STORE_PATH)
        return conn


# ---------------------------------------------------------------------------
# PostgreSQL setup (optional; requires DATABASE_URL or DB_* env vars)
# ---------------------------------------------------------------------------
_pg_engine      = None
_pg_ok          = False          # True once first successful write confirmed
_pg_lock        = threading.Lock()
_pg_init_tried  = False


# DDL executed on first connect so no manual migration needed
_PG_DDL = """
CREATE TABLE IF NOT EXISTS live_arbs_cloud (
    id                   BIGSERIAL PRIMARY KEY,
    detected_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    strategy_type        TEXT         NOT NULL,
    ticker               TEXT         NOT NULL,
    legs                 JSONB,
    yes_ask              DOUBLE PRECISION,
    no_ask               DOUBLE PRECISION,
    gross_edge_cents     DOUBLE PRECISION NOT NULL,
    fees_cents           DOUBLE PRECISION NOT NULL,
    net_edge_cents       DOUBLE PRECISION NOT NULL,
    executable_contracts INTEGER,
    classification       TEXT         NOT NULL DEFAULT 'A',
    confidence           TEXT         NOT NULL DEFAULT 'MED',
    prices_json          JSONB,
    notes                TEXT
);
ALTER TABLE live_arbs_cloud ADD COLUMN IF NOT EXISTS confidence TEXT NOT NULL DEFAULT 'MED';
CREATE INDEX IF NOT EXISTS idx_livecloud_detected ON live_arbs_cloud(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_livecloud_strategy ON live_arbs_cloud(strategy_type);
CREATE INDEX IF NOT EXISTS idx_livecloud_ticker   ON live_arbs_cloud(ticker);
"""


def _get_pg_engine():
    """Return a SQLAlchemy engine for Postgres, or None if not configured."""
    global _pg_engine, _pg_ok, _pg_init_tried
    if _pg_init_tried:
        return _pg_engine

    with _pg_lock:
        if _pg_init_tried:
            return _pg_engine

        # Support DATABASE_URL or individual DB_* vars (same as config.py)
        # Also try st.secrets directly — in case the env bridge in app.py
        # hasn't run yet (e.g. background scanner thread starts first).
        db_url = os.environ.get("DATABASE_URL", "").strip()
        if not db_url:
            try:
                import streamlit as st
                db_url = (st.secrets.get("DATABASE_URL") or "").strip()
            except Exception:
                pass
        if not db_url:
            host = os.environ.get("DB_HOST", "").strip()
            if host:
                port = os.environ.get("DB_PORT", "5432")
                name = os.environ.get("DB_NAME", "postgres")
                user = os.environ.get("DB_USER", "postgres")
                pw   = os.environ.get("DB_PASS", "")
                db_url = f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{name}"

        if not db_url:
            # Don't set _pg_init_tried — allow retry once DATABASE_URL appears
            logger.debug("live_arb_store: no DATABASE_URL yet — will retry on next call")
            return None

        # Neon uses postgres:// scheme; normalise
        db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
        if "postgresql://" in db_url and "psycopg2" not in db_url:
            db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

        try:
            from sqlalchemy import create_engine, text
            engine = create_engine(
                db_url,
                pool_size=2,
                max_overflow=3,
                pool_pre_ping=True,
                connect_args={"connect_timeout": 5},
            )
            # Test connection + auto-create table
            with engine.begin() as conn:
                conn.execute(text(_PG_DDL))
            _pg_engine = engine
            _pg_ok = True
            _pg_init_tried = True  # only lock in after success so transient failures can retry
            logger.info("live_arb_store: PostgreSQL connected — arbs will be dual-written")
        except Exception as exc:
            logger.warning("live_arb_store: PostgreSQL unavailable (%s) — will retry next call", exc)
            _pg_engine = None
            # leave _pg_init_tried = False so next call retries (handles Neon cold-start)

        return _pg_engine


# ---------------------------------------------------------------------------
# Sports market prefixes — excluded from all query results (thin/stale books)
# ---------------------------------------------------------------------------
_SPORTS_PREFIXES = (
    "KXLIGA", "KXLALIGA", "KXNBA", "KXNFL", "KXMLB", "KXNHL",
    "KXEPL", "KXSERIEA", "KXBUNDES", "KXMLS", "KXUCL",
    "KXUEFA", "KXNCAAF", "KXNCAAB", "KXWNBA", "KXPGA",
    "KXTENNIS", "KXFORMULA", "KXSOCCER", "KXCRICKET", "KXRUGBY", "KXGOLF",
    "KXUFC", "KXBOXING", "KXMMA",
)
_SPORTS_FILTER_PG = " AND ".join(f"ticker NOT LIKE '{p}%'" for p in _SPORTS_PREFIXES)
_SPORTS_FILTER_SQ = _SPORTS_FILTER_PG  # SQLite LIKE syntax is identical

# ---------------------------------------------------------------------------
# Strategy allowlist
# ---------------------------------------------------------------------------
_PERSISTABLE_STRATEGIES = {
    "yes_no_complement",
    "collectively_exhaustive",
    "mutually_exclusive",
    "threshold_order",
    "superset",
}


# ---------------------------------------------------------------------------
# persist() — dual-write entry point
# ---------------------------------------------------------------------------
def persist(opp: Dict[str, Any]) -> None:
    """
    Write a live-detected arb to SQLite (always) and PostgreSQL (when available).
    Safe to call from any thread. Never raises — errors are logged at DEBUG level.
    Persists all five strategy types: yes_no_complement, collectively_exhaustive,
    mutually_exclusive, threshold_order, and superset.
    """
    strategy = opp.get("strategy", "yes_no_complement")
    if strategy not in _PERSISTABLE_STRATEGIES:
        return

    # Guard: never persist ghost arbs (empty ticker or zero/negative edge)
    _ticker_chk = str(opp.get("ticker", "") or "").strip()
    _net_chk = float(opp.get("net_edge_cents", 0) or 0)
    if not _ticker_chk or _net_chk <= 0:
        logger.debug("live_arb_store.persist skipping ghost arb: ticker=%r net=%s", _ticker_chk, _net_chk)
        return

    ts = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(opp.get("detected_at_ts") or time.time()),
    )
    ticker   = opp.get("ticker", "")
    legs_raw = opp.get("legs")
    legs_str = json.dumps(legs_raw) if legs_raw else None   # SQLite: JSON string
    yes_ask  = opp.get("yes_ask")
    no_ask   = opp.get("no_ask")
    gross    = float(opp.get("gross_edge_cents", 0))
    fees     = float(opp.get("fees_cents", 0))
    net      = float(opp.get("net_edge_cents", 0))
    qty      = opp.get("executable_contracts")
    conf     = opp.get("confidence", "MED")   # HIGH/MED/LOW based on L2 depth
    prices   = json.dumps({"yes_ask": yes_ask, "no_ask": no_ask})

    # -- 1. SQLite (always) ---------------------------------------------------
    try:
        conn = _get_sqlite()
        with _sqlite_lock:
            conn.execute(
                """INSERT INTO live_arbs
                   (detected_at, strategy_type, ticker, legs,
                    yes_ask, no_ask, gross_edge_cents, fees_cents,
                    net_edge_cents, executable_contracts, classification, confidence, prices_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ts, strategy, ticker, legs_str,
                 yes_ask, no_ask, gross, fees, net, qty, "A", conf, prices),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("live_arb_store.persist SQLite failed: %s", exc)

    # -- 2. PostgreSQL (when DATABASE_URL is set) ------------------------------
    engine = _get_pg_engine()
    if engine is None:
        # Retrieve the SQLite rowid for the message (last insert)
        try:
            with _sqlite_lock:
                opp_id = _sqlite_conn.execute("SELECT last_insert_rowid()").fetchone()[0] if _sqlite_conn is not None else "unknown"
        except Exception:
            opp_id = "unknown"
        logger.warning(
            "persist: PG engine not ready — arb written to SQLite only (id=%s)", opp_id
        )
        return
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO live_arbs_cloud
                        (detected_at, strategy_type, ticker, legs,
                         yes_ask, no_ask, gross_edge_cents, fees_cents,
                         net_edge_cents, executable_contracts, classification, confidence, prices_json)
                    VALUES
                        (CAST(:ts AS TIMESTAMPTZ), :strategy, :ticker,
                         CAST(:legs AS JSONB),
                         :yes_ask, :no_ask, :gross, :fees, :net, :qty, 'A', :conf,
                         CAST(:prices AS JSONB))
                """),
                {
                    "ts":       ts,
                    "strategy": strategy,
                    "ticker":   ticker,
                    "legs":     legs_str or "null",
                    "yes_ask":  yes_ask,
                    "no_ask":   no_ask,
                    "gross":    gross,
                    "fees":     fees,
                    "net":      net,
                    "qty":      qty,
                    "conf":     conf,
                    "prices":   prices,
                },
            )
    except Exception as exc:
        logger.warning("live_arb_store.persist Postgres failed: %s", exc)


# ---------------------------------------------------------------------------
# query() — read from Postgres when available, else SQLite
# ---------------------------------------------------------------------------
def query(
    days_back: int = 90,
    strategy: Optional[str] = None,
    min_net_edge_cents: float = 0.0,
) -> List[Dict]:
    """Return live arb records as a list of dicts (newest first)."""
    engine = _get_pg_engine()
    if engine is not None:
        return _query_pg(engine, days_back, strategy, min_net_edge_cents)
    return _query_sqlite(days_back, strategy, min_net_edge_cents)


_MIN_NET_FLOOR = 2.0   # Hard floor matching the scanner's _MIN_NET_CENTS gate (2¢).
                       # Prevents pre-fix ghost arbs (wrong fee formula era) from surfacing
                       # even if they exist in the DB with positive but incorrect net edges.


def _query_pg(engine, days_back, strategy, min_net_edge_cents) -> List[Dict]:
    try:
        from sqlalchemy import text
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() - days_back * 86400),
        )
        # Enforce a hard floor matching the scanner gate so stale/pre-fix rows never surface.
        effective_min = max(float(min_net_edge_cents), _MIN_NET_FLOOR)
        # DISTINCT ON (ticker) keeps only the best detection per unique ticker string,
        # suppressing re-detections that occur every 5 min as Gate 5 TTL resets.
        # Python-level dedup in get_live_arb_history() handles leg-count variations
        # (e.g. "(2 legs)" vs "(3 legs)" for the same event) after query time.
        sql = f"""
            SELECT DISTINCT ON (ticker)
                   id, detected_at::text, strategy_type, ticker,
                   legs::text, yes_ask, no_ask,
                   gross_edge_cents, fees_cents, net_edge_cents,
                   executable_contracts, classification, confidence
            FROM live_arbs_cloud
            WHERE detected_at >= CAST(:cutoff AS TIMESTAMPTZ)
              AND net_edge_cents >= :min_net
              AND gross_edge_cents < 50
              AND gross_edge_cents > 0
              AND ticker != ''
              AND strategy_type != 'collectively_exhaustive'
              AND {_SPORTS_FILTER_PG}
        """
        params: dict = {"cutoff": cutoff, "min_net": effective_min}
        if strategy:
            sql += " AND strategy_type = :strategy"
            params["strategy"] = strategy
        sql += " ORDER BY ticker, net_edge_cents DESC LIMIT 5000"

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()

        cols = [
            "id", "detected_at", "strategy_type", "ticker", "legs",
            "yes_ask", "no_ask", "gross_edge_cents", "fees_cents",
            "net_edge_cents", "executable_contracts", "classification", "confidence",
        ]
        results = []
        for r in rows:
            d = dict(zip(cols, r))
            # Parse legs JSON string back to list
            if d.get("legs") and d["legs"] not in (None, "null"):
                try:
                    d["legs"] = json.loads(d["legs"])
                except Exception:
                    pass
            results.append(d)
        return results
    except Exception as exc:
        logger.warning("live_arb_store._query_pg failed: %s — falling back to SQLite", exc)
        return _query_sqlite(days_back, strategy, min_net_edge_cents)


def _query_sqlite(days_back, strategy, min_net_edge_cents) -> List[Dict]:
    try:
        conn = _get_sqlite()
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() - days_back * 86400),
        )
        effective_min = max(float(min_net_edge_cents), _MIN_NET_FLOOR)
        # Deduplicate by ticker — keep only the most recent row per unique event.
        # SQLite lacks DISTINCT ON, so use a subquery to get the max id per ticker.
        sql = (
            "SELECT id, detected_at, strategy_type, ticker, legs, yes_ask, no_ask, "
            "gross_edge_cents, fees_cents, net_edge_cents, executable_contracts, "
            "classification, confidence FROM live_arbs "
            f"WHERE detected_at >= ? AND net_edge_cents >= ? "
            f"AND gross_edge_cents < 50 AND gross_edge_cents > 0 AND ticker != '' "
            f"AND strategy_type != 'collectively_exhaustive' "
            f"AND {_SPORTS_FILTER_SQ} "
            "AND id IN (SELECT MAX(id) FROM live_arbs GROUP BY ticker)"
        )
        params: list = [cutoff, effective_min]
        if strategy:
            sql += " AND strategy_type = ?"
            params.append(strategy)
        sql += " ORDER BY detected_at DESC LIMIT 5000"
        with _sqlite_lock:
            rows = conn.execute(sql, params).fetchall()
        cols = [
            "id", "detected_at", "strategy_type", "ticker", "legs",
            "yes_ask", "no_ask", "gross_edge_cents", "fees_cents",
            "net_edge_cents", "executable_contracts", "classification", "confidence",
        ]
        results = []
        for r in rows:
            d = dict(zip(cols, r))
            if d.get("legs"):
                try:
                    d["legs"] = json.loads(d["legs"])
                except Exception:
                    pass
            results.append(d)
        return results
    except Exception as exc:
        logger.warning("live_arb_store._query_sqlite failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# count_today() — convenience helper for the status bar
# ---------------------------------------------------------------------------
def count_today() -> int:
    """Number of live arbs detected today (UTC) — checks Postgres first."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    engine = _get_pg_engine()
    if engine is not None:
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                conn.execute(text("SET statement_timeout = '5000'"))
                row = conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM live_arbs_cloud "
                        f"WHERE detected_at >= :d "
                        f"AND net_edge_cents >= :min_net "
                        f"AND gross_edge_cents < 50 AND gross_edge_cents > 0 "
                        f"AND ticker != '' AND {_SPORTS_FILTER_PG}"
                    ),
                    {"d": f"{today}T00:00:00Z", "min_net": _MIN_NET_FLOOR},
                ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            pass
    # SQLite fallback
    try:
        conn = _get_sqlite()
        with _sqlite_lock:
            row = conn.execute(
                f"SELECT COUNT(*) FROM live_arbs WHERE detected_at >= ? "
                f"AND net_edge_cents >= ? "
                f"AND gross_edge_cents < 50 AND gross_edge_cents > 0 "
                f"AND ticker != '' AND {_SPORTS_FILTER_SQ}",
                (f"{today}T00:00:00Z", _MIN_NET_FLOOR),
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
