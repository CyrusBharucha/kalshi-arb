"""
dashboard/data_layer.py
=======================
Clean data-access layer for the Streamlit dashboard.

All database queries are centralised here.
Streamlit caching is applied at this layer - pages must NOT cache raw SQL.
All functions return (data, error_msg) tuples so pages can gracefully degrade.
"""

from __future__ import annotations

import logging
import math
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Add repo root to sys.path once at module load so all sub-package imports
# (database.repository, cross_asset.*, analysis.*, etc.) resolve correctly
# without needing a sys.path.insert inside every function.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQLite fallback — used on Streamlit Cloud where PostgreSQL is unreachable.
# dashboard.db is committed to the repo and read as a local file.
# ---------------------------------------------------------------------------
import sqlite3 as _sqlite3
from pathlib import Path as _Path

# Resolve dashboard.db — try multiple locations in case __file__ is unusual
def _find_sqlite() -> _Path:
    candidates = [
        _Path(__file__).parent / "dashboard.db",          # normal: dashboard/
        _Path(__file__).parent.parent / "dashboard" / "dashboard.db",  # from repo root
        _Path("dashboard/dashboard.db"),                   # cwd relative
        _Path("dashboard.db"),                             # cwd
    ]
    for p in candidates:
        try:
            if p.resolve().exists():
                return p.resolve()
        except Exception:
            pass
    return _Path(__file__).parent / "dashboard.db"        # fallback (may not exist)

_SQLITE_PATH = _find_sqlite()
_USE_SQLITE: Optional[bool] = None   # None = not yet probed


def _sqlite_conn():
    """Return a sqlite3 connection to the bundled dashboard.db, or None."""
    if _SQLITE_PATH.exists():
        c = _sqlite3.connect(_SQLITE_PATH, check_same_thread=False)
        c.row_factory = _sqlite3.Row
        return c
    return None


import re as _re

# Regex that matches threshold/price-range market suffixes which are NOT CE-valid.
# These produce false-positive CE arbs because multiple legs can resolve YES
# simultaneously (e.g. buying "≥1%" AND "≥4%" both pay out when margin=10%).
# Gate 3d in ws_bridge.py blocks these in the live scanner; this filter cleans
# legacy rows that entered before Gate 3d was deployed (Sep 7 2026 04:12 AM).
_THRESHOLD_SUFFIX_RE = _re.compile(r'-(?:P|A)\d+(?:,|$|\]|")')

def _is_threshold_arb(markets_involved) -> bool:
    """Return True if the arb's legs are threshold/price-range bins (false positive)."""
    if not markets_involved:
        return False
    try:
        if isinstance(markets_involved, str):
            import json as _j
            try:
                markets_involved = _j.loads(markets_involved)
            except Exception:
                # Try as a Postgres array literal {A,B,C}
                markets_involved = markets_involved.strip("{}").split(",")
        legs = [str(m) for m in markets_involved]
        if not legs:
            return False
        suffixes = [l.rsplit("-", 1)[-1] for l in legs]
        # Block P\d+ and A\d+ (price-range bins) regardless of count
        if all(_re.match(r'^P\d+$', s) for s in suffixes):
            return True
        if all(_re.match(r'^A\d+$', s) for s in suffixes):
            return True
        # Block T\d+ (count thresholds) when 3+ legs — a 2-leg T0/T1+ can be CE
        if len(suffixes) > 2 and all(_re.match(r'^T\d+$', s) for s in suffixes):
            return True
        # Block pure numeric suffixes (e.g. 72000, 73000 price levels)
        if all(_re.match(r'^\d+(\.\d+)?$', s) for s in suffixes):
            return True
    except Exception:
        pass
    return False


def _filter_threshold_arbs(df: pd.DataFrame, col: str = "markets_involved") -> pd.DataFrame:
    """Drop rows that are threshold/price-range false-positive CE arbs."""
    if df.empty or col not in df.columns:
        return df
    mask = df[col].apply(_is_threshold_arb)
    return df[~mask].reset_index(drop=True)


def _db_available() -> bool:
    """Return True if PostgreSQL is reachable; sets _USE_SQLITE accordingly."""
    global _USE_SQLITE
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            conn.execute(text("SELECT 1"))
        _USE_SQLITE = False
        return True
    except Exception:
        _USE_SQLITE = _SQLITE_PATH.exists()
        return False


def _sqlite_df(query: str, params: tuple = ()) -> pd.DataFrame:
    """Run a query against the bundled SQLite DB and return a DataFrame."""
    c = _sqlite_conn()
    if c is None:
        return pd.DataFrame()
    try:
        df = pd.read_sql_query(query, c, params=params)
        c.close()
        return df
    except Exception as exc:
        logger.warning("SQLite query failed: %s", exc)
        c.close()
        return pd.DataFrame()


def _safe_sql(query_fn):
    """Decorator: wraps a DB query; falls back to SQLite; returns (DataFrame, error_str | None)."""
    def wrapper(*args, **kwargs) -> Tuple[pd.DataFrame, Optional[str]]:
        try:
            result = query_fn(*args, **kwargs)
            return result, None
        except Exception as exc:
            logger.warning("DB query failed: %s: %s", query_fn.__name__, exc)
            return pd.DataFrame(), str(exc)
    return wrapper


# -- System health --------------------------------------------------------------

@st.cache_data(ttl=60)
def get_system_health() -> Dict[str, Any]:
    """
    Returns a dict of current system health indicators.
    Safe to call even when DB is down.
    """
    health: Dict[str, Any] = {
        "db_connected": False,
        "db_latency_ms": None,
        "db_size_mb": None,
        "markets_total": None,
        "events_total": None,
        "trades_total": None,
        "l2_snapshots_total": None,
        "arb_opportunities_open": None,
        "latest_snapshot_ts": None,
        "relationships_total": None,
    }
    try:
        import time
        from sqlalchemy import create_engine, text
        import config as _cfg
        _test_engine = create_engine(
            _cfg.DB_URL,
            connect_args={"connect_timeout": 5},  # fail fast if no PostgreSQL
            pool_pre_ping=False,
        )
        from database.repository import get_engine
        engine = get_engine()
        t0 = time.monotonic()
        with _test_engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            conn.execute(text("SELECT 1"))
            health["db_latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
            health["db_connected"] = True

            def _count(table):
                try:
                    r = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    return r.scalar()
                except Exception:
                    return None

            health["markets_total"]           = _count("markets")
            health["events_total"]            = _count("events")
            health["trades_total"]            = _count("trades")
            health["relationships_total"]     = _count("contract_relationships")
            health["arb_opportunities_open"]  = conn.execute(
                text("SELECT COUNT(*) FROM arbitrage_opportunities WHERE status='open'")
            ).scalar()
            health["latest_snapshot_ts"] = conn.execute(
                text("SELECT MAX(snapshot_ts) FROM market_snapshots")
            ).scalar()
            # L2 snapshots written by scripts/synthesis_l2_writer.py
            try:
                health["l2_snapshots_total"] = conn.execute(
                    text("SELECT COUNT(*) FROM l2_snapshots")
                ).scalar()
            except Exception:
                health["l2_snapshots_total"] = None
            # DB size
            try:
                r = conn.execute(text(
                    "SELECT pg_size_pretty(pg_database_size(current_database()))"
                ))
                health["db_size_mb"] = r.scalar()
            except Exception:
                pass
    except Exception as exc:
        health["db_error"] = str(exc)
        # Fallback: populate from SQLite
        global _USE_SQLITE
        if _SQLITE_PATH.exists():
            health["db_mode"] = "sqlite"
            _USE_SQLITE = True  # signal other functions to skip PostgreSQL
            try:
                c = _sqlite_conn()
                def _sc(q):
                    try: return c.execute(q).fetchone()[0]
                    except: return None
                health["markets_total"]          = _sc("SELECT COUNT(*) FROM markets")
                health["relationships_total"]    = _sc("SELECT COUNT(*) FROM contract_relationships")
                # SQLite snapshot has status='historical' (not 'open') — count all arb opps
                health["arb_opportunities_open"] = _sc("SELECT COUNT(*) FROM arbitrage_opportunities")
                health["l2_snapshots_total"] = 0
                c.close()
            except Exception:
                pass
    return health


# -- Overview / coverage stats -------------------------------------------------

@st.cache_data(ttl=30)
def get_coverage_stats() -> Dict[str, Any]:
    """Market coverage summary for the Overview page."""
    stats: Dict[str, Any] = {
        "markets_monitored": 0,
        "total_markets": 0,
        "candlestick_markets": 0,
        "relationships": 0,
        "canadian_markets": 0,
        "markets_with_l2": 0,
        "markets_with_relationships": 0,
        "markets_liquid": 0,
        "markets_with_arb": 0,
        "l2_coverage_days": 0,
        "l2_earliest_ts": None,
    }
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            stats["markets_monitored"] = conn.execute(
                text("SELECT COUNT(*) FROM markets WHERE status IN ('open','active')")
            ).scalar() or 0

            stats["total_markets"] = conn.execute(
                text("SELECT COUNT(*) FROM markets")
            ).scalar() or 0

            stats["relationships"] = conn.execute(
                text("SELECT COUNT(*) FROM contract_relationships")
            ).scalar() or 0

            try:
                stats["candlestick_markets"] = conn.execute(
                    text("SELECT COUNT(DISTINCT market_id) FROM candlesticks")
                ).scalar() or 0
            except Exception:
                pass

            stats["canadian_markets"] = conn.execute(
                text("SELECT COUNT(*) FROM markets m JOIN events e ON m.event_ticker=e.event_ticker WHERE m.status IN ('open','active') AND e.canadian_relevance >= 1")
            ).scalar() or 0

            stats["markets_with_relationships"] = conn.execute(
                text("SELECT COUNT(DISTINCT market_id_1) FROM contract_relationships")
            ).scalar() or 0

            stats["markets_with_arb"] = conn.execute(
                text("SELECT COUNT(DISTINCT markets_involved[1]) FROM arbitrage_opportunities WHERE status='open'")
            ).scalar() or 0

            # Liquid = has snapshot with both bid and ask
            stats["markets_liquid"] = conn.execute(
                text("""
                    SELECT COUNT(DISTINCT s.market_id)
                    FROM market_snapshots s
                    JOIN (SELECT market_id, MAX(snapshot_ts) max_ts FROM market_snapshots GROUP BY market_id) latest
                      ON s.market_id=latest.market_id AND s.snapshot_ts=latest.max_ts
                    WHERE s.yes_bid IS NOT NULL AND s.yes_ask IS NOT NULL
                      AND s.yes_ask > s.yes_bid
                """)
            ).scalar() or 0

            # L2 coverage (snapshots written by scripts/synthesis_l2_writer.py)
            try:
                row = conn.execute(
                    text("SELECT MIN(snapped_at), MAX(snapped_at) FROM l2_snapshots")
                ).fetchone()
                if row and row[0]:
                    earliest = row[0] if hasattr(row[0], "tzinfo") else datetime.fromtimestamp(row[0], tz=timezone.utc)
                    latest   = row[1] if hasattr(row[1], "tzinfo") else datetime.fromtimestamp(row[1], tz=timezone.utc)
                    stats["l2_coverage_days"] = (latest - earliest).days
                    stats["l2_earliest_ts"]   = earliest.isoformat()
                    stats["markets_with_l2"]  = conn.execute(
                        text("SELECT COUNT(DISTINCT market_id) FROM l2_snapshots")
                    ).scalar() or 0
            except Exception:
                pass

    except Exception as exc:
        logger.warning("get_coverage_stats failed: %s", exc)
        # SQLite fallback — populate from bundled snapshot + known L2 facts
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                def _sc(q):
                    try: return c.execute(q).fetchone()[0]
                    except: return None
                stats["markets_monitored"]        = _sc("SELECT COUNT(*) FROM markets WHERE status='active'") or 0
                stats["total_markets"]            = stats["markets_monitored"]
                stats["candlestick_markets"]      = _sc("SELECT COUNT(DISTINCT market_id) FROM candlesticks") or 0
                stats["relationships"]            = _sc("SELECT COUNT(*) FROM contract_relationships") or 0
                stats["markets_with_relationships"]= _sc("SELECT COUNT(DISTINCT market_id_1) FROM contract_relationships") or 0
                # SQLite snapshot has status='historical' — 0 open arb opportunities
                stats["markets_with_arb"]         = _sc("SELECT COUNT(*) FROM arbitrage_opportunities WHERE status='open'") or 0
                # Canadian markets — match by title keywords (verified: 195 markets in snapshot)
                try:
                    ca_count = c.execute("""
                        SELECT COUNT(DISTINCT ticker) FROM markets
                        WHERE (
                            title LIKE '%Canada%' OR title LIKE '%Canadian%'
                            OR title LIKE '%BOC%' OR title LIKE '%Bank of Canada%'
                        )
                    """).fetchone()[0]
                    stats["canadian_markets"] = int(ca_count or 0)
                except Exception:
                    stats["canadian_markets"] = 0
                stats["markets_with_l2"]  = 0
                stats["markets_liquid"]   = 0
                stats["l2_coverage_days"] = 0
                stats["l2_earliest_ts"]   = None
                c.close()
            except Exception:
                pass
    return stats


# -- Open markets --------------------------------------------------------------

@st.cache_data(ttl=60, max_entries=2)
def get_open_markets(
    status_filter: str = "open",
    category_filter: Optional[str] = None,
    canadian_only: bool = False,
    min_volume: float = 0.0,
    limit: int = 5000,
) -> Tuple[pd.DataFrame, Optional[str]]:
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()

        params: Dict[str, Any] = {
            "min_vol": min_volume,
            "limit": limit,
        }
        # Kalshi has used both "open" and "active" for a tradeable market, and
        # the markets table holds both spellings depending on when the row was
        # ingested. Treat the two as synonyms so the default filter does not
        # silently return zero rows.
        if status_filter in ("open", "active"):
            filters = ["m.status IN ('open','active')"]
        else:
            params["status"] = status_filter
            filters = ["m.status = :status"]
        if category_filter:
            filters.append("m.category = :category")
            params["category"] = category_filter
        if canadian_only:
            filters.append("e.canadian_relevance >= 1")

        where = " AND ".join(filters)

        sql = text(f"""
            SELECT
                m.ticker,
                m.event_ticker,
                m.category,
                m.title,
                m.status,
                m.close_time,
                e.canadian_relevance,
                s.yes_bid,
                s.yes_ask,
                (s.yes_ask - s.yes_bid) AS spread,
                s.last_price,
                s.volume,
                s.open_interest,
                s.snapshot_ts AS last_update,
                COALESCE(r.rel_count, 0) AS relationship_count,
                CASE WHEN ao.ticker IS NOT NULL THEN TRUE ELSE FALSE END AS has_arb
            FROM markets m
            JOIN events e ON m.event_ticker = e.event_ticker
            LEFT JOIN LATERAL (
                SELECT yes_bid, yes_ask, last_price, volume, open_interest, snapshot_ts
                FROM market_snapshots
                WHERE market_id = m.market_id
                ORDER BY snapshot_ts DESC LIMIT 1
            ) s ON TRUE
            LEFT JOIN (
                SELECT market_id_1 AS mid, COUNT(*) AS rel_count
                FROM contract_relationships
                GROUP BY market_id_1
            ) r ON m.market_id = r.mid
            LEFT JOIN (
                SELECT DISTINCT markets_involved[1] AS ticker
                FROM arbitrage_opportunities WHERE status='open'
            ) ao ON m.ticker = ao.ticker
            WHERE {where}
              AND COALESCE(s.volume, 0) >= :min_vol
            ORDER BY COALESCE(s.volume, 0) DESC NULLS LAST
            LIMIT :limit
        """)

        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(sql, conn, params=params)
        return df, None
    except Exception as exc:
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                q = "SELECT ticker, event_ticker, category, title, status, close_time FROM markets WHERE 1=1"
                params_sq: list = []
                if status_filter in ("open", "active"):
                    q += " AND status IN ('open','active','active/listed')"
                elif status_filter:
                    q += " AND status = ?"; params_sq.append(status_filter)
                if category_filter:
                    # Snapshot DB has NULL categories — match by ticker prefix or title keyword
                    ticker_prefixes = _CATEGORY_TO_TICKER_PREFIXES.get(category_filter.lower(), [])
                    if ticker_prefixes:
                        ticker_clauses = " OR ".join(f"ticker LIKE '{pfx}%'" for pfx in ticker_prefixes)
                        q += f" AND ({ticker_clauses})"
                    else:
                        # Fallback: title keyword search
                        q += " AND (category = ? OR title LIKE ?)"; params_sq.extend([category_filter, f"%{category_filter}%"])
                if canadian_only:
                    # Match Canadian keywords in ticker or title (snapshot has no canadian_relevance)
                    q += (" AND (ticker LIKE 'KXBOC%' OR ticker LIKE 'KXCAD%'"
                          " OR title LIKE '%Canada%' OR title LIKE '%Canadian%'"
                          " OR title LIKE '%Bank of Canada%' OR title LIKE '%CORRA%')")
                q += f" ORDER BY rowid DESC LIMIT {int(limit)}"
                df = pd.read_sql_query(q, c, params=tuple(params_sq))
                c.close()
                # Derive a readable category from ticker prefix (all SQLite categories are NULL)
                if not df.empty and "ticker" in df.columns:
                    df["category"] = df["ticker"].apply(_derive_category_from_ticker)
                return df, None
            except Exception:
                pass
        return pd.DataFrame(), str(exc)


# -- Live arbitrage opportunities ----------------------------------------------

def _sqlite_arb_fallback(
    min_net_edge_cents: float,
    min_qty: int,
    strategy: Optional[str],
    canadian_only: bool,
) -> Tuple[pd.DataFrame, Optional[str]]:
    """Read arbitrage opportunities directly from SQLite using raw cursor (no pd.read_sql)."""
    for _sp in [_SQLITE_PATH,
                _Path(__file__).parent / "dashboard.db",
                _Path(__file__).parent.parent / "dashboard" / "dashboard.db",
                _Path("dashboard/dashboard.db")]:
        try:
            if not _sp.exists():
                continue
            c = _sqlite3.connect(str(_sp), check_same_thread=False)
            _q = ("SELECT * FROM arbitrage_opportunities "
                  "WHERE CAST(net_edge AS REAL) >= ?")
            _params: list = [min_net_edge_cents / 100.0]
            if strategy:
                _q += " AND strategy_type = ?"; _params.append(strategy)
            if min_qty > 1:
                _q += " AND CAST(max_executable_contracts AS REAL) >= ?"; _params.append(float(min_qty))
            _q += " ORDER BY CAST(net_edge AS REAL) DESC LIMIT 500"
            _cur = c.execute(_q, _params)
            _cols = [d[0] for d in _cur.description]
            _rows = _cur.fetchall()
            c.close()
            df = pd.DataFrame(_rows, columns=_cols)
            if df.empty:
                continue
            if "net_edge" in df.columns:
                df["net_edge_cents"] = pd.to_numeric(df["net_edge"], errors="coerce") * 100
            if "gross_edge" in df.columns:
                df["gross_edge_cents"] = pd.to_numeric(df["gross_edge"], errors="coerce") * 100
            if "total_fees" in df.columns:
                df["fees_cents"] = pd.to_numeric(df["total_fees"], errors="coerce") * 100
            if "max_executable_contracts" in df.columns:
                df = df.rename(columns={"max_executable_contracts": "qty"})
            if "max_net_profit" in df.columns and "net_edge_cents" in df.columns and "qty" in df.columns:
                null_mask = df["max_net_profit"].isna()
                if null_mask.any():
                    df.loc[null_mask, "max_net_profit"] = (
                        pd.to_numeric(df.loc[null_mask, "net_edge_cents"], errors="coerce") / 100
                        * pd.to_numeric(df.loc[null_mask, "qty"], errors="coerce")
                    )
            if "detected_at" in df.columns:
                _now = datetime.now(timezone.utc)
                _ts = pd.to_datetime(df["detected_at"], utc=True, errors="coerce")
                df["age_seconds"] = (_now - _ts).dt.total_seconds()
            # Drop threshold/price-range false-positive CE arbs
            if "markets_involved" in df.columns:
                df = _filter_threshold_arbs(df, col="markets_involved")
            if canadian_only and not df.empty and "markets_involved" in df.columns:
                df = _filter_canadian_opps(df)
            logger.debug("_sqlite_arb_fallback: %d rows from %s", len(df), _sp)
            return df, None
        except Exception as _e:
            logger.warning("_sqlite_arb_fallback failed (%s): %s", _sp, _e)
    return pd.DataFrame(), "SQLite fallback: no data found"


@st.cache_data(ttl=30)
def get_live_arb_opportunities(
    strategy: Optional[str] = None,
    min_net_edge_cents: float = 0.0,
    min_qty: int = 1,
    canadian_only: bool = False,
) -> Tuple[pd.DataFrame, Optional[str]]:
    # Fast path: skip PostgreSQL when SQLite is available and PG hasn't been confirmed working.
    # _USE_SQLITE=None means "not probed yet" — if SQLite exists we prefer it over a slow
    # PG connection attempt that will fail on Streamlit Cloud.
    if _USE_SQLITE is True and _SQLITE_PATH.exists():
        return _sqlite_arb_fallback(min_net_edge_cents, min_qty, strategy, canadian_only)
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()

        params: Dict[str, Any] = {
            "min_edge": min_net_edge_cents / 100.0,
            "min_qty":  min_qty,
        }
        filters = [
            "a.status = 'open'",
            "a.net_edge >= :min_edge",
            "(a.max_executable_contracts IS NULL OR a.max_executable_contracts >= :min_qty)",
            "a.strategy_type != 'collectively_exhaustive'",
        ]
        if strategy:
            filters.append("a.strategy_type = :strategy")
            params["strategy"] = strategy
        where = " AND ".join(filters)

        sql = text(f"""
            SELECT
                a.opportunity_id,
                a.detected_at,
                a.strategy_type,
                a.classification,
                a.markets_involved,
                (a.gross_edge  * 100)::numeric(8,2) AS gross_edge_cents,
                (a.total_fees  * 100)::numeric(8,2) AS fees_cents,
                (a.net_edge    * 100)::numeric(8,2) AS net_edge_cents,
                a.max_executable_contracts            AS qty,
                (a.max_net_profit)::numeric(8,4)     AS max_net_profit,
                EXTRACT(EPOCH FROM (NOW() - a.detected_at))::int AS age_seconds
            FROM arbitrage_opportunities a
            WHERE {where}
            ORDER BY a.net_edge DESC NULLS LAST
            LIMIT 500
        """)

        with engine.connect() as conn:
            # Fail fast (5 s) so the page doesn't hang when Neon is slow.
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(sql, conn, params=params)

            # Only fall back to SQLite if PG is genuinely empty (no rows at all in the
            # table), not just because the current query returned zero arbs.  An empty
            # query result when PG is connected means there are legitimately no open arbs
            # right now — do not surface stale SQLite data as if it were live.
            # NOTE: conn must be used inside the `with` block — it is closed on exit.
            if df.empty and _SQLITE_PATH.exists():
                try:
                    _pg_total = pd.read_sql(
                        text("SELECT COUNT(*) AS n FROM live_arbs_cloud"), conn
                    )
                    if int(_pg_total["n"].iloc[0]) == 0:
                        raise RuntimeError("pg_empty_fallback_to_sqlite")
                except RuntimeError:
                    raise
                except Exception:
                    pass  # PG count failed — trust the empty df, don't fall back

        # Sports filter: exclude arbs where all involved markets are sports tickers
        if not df.empty and "markets_involved" in df.columns:
            _SPORTS_PFX_SET = _SPORTS_PFX_DL if "_SPORTS_PFX_DL" in dir() else (
                "KXLIGA", "KXLALIGA", "KXNBA", "KXNFL", "KXMLB", "KXNHL",
                "KXEPL", "KXSERIEA", "KXBUNDES", "KXMLS", "KXUCL", "KXUEFA",
                "KXNCAAF", "KXNCAAB", "KXWNBA", "KXPGA", "KXTENNIS", "KXFORMULA",
                "KXSOCCER", "KXCRICKET", "KXRUGBY", "KXGOLF", "KXUFC", "KXBOXING", "KXMMA",
            )
            def _is_sports_row(mkts):
                if not mkts:
                    return False
                tickers = mkts if isinstance(mkts, list) else [str(mkts)]
                return all(any(str(t).upper().startswith(p) for p in _SPORTS_PFX_SET) for t in tickers)
            df = df[~df["markets_involved"].apply(_is_sports_row)]

        # Threshold filter: remove P\d+/A\d+/T\d+ false-positive CE arbs (pre-Gate-3d legacy)
        df = _filter_threshold_arbs(df, col="markets_involved")

        # Canadian filter: check if any involved market is Canadian
        if canadian_only and not df.empty and "markets_involved" in df.columns:
            df = _filter_canadian_opps(df)

        return df, None
    except Exception as exc:
        return _sqlite_arb_fallback(min_net_edge_cents, min_qty, strategy, canadian_only)


# Maps UI category labels (lowercase) → Kalshi ticker prefix patterns for SQLite filtering.
_CATEGORY_TO_TICKER_PREFIXES: Dict[str, list] = {
    "elections":          ["KXMVES", "KXMVEC", "KXMIDT", "KXNEXT"],
    "crypto":             ["KXBTC", "KXDOGE", "KXETH", "KXXRP", "KXBNB", "KXHYPE", "KXSOLD", "KXSOLE"],
    "sports":             ["KXMLB", "KXNFL", "KXNCAA", "KXWNBA", "KXCYCL", "KXITF", "KXNBA"],
    "finance/markets":    ["KXNASD", "KXINX", "KXSPX", "KXDOW", "KXRUSD"],
    "weather":            ["KXTEMP", "KXWEAT"],
    "bank of canada":     ["KXBOC", "KXCB", "KXCORR"],
    "canadian dollar":    ["KXCAD"],
    "commodities":        ["KXWTI", "KXOIL"],
    "politics":           ["KXPOL", "KXPRES", "KXELEC"],
    "other":              [],
}


def _derive_category_from_ticker(ticker: str) -> str:
    """Map a Kalshi ticker to a human-readable category using prefix rules."""
    if not ticker:
        return "Other"
    t = str(ticker).upper()
    _MAP = [
        # ── Politics ──────────────────────────────────────────────────────────
        ("KXMVEC",              "Elections · Multi-Victor"),
        ("KXPRES",              "Politics · Presidential"),
        ("KXPRESPERSON",        "Politics · Presidential"),
        ("KXVPRESNOMR",         "Politics · Presidential"),      # VP Presidential nominee race
        ("KXTRUMP",             "Politics · Presidential"),      # Trump approval/out/etc (broad)
        ("KX2028",              "Politics · Presidential"),      # 2028 US presidential race
        ("KXELEC",              "Politics · Elections"),
        ("CONTROLH",            "Politics · Congress Control"),  # CONTROLH-YYYY-D House control
        ("CONTROLS",            "Politics · Congress Control"),  # CONTROLS-YYYY-D Senate control
        ("SENATER",             "Politics · Senate Race"),
        ("SENATETX",            "Politics · Senate Race"),
        ("KXDSENATESEATS",      "Politics · Congress"),          # D Senate seats count
        ("KXRHOUSESEATS",       "Politics · Congress"),          # R House seats count
        ("KXHOUSEPOPVOTEMARGIN","Politics · Elections"),         # House popular vote margin
        ("HOUSE",               "Politics · Elections"),         # HOUSENY17, HOUSEPA8 district races
        ("GOVPARTY",            "Politics · Governor"),
        ("KXMAYORLA",           "Politics · Elections"),
        ("KXVOTEPRIMARY",       "Politics · Elections"),
        ("KXMID",               "Politics · Midterm"),
        ("KXNEXT",              "Politics · Leadership"),        # next PM/Secretary/official
        ("KXLEADERSOUT",        "Politics · Leadership"),        # leaders leaving office
        ("KXCABOUT",            "Politics · Leadership"),        # Cabinet members out
        ("KXIMPEACH",           "Politics · Justice"),           # Impeachment
        ("KXARREST",            "Politics · Justice"),           # Arrest markets
        ("KXALITOOUT",          "Politics · Justice"),           # Alito leaving SCOTUS
        ("KXFEDERALCHARGE",     "Politics · Justice"),           # federal criminal charges — BEFORE KXFED
        ("KXBRPRES",            "Politics · International"),     # Brazilian President
        ("KXVENEZUELALEADER",   "Politics · International"),
        ("KXQUEBECASSEMBLY",    "Politics · International"),     # Quebec provincial politics
        ("KXHORMUZNORM",        "Politics · Geopolitics"),       # Strait of Hormuz normalisation
        ("KXGREENLANDPRICE",    "Politics · Geopolitics"),       # Greenland purchase price — before KXGREENLAND
        ("KXGREENLAND",         "Politics · Geopolitics"),       # Greenland acquisition
        ("KXSTATE51",           "Politics · Geopolitics"),       # 51st US state markets
        ("KXUSAIRANAGREEMENT",  "Politics · Geopolitics"),       # US-Iran agreement
        ("KXGREENTERRITORY",    "Politics · Geopolitics"),       # Green territory
        ("KXABRAHAMSA",         "Politics · Geopolitics"),       # Abraham Accords
        ("KXDEPORTATIONS",      "Politics · Presidential"),      # Trump deportation policy
        ("KXPRIM",              "Politics · Primary"),
        ("KXBALANCE",           "Politics · Balance of Power"),

        # ── Sports ────────────────────────────────────────────────────────────
        ("KXNFLW",              "Sports · NFL"),
        ("KXNFLF",              "Sports · NFL"),
        ("KXNFLD",              "Sports · NFL"),
        ("KXNFLGAME",           "Sports · NFL"),
        ("KXNFLDROTY",          "Sports · NFL"),
        ("KXNFL",               "Sports · NFL"),                 # generic KXNFL- fallback
        ("KXBILLS",             "Sports · NFL"),                 # Buffalo Bills in-game markets
        ("KXSTARTINGQB",        "Sports · NFL"),                 # Starting QB week-1 markets
        ("KXMLB",               "Sports · MLB"),
        ("KXHEISMAN",           "Sports · NCAA Football"),       # Heisman Trophy
        ("KXNCAAMBNEXTCOACH",   "Sports · NCAA Basketball"),     # must come before KXNCAAMB
        ("KXNCAAMB",            "Sports · NCAA Basketball"),     # NCAA Men's Basketball
        ("KXNCAAF",             "Sports · NCAA Football"),
        ("KXMARMAD",            "Sports · NCAA Basketball"),     # March Madness
        ("KXWNBA",              "Sports · WNBA"),
        ("KXNBA",               "Sports · NBA"),
        ("KXNHL",               "Sports · NHL"),
        ("KXBALLONDOR",         "Sports · Soccer"),              # Ballon d'Or — before KXBALL*
        ("KXEPL",               "Sports · Soccer"),
        ("KXSOCCER",            "Sports · Soccer"),
        ("KXF1CONSTRUCTORS",    "Sports · Formula 1"),           # must come before KXF1
        ("KXF1",                "Sports · Formula 1"),
        ("KXATP",               "Sports · Tennis"),
        ("KXBOXING",            "Sports · Combat Sports"),
        ("KXUFCL",              "Sports · Combat Sports"),       # UFC lightweight/heavyweight
        ("KXBBALLTEAMUSA",      "Sports · Basketball"),          # USA Basketball Olympics team
        ("KXCOACH",             "Sports · Coaching"),

        # ── Finance ───────────────────────────────────────────────────────────
        ("KXCB",                "Finance · Bank of Canada"),
        ("KXBOC",               "Finance · Bank of Canada"),
        ("KXFED",               "Finance · Federal Reserve"),
        ("KXRATECUTCOUNT",      "Finance · Federal Reserve"),    # rate cut count
        ("FEDHIKE",             "Finance · Federal Reserve"),    # Fed hike (no KX prefix)
        ("KXCPI",               "Finance · Inflation"),
        ("KXGDP",               "Finance · Economics"),
        ("KXOIL",               "Finance · Energy"),
        ("KXWTI",               "Finance · Energy"),
        ("KXAAAGASM",           "Finance · Energy"),
        ("KXRT",                "Finance · Interest Rates"),
        ("KXRTCO",              "Finance · Interest Rates"),
        ("KXBRINFHIGH",         "Finance · Interest Rates"),     # Brazil interest rates
        ("KXFIND",              "Finance · Indicators"),
        ("KXEARN",              "Finance · Earnings"),
        ("KXIPOANTHROPIC",      "Finance · IPO"),                # must come before KXIPO
        ("KXIPOOPENAI",         "Finance · IPO"),                # must come before KXIPO
        ("KXIPO",               "Finance · IPO"),
        ("KXNASDAQ100Y",        "Finance · Indices"),
        ("KXINXMINY",           "Finance · Indices"),
        ("KXINXY",              "Finance · Indices"),
        ("KXME",                "Finance · Indices"),            # large-cap index (~69k-74k price range)
        ("KXGOLDDIRY",          "Finance · Commodities"),
        ("KXCOMPANYACTIONMERGER","Finance · M&A"),
        ("KXACQANNOUNCEANNUAL", "Finance · M&A"),
        ("KXTAKEOVERACQWB",     "Finance · M&A"),
        ("KXAAPLA",             "Finance · Stocks"),             # Apple annual metrics
        ("KXAMZNA",             "Finance · Stocks"),             # Amazon annual metrics
        ("KXTSLAA",             "Finance · Stocks"),             # Tesla annual metrics
        ("KXMTCHA",             "Finance · Stocks"),             # Match Group annual payers
        ("KXYOUA",              "Finance · Stocks"),             # YouTube annual members
        ("KXRIVNA",             "Finance · Stocks"),             # Rivian annual deliveries
        ("KXBAA",               "Finance · Stocks"),             # Boeing annual deliveries
        ("KXCVNAA",             "Finance · Stocks"),             # Carvana annual units
        ("KXRDDTA",             "Finance · Stocks"),             # Roblox DAU metrics
        ("KXTOSTA",             "Finance · Retail"),             # Total Starbucks locations
        ("KXMUSKWEAL",          "Finance · Tech"),
        ("KXUSACOMPANYSTAKE",   "Finance · Investment"),

        # ── Tech & AI ─────────────────────────────────────────────────────────
        ("KXANTHROPICLEADLEFT", "Tech · AI"),
        ("KXCODINGMODEL",       "Tech · AI"),
        ("KXH200MAX",           "Tech · AI"),                    # H200 GPU performance
        ("KXOAIHARDWARE",       "Tech · AI"),                    # OpenAI hardware
        ("KXWAYMOCITY",         "Tech · AI"),                    # Waymo city expansion
        ("KXARTIST",            "Entertainment · Music"),        # artist streaming — BEFORE KXARTI
        ("KXARTI",              "Tech · AI"),
        ("KXLLM",               "Tech · AI"),

        # ── Entertainment ─────────────────────────────────────────────────────
        ("KXBOND",              "Entertainment · Film"),         # James Bond casting
        ("KXROLEINPROD",        "Entertainment · TV"),           # Role in production markets
        ("KXROLEATGRAMMYS",     "Entertainment · Music"),
        ("KXTOPARTISTUSA",      "Entertainment · Music"),        # must come before KXTOPARTIST
        ("KXTOPARTIST",         "Entertainment · Music"),
        ("KXMUSICREPORT",       "Entertainment · Music"),
        ("KXALBUMRELEASE",      "Entertainment · Music"),
        ("KX1SONG",             "Entertainment · Music"),        # #1 song charts
        ("KXGAMEAWARDS",        "Entertainment · Gaming"),
        ("KXGTA6",              "Entertainment · Gaming"),       # GTA 6 release (with KX prefix)
        ("GTA6",                "Entertainment · Gaming"),       # GTA 6 release (no KX prefix)
        ("KXIPHONERELEASE",     "Tech · Consumer"),              # iPhone release date markets
        ("KXTIME",              "Entertainment · Media"),        # Time magazine Person of Year
        ("KXWEEKSNUM1",         "Entertainment · Music"),        # weeks at #1 on charts
        ("KXAISTREAMSERIES",    "Entertainment · Streaming"),

        # ── Science ───────────────────────────────────────────────────────────
        ("KXTEMP",              "Science · Weather"),
        ("KXFIRSTHURRICANE",    "Science · Weather"),
        ("KXSPACEXCOUNT",       "Science · Space"),
        ("KXALIENS",            "Science · Space"),
        ("KXRIEMANN",           "Science · Math"),
        ("KXSCREWWORMCOUNT",    "Science · Agriculture"),

        # ── Crypto ────────────────────────────────────────────────────────────
        ("KXBTC",               "Crypto · Bitcoin"),
        ("KXETH",               "Crypto · Ethereum"),
    ]
    for prefix, category in _MAP:
        if t.startswith(prefix):
            return category
    # Fallback: use the 2nd segment of the ticker
    parts = t.replace("KX", "", 1).split("-")
    return parts[0].title() if parts else "Other"


def _filter_canadian_opps(df: pd.DataFrame) -> pd.DataFrame:
    """Filter opportunities to those involving at least one Canadian market."""
    import json
    canadian_pattern = r'(?:canada|boc|cad|toronto|ottawa|ontario|quebec|alberta|bc\b)'
    mask = df["markets_involved"].astype(str).str.lower().str.contains(
        canadian_pattern, regex=True, na=False
    )
    return df[mask]


@st.cache_data(ttl=60)
def get_recently_closed_opps(hours: int = 1) -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Fetch arbitrage opportunities that closed (status != 'open') within the
    last `hours` hours. Used on the Live Arb page to show DISAPPEARED/SETTLED
    opportunities for context.

    Returns columns matching get_live_arb_opportunities for consistent display.
    """
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        sql = text("""
            SELECT
                a.opportunity_id,
                a.detected_at,
                a.closed_at,
                a.strategy_type,
                a.classification,
                a.status,
                a.markets_involved,
                (a.gross_edge  * 100)::numeric(8,2) AS gross_edge_cents,
                (a.total_fees  * 100)::numeric(8,2) AS fees_cents,
                (a.net_edge    * 100)::numeric(8,2) AS net_edge_cents,
                a.max_executable_contracts            AS qty,
                a.max_net_profit,
                a.duration_seconds,
                EXTRACT(EPOCH FROM (NOW() - a.detected_at))::int AS age_seconds
            FROM arbitrage_opportunities a
            WHERE a.status != 'open'
              AND a.detected_at >= :since
            ORDER BY a.closed_at DESC NULLS FIRST
            LIMIT 100
        """)
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(sql, conn, params={"since": since})
        return df, None
    except Exception as exc:
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                df = pd.read_sql_query(
                    "SELECT * FROM arbitrage_opportunities WHERE status != 'open' "
                    "AND detected_at >= ? ORDER BY detected_at DESC LIMIT 50",
                    c,
                    params=(since.isoformat(),),
                )
                c.close()
                if "net_edge" in df.columns:
                    df["net_edge_cents"] = pd.to_numeric(df["net_edge"], errors="coerce") * 100
                return df, None
            except Exception: pass
        return pd.DataFrame(), str(exc)


def get_opportunity_detail(opportunity_id: str) -> Tuple[Dict, Optional[str]]:
    """Fetch full detail for one arbitrage opportunity."""
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()

        sql = text("""
            SELECT a.*,
                   r.relationship_type,
                   r.confidence AS confidence_score,
                   r.logical_constraint AS constraint_description
            FROM arbitrage_opportunities a
            LEFT JOIN contract_relationships r
              ON a.markets_involved[1] = r.market_id_1
             AND a.markets_involved[2] = r.market_id_2
            WHERE a.opportunity_id = :oid
        """)
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            row = conn.execute(sql, {"oid": opportunity_id}).fetchone()
        if row is None:
            return {}, "Opportunity not found"
        return dict(row._mapping), None
    except Exception as exc:
        return {}, str(exc)


# -- Order book (L2) -----------------------------------------------------------

def get_live_orderbook(ticker: str) -> Tuple[Dict, Optional[str]]:
    """
    Return the current live orderbook for a ticker from the L2Cache singleton.
    Falls back to latest DB snapshot if the live cache is not running.
    NOT cached — reads from in-memory LiveState (fast) so p04 always shows fresh data.
    """
    # Try live cache first
    try:
        from dashboard.live_state import get_live_state
        state = get_live_state()
        book = state.get_book(ticker)
        if book:
            return book, None
    except Exception:
        pass

    # Fall back to latest DB snapshot
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()

        sql = text("""
            SELECT s.yes_bid, s.yes_ask, s.no_bid, s.no_ask,
                   s.snapshot_ts, m.title
            FROM market_snapshots s
            JOIN markets m ON s.market_id = m.market_id
            WHERE m.ticker = :ticker
            ORDER BY s.snapshot_ts DESC LIMIT 1
        """)
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            row = conn.execute(sql, {"ticker": ticker}).fetchone()
        if row:
            d = dict(row._mapping)
            return {
                "ticker": ticker,
                "title": d.get("title", ""),
                "yes_bids": [[d.get("yes_bid", 0), None]],
                "yes_asks": [[d.get("yes_ask", 1), None]],
                "source": "db_snapshot",
                "ts": str(d.get("snapshot_ts", "")),
                "l2_available": False,
            }, None
    except Exception as exc:
        return {}, str(exc)

    return {}, "No data available"


# -- Historical arbitrage ------------------------------------------------------

@st.cache_data(ttl=120, max_entries=2)
def get_historical_arb_summary(
    strategy: Optional[str] = None,
    classification: Optional[str] = None,
    min_net_edge_cents: float = 0.0,
    days_back: int = 90,
) -> Tuple[pd.DataFrame, Optional[str]]:
    try:
        from database.repository import get_engine, get_historical_arbitrage_summary
        from database.repository import session_scope
        engine = get_engine()

        start = datetime.now(timezone.utc) - timedelta(days=days_back)
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            # Use repository function but re-implement to avoid session complexity
            params: Dict[str, Any] = {
                "min_edge": min_net_edge_cents / 100.0,
                "start": start,
            }
            filters = ["a.net_edge >= :min_edge", "a.detected_at >= :start"]
            if strategy:
                filters.append("a.strategy_type = :strategy")
                params["strategy"] = strategy
            if classification:
                filters.append("a.classification = :classification")
                params["classification"] = classification

            sql = text(f"""
                SELECT
                    a.opportunity_id,
                    a.detected_at,
                    a.strategy_type,
                    a.classification,
                    a.markets_involved,
                    (a.gross_edge * 100)::numeric(8,2)  AS gross_edge_cents,
                    (a.total_fees * 100)::numeric(8,2)  AS fees_cents,
                    (a.net_edge   * 100)::numeric(8,2)  AS net_edge_cents,
                    a.max_executable_contracts            AS qty,
                    a.max_net_profit,
                    a.duration_seconds,
                    a.settlement_outcome,
                    a.realised_net_pnl,
                    a.status
                FROM arbitrage_opportunities a
                WHERE {' AND '.join(filters)}
                ORDER BY a.detected_at DESC
                LIMIT 5000
            """)
            df = pd.read_sql(sql, conn, params=params)
        # If PG returned nothing but SQLite has data, fall through to SQLite
        if df.empty and _SQLITE_PATH.exists():
            raise RuntimeError("pg_empty_fallback_to_sqlite")
        return df, None
    except Exception as exc:
        # SQLite fallback — try all known paths, not just _SQLITE_PATH
        _sqlite_candidates = [_SQLITE_PATH]
        for _extra in [
            _Path(__file__).parent / "dashboard.db",
            _Path(__file__).parent.parent / "dashboard" / "dashboard.db",
            _Path("dashboard/dashboard.db"),
        ]:
            if _extra not in _sqlite_candidates:
                _sqlite_candidates.append(_extra)
        for _sp in _sqlite_candidates:
            try:
                if not _sp.exists():
                    continue
                import sqlite3 as _sq3
                c = _sq3.connect(str(_sp), check_same_thread=False)
                start = datetime.now(timezone.utc) - timedelta(days=days_back)
                # Use date-only prefix to avoid timezone suffix comparison issues
                start_str = start.strftime("%Y-%m-%d")
                q = ("SELECT * FROM arbitrage_opportunities "
                     "WHERE substr(detected_at,1,10) >= ? AND CAST(net_edge AS REAL) >= ?")
                params_sq: list = [start_str, min_net_edge_cents / 100.0]
                if strategy:
                    q += " AND strategy_type = ?"
                    params_sq.append(strategy)
                if classification:
                    q += " AND classification = ?"
                    params_sq.append(classification)
                q += " ORDER BY detected_at DESC LIMIT 5000"
                _cur2 = c.execute(q, params_sq)
                _cols2 = [d[0] for d in _cur2.description]
                df = pd.DataFrame(_cur2.fetchall(), columns=_cols2)
                c.close()
                # Rename / derive columns to match expected names
                if "net_edge" in df.columns:
                    df["net_edge_cents"] = pd.to_numeric(df["net_edge"], errors="coerce") * 100
                if "gross_edge" in df.columns:
                    df["gross_edge_cents"] = pd.to_numeric(df["gross_edge"], errors="coerce") * 100
                if "total_fees" in df.columns:
                    df["fees_cents"] = pd.to_numeric(df["total_fees"], errors="coerce") * 100
                if "max_executable_contracts" in df.columns:
                    df = df.rename(columns={"max_executable_contracts": "qty"})
                if "max_net_profit" in df.columns and "net_edge_cents" in df.columns and "qty" in df.columns:
                    null_mask = df["max_net_profit"].isna()
                    if null_mask.any():
                        df.loc[null_mask, "max_net_profit"] = (
                            pd.to_numeric(df.loc[null_mask, "net_edge_cents"], errors="coerce") / 100
                            * pd.to_numeric(df.loc[null_mask, "qty"], errors="coerce")
                        )
                logger.debug("get_historical_arb_summary: SQLite fallback returned %d rows from %s", len(df), _sp)
                return df, None
            except Exception as _sq_exc:
                logger.warning("get_historical_arb_summary SQLite fallback failed (%s): %s", _sp, _sq_exc)
        return pd.DataFrame(), str(exc)


@st.cache_data(ttl=30, max_entries=4)  # 30s TTL — history page is not real-time, avoids Neon round-trip on every widget
def get_live_arb_history(
    days_back: int = 90,
    strategy: Optional[str] = None,
    min_net_edge_cents: float = 0.0,
) -> pd.DataFrame:
    """
    Return live-detected arbs as a DataFrame.
    Reads from PostgreSQL when DATABASE_URL is configured (Neon), else SQLite.
    PostgreSQL records survive restarts; SQLite records are ephemeral on Streamlit Cloud.
    Cache TTL is 5s so the page stays near-real-time with the 1s scanner.
    """
    from dashboard.live_arb_store import query as _laq
    rows = _laq(days_back=days_back, strategy=strategy,
                min_net_edge_cents=min_net_edge_cents)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Drop threshold/price-range false-positive CE arbs (pre-Gate-3d legacy rows)
    if "legs" in df.columns:
        df = _filter_threshold_arbs(df, col="legs")
    # Dedup by event key: strip "(N legs)" suffix so varying leg-count strings
    # for the same event collapse to a single row (keep highest net_edge).
    if "ticker" in df.columns and not df.empty:
        import re as _re_dedup
        df["_event_key"] = df["ticker"].str.replace(
            r'\s*\(\d+ legs\)\s*$', '', regex=True
        )
        df["_net_num"] = pd.to_numeric(df.get("net_edge_cents"), errors="coerce").fillna(0)
        df = (
            df.sort_values("_net_num", ascending=False)
              .drop_duplicates(subset=["_event_key"])
              .drop(columns=["_event_key", "_net_num"])
              .reset_index(drop=True)
        )
    df = df.rename(columns={"executable_contracts": "qty"})
    df["status"] = "open"
    df["duration_seconds"] = None
    df["settlement_outcome"] = None
    df["realised_net_pnl"] = None
    qty_series = pd.to_numeric(df["qty"] if "qty" in df.columns else pd.Series(dtype=float), errors="coerce")
    df["max_net_profit"] = (
        pd.to_numeric(df["net_edge_cents"], errors="coerce") / 100.0
        * qty_series
    ).where(qty_series.notna())
    df["markets_involved"] = df.apply(
        lambda r: r.get("legs") or [r.get("ticker", "")], axis=1
    )
    return df


@st.cache_data(ttl=120)
def get_historical_arb_stats() -> Dict[str, Any]:
    """Aggregate stats for the historical arb page KPI row."""
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()

        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            r = conn.execute(text("""
                SELECT
                    COUNT(*)                              AS total_opps,
                    COUNT(*) FILTER (WHERE classification='A') AS executable_opps,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY net_edge) * 100 AS median_edge_cents,
                    AVG(net_edge) * 100                  AS mean_edge_cents,
                    MAX(net_edge) * 100                  AS max_edge_cents,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_seconds) AS median_lifetime_s,
                    MIN(detected_at)                     AS earliest,
                    MAX(detected_at)                     AS latest
                FROM arbitrage_opportunities
                WHERE net_edge > 0
            """)).fetchone()
        if r:
            row = dict(r._mapping)
            return {
                "total_opportunities":     int(row.get("total_opps") or 0),
                "executable_opportunities": int(row.get("executable_opps") or 0),
                "median_edge_cents":       float(row.get("median_edge_cents") or 0),
                "mean_edge_cents":         float(row.get("mean_edge_cents") or 0),
                "max_edge_cents":          float(row.get("max_edge_cents") or 0),
                "median_lifetime_s":       float(row.get("median_lifetime_s") or 0),
                "date_earliest":           row.get("earliest"),
                "date_latest":             row.get("latest"),
            }
    except Exception as exc:
        logger.warning("get_historical_arb_stats: %s", exc)
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                # Probe which columns exist in this snapshot
                _cols_info = c.execute("PRAGMA table_info(arbitrage_opportunities)").fetchall()
                _col_names = {r[1] for r in _cols_info}
                _dur_expr = (
                    "CAST(duration_seconds AS REAL) AS dur_s"
                    if "duration_seconds" in _col_names
                    else "NULL AS dur_s"
                )
                raw = pd.read_sql_query(f"""
                    SELECT CAST(net_edge AS REAL)*100 AS edge_cents,
                           classification,
                           {_dur_expr},
                           detected_at
                    FROM arbitrage_opportunities
                    WHERE CAST(net_edge AS REAL) > 0
                """, c)
                c.close()
                if not raw.empty:
                    edges = raw["edge_cents"].dropna()
                    durs  = raw["dur_s"].dropna() if "dur_s" in raw.columns else pd.Series([], dtype=float)
                    return {
                        "total_opportunities":      len(raw),
                        "executable_opportunities": int((raw["classification"] == "A").sum()),
                        "median_edge_cents":        round(float(edges.quantile(0.5)), 2) if len(edges) else 0,
                        "mean_edge_cents":          round(float(edges.mean()), 2) if len(edges) else 0,
                        "max_edge_cents":           round(float(edges.max()), 2) if len(edges) else 0,
                        "median_lifetime_s":        round(float(durs.quantile(0.5)), 1) if len(durs) else 0,
                        "date_earliest":            raw["detected_at"].min(),
                        "date_latest":              raw["detected_at"].max(),
                    }
            except Exception:
                pass
    return {}


# -- Backtest ------------------------------------------------------------------

@st.cache_data(ttl=300)
def get_backtest_runs() -> Tuple[pd.DataFrame, Optional[str]]:
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()

        sql = text("""
            SELECT
                run_id,
                COUNT(*)       AS n_trades,
                MIN(entry_ts)  AS start_date,
                MAX(entry_ts)  AS end_date,
                SUM(net_pnl)   AS total_pnl,
                AVG(net_pnl)   AS avg_pnl,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*),0) AS win_rate
            FROM backtest_trades
            GROUP BY run_id
            ORDER BY start_date DESC
            LIMIT 50
        """)
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(sql, conn)
        return df, None
    except Exception as exc:
        return pd.DataFrame(), str(exc)


@st.cache_data(ttl=120)
def get_backtest_trades(run_id: str) -> Tuple[pd.DataFrame, Optional[str]]:
    try:
        from database.repository import get_engine, get_backtest_performance
        engine = get_engine()

        from sqlalchemy import text
        sql = text("""
            SELECT bt.*, m.ticker, m.event_ticker, m.title AS market_title
            FROM backtest_trades bt
            JOIN markets m ON bt.market_id = m.market_id
            WHERE bt.run_id = CAST(:run_id AS TEXT)
            ORDER BY bt.entry_ts
        """)
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(sql, conn, params={"run_id": run_id})
        return df, None
    except Exception as exc:
        return pd.DataFrame(), str(exc)


# -- Cross-asset ----------------------------------------------------------------

@st.cache_data(ttl=300)
def get_cross_asset_data(
    market_id: str,
    asset: str,
    days: int = 60,
) -> Tuple[pd.DataFrame, Optional[str]]:
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        start = datetime.now(timezone.utc) - timedelta(days=days)

        sql = text("""
            SELECT
                cs.spread_ts,
                cs.kalshi_probability,
                cs.trad_probability,
                (cs.kalshi_probability - cs.trad_probability) AS spread,
                cs.trad_price, cs.trad_instrument, cs.kalshi_liquidity
            FROM cross_asset_spreads cs
            WHERE cs.market_id = :market_id
              AND cs.asset = :asset
              AND cs.spread_ts >= :start
            ORDER BY cs.spread_ts
        """)
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(sql, conn, params={
                "market_id": market_id, "asset": asset, "start": start
            })
        return df, None
    except Exception as exc:
        return pd.DataFrame(), str(exc)


@st.cache_data(ttl=300)
def get_cross_asset_signals() -> Tuple[pd.DataFrame, Optional[str]]:
    """Read cross_asset_model_spreads from SQLite — returns real BOC/FX signals."""
    try:
        conn = _sqlite_conn()
        if conn is None:
            return pd.DataFrame(), "No SQLite connection"
        df = pd.read_sql_query(
            "SELECT * FROM cross_asset_model_spreads ORDER BY ABS(CAST(edge_pct AS REAL)) DESC",
            conn
        )
        conn.close()
        return df, None
    except Exception as exc:
        return pd.DataFrame(), str(exc)


# -- Canadian markets ----------------------------------------------------------

@st.cache_data(ttl=120)
def get_canadian_markets() -> Tuple[pd.DataFrame, Optional[str]]:
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()

        sql = text("""
            SELECT
                m.ticker,
                m.event_ticker,
                m.category,
                m.title,
                m.status,
                m.close_time,
                e.canadian_relevance,
                e.geographic_region,
                s.yes_bid,
                s.yes_ask,
                s.last_price,
                s.volume,
                s.open_interest,
                s.snapshot_ts AS last_update
            FROM markets m
            JOIN events e ON m.event_ticker = e.event_ticker
            LEFT JOIN LATERAL (
                SELECT yes_bid, yes_ask, last_price, volume, open_interest, snapshot_ts
                FROM market_snapshots
                WHERE market_id = m.market_id
                ORDER BY snapshot_ts DESC LIMIT 1
            ) s ON TRUE
            WHERE e.canadian_relevance >= 1
              AND m.status IN ('open','active')
            ORDER BY e.canadian_relevance DESC, COALESCE(s.volume, 0) DESC NULLS LAST
        """)
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(sql, conn)
        return df, None
    except Exception as exc:
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                canadian_kw = (
                    "ticker LIKE 'KXBOC%' OR ticker LIKE 'KXCAD%'"
                    " OR category LIKE '%Canada%' OR category LIKE '%canadian%'"
                    " OR title LIKE '%Canada%' OR title LIKE '%Canadian%'"
                    " OR title LIKE '%BOC%' OR title LIKE '%Bank of Canada%'"
                    " OR title LIKE '%CORRA%' OR title LIKE '%CAD%'"
                    " OR title LIKE '%Toronto%' OR title LIKE '%Ottawa%'"
                    " OR title LIKE '%Quebec%' OR title LIKE '%Alberta%'"
                )
                df = pd.read_sql_query(
                    f"SELECT ticker, event_ticker, category, title, status, close_time "
                    f"FROM markets WHERE ({canadian_kw}) "
                    f"ORDER BY rowid DESC LIMIT 200",
                    c,
                )
                c.close()
                return df, None
            except Exception: pass
        return pd.DataFrame(), str(exc)


# -- Canadian historical arb stats ----------------------------------------------

@st.cache_data(ttl=300)
def get_canadian_historical_arb_stats() -> Dict[str, Any]:
    """
    Aggregate stats for historical arb opportunities involving Canadian markets.
    Queries arbitrage_opportunities filtered to market_ids with canadian_relevance >= 1.
    """
    stats: Dict[str, Any] = {
        "total_opps": 0, "executable_opps": 0,
        "median_edge_cents": 0.0, "max_edge_cents": 0.0,
        "median_lifetime_s": 0.0, "error": None,
    }
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            r = conn.execute(text("""
                WITH canadian_mids AS (
                    SELECT DISTINCT m.market_id
                    FROM markets m
                    JOIN events e ON e.event_ticker = m.event_ticker
                    WHERE e.canadian_relevance >= 1
                )
                SELECT
                    COUNT(*)                              AS total_opps,
                    COUNT(*) FILTER (WHERE a.classification='A') AS executable_opps,
                    ROUND(
                        PERCENTILE_CONT(0.5) WITHIN GROUP
                            (ORDER BY a.net_edge)::numeric * 100, 2
                    )                                     AS median_edge_cents,
                    ROUND(MAX(a.net_edge)::numeric * 100, 2) AS max_edge_cents,
                    ROUND(
                        PERCENTILE_CONT(0.5) WITHIN GROUP
                            (ORDER BY a.duration_seconds)::numeric, 1
                    )                                     AS median_lifetime_s
                FROM arbitrage_opportunities a
                WHERE a.net_edge > 0
                  AND EXISTS (
                      SELECT 1 FROM canadian_mids cm
                      WHERE cm.market_id = ANY(a.markets_involved)
                  )
            """)).fetchone()
            if r:
                stats["total_opps"]       = int(r[0] or 0)
                stats["executable_opps"]  = int(r[1] or 0)
                stats["median_edge_cents"] = float(r[2] or 0)
                stats["max_edge_cents"]   = float(r[3] or 0)
                stats["median_lifetime_s"] = float(r[4] or 0)
    except Exception as exc:
        # SQLite fallback: filter arbitrage_opportunities by Canadian ticker prefixes
        # in the markets_involved column (stored as PostgreSQL array notation {T1,T2}).
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                _ci2 = c.execute("PRAGMA table_info(arbitrage_opportunities)").fetchall()
                _cn2 = {r[1] for r in _ci2}
                _dur2 = "CAST(duration_seconds AS REAL)" if "duration_seconds" in _cn2 else "NULL"
                rows = c.execute(f"""
                    SELECT
                        COUNT(*) AS total_opps,
                        SUM(CASE WHEN classification = 'A' THEN 1 ELSE 0 END) AS executable_opps,
                        AVG(CAST(net_edge AS REAL)) * 100 AS avg_edge_cents,
                        MAX(CAST(net_edge AS REAL)) * 100 AS max_edge_cents,
                        AVG({_dur2}) AS avg_lifetime_s
                    FROM arbitrage_opportunities
                    WHERE CAST(net_edge AS REAL) > 0
                      AND (
                        markets_involved LIKE '%KXBOC%'
                        OR markets_involved LIKE '%KXCAD%'
                        OR markets_involved LIKE '%BOC%'
                        OR markets_involved LIKE '%CORRA%'
                      )
                """).fetchone()
                c.close()
                if rows and rows[0]:
                    stats["total_opps"]        = int(rows[0] or 0)
                    stats["executable_opps"]   = int(rows[1] or 0)
                    stats["median_edge_cents"]  = round(float(rows[2] or 0), 2)
                    stats["max_edge_cents"]    = round(float(rows[3] or 0), 2)
                    stats["median_lifetime_s"]  = round(float(rows[4] or 0), 1)
                    # Clear error — we got data from SQLite
                    stats["error"] = None
                    return stats
            except Exception:
                pass
        stats["error"] = str(exc)
    return stats


# -- Relationship stats ---------------------------------------------------------

@st.cache_data(ttl=300)
def get_relationship_stats() -> Dict[str, Any]:
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            r = conn.execute(text("""
                SELECT
                    relationship_type,
                    COUNT(*) AS count,
                    AVG(confidence) AS avg_confidence
                FROM contract_relationships
                GROUP BY relationship_type
                ORDER BY count DESC
            """)).fetchall()
        return {row[0]: {"count": row[1], "avg_confidence": float(row[2] or 0)} for row in r}
    except Exception as exc:
        logger.warning("get_relationship_stats: %s", exc)
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                rows = c.execute("""
                    SELECT relationship_type, COUNT(*) AS cnt, AVG(CAST(confidence AS REAL)) AS avg_conf
                    FROM contract_relationships GROUP BY relationship_type ORDER BY cnt DESC
                """).fetchall()
                c.close()
                return {r[0]: {"count": r[1], "avg_confidence": float(r[2] or 0)} for r in rows}
            except Exception: pass
        return {}


# -- Research / empirical stats -------------------------------------------------

@st.cache_data(ttl=600)
def get_research_summary() -> Dict[str, Any]:
    """Aggregate empirical research findings."""
    stats: Dict[str, Any] = {}
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            # Relationship breakdown
            r = conn.execute(text("""
                SELECT relationship_type, COUNT(*) FROM contract_relationships
                GROUP BY relationship_type
            """)).fetchall()
            stats["relationships_by_type"] = {row[0]: row[1] for row in r}

            # Arb opportunity breakdown
            r2 = conn.execute(text("""
                SELECT strategy_type, classification, COUNT(*),
                       AVG(net_edge)*100, MAX(net_edge)*100
                FROM arbitrage_opportunities
                WHERE net_edge > 0
                GROUP BY strategy_type, classification
                ORDER BY COUNT(*) DESC
            """)).fetchall()
            stats["arb_by_strategy"] = [
                {"strategy": row[0], "class": row[1], "count": row[2],
                 "avg_edge_cents": float(row[3] or 0), "max_edge_cents": float(row[4] or 0)}
                for row in r2
            ]

            # Detected violations
            r3 = conn.execute(text("""
                SELECT COUNT(*) FROM arbitrage_opportunities
                WHERE classification = 'A' AND net_edge > 0
            """)).scalar()
            stats["class_a_violations"] = r3 or 0

            # Data quality
            r4 = conn.execute(text(
                "SELECT COUNT(*) FROM arbitrage_opportunities WHERE classification='B'"
            )).scalar()
            stats["class_b_opportunities"] = r4 or 0

    except Exception as exc:
        stats["error"] = str(exc)
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                r2 = c.execute("""
                    SELECT strategy_type, classification, COUNT(*),
                           AVG(CAST(net_edge AS REAL))*100, MAX(CAST(net_edge AS REAL))*100
                    FROM arbitrage_opportunities WHERE CAST(net_edge AS REAL) > 0
                    GROUP BY strategy_type, classification ORDER BY COUNT(*) DESC
                """).fetchall()
                stats["arb_by_strategy"] = [
                    {"strategy": r[0], "class": r[1], "count": r[2],
                     "avg_edge_cents": float(r[3] or 0), "max_edge_cents": float(r[4] or 0)}
                    for r in r2
                ]
                r3 = c.execute("SELECT COUNT(*) FROM arbitrage_opportunities WHERE classification='A' AND CAST(net_edge AS REAL) > 0").fetchone()
                stats["class_a_violations"] = int(r3[0] or 0) if r3 else 0
                r4 = c.execute("SELECT COUNT(*) FROM arbitrage_opportunities WHERE classification='B'").fetchone()
                stats["class_b_opportunities"] = int(r4[0] or 0) if r4 else 0
                # Relationship breakdown
                rr = c.execute("SELECT relationship_type, COUNT(*) FROM contract_relationships GROUP BY relationship_type").fetchall()
                stats["relationships_by_type"] = {r[0]: r[1] for r in rr}
                stats.pop("error", None)
                c.close()
            except Exception: pass
    return stats


# -- Data maintenance ----------------------------------------------------------

def purge_prefixarbs(dry_run: bool = True) -> dict:
    """
    Remove ghost arb records that were written before a bug-fix (net_edge_cents > 50).
    These inflated totals in live_arbs_cloud (PostgreSQL) and live_arbs (SQLite).

    Parameters
    ----------
    dry_run : bool
        If True (default), only count affected rows; do not delete.

    Returns
    -------
    dict with keys:
        - "would_delete"  (int) — rows that would be deleted (dry_run=True)
        - "deleted"       (int) — rows deleted (dry_run=False)
        - "dry_run"       (bool)
        - "pg_error"      (str | None) — PostgreSQL error, if any
        - "sqlite_error"  (str | None) — SQLite error, if any
    """
    result: dict = {"dry_run": dry_run, "pg_error": None, "sqlite_error": None}

    # --- PostgreSQL (Neon) ---
    pg_count = 0
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(text("SET statement_timeout = '10000'"))
            r = conn.execute(
                text("SELECT COUNT(*) FROM live_arbs_cloud WHERE net_edge_cents > 50")
            )
            pg_count = int(r.scalar() or 0)
            if not dry_run:
                conn.execute(
                    text("DELETE FROM live_arbs_cloud WHERE net_edge_cents > 50")
                )
        if dry_run:
            logger.info("purge_prefixarbs dry_run: would delete %d rows from live_arbs_cloud (PG)", pg_count)
        else:
            logger.info("purge_prefixarbs: deleted %d rows from live_arbs_cloud (PG)", pg_count)
    except Exception as exc:
        result["pg_error"] = str(exc)
        logger.warning("purge_prefixarbs PG error: %s", exc)

    # --- SQLite ---
    sqlite_count = 0
    _la_db = _Path(__file__).parent / "live_arbs.db"
    try:
        import sqlite3 as _sq
        if _la_db.exists():
            conn_sq = _sq.connect(str(_la_db), check_same_thread=False)
            r2 = conn_sq.execute(
                "SELECT COUNT(*) FROM live_arbs WHERE net_edge_cents > 50"
            ).fetchone()
            sqlite_count = int(r2[0] or 0) if r2 else 0
            if not dry_run:
                conn_sq.execute(
                    "DELETE FROM live_arbs WHERE net_edge_cents > 50"
                )
                conn_sq.commit()
            conn_sq.close()
            if dry_run:
                logger.info("purge_prefixarbs dry_run: would delete %d rows from live_arbs (SQLite)", sqlite_count)
            else:
                logger.info("purge_prefixarbs: deleted %d rows from live_arbs (SQLite)", sqlite_count)
        else:
            logger.info("purge_prefixarbs: live_arbs.db not found at %s, skipping SQLite", _la_db)
    except Exception as exc:
        result["sqlite_error"] = str(exc)
        logger.warning("purge_prefixarbs SQLite error: %s", exc)

    total = pg_count + sqlite_count
    if dry_run:
        result["would_delete"] = total
    else:
        result["deleted"] = total

    return result


# -- Data quality (system page) -------------------------------------------------

@st.cache_data(ttl=300)
def get_data_quality_stats() -> Dict[str, Any]:
    """
    Run data quality diagnostics (from database/analytics.sql).
    Returns counts of anomalies: crossed books, impossible prices,
    duplicate trades, orphan relationships, and ingestion health.
    """
    stats: Dict[str, Any] = {
        "crossed_books":        0,
        "impossible_prices":    0,
        "duplicate_trades":     0,
        "orphan_relationships": 0,
        "ingestion_rows_24h":   0,
        "ingestion_runs_24h":   0,
        "error":                None,
    }
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            # Crossed books (bid > ask in l2_snapshots top-of-book)
            try:
                r = conn.execute(text("""
                    SELECT COUNT(*) FROM l2_snapshots
                    WHERE yes_best_bid IS NOT NULL AND yes_best_ask IS NOT NULL
                      AND yes_best_bid > yes_best_ask
                """)).scalar()
                stats["crossed_books"] = int(r or 0)
            except Exception:
                pass

            # Impossible prices in trades (price outside [0, 1] or qty < 0)
            try:
                r2 = conn.execute(text("""
                    SELECT COUNT(*) FROM trades
                    WHERE price < 0 OR price > 1 OR quantity < 0
                """)).scalar()
                stats["impossible_prices"] = int(r2 or 0)
            except Exception:
                pass

            # Duplicate trades (same market/ts/price/qty/side)
            try:
                r3 = conn.execute(text("""
                    SELECT COUNT(*) FROM (
                        SELECT market_id, trade_ts, price, quantity, taker_side
                        FROM trades
                        GROUP BY market_id, trade_ts, price, quantity, taker_side
                        HAVING COUNT(*) > 1
                    ) dups
                """)).scalar()
                stats["duplicate_trades"] = int(r3 or 0)
            except Exception:
                pass

            # Orphan relationships (FK to market that no longer exists)
            try:
                r4 = conn.execute(text("""
                    SELECT COUNT(*) FROM contract_relationships cr
                    LEFT JOIN markets m1 ON m1.market_id = cr.market_id_1
                    LEFT JOIN markets m2 ON m2.market_id = cr.market_id_2
                    WHERE m1.market_id IS NULL OR m2.market_id IS NULL
                """)).scalar()
                stats["orphan_relationships"] = int(r4 or 0)
            except Exception:
                pass

            # Ingestion health: rows inserted in last 24h
            try:
                r5 = conn.execute(text("""
                    SELECT COUNT(*) AS runs, SUM(rows_inserted) AS rows
                    FROM ingestion_log
                    WHERE run_ts >= NOW() - INTERVAL '24 hours'
                """)).fetchone()
                if r5:
                    stats["ingestion_runs_24h"] = int(r5[0] or 0)
                    stats["ingestion_rows_24h"] = int(r5[1] or 0)
            except Exception:
                pass

    except Exception as exc:
        stats["error"] = str(exc)
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                # Use data_quality_log (check_name / severity / count columns)
                # Get latest count per check_name from the most recent run
                try:
                    r_dq = c.execute("""
                        SELECT check_name, CAST(count AS INTEGER)
                        FROM data_quality_log
                        WHERE id IN (
                            SELECT MAX(CAST(id AS INTEGER)) FROM data_quality_log
                            GROUP BY check_name
                        )
                    """).fetchall()
                    for check, cnt in r_dq:
                        ch = check.lower()
                        if "crossed" in ch: stats["crossed_books"] = int(cnt or 0)
                        elif "price_bounds" in ch or "impossible" in ch: stats["impossible_prices"] = int(cnt or 0)
                        elif "duplicate" in ch: stats["duplicate_trades"] = int(cnt or 0)
                        elif "orphan" in ch: stats["orphan_relationships"] = int(cnt or 0)
                except Exception: pass
                try:
                    # SQLite snapshot is historical — 24h filter returns 0.
                    # Return all-time totals instead; p10 will label them "SNAPSHOT TOTAL".
                    r_il = c.execute("""
                        SELECT COUNT(*) AS runs, SUM(CAST(rows_inserted AS INTEGER)) AS rows
                        FROM ingestion_log
                    """).fetchone()
                    if r_il:
                        stats["ingestion_runs_24h"] = int(r_il[0] or 0)
                        stats["ingestion_rows_24h"] = int(r_il[1] or 0)
                        stats["ingestion_sqlite_alltime"] = True  # flag for display layer
                except Exception: pass
                stats.pop("error", None)
                c.close()
            except Exception: pass
    return stats


@st.cache_data(ttl=300)
def get_ingestion_log(limit: int = 100) -> Tuple[pd.DataFrame, Optional[str]]:
    """Last N ingestion log rows for the system monitoring page."""
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(text("""
                SELECT job_type, target, rows_inserted, rows_skipped,
                       status, error_message, run_ts
                FROM ingestion_log
                ORDER BY run_ts DESC
                LIMIT :lim
            """), conn, params={"lim": limit})
        return df, None
    except Exception as exc:
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                df = pd.read_sql_query(
                    "SELECT job_type, target, rows_inserted, rows_skipped, status, error_message, run_ts "
                    f"FROM ingestion_log ORDER BY run_ts DESC LIMIT {int(limit)}",
                    c,
                )
                c.close()
                return df, None
            except Exception: pass
        return pd.DataFrame(), str(exc)


# -- Candlestick analytics (p09 Research) -------------------------------------

@st.cache_data(ttl=600, max_entries=2)
def get_top_markets_by_volume(limit: int = 25) -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Return top markets by total volume from candlesticks table.
    Works in SQLite mode (candlesticks table is in dashboard.db).
    Joins to markets for title + category.
    """
    sql = """
        SELECT c.market_id AS ticker,
               m.title,
               m.category,
               m.close_time,
               SUM(c.volume)              AS total_volume,
               COUNT(*)                   AS candle_count,
               SUBSTR(MIN(c.period_end_ts),1,10) AS first_candle,
               SUBSTR(MAX(c.period_end_ts),1,10) AS last_candle,
               AVG(c.price_mean)          AS avg_price,
               AVG(c.yes_ask_close - c.yes_bid_close) AS avg_spread
        FROM candlesticks c
        LEFT JOIN markets m ON m.ticker = c.market_id
        GROUP BY c.market_id, m.title, m.category, m.close_time
        ORDER BY total_volume DESC
        LIMIT ?
    """
    def _infer_category(ticker_str: str) -> str:
        """Infer a display category from the ticker prefix (delegates to _derive_category_from_ticker)."""
        if not ticker_str:
            return ""
        cat = _derive_category_from_ticker(ticker_str)
        # All real mapped categories contain " · " (e.g., "Sports · NFL", "Finance · Federal Reserve").
        # The fallback is a title-cased ticker segment (e.g., "Testmatch") — suppress it so the
        # table column stays blank rather than showing raw meaningless segments.
        return cat if " · " in cat else ""

    try:
        # Try SQLite first (primary on Streamlit Cloud)
        conn = _sqlite_conn()
        if conn:
            df = pd.read_sql_query(sql, conn, params=(limit,))
            conn.close()
            if not df.empty:
                # Fill missing category from heuristic when JOIN returned nulls
                if "category" in df.columns:
                    null_mask = df["category"].isna() | (df["category"] == "")
                    if null_mask.any():
                        df.loc[null_mask, "category"] = df.loc[null_mask, "ticker"].apply(_infer_category)
                return df, None
        # Try PostgreSQL via SQLAlchemy engine
        try:
            from database.repository import get_engine as _ge_vol
            _eng_vol = _ge_vol()
            sql_pg = sql.replace(" ?", " %s").replace("LIMIT ?", "LIMIT %s")
            df = pd.read_sql_query(sql_pg, _eng_vol, params=(limit,))
            if not df.empty:
                return df, None
        except Exception:
            pass
        return pd.DataFrame(), "No candlestick data found (SQLite or PostgreSQL)"
    except Exception as exc:
        return pd.DataFrame(), str(exc)


@st.cache_data(ttl=300)
def get_market_price_history(
    ticker: str,
    interval: int = 60,
    limit: int = 500,
) -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Return OHLC price history for a specific market ticker.
    interval: 15=15min, 60=1hour, 1440=1day
    """
    sql = """
        SELECT c.period_end_ts,
               c.price_open, c.price_high, c.price_low, c.price_close,
               c.yes_bid_close, c.yes_ask_close,
               c.volume, c.open_interest
        FROM candlesticks c
        WHERE c.market_id = ?
          AND c.period_interval = ?
          AND c.price_close IS NOT NULL
        ORDER BY c.period_end_ts DESC
        LIMIT ?
    """
    try:
        conn = _sqlite_conn()
        if conn:
            df = pd.read_sql_query(sql, conn, params=(ticker, interval, limit))
            conn.close()
            if not df.empty:
                df = df.sort_values("period_end_ts")
                return df, None
        # Try PostgreSQL via SQLAlchemy engine
        try:
            from database.repository import get_engine as _ge_hist
            _eng_hist = _ge_hist()
            sql_pg = sql.replace("WHERE c.market_id = ?", "WHERE c.market_id = %s").replace("AND c.period_interval = ?", "AND c.period_interval = %s").replace("LIMIT ?", "LIMIT %s")
            df = pd.read_sql_query(sql_pg, _eng_hist, params=(ticker, interval, limit))
            if not df.empty:
                df = df.sort_values("period_end_ts")
                return df, None
        except Exception:
            pass
        return pd.DataFrame(), "No price history found"
    except Exception as exc:
        return pd.DataFrame(), str(exc)




# -- Table sizes (system page) --------------------------------------------------

@st.cache_data(ttl=30, max_entries=1)
def get_live_market_summary() -> Dict[str, Any]:
    """
    Build a market summary from the live WebSocket state.
    Works without PostgreSQL — used as fallback on Streamlit Cloud.
    Cached for 30s to avoid re-scanning 8k markets on every Streamlit rerun.
    """
    try:
        from dashboard.live_state import get_live_state
        state = get_live_state()
        ws_stats = state.get_stats()
        all_quotes = state.snapshot_all()

        if not all_quotes:
            return {"source": "live", "markets": 0}

        quotes = list(all_quotes.values())
        spreads = [q.spread for q in quotes if q.spread < 1.0]
        mids = [q.mid for q in quotes if 0.01 < q.mid < 0.99]
        tight = [q for q in quotes if q.spread <= 0.05]
        with_depth = [q for q in quotes if q.l2_available]

        def _kalshi_fee(ask: float) -> float:
            """Kalshi taker fee per contract side: min($0.035, ceil(0.07*P*(1-P)*100)/100)"""
            return min(0.035, math.ceil(0.07 * ask * (1.0 - ask) * 100) / 100)

        _SPORTS_PFX_DL = (
            "KXLIGA", "KXLALIGA", "KXNBA", "KXNFL", "KXMLB", "KXNHL", "KXEPL",
            "KXSERIEA", "KXBUNDES", "KXMLS", "KXUCL", "KXUEFA",
            "KXNCAAF", "KXNCAAB", "KXWNBA", "KXPGA", "KXTENNIS", "KXFORMULA",
            "KXSOCCER", "KXCRICKET", "KXRUGBY", "KXGOLF", "KXUFC", "KXBOXING", "KXMMA",
        )
        def _is_sports_dl(ticker: str) -> bool:
            u = ticker.upper()
            return any(u.startswith(p) for p in _SPORTS_PFX_DL)

        # -- Strategy 1: Complement arb (single-market YES+NO)
        # Gates applied here to match ws_bridge.py scanner quality:
        #   G1: both sides must be fresh (not stale — age <= 300s via LiveState.is_stale)
        #   G2: both sides must be plausible (>= 3c) — settled markets leave near-zero phantom asks
        #   G3: net edge must be <= 30c — anything above is almost certainly stale/settled noise
        # Build event-group lookup for threshold/bracket detection
        import re as _re_dl
        from collections import defaultdict as _dd_dl
        _by_evt_dl: Dict[str, list] = _dd_dl(list)
        for q in quotes:
            _ep = q.ticker.rsplit("-", 1)[0]
            _by_evt_dl[_ep].append(q)

        def _numeric_suffix_dl(ticker: str) -> float:
            last = ticker.rsplit("-", 1)[-1]
            nums = _re_dl.findall(r"\d+\.?\d*", last)
            return float(nums[-1]) if nums else -1.0

        complement_arbs = []
        for q in quotes:
            if q.is_stale:                              # G1: stale quote → skip
                continue
            if _is_sports_dl(q.ticker):                 # skip sports stat markets (thin/stale)
                continue
            if q.yes_ask <= 0 or q.no_ask <= 0:
                continue
            if q.yes_ask < 0.05 or q.no_ask < 0.05:    # G2: price floor (match ws_bridge)
                continue
            # G2b: block threshold/bracket markets (many contracts per event)
            _ep_dl = q.ticker.rsplit("-", 1)[0]
            if len(_by_evt_dl.get(_ep_dl, [])) > 5:
                continue
            if _numeric_suffix_dl(q.ticker) >= 0:
                continue
            cost_both = q.yes_ask + q.no_ask
            if cost_both < 1.0:
                gross = 1.0 - cost_both
                if gross > 0.10:                        # G3: >10c gross is stale/crossed (match ws_bridge)
                    continue
                # No Kalshi REST call here — structural gates above are sufficient.
                # REST calls in this hot path cause 100-500ms blocking per candidate.
                fees  = _kalshi_fee(q.yes_ask) + _kalshi_fee(q.no_ask)
                net   = gross - fees
                if net < 0.001:                         # G4: must be net positive after fees
                    continue
                if net > 0:
                    complement_arbs.append({
                        "ticker": q.ticker,
                        "strategy": "yes_no_complement",
                        "yes_ask": q.yes_ask,
                        "no_ask": q.no_ask,
                        "gross_edge_cents": round(gross * 100, 2),
                        "fees_cents": round(fees * 100, 2),
                        "net_edge_cents": round(net * 100, 2),
                        "spread": q.spread,
                        "age_s": round(q.age_seconds, 0),
                    })
        complement_arbs.sort(key=lambda x: x["net_edge_cents"], reverse=True)

        # -- Strategy 2: Collectively-exhaustive bucket arb (same event, sum YES asks < 1.0)
        # Group markets by event prefix (ticker up to last '-')
        from collections import defaultdict
        _by_event: Dict[str, list] = defaultdict(list)
        for q in quotes:
            if q.is_stale:      # same freshness gate as complement scan
                continue
            parts = q.ticker.rsplit("-", 1)
            if len(parts) == 2:
                _by_event[parts[0]].append(q)
        ce_arbs = []
        for event_prefix, legs in _by_event.items():
            if len(legs) < 2 or len(legs) > 20:
                continue
            if _is_sports_dl(event_prefix):             # skip sports stat markets
                continue
            # Only use fresh legs with plausible prices (>= 3c) to avoid phantom settled legs
            legs_sorted = sorted(
                [l for l in legs if l.yes_ask >= 0.03],
                key=lambda l: l.yes_ask,
            )
            if len(legs_sorted) < 2:
                continue
            total_yes_ask = sum(l.yes_ask for l in legs_sorted)
            n_seen = len(legs_sorted)
            # If sum of all YES asks < 1.0 — collectively exhaustive arb candidate
            #
            # Two-layer completeness protocol:
            #   L1 — sum lower-bound: must see >= 75% of probability mass.
            #         If we're missing legs, the partial sum can be tiny (e.g. 0.30
            #         for 2-of-5 legs) — that's NOT an arb, we just don't see all legs.
            #   L2 — Full Kalshi API verification (fail-closed, 45s cache):
            #         * official leg count vs seen count (missing-leg detection)
            #         * every leg ticker present in official set
            #         * non-zero liquidity (volume_fp or open_interest_fp)
            #         * sum(YES asks) from API prices < 0.98
            #         * Gate 5 structural CE check with full title/rules market data
            if not (0.75 <= total_yes_ask < 0.98):  # L1: must see >= 75% probability mass
                continue
            # L1b: sum >= 1.00 is mathematically impossible — block unconditionally.
            # (Catches price inversions or cached stale asks that inflate the sum.)
            if total_yes_ask >= 1.00:
                continue
            # L1c: multi-candidate political races require >= 4 visible legs.
            # Races with 5+ candidates can pass L1 on a partial subset (e.g. 2 legs
            # at 0.75+0.17=0.92) even though the full slate sums well above $1.
            _RACE_KW_DL = ("MAYOR", "PRESIDENT", "SENATE", "HOUSE", "GOV")
            if any(kw in event_prefix.upper() for kw in _RACE_KW_DL) and n_seen < 4:
                continue
            # No Kalshi REST call here — REST per CE candidate adds 200-500ms each.
            # The ws_bridge CE scanner with fail-closed Synthesis event_groups gate
            # is the authoritative source; this display scan uses structural gates only.
            gross = 1.0 - total_yes_ask
            fees  = sum(_kalshi_fee(l.yes_ask) for l in legs_sorted)
            net   = gross - fees
            if net > 0.005:  # min 0.5c net
                ce_arbs.append({
                    "ticker": f"{event_prefix} ({len(legs_sorted)} legs)",
                    "event_ticker": event_prefix,   # needed by _is_structural_ce in p01
                    "strategy": "collectively_exhaustive",
                    "yes_ask": round(total_yes_ask, 4),
                    "no_ask": 0.0,
                    "gross_edge_cents": round(gross * 100, 2),
                    "fees_cents": round(fees * 100, 2),
                    "net_edge_cents": round(net * 100, 2),
                    "spread": round(sum(l.spread for l in legs_sorted) / len(legs_sorted), 4),
                    "age_s": round(sum(l.age_seconds for l in legs_sorted) / len(legs_sorted), 0),
                    "legs": [l.ticker for l in legs_sorted],
                })
        ce_arbs.sort(key=lambda x: x["net_edge_cents"], reverse=True)

        # -- Strategies 3-5: pull ME / threshold / superset from live_state queue
        # ws_bridge already validates and emits these; no need to recompute here.
        try:
            from dashboard.live_state import get_live_state as _gls_dl
            _recent = _gls_dl().get_recent_opportunities(limit=200)
            _other_strategies = {"mutually_exclusive", "threshold_order", "superset"}
            _cutoff_ts = time.time() - 300  # only show arbs from last 5 min
            other_arbs = [
                o for o in _recent
                if o.get("strategy") in _other_strategies
                and o.get("detected_at_ts", 0) >= _cutoff_ts
            ]
            other_arbs.sort(key=lambda x: x.get("net_edge_cents", 0), reverse=True)
        except Exception:
            other_arbs = []

        # Combined arb list
        all_live_arbs = complement_arbs[:30] + ce_arbs[:20] + other_arbs[:20]
        all_live_arbs.sort(key=lambda x: x["net_edge_cents"], reverse=True)

        # Top markets by tightest spread
        tight_sorted = sorted(quotes, key=lambda q: q.spread)[:20]
        top_markets = [{
            "ticker": q.ticker,
            "yes_bid": q.yes_bid,
            "yes_ask": q.yes_ask,
            "spread": q.spread,
            "mid": q.mid,
            "l2_depth": len(q.yes_bids) + len(q.yes_asks),
            "age_s": round(q.age_seconds, 0),
        } for q in tight_sorted]

        return {
            "source": "live",
            "markets": len(quotes),
            "avg_spread": round(sum(spreads) / len(spreads), 4) if spreads else None,
            "tight_spread_count": len(tight),
            "with_l2_depth": len(with_depth),
            "complement_arbs": all_live_arbs[:50],   # combined all strategies
            "complement_only": complement_arbs[:30],
            "ce_arbs": ce_arbs[:20],
            "other_arbs": other_arbs[:20],
            "top_markets": top_markets,
            "connected": ws_stats.get("connected", False),
            "messages_per_sec": ws_stats.get("messages_per_sec", 0),
            "messages_total": ws_stats.get("messages_total", 0),
        }
    except Exception as exc:
        logger.warning("get_live_market_summary: %s", exc)
        return {"source": "live", "markets": 0, "error": str(exc)}


@st.cache_data(ttl=300)
def get_arb_time_of_day_stats() -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Breaks down arbitrage opportunities by hour of day (UTC).
    Returns a DataFrame with columns: hour, count, avg_edge_cents, executable_count.
    Used for the 'when do arbs occur?' analysis on p05_historical_arb.
    """
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(text("""
                SELECT
                    EXTRACT(HOUR FROM detected_at AT TIME ZONE 'UTC')::int AS hour,
                    COUNT(*) AS count,
                    AVG(net_edge) * 100  AS avg_edge_cents,
                    COUNT(*) FILTER (WHERE classification='A') AS executable_count
                FROM arbitrage_opportunities
                WHERE net_edge > 0
                GROUP BY 1
                ORDER BY 1
            """), conn)
        return df, None
    except Exception as exc:
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                # Fetch raw detected_at and use pandas for UTC-aware hour extraction
                raw = pd.read_sql_query(
                    "SELECT detected_at, CAST(net_edge AS REAL) AS net_edge, classification "
                    "FROM arbitrage_opportunities WHERE CAST(net_edge AS REAL) > 0",
                    c,
                )
                c.close()
                if not raw.empty:
                    raw["_ts"] = pd.to_datetime(raw["detected_at"], utc=True, errors="coerce")
                    raw["hour"] = raw["_ts"].dt.hour  # true UTC hour
                    df = (
                        raw.groupby("hour")
                        .agg(
                            count=("net_edge", "count"),
                            avg_edge_cents=("net_edge", lambda x: x.mean() * 100),
                            executable_count=("classification", lambda x: (x == "A").sum()),
                        )
                        .reset_index()
                        .sort_values("hour")
                    )
                    return df, None
            except Exception: pass
        return pd.DataFrame(), str(exc)


@st.cache_data(ttl=300)
def get_arb_day_of_week_stats() -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Breaks down arbitrage opportunities by day of week (0=Sunday .. 6=Saturday).
    Columns: dow_num, dow_label, count, avg_edge_cents, median_lifetime_s.
    """
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(text("""
                SELECT
                    EXTRACT(DOW FROM detected_at AT TIME ZONE 'UTC')::int AS dow_num,
                    TO_CHAR(detected_at AT TIME ZONE 'UTC', 'Dy')        AS dow_label,
                    COUNT(*) AS count,
                    AVG(net_edge) * 100 AS avg_edge_cents,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_seconds) AS median_lifetime_s
                FROM arbitrage_opportunities
                WHERE net_edge > 0
                GROUP BY 1, 2
                ORDER BY 1
            """), conn)
        return df, None
    except Exception as exc:
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                # Fetch raw and use pandas for UTC-aware day-of-week (0=Mon..6=Sun in pandas;
                # remap to 0=Sun..6=Sat to match the PostgreSQL EXTRACT(DOW ...) convention)
                _DOW_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                raw = pd.read_sql_query(
                    "SELECT detected_at, CAST(net_edge AS REAL) AS net_edge "
                    "FROM arbitrage_opportunities WHERE CAST(net_edge AS REAL) > 0",
                    c,
                )
                c.close()
                if not raw.empty:
                    raw["_ts"] = pd.to_datetime(raw["detected_at"], utc=True, errors="coerce")
                    # pandas weekday: 0=Mon..6=Sun; convert to 0=Sun..6=Sat
                    raw["dow_num"] = (raw["_ts"].dt.weekday + 1) % 7
                    raw["dow_label"] = raw["dow_num"].map(lambda d: _DOW_NAMES[d])
                    df = (
                        raw.groupby(["dow_num", "dow_label"])
                        .agg(count=("net_edge", "count"),
                             avg_edge_cents=("net_edge", lambda x: x.mean() * 100))
                        .reset_index()
                        .sort_values("dow_num")
                    )
                    df["median_lifetime_s"] = 0
                    return df, None
            except Exception: pass
        return pd.DataFrame(), str(exc)


@st.cache_data(ttl=300)
def get_arb_settlement_proximity_stats() -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Computes arb count / avg edge grouped by days-until-settlement at time of detection.
    Requires markets.close_time.  Buckets: <1d, 1-3d, 3-7d, 7-14d, 14-30d, 30d+.
    """
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(text("""
                WITH opp_markets AS (
                    SELECT
                        a.opportunity_id,
                        a.detected_at,
                        a.net_edge,
                        a.classification,
                        a.duration_seconds,
                        a.markets_involved[1] AS ticker_raw
                ),
                enriched AS (
                    SELECT
                        om.*,
                        m.close_time,
                        EXTRACT(EPOCH FROM (m.close_time - om.detected_at)) / 86400.0 AS days_to_settle
                    FROM opp_markets om
                    JOIN markets m
                      ON m.ticker = TRIM(BOTH '"' FROM om.ticker_raw)
                    WHERE m.close_time IS NOT NULL
                      AND om.net_edge > 0
                )
                SELECT
                    CASE
                        WHEN days_to_settle < 1   THEN '< 1 day'
                        WHEN days_to_settle < 3   THEN '1-3 days'
                        WHEN days_to_settle < 7   THEN '3-7 days'
                        WHEN days_to_settle < 14  THEN '7-14 days'
                        WHEN days_to_settle < 30  THEN '14-30 days'
                        ELSE '30+ days'
                    END AS bucket,
                    CASE
                        WHEN days_to_settle < 1   THEN 0
                        WHEN days_to_settle < 3   THEN 1
                        WHEN days_to_settle < 7   THEN 2
                        WHEN days_to_settle < 14  THEN 3
                        WHEN days_to_settle < 30  THEN 4
                        ELSE 5
                    END AS bucket_order,
                    COUNT(*) AS count,
                    AVG(net_edge) * 100 AS avg_edge_cents,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_seconds)
                                         AS median_lifetime_s
                FROM enriched
                GROUP BY 1, 2
                ORDER BY 2
            """), conn)
        return df, None
    except Exception as exc:
        # Settlement proximity requires markets.close_time; SQLite snapshot lacks this.
        # Return empty so p05 shows the explanatory message rather than a nonsense chart.
        if _SQLITE_PATH.exists():
            return pd.DataFrame(), None
        return pd.DataFrame(), str(exc)


@st.cache_data(ttl=300)
def get_arb_edge_by_strategy_class() -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Returns edge percentiles (25th, median, 75th, 95th) and count grouped by
    strategy_type x classification. Used for the research summary page.
    """
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(text("""
                SELECT
                    strategy_type,
                    classification,
                    COUNT(*) AS count,
                    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY net_edge)::numeric * 100, 2)
                        AS p25_edge_cents,
                    ROUND(PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY net_edge)::numeric * 100, 2)
                        AS median_edge_cents,
                    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY net_edge)::numeric * 100, 2)
                        AS p75_edge_cents,
                    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY net_edge)::numeric * 100, 2)
                        AS p95_edge_cents,
                    ROUND(AVG(duration_seconds)::numeric, 0) AS avg_lifetime_s
                FROM arbitrage_opportunities
                WHERE net_edge > 0
                GROUP BY 1, 2
                ORDER BY 1, 2
            """), conn)
        return df, None
    except Exception as exc:
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                # Check if duration_seconds column exists (may be absent in older snapshots)
                _ci = c.execute("PRAGMA table_info(arbitrage_opportunities)").fetchall()
                _cn = {r[1] for r in _ci}
                _dur_sel = "CAST(duration_seconds AS REAL)" if "duration_seconds" in _cn else "NULL"
                # Fetch raw data and compute percentiles in Python (SQLite lacks PERCENTILE_CONT)
                raw = pd.read_sql_query(f"""
                    SELECT strategy_type, classification,
                           CAST(net_edge AS REAL)*100 AS edge_cents,
                           {_dur_sel} AS duration_seconds
                    FROM arbitrage_opportunities WHERE CAST(net_edge AS REAL) > 0
                """, c)
                c.close()
                if not raw.empty:
                    rows = []
                    for (strat, cls), grp in raw.groupby(["strategy_type", "classification"]):
                        edges = grp["edge_cents"].dropna()
                        durs  = grp["duration_seconds"].dropna()
                        rows.append({
                            "strategy_type": strat,
                            "classification": cls,
                            "count": len(grp),
                            "p25_edge_cents":    round(float(edges.quantile(0.25)), 2),
                            "median_edge_cents": round(float(edges.quantile(0.50)), 2),
                            "p75_edge_cents":    round(float(edges.quantile(0.75)), 2),
                            "p95_edge_cents":    round(float(edges.quantile(0.95)), 2),
                            "avg_lifetime_s":    round(float(durs.mean()), 0) if len(durs) else 0,
                        })
                    df = pd.DataFrame(rows).sort_values(["strategy_type", "classification"])
                    return df, None
            except Exception: pass
        return pd.DataFrame(), str(exc)


@st.cache_data(ttl=300)
def get_arb_rolling_7d() -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Rolling 7-day count and average edge for the trend chart on the historical arb page.
    Returns columns: day, count, avg_edge_cents, executable_count.
    """
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(text("""
                WITH daily AS (
                    SELECT
                        DATE_TRUNC('day', detected_at AT TIME ZONE 'UTC') AS day,
                        COUNT(*) AS count,
                        AVG(net_edge) * 100 AS avg_edge_cents,
                        COUNT(*) FILTER (WHERE classification='A') AS executable_count
                    FROM arbitrage_opportunities
                    WHERE net_edge > 0
                    GROUP BY 1
                )
                SELECT
                    day,
                    count,
                    avg_edge_cents,
                    executable_count,
                    SUM(count) OVER (
                        ORDER BY day
                        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                    ) AS rolling_7d_count
                FROM daily
                ORDER BY day
            """), conn)
        return df, None
    except Exception as exc:
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                df = pd.read_sql_query("""
                    SELECT strftime('%Y-%m-%d', substr(detected_at, 1, 19)) AS day,
                           COUNT(*) AS count,
                           AVG(CAST(net_edge AS REAL))*100 AS avg_edge_cents,
                           COUNT(CASE WHEN classification='A' THEN 1 END) AS executable_count,
                           COUNT(*) AS rolling_7d_count
                    FROM arbitrage_opportunities WHERE CAST(net_edge AS REAL) > 0
                    GROUP BY 1 ORDER BY 1
                """, c)
                c.close()
                return df, None
            except Exception: pass
        return pd.DataFrame(), str(exc)


@st.cache_data(ttl=300)
def get_arb_by_category() -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Aggregate arbitrage opportunities by market category.
    Joins arbitrage_opportunities → markets → events to get the category.
    Returns columns: category, count, avg_edge_cents, class_a_count, class_b_count.
    """
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(text("""
                SELECT
                    COALESCE(e.category, 'unknown')   AS category,
                    COUNT(DISTINCT a.opportunity_id)  AS count,
                    AVG(a.net_edge) * 100             AS avg_edge_cents,
                    COUNT(*) FILTER (WHERE a.classification='A') AS class_a_count,
                    COUNT(*) FILTER (WHERE a.classification='B') AS class_b_count
                FROM arbitrage_opportunities a
                JOIN LATERAL (
                    SELECT ticker
                    FROM unnest(a.markets_involved) AS t(ticker)
                    LIMIT 1
                ) tickers ON true
                LEFT JOIN markets m ON m.ticker = tickers.ticker
                LEFT JOIN events  e ON e.event_ticker = m.event_ticker
                WHERE a.net_edge > 0
                GROUP BY 1
                ORDER BY count DESC
                LIMIT 50
            """), conn)
        return df, None
    except Exception as exc:
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                # arbitrage_opportunities has no category column; derive from markets_involved ticker.
                # Fetch per-row data so pandas can count correctly (AVG here would give count=1).
                raw = pd.read_sql_query("""
                    SELECT markets_involved, classification,
                           CAST(net_edge AS REAL)*100 AS net_edge_cents
                    FROM arbitrage_opportunities WHERE CAST(net_edge AS REAL) > 0
                """, c)
                c.close()
                if not raw.empty:
                    # Extract first ticker — handles both JSON ["T1","T2"] and
                    # PostgreSQL array {T1,T2} notation stored in the TEXT column.
                    import json as _json_cat
                    def _first_ticker(v: str) -> str:
                        if not v:
                            return ""
                        s = str(v).strip()
                        # Try JSON array first
                        if s.startswith("["):
                            try:
                                items = _json_cat.loads(s)
                                if items and isinstance(items, list):
                                    return str(items[0]).strip()
                            except Exception:
                                pass
                        # PostgreSQL array notation: {TICKER1,TICKER2,...}
                        s2 = s.strip("{}")
                        return s2.split(",")[0].strip() if s2 else ""
                    raw["category"] = raw["markets_involved"].apply(
                        lambda v: _derive_category_from_ticker(_first_ticker(v))
                    )
                    df = (
                        raw.groupby("category")
                        .agg(
                            count=("net_edge_cents", "count"),
                            avg_edge_cents=("net_edge_cents", "mean"),
                            class_a_count=("classification", lambda x: (x == "A").sum()),
                            class_b_count=("classification", lambda x: (x == "B").sum()),
                        )
                        .reset_index()
                        .sort_values("count", ascending=False)
                        .head(50)
                    )
                    return df, None
            except Exception: pass
        return pd.DataFrame(), str(exc)


def get_table_sizes() -> pd.DataFrame:
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()

        sql = text("""
            SELECT
                schemaname,
                relname AS table_name,
                n_live_tup AS row_count,
                pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                pg_total_relation_size(relid) AS size_bytes
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
        """)
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            result = conn.execute(sql)
            rows = result.fetchall()
            return pd.DataFrame(rows, columns=list(result.keys()))
    except Exception:
        if _SQLITE_PATH.exists():
            try:
                c = _sqlite_conn()
                tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                rows = []
                for (tbl,) in tables:
                    cnt = c.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]
                    rows.append({"table_name": tbl, "row_count": cnt, "total_size": "SQLite", "size_bytes": 0})
                c.close()
                return pd.DataFrame(rows)
            except Exception:
                pass
        return pd.DataFrame()


# -- External market data (cross-asset page) -----------------------------------

@st.cache_data(ttl=3600)
def get_external_market_prices(asset_names: list | None = None, days: int = 90) -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Read external market data (yfinance + BOC VALET) from PostgreSQL.
    Falls back to empty DataFrame if table doesn't exist yet.
    Returns DataFrame: asset_name, obs_date, close_val, open_val, high_val, low_val, volume_val.
    """
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        if asset_names:
            placeholders = ", ".join(f"'{a}'" for a in asset_names)
            where_asset  = f"AND asset_name IN ({placeholders})"
        else:
            where_asset = ""
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            df = pd.read_sql(text(f"""
                SELECT asset_name, obs_date, close_val, open_val, high_val, low_val, volume_val, source
                FROM ext_market_daily
                WHERE obs_date >= CAST(:cutoff AS DATE)
                {where_asset}
                ORDER BY asset_name, obs_date
            """), conn, params={"cutoff": cutoff})
        return df, None
    except Exception as exc:
        return pd.DataFrame(), str(exc)


@st.cache_data(ttl=3600)
def get_latest_external_prices() -> Dict[str, float]:
    """Return most recent close_val per asset from ext_market_daily."""
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = '5000'"))
            rows = conn.execute(text("""
                SELECT DISTINCT ON (asset_name) asset_name, close_val
                FROM ext_market_daily
                WHERE close_val IS NOT NULL
                ORDER BY asset_name, obs_date DESC
            """)).fetchall()
        return {r[0]: float(r[1]) for r in rows if r[1] is not None}
    except Exception as exc:
        logger.debug("get_latest_external_prices: %s", exc)
        return {}


def upsert_ext_prices(prices: dict) -> None:
    """
    Upsert a batch of external price records into ext_market_daily.

    prices: dict mapping asset key (e.g. "us2y", "us10y", "vix") to float close value.
    Writes trade_date=today, source="yahoo_finance".
    """
    if not prices:
        return
    try:
        from database.repository import get_engine
        from sqlalchemy import text
        from datetime import date

        engine = get_engine()
        today = date.today().isoformat()
        rows = [
            {
                "asset": key,
                "src":   "yahoo_finance",
                "date":  today,
                "close": float(val),
            }
            for key, val in prices.items()
            if val is not None
        ]
        if not rows:
            return
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO ext_market_daily
                    (asset_name, source, obs_date, close_val)
                SELECT :asset, :src, CAST(:date AS DATE), :close
                ON CONFLICT (asset_name, obs_date) DO UPDATE SET
                    close_val  = EXCLUDED.close_val,
                    fetched_at = NOW()
            """), rows)
    except Exception as exc:
        logger.debug("upsert_ext_prices: %s", exc)
