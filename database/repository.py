"""
database/repository.py
Data-access layer - wraps SQLAlchemy session and raw SQL queries.
All heavy analytical queries use raw SQL so SQL is a genuine architectural component.
"""

from __future__ import annotations
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, Optional, List, Dict, Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from database.models import (
    Base, Event, Market, MarketSnapshot, Candlestick, Trade,
    ArbitrageOpportunity, BacktestTrade, ExternalMarketData,
    MacroEvent, CrossAssetSpread, IngestionLog, OrderBookSnapshot,
    ContractRelationship,
)

logger = logging.getLogger(__name__)


# -- Engine / Session setup -----------------------------------------------------

_engine = None
_Session = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            config.DB_URL,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            # Recycle before Neon's 5-min idle-connection timeout so the pool
            # never hands out a connection Neon already closed server-side.
            pool_recycle=240,
            # TCP keepalives keep the connection warm through NAT/firewall idle
            # timeouts and reduce Neon cold-start frequency.
            connect_args={
                "options": "-c statement_timeout=5000",
                "keepalives": 1,
                "keepalives_idle": 60,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
        )
    return _engine


def get_session_factory():
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _Session


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables if they do not exist (dev convenience - prefer schema.sql in prod)."""
    Base.metadata.create_all(get_engine())
    logger.info("Database tables created/verified.")


# -- Single-row upsert helpers (kept for non-bulk callers) ---------------------

def upsert_event(session: Session, data: Dict[str, Any]) -> None:
    stmt = pg_insert(Event).values(**data)
    stmt = stmt.on_conflict_do_update(
        index_elements=["event_ticker"],
        set_={k: stmt.excluded[k] for k in data if k != "event_ticker"},
    )
    session.execute(stmt)


def upsert_market(session: Session, data: Dict[str, Any]) -> None:
    stmt = pg_insert(Market).values(**data)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker"],
        set_={k: stmt.excluded[k] for k in data if k not in ("market_id", "ticker")},
    )
    session.execute(stmt)


def upsert_candlestick(session: Session, data: Dict[str, Any]) -> None:
    stmt = pg_insert(Candlestick).values(**data)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_candle_market_period_ts")
    session.execute(stmt)


def upsert_trade(session: Session, data: Dict[str, Any]) -> None:
    stmt = pg_insert(Trade).values(**data)
    stmt = stmt.on_conflict_do_nothing(index_elements=["trade_id"])
    session.execute(stmt)


# -- Bulk upsert helpers (1000-row batched inserts) ----------------------------

def bulk_upsert_events(session: Session, batch: List[Dict[str, Any]]) -> int:
    """
    INSERT ... ON CONFLICT DO UPDATE for a list of event dicts.
    All rows are committed in a single statement - ~100× faster than per-row.
    Returns number of rows in the batch (all attempted, conflicts updated).
    """
    if not batch:
        return 0
    stmt = pg_insert(Event).values(batch)
    # Build update set from the first row's keys (all rows have same schema)
    update_cols = {k: stmt.excluded[k] for k in batch[0] if k != "event_ticker"}
    stmt = stmt.on_conflict_do_update(
        index_elements=["event_ticker"],
        set_=update_cols,
    )
    session.execute(stmt)
    return len(batch)


def bulk_upsert_markets(session: Session, batch: List[Dict[str, Any]]) -> int:
    """
    INSERT ... ON CONFLICT DO UPDATE for a list of market dicts.
    If the parent event_ticker is not in the events table the row is silently
    skipped (ON CONFLICT DO NOTHING fallback via exception handling in caller).
    """
    if not batch:
        return 0
    stmt = pg_insert(Market).values(batch)
    update_cols = {
        k: stmt.excluded[k]
        for k in batch[0]
        if k not in ("market_id", "ticker")
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker"],
        set_=update_cols,
    )
    session.execute(stmt)
    return len(batch)


def bulk_upsert_candlesticks(session: Session, batch: List[Dict[str, Any]]) -> int:
    """INSERT ... ON CONFLICT DO NOTHING for candlestick rows."""
    if not batch:
        return 0
    stmt = pg_insert(Candlestick).values(batch)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_candle_market_period_ts")
    session.execute(stmt)
    return len(batch)


def bulk_upsert_trades(session: Session, batch: List[Dict[str, Any]]) -> int:
    """INSERT ... ON CONFLICT DO NOTHING for trade rows."""
    if not batch:
        return 0
    stmt = pg_insert(Trade).values(batch)
    stmt = stmt.on_conflict_do_nothing(index_elements=["trade_id"])
    session.execute(stmt)
    return len(batch)


# -- Other insert helpers -------------------------------------------------------

def insert_snapshot(session: Session, data: Dict[str, Any]) -> None:
    snap = MarketSnapshot(**data)
    session.add(snap)


def insert_order_book(session: Session, rows: List[Dict[str, Any]]) -> None:
    if rows:
        session.execute(pg_insert(OrderBookSnapshot), rows)


def upsert_relationship(session: Session, data: Dict[str, Any]) -> None:
    stmt = pg_insert(ContractRelationship).values(**data)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_relationship",
        set_={k: stmt.excluded[k] for k in data if k not in
              ("market_id_1", "market_id_2", "relationship_type")},
    )
    session.execute(stmt)


def insert_opportunity(session: Session, data: Dict[str, Any]) -> str:
    opp = ArbitrageOpportunity(**data)
    session.add(opp)
    session.flush()
    return str(opp.opportunity_id)


def upsert_external_data(session: Session, data: Dict[str, Any]) -> None:
    stmt = pg_insert(ExternalMarketData).values(**data)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ext_market_data",
        set_={k: stmt.excluded[k] for k in data if k not in
              ("price_ts", "asset", "instrument", "tenor", "source")},
    )
    session.execute(stmt)


def log_ingestion(session: Session, **kwargs) -> None:
    log = IngestionLog(**kwargs)
    session.add(log)


# -- Read queries (raw SQL for analytical power) --------------------------------

def get_all_open_markets(session: Session) -> pd.DataFrame:
    """Return all open markets with their latest snapshot prices."""
    sql = text("""
        WITH latest_snaps AS (
            SELECT DISTINCT ON (market_id)
                market_id, yes_bid, yes_ask, no_bid, no_ask,
                last_price, volume, open_interest, snapshot_ts
            FROM market_snapshots
            ORDER BY market_id, snapshot_ts DESC
        )
        SELECT
            m.market_id, m.ticker, m.event_ticker, m.series_ticker,
            m.title, m.category, m.close_time,
            s.yes_bid, s.yes_ask, s.no_bid, s.no_ask,
            s.last_price, s.volume, s.open_interest, s.snapshot_ts
        FROM markets m
        LEFT JOIN latest_snaps s ON m.market_id = s.market_id
        WHERE m.status IN ('open','active')
        ORDER BY m.event_ticker, m.ticker
    """)
    return pd.read_sql(sql, session.get_bind())


def get_markets_by_event(session: Session, event_ticker: str) -> pd.DataFrame:
    sql = text("""
        WITH latest_snaps AS (
            SELECT DISTINCT ON (market_id)
                market_id, yes_bid, yes_ask, no_bid, no_ask,
                last_price, volume, open_interest, snapshot_ts
            FROM market_snapshots
            ORDER BY market_id, snapshot_ts DESC
        )
        SELECT m.*, s.yes_bid, s.yes_ask, s.no_bid, s.no_ask,
               s.last_price, s.volume, s.open_interest, s.snapshot_ts
        FROM markets m
        LEFT JOIN latest_snaps s ON m.market_id = s.market_id
        WHERE m.event_ticker = :event_ticker
        ORDER BY m.floor_strike NULLS LAST, m.ticker
    """)
    return pd.read_sql(sql, session.get_bind(), params={"event_ticker": event_ticker})


def get_candlesticks(
    session: Session,
    market_id: str,
    period_interval: int,
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
) -> pd.DataFrame:
    params: Dict[str, Any] = {"market_id": market_id, "period": period_interval}
    where_extra = ""
    if start_ts:
        where_extra += " AND period_end_ts >= :start_ts"
        params["start_ts"] = start_ts
    if end_ts:
        where_extra += " AND period_end_ts <= :end_ts"
        params["end_ts"] = end_ts

    sql = text(f"""
        SELECT *
        FROM candlesticks
        WHERE market_id = :market_id
          AND period_interval = :period
          {where_extra}
        ORDER BY period_end_ts
    """)
    return pd.read_sql(sql, session.get_bind(), params=params)


def get_live_arbitrage_opportunities(session: Session) -> pd.DataFrame:
    sql = text("""
        SELECT
            a.opportunity_id, a.detected_at, a.strategy_type,
            a.classification, a.markets_involved,
            a.gross_edge, a.total_fees, a.net_edge,
            a.max_net_profit, a.max_executable_contracts,
            EXTRACT(EPOCH FROM (NOW() - a.detected_at)) AS age_seconds
        FROM arbitrage_opportunities a
        WHERE a.status = 'open'
        ORDER BY a.net_edge DESC NULLS LAST
    """)
    return pd.read_sql(sql, session.bind)


def get_historical_arbitrage_summary(
    session: Session,
    strategy_type: Optional[str] = None,
    classification: Optional[str] = None,
    min_net_edge: float = 0.0,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> pd.DataFrame:
    params: Dict[str, Any] = {"min_net_edge": min_net_edge}
    filters = ["a.net_edge >= :min_net_edge"]

    if strategy_type:
        filters.append("a.strategy_type = :strategy_type")
        params["strategy_type"] = strategy_type
    if classification:
        filters.append("a.classification = :classification")
        params["classification"] = classification
    if start_date:
        filters.append("a.detected_at >= :start_date")
        params["start_date"] = start_date
    if end_date:
        filters.append("a.detected_at <= :end_date")
        params["end_date"] = end_date

    where = "WHERE " + " AND ".join(filters)

    sql = text(f"""
        SELECT
            a.opportunity_id, a.detected_at, a.strategy_type, a.classification,
            a.markets_involved, a.gross_edge, a.total_fees, a.net_edge,
            a.max_net_profit, a.duration_seconds,
            a.settlement_outcome, a.realised_net_pnl, a.status
        FROM arbitrage_opportunities a
        {where}
        ORDER BY a.detected_at DESC
    """)
    return pd.read_sql(sql, session.get_bind(), params=params)


def get_event_market_matrix(session: Session, event_ticker: str) -> pd.DataFrame:
    sql = text("""
        WITH latest_snaps AS (
            SELECT DISTINCT ON (market_id)
                market_id, yes_bid, yes_ask, volume, open_interest, snapshot_ts
            FROM market_snapshots
            ORDER BY market_id, snapshot_ts DESC
        )
        SELECT
            m.market_id, m.ticker, m.title, m.floor_strike, m.cap_strike,
            s.yes_bid, s.yes_ask, s.volume, s.open_interest,
            s.snapshot_ts
        FROM markets m
        JOIN latest_snaps s ON m.market_id = s.market_id
        WHERE m.event_ticker = :event_ticker
          AND m.status IN ('open','active')
          AND s.yes_ask IS NOT NULL
        ORDER BY m.floor_strike NULLS LAST, m.ticker
    """)
    return pd.read_sql(sql, session.bind, params={"event_ticker": event_ticker})


def get_cross_asset_spread_history(
    session: Session,
    market_id: str,
    asset: str,
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
) -> pd.DataFrame:
    params: Dict[str, Any] = {"market_id": market_id, "asset": asset}
    where_extra = ""
    if start_ts:
        where_extra += " AND spread_ts >= :start_ts"
        params["start_ts"] = start_ts
    if end_ts:
        where_extra += " AND spread_ts <= :end_ts"
        params["end_ts"] = end_ts

    sql = text(f"""
        SELECT
            cs.spread_ts,
            cs.kalshi_probability, cs.trad_probability,
            (cs.kalshi_probability - cs.trad_probability) AS spread,
            cs.trad_price, cs.trad_instrument, cs.conversion_method,
            cs.kalshi_liquidity
        FROM cross_asset_spreads cs
        WHERE cs.market_id = :market_id
          AND cs.asset = :asset
          {where_extra}
        ORDER BY cs.spread_ts
    """)
    return pd.read_sql(sql, session.get_bind(), params=params)


def get_backtest_performance(session: Session, run_id: str) -> pd.DataFrame:
    sql = text("""
        SELECT
            bt.id, bt.market_id, bt.side, bt.action,
            bt.entry_ts, bt.exit_ts, bt.entry_price, bt.exit_price,
            bt.quantity, bt.taker_fees, bt.maker_fees, bt.slippage,
            bt.gross_pnl, bt.net_pnl,
            m.ticker, m.event_ticker
        FROM backtest_trades bt
        JOIN markets m ON bt.market_id = m.market_id
        WHERE bt.run_id = :run_id
        ORDER BY bt.entry_ts
    """)
    return pd.read_sql(sql, session.bind, params={"run_id": run_id})


def get_complement_candidates(session: Session) -> pd.DataFrame:
    sql = text("SELECT * FROM v_complement_candidates ORDER BY gross_edge_both DESC")
    return pd.read_sql(sql, session.bind)


def get_markets_needing_candlesticks(
    session: Session,
    period_interval: int,
    lookback_days: int = 30,
) -> List[str]:
    sql = text("""
        SELECT m.market_id
        FROM markets m
        WHERE m.status IN ('open', 'settled')
          AND NOT EXISTS (
              SELECT 1 FROM candlesticks c
              WHERE c.market_id = m.market_id
                AND c.period_interval = :period
                AND c.period_end_ts >= NOW() - (:days * INTERVAL '1 day')
          )
        ORDER BY m.market_id
    """)
    result = session.execute(sql, {"period": period_interval, "days": lookback_days})
    return [row[0] for row in result]


def get_external_data_series(
    session: Session,
    asset: str,
    instrument: str,
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
) -> pd.DataFrame:
    params: Dict[str, Any] = {"asset": asset, "instrument": instrument}
    where_extra = ""
    if start_ts:
        where_extra += " AND price_ts >= :start_ts"
        params["start_ts"] = start_ts
    if end_ts:
        where_extra += " AND price_ts <= :end_ts"
        params["end_ts"] = end_ts

    sql = text(f"""
        SELECT price_ts, asset, instrument, tenor, price, bid, ask, volume, source
        FROM external_market_data
        WHERE asset = :asset AND instrument = :instrument
        {where_extra}
        ORDER BY price_ts
    """)
    return pd.read_sql(sql, session.get_bind(), params=params)
