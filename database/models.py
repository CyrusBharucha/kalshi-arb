"""
database/models.py
SQLAlchemy ORM models - mirror of schema.sql.
Use these for Python-level queries and inserts; raw SQL in repository.py for complex analytics.
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index,
    Integer, JSON, Numeric, SmallInteger, String, Text, UniqueConstraint,
    ARRAY, CHAR,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import TIMESTAMP as _TIMESTAMP

# SQLAlchemy does not export TIMESTAMPTZ; use TIMESTAMP(timezone=True) instead.
TIMESTAMPTZ = _TIMESTAMP(timezone=True)
from sqlalchemy.orm import DeclarativeBase, relationship, Mapped


class Base(DeclarativeBase):
    pass


# -- Events ---------------------------------------------------------------------

class Event(Base):
    __tablename__ = "events"

    event_id            = Column(Text, primary_key=True)
    event_ticker        = Column(Text, nullable=False, unique=True, index=True)
    title               = Column(Text, nullable=False)
    category            = Column(Text)
    sub_category        = Column(Text)
    series_ticker       = Column(Text, index=True)
    event_time          = Column(TIMESTAMPTZ)
    settlement_time     = Column(TIMESTAMPTZ)
    geographic_region   = Column(Text)
    canadian_relevance  = Column(SmallInteger, default=0)
    mutually_exclusive  = Column(Boolean, default=True)
    collectively_exhaustive = Column(Boolean, default=True)
    status              = Column(Text, nullable=False, default="open")
    raw_json            = Column(JSONB)
    created_at          = Column(TIMESTAMPTZ, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at          = Column(TIMESTAMPTZ, nullable=False, default=lambda: datetime.now(timezone.utc),
                                  onupdate=lambda: datetime.now(timezone.utc))

    markets = relationship("Market", back_populates="event",
                           foreign_keys="Market.event_ticker",
                           primaryjoin="Event.event_ticker == Market.event_ticker")


# -- Markets --------------------------------------------------------------------

class Market(Base):
    __tablename__ = "markets"

    market_id           = Column(Text, primary_key=True)
    ticker              = Column(Text, nullable=False, unique=True, index=True)
    event_ticker        = Column(Text, ForeignKey("events.event_ticker",
                                                   ondelete="CASCADE"),
                                  nullable=False, index=True)
    series_ticker       = Column(Text, index=True)
    title               = Column(Text, nullable=False)
    subtitle            = Column(Text)
    category            = Column(Text)
    market_type         = Column(Text)
    outcome_type        = Column(Text)
    status              = Column(Text, nullable=False, default="open", index=True)
    open_time           = Column(TIMESTAMPTZ)
    close_time          = Column(TIMESTAMPTZ, index=True)
    expiration_time     = Column(TIMESTAMPTZ)
    settlement_time     = Column(TIMESTAMPTZ)
    settlement_source   = Column(Text)
    settlement_result   = Column(Text)
    settlement_value    = Column(Numeric(6, 4))
    floor_strike        = Column(Numeric(20, 6))   # was (12,6) - overflows for strikes ≥ 1M
    cap_strike          = Column(Numeric(20, 6))   # was (12,6) - overflows for strikes ≥ 1M
    rules_primary       = Column(Text)
    rules_secondary     = Column(Text)
    expected_expiry_time = Column(TIMESTAMPTZ)
    raw_json            = Column(JSONB)
    created_at          = Column(TIMESTAMPTZ, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at          = Column(TIMESTAMPTZ, nullable=False, default=lambda: datetime.now(timezone.utc),
                                  onupdate=lambda: datetime.now(timezone.utc))

    event = relationship("Event", back_populates="markets",
                          foreign_keys=[event_ticker],
                          primaryjoin="Market.event_ticker == Event.event_ticker")
    snapshots    = relationship("MarketSnapshot", back_populates="market",
                                 cascade="all, delete-orphan")
    candlesticks = relationship("Candlestick", back_populates="market",
                                 cascade="all, delete-orphan")
    trades       = relationship("Trade", back_populates="market",
                                 cascade="all, delete-orphan")


# -- Market Snapshots -----------------------------------------------------------

class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_ts   = Column(TIMESTAMPTZ, nullable=False)
    market_id     = Column(Text, ForeignKey("markets.market_id", ondelete="CASCADE"),
                            nullable=False)
    yes_bid       = Column(Numeric(6, 4))
    yes_ask       = Column(Numeric(6, 4))
    no_bid        = Column(Numeric(6, 4))
    no_ask        = Column(Numeric(6, 4))
    last_price    = Column(Numeric(6, 4))
    volume        = Column(Numeric(16, 2))
    open_interest = Column(Numeric(16, 2))
    source        = Column(Text, default="api_poll")

    market = relationship("Market", back_populates="snapshots")

    __table_args__ = (
        Index("idx_snapshots_market_ts", "market_id", "snapshot_ts"),
    )


# -- Candlesticks ---------------------------------------------------------------

class Candlestick(Base):
    __tablename__ = "candlesticks"

    id              = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id       = Column(Text, ForeignKey("markets.market_id", ondelete="CASCADE"),
                              nullable=False)
    period_interval = Column(SmallInteger, nullable=False)
    period_end_ts   = Column(TIMESTAMPTZ, nullable=False)

    yes_bid_open    = Column(Numeric(6, 4))
    yes_bid_high    = Column(Numeric(6, 4))
    yes_bid_low     = Column(Numeric(6, 4))
    yes_bid_close   = Column(Numeric(6, 4))
    yes_ask_open    = Column(Numeric(6, 4))
    yes_ask_high    = Column(Numeric(6, 4))
    yes_ask_low     = Column(Numeric(6, 4))
    yes_ask_close   = Column(Numeric(6, 4))

    price_open      = Column(Numeric(6, 4))
    price_high      = Column(Numeric(6, 4))
    price_low       = Column(Numeric(6, 4))
    price_close     = Column(Numeric(6, 4))
    price_mean      = Column(Numeric(6, 4))
    price_previous  = Column(Numeric(6, 4))

    volume          = Column(Numeric(16, 2))
    open_interest   = Column(Numeric(16, 2))

    market = relationship("Market", back_populates="candlesticks")

    __table_args__ = (
        UniqueConstraint("market_id", "period_interval", "period_end_ts",
                          name="uq_candle_market_period_ts"),
        Index("idx_candles_market_period_ts", "market_id", "period_interval", "period_end_ts"),
    )


# -- Order Book Snapshots -------------------------------------------------------

class OrderBookSnapshot(Base):
    __tablename__ = "order_book_snapshots"

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_ts = Column(TIMESTAMPTZ, nullable=False)
    market_id   = Column(Text, ForeignKey("markets.market_id", ondelete="CASCADE"),
                          nullable=False)
    side        = Column(CHAR(3), nullable=False)    # 'yes' | 'no'
    price       = Column(Numeric(6, 4), nullable=False)
    quantity    = Column(Numeric(16, 2), nullable=False)
    level_rank  = Column(SmallInteger, nullable=False)

    __table_args__ = (
        Index("idx_ob_market_ts", "market_id", "snapshot_ts"),
    )


# -- Public Trades --------------------------------------------------------------

class Trade(Base):
    __tablename__ = "trades"

    trade_id   = Column(Text, primary_key=True)
    trade_ts   = Column(TIMESTAMPTZ, nullable=False, index=True)
    market_id  = Column(Text, ForeignKey("markets.market_id", ondelete="CASCADE"),
                         nullable=False)
    price      = Column(Numeric(6, 4), nullable=False)
    quantity   = Column(Numeric(16, 2), nullable=False)
    taker_side = Column(Text)
    source     = Column(Text, default="api")

    market = relationship("Market", back_populates="trades")

    __table_args__ = (
        Index("idx_trades_market_ts", "market_id", "trade_ts"),
    )


# -- Contract Relationships -----------------------------------------------------

class ContractRelationship(Base):
    __tablename__ = "contract_relationships"

    id                  = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id_1         = Column(Text, ForeignKey("markets.market_id"), nullable=False)
    market_id_2         = Column(Text, ForeignKey("markets.market_id"), nullable=False)
    relationship_type   = Column(Text, nullable=False)
    logical_constraint  = Column(Text)
    implied_inequality  = Column(Text)
    confidence          = Column(Numeric(4, 3), default=1.0)
    verified            = Column(Boolean, default=False)
    created_at          = Column(TIMESTAMPTZ, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("market_id_1", "market_id_2", "relationship_type",
                          name="uq_relationship"),
        # Queries filtering on market_id_2 alone (join pattern) cannot use the
        # triple unique-constraint index — add a dedicated covering index.
        Index("idx_cr_mid1", "market_id_1"),
        Index("idx_cr_mid2", "market_id_2"),
    )


# -- Arbitrage Opportunities ----------------------------------------------------

class ArbitrageOpportunity(Base):
    __tablename__ = "arbitrage_opportunities"

    opportunity_id      = Column(UUID(as_uuid=True), primary_key=True,
                                  default=uuid.uuid4)
    detected_at         = Column(TIMESTAMPTZ, nullable=False, default=lambda: datetime.now(timezone.utc))
    strategy_type       = Column(Text, nullable=False)
    classification      = Column(CHAR(1), nullable=False)
    markets_involved    = Column(ARRAY(Text), nullable=False)
    prices_json         = Column(JSONB, nullable=False)

    gross_edge          = Column(Numeric(8, 4))
    total_fees          = Column(Numeric(8, 4))
    estimated_slippage  = Column(Numeric(8, 4))
    net_edge            = Column(Numeric(8, 4))

    max_executable_contracts = Column(Numeric(12, 2))
    max_gross_profit    = Column(Numeric(12, 4))
    max_net_profit      = Column(Numeric(12, 4))

    status              = Column(Text, nullable=False, default="open")
    closed_at           = Column(TIMESTAMPTZ)
    duration_seconds    = Column(Numeric(12, 2))

    settlement_outcome  = Column(Text)
    realised_gross_pnl  = Column(Numeric(12, 4))
    realised_net_pnl    = Column(Numeric(12, 4))

    notes               = Column(Text)
    created_at          = Column(TIMESTAMPTZ, nullable=False, default=lambda: datetime.now(timezone.utc))

    backtest_trades = relationship("BacktestTrade", back_populates="opportunity")


# -- Backtest Trades ------------------------------------------------------------

class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id              = Column(BigInteger, primary_key=True, autoincrement=True)
    opportunity_id  = Column(UUID(as_uuid=True),
                              ForeignKey("arbitrage_opportunities.opportunity_id"))
    run_id          = Column(Text, nullable=False, index=True)
    market_id       = Column(Text, ForeignKey("markets.market_id"), nullable=False)
    side            = Column(Text, nullable=False)
    action          = Column(Text, nullable=False)
    entry_ts        = Column(TIMESTAMPTZ, nullable=False)
    exit_ts         = Column(TIMESTAMPTZ)
    entry_price     = Column(Numeric(6, 4), nullable=False)
    exit_price      = Column(Numeric(6, 4))
    quantity        = Column(Numeric(12, 2), nullable=False)
    taker_fees      = Column(Numeric(10, 4))
    maker_fees      = Column(Numeric(10, 4))
    slippage        = Column(Numeric(10, 4))
    gross_pnl       = Column(Numeric(12, 4))
    net_pnl         = Column(Numeric(12, 4))
    notes           = Column(Text)

    opportunity = relationship("ArbitrageOpportunity", back_populates="backtest_trades")


# -- External Market Data -------------------------------------------------------

class ExternalMarketData(Base):
    __tablename__ = "external_market_data"

    id              = Column(BigInteger, primary_key=True, autoincrement=True)
    price_ts        = Column(TIMESTAMPTZ, nullable=False)
    asset           = Column(Text, nullable=False)
    instrument      = Column(Text, nullable=False)
    tenor           = Column(Text)
    price           = Column(Numeric(18, 6))
    bid             = Column(Numeric(18, 6))
    ask             = Column(Numeric(18, 6))
    volume          = Column(Numeric(22, 4))
    source          = Column(Text, nullable=False)
    source_series_id = Column(Text)

    __table_args__ = (
        UniqueConstraint("price_ts", "asset", "instrument", "tenor", "source",
                          name="uq_ext_market_data"),
        Index("idx_ext_asset_ts", "asset", "price_ts"),
    )


# -- Macro Events ---------------------------------------------------------------

class MacroEvent(Base):
    __tablename__ = "macro_events"

    event_id            = Column(BigInteger, primary_key=True, autoincrement=True)
    event_ts            = Column(TIMESTAMPTZ, nullable=False)
    country             = Column(CHAR(2), nullable=False, default="CA")
    event_type          = Column(Text, nullable=False)
    description         = Column(Text)
    actual              = Column(Numeric(18, 6))
    consensus           = Column(Numeric(18, 6))
    previous            = Column(Numeric(18, 6))
    surprise_std        = Column(Numeric(10, 6))
    source              = Column(Text, nullable=False, default="manual")
    kalshi_event_ticker = Column(Text, ForeignKey("events.event_ticker"))

    __table_args__ = (
        UniqueConstraint("event_ts", "country", "event_type", name="uq_macro_event"),
        Index("idx_macro_ts", "event_ts"),
    )


# -- Cross-Asset Spreads --------------------------------------------------------

class CrossAssetSpread(Base):
    __tablename__ = "cross_asset_spreads"

    id                  = Column(BigInteger, primary_key=True, autoincrement=True)
    spread_ts           = Column(TIMESTAMPTZ, nullable=False)
    market_id           = Column(Text, ForeignKey("markets.market_id"), nullable=False)
    asset               = Column(Text, nullable=False)
    kalshi_probability  = Column(Numeric(6, 4))
    trad_probability    = Column(Numeric(6, 4))
    trad_price          = Column(Numeric(18, 6))
    trad_instrument     = Column(Text)
    conversion_method   = Column(Text)
    kalshi_liquidity    = Column(Numeric(16, 2))
    notes               = Column(Text)

    __table_args__ = (
        UniqueConstraint("spread_ts", "market_id", "asset", name="uq_spread"),
        Index("idx_spread_market_ts", "market_id", "spread_ts"),
    )


# -- Ingestion Log --------------------------------------------------------------

class IngestionLog(Base):
    __tablename__ = "ingestion_log"

    id              = Column(BigInteger, primary_key=True, autoincrement=True)
    run_ts          = Column(TIMESTAMPTZ, nullable=False, default=lambda: datetime.now(timezone.utc))
    job_type        = Column(Text, nullable=False)
    target          = Column(Text)
    period_interval = Column(SmallInteger)
    rows_inserted   = Column(Integer, default=0)
    rows_skipped    = Column(Integer, default=0)
    status          = Column(Text, nullable=False, default="success")
    error_message   = Column(Text)
    cursor_end      = Column(Text)
