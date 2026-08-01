-- ============================================================
-- Kalshi Arbitrage Engine — PostgreSQL Schema
-- Run once against an empty `kalshi_arb` database.
-- ============================================================

-- Enable timezone support
SET timezone = 'UTC';

-- ── Extensions ──────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "btree_gist";   -- for exclusion constraints


-- ============================================================
-- EVENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS events (
    event_id            TEXT        PRIMARY KEY,
    event_ticker        TEXT        NOT NULL UNIQUE,
    title               TEXT        NOT NULL,
    category            TEXT,
    sub_category        TEXT,
    series_ticker       TEXT,
    event_time          TIMESTAMPTZ,           -- expected resolution time
    settlement_time     TIMESTAMPTZ,           -- actual settlement
    geographic_region   TEXT,                  -- 'Canada', 'US', 'Global', etc.
    canadian_relevance  SMALLINT    DEFAULT 0, -- 0-10 score assigned by classifier
    mutually_exclusive  BOOLEAN     DEFAULT TRUE,  -- do outcomes sum to 1?
    collectively_exhaustive BOOLEAN DEFAULT TRUE,
    status              TEXT        NOT NULL DEFAULT 'open',  -- open | settled | closed
    raw_json            JSONB,                 -- full API response snapshot
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_series   ON events(series_ticker);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
CREATE INDEX IF NOT EXISTS idx_events_canadian ON events(canadian_relevance DESC);
CREATE INDEX IF NOT EXISTS idx_events_status   ON events(status);


-- ============================================================
-- MARKETS
-- ============================================================
CREATE TABLE IF NOT EXISTS markets (
    market_id           TEXT        PRIMARY KEY,   -- Kalshi ticker (e.g. "KXBOC-25JAN-T3.00")
    ticker              TEXT        NOT NULL UNIQUE,
    event_ticker        TEXT        NOT NULL REFERENCES events(event_ticker) ON DELETE CASCADE,
    series_ticker       TEXT,
    title               TEXT        NOT NULL,
    subtitle            TEXT,
    category            TEXT,
    market_type         TEXT,                      -- binary | scalar | multivariate
    outcome_type        TEXT,                      -- yes_no | range | threshold
    status              TEXT        NOT NULL DEFAULT 'open',  -- open | settled | closed | voided
    open_time           TIMESTAMPTZ,
    close_time          TIMESTAMPTZ,
    expiration_time     TIMESTAMPTZ,
    settlement_time     TIMESTAMPTZ,
    settlement_source   TEXT,
    settlement_result   TEXT,                      -- 'yes' | 'no' | null
    settlement_value    NUMERIC(6,4),              -- 1.0 or 0.0 post settlement
    floor_strike        NUMERIC(12,6),             -- for threshold/range contracts
    cap_strike          NUMERIC(12,6),
    rules_primary       TEXT,
    rules_secondary     TEXT,
    expected_expiry_time TIMESTAMPTZ,
    raw_json            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_markets_event    ON markets(event_ticker);
CREATE INDEX IF NOT EXISTS idx_markets_status   ON markets(status);
CREATE INDEX IF NOT EXISTS idx_markets_series   ON markets(series_ticker);
CREATE INDEX IF NOT EXISTS idx_markets_close    ON markets(close_time);


-- ============================================================
-- MARKET SNAPSHOTS
-- Point-in-time price/volume observations (live + collected)
-- ============================================================
CREATE TABLE IF NOT EXISTS market_snapshots (
    id                  BIGSERIAL   PRIMARY KEY,
    snapshot_ts         TIMESTAMPTZ NOT NULL,
    market_id           TEXT        NOT NULL REFERENCES markets(market_id) ON DELETE CASCADE,
    yes_bid             NUMERIC(6,4),   -- best resting YES bid (dollars, e.g. 0.5600)
    yes_ask             NUMERIC(6,4),   -- best resting YES ask
    no_bid              NUMERIC(6,4),   -- = 1 - yes_ask
    no_ask              NUMERIC(6,4),   -- = 1 - yes_bid
    last_price          NUMERIC(6,4),   -- last traded price
    volume              NUMERIC(16,2),  -- cumulative volume (contracts)
    open_interest       NUMERIC(16,2),
    source              TEXT DEFAULT 'api_poll'  -- 'api_poll' | 'websocket' | 'candlestick'
);

CREATE INDEX IF NOT EXISTS idx_snapshots_market_ts
    ON market_snapshots(market_id, snapshot_ts DESC);
-- Hypertable candidate if TimescaleDB is available (use partitioning otherwise):
-- PARTITION BY RANGE (snapshot_ts);


-- ============================================================
-- CANDLESTICKS
-- OHLC data from Kalshi candlestick API (1m / 1h / 1d)
-- ============================================================
CREATE TABLE IF NOT EXISTS candlesticks (
    id                  BIGSERIAL   PRIMARY KEY,
    market_id           TEXT        NOT NULL REFERENCES markets(market_id) ON DELETE CASCADE,
    period_interval     SMALLINT    NOT NULL,   -- 1, 60, or 1440 minutes
    period_end_ts       TIMESTAMPTZ NOT NULL,

    -- YES bid OHLC
    yes_bid_open        NUMERIC(6,4),
    yes_bid_high        NUMERIC(6,4),
    yes_bid_low         NUMERIC(6,4),
    yes_bid_close       NUMERIC(6,4),

    -- YES ask OHLC
    yes_ask_open        NUMERIC(6,4),
    yes_ask_high        NUMERIC(6,4),
    yes_ask_low         NUMERIC(6,4),
    yes_ask_close       NUMERIC(6,4),

    -- Trade price OHLC (nullable when no trades occurred in period)
    price_open          NUMERIC(6,4),
    price_high          NUMERIC(6,4),
    price_low           NUMERIC(6,4),
    price_close         NUMERIC(6,4),
    price_mean          NUMERIC(6,4),
    price_previous      NUMERIC(6,4),

    volume              NUMERIC(16,2),
    open_interest       NUMERIC(16,2),

    UNIQUE (market_id, period_interval, period_end_ts)
);

CREATE INDEX IF NOT EXISTS idx_candles_market_period_ts
    ON candlesticks(market_id, period_interval, period_end_ts DESC);


-- ============================================================
-- ORDER BOOK SNAPSHOTS
-- Collected going forward (not available historically from Kalshi)
-- ============================================================
CREATE TABLE IF NOT EXISTS order_book_snapshots (
    id                  BIGSERIAL   PRIMARY KEY,
    snapshot_ts         TIMESTAMPTZ NOT NULL,
    market_id           TEXT        NOT NULL REFERENCES markets(market_id) ON DELETE CASCADE,
    side                CHAR(3)     NOT NULL,   -- 'yes' or 'no'
    price               NUMERIC(6,4) NOT NULL,
    quantity            NUMERIC(16,2) NOT NULL,
    level_rank          SMALLINT    NOT NULL    -- 1 = best
);

CREATE INDEX IF NOT EXISTS idx_ob_market_ts
    ON order_book_snapshots(market_id, snapshot_ts DESC);


-- ============================================================
-- PUBLIC TRADES
-- ============================================================
CREATE TABLE IF NOT EXISTS trades (
    trade_id            TEXT        PRIMARY KEY,   -- Kalshi trade ID
    trade_ts            TIMESTAMPTZ NOT NULL,
    market_id           TEXT        NOT NULL REFERENCES markets(market_id) ON DELETE CASCADE,
    price               NUMERIC(6,4) NOT NULL,    -- trade price (dollars)
    quantity            NUMERIC(16,2) NOT NULL,
    taker_side          TEXT,                      -- 'yes' | 'no' | null
    source              TEXT DEFAULT 'api'
);

CREATE INDEX IF NOT EXISTS idx_trades_market_ts
    ON trades(market_id, trade_ts DESC);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(trade_ts DESC);


-- ============================================================
-- CONTRACT RELATIONSHIPS
-- Detected logical links between markets
-- ============================================================
CREATE TABLE IF NOT EXISTS contract_relationships (
    id                  BIGSERIAL   PRIMARY KEY,
    market_id_1         TEXT        NOT NULL REFERENCES markets(market_id),
    market_id_2         TEXT        NOT NULL REFERENCES markets(market_id),
    relationship_type   TEXT        NOT NULL,
        -- 'complement'          YES_A + NO_A = $1.00
        -- 'mutually_exclusive'  cannot both resolve YES
        -- 'collectively_exhaustive' at least one must resolve YES
        -- 'subset'              B resolves YES ⟹ A resolves YES
        -- 'superset'            A resolves YES ⟹ B resolves YES
        -- 'threshold_order'     P(A) ≥ P(B) by logical constraint
        -- 'interval'            A contains B's strike range
    logical_constraint  TEXT,       -- human-readable description
    implied_inequality  TEXT,       -- e.g. 'P(A) >= P(B)'
    confidence          NUMERIC(4,3) DEFAULT 1.0,  -- 0-1; 1.0 = mathematically certain
    verified            BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (market_id_1, market_id_2, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_rel_market1 ON contract_relationships(market_id_1);
CREATE INDEX IF NOT EXISTS idx_rel_market2 ON contract_relationships(market_id_2);
CREATE INDEX IF NOT EXISTS idx_rel_type    ON contract_relationships(relationship_type);


-- ============================================================
-- ARBITRAGE OPPORTUNITIES
-- Every detected candidate — proven or potential
-- ============================================================
CREATE TABLE IF NOT EXISTS arbitrage_opportunities (
    opportunity_id      UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    strategy_type       TEXT        NOT NULL,
        -- 'yes_no_complement' | 'mutually_exclusive' | 'nested_logical' | 'combinatorial'
    classification      CHAR(1)     NOT NULL,   -- A | B | C | D | E (see docs/api_research.md)
    markets_involved    TEXT[]      NOT NULL,    -- array of market_id strings

    -- Raw prices at detection
    prices_json         JSONB       NOT NULL,   -- {market_id: {yes_bid, yes_ask, ...}}

    -- Edge calculation
    gross_edge          NUMERIC(8,4),           -- dollars per contract set
    total_fees          NUMERIC(8,4),
    estimated_slippage  NUMERIC(8,4),
    net_edge            NUMERIC(8,4),           -- gross - fees - slippage

    -- Liquidity
    max_executable_contracts NUMERIC(12,2),     -- limited by shallowest leg
    max_gross_profit    NUMERIC(12,4),          -- net_edge * max_executable_contracts
    max_net_profit      NUMERIC(12,4),

    -- Lifecycle
    status              TEXT NOT NULL DEFAULT 'open',
        -- 'open' | 'expired' | 'settled' | 'partial'
    closed_at           TIMESTAMPTZ,
    duration_seconds    NUMERIC(12,2),

    -- Settlement verification
    settlement_outcome  TEXT,                   -- what actually happened
    realised_gross_pnl  NUMERIC(12,4),
    realised_net_pnl    NUMERIC(12,4),

    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_arb_detected   ON arbitrage_opportunities(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_arb_strategy   ON arbitrage_opportunities(strategy_type);
CREATE INDEX IF NOT EXISTS idx_arb_class      ON arbitrage_opportunities(classification);
CREATE INDEX IF NOT EXISTS idx_arb_net_edge   ON arbitrage_opportunities(net_edge DESC);
CREATE INDEX IF NOT EXISTS idx_arb_status     ON arbitrage_opportunities(status);


-- ============================================================
-- BACKTEST TRADES
-- Simulated trades from the backtester
-- ============================================================
CREATE TABLE IF NOT EXISTS backtest_trades (
    id                  BIGSERIAL   PRIMARY KEY,
    opportunity_id      UUID        REFERENCES arbitrage_opportunities(opportunity_id),
    run_id              TEXT        NOT NULL,   -- backtest run identifier
    market_id           TEXT        NOT NULL REFERENCES markets(market_id),
    side                TEXT        NOT NULL,   -- 'yes' | 'no'
    action              TEXT        NOT NULL,   -- 'buy' | 'sell'
    entry_ts            TIMESTAMPTZ NOT NULL,
    exit_ts             TIMESTAMPTZ,
    entry_price         NUMERIC(6,4) NOT NULL,
    exit_price          NUMERIC(6,4),           -- settlement price (1.0 or 0.0)
    quantity            NUMERIC(12,2) NOT NULL,
    taker_fees          NUMERIC(10,4),
    maker_fees          NUMERIC(10,4),
    slippage            NUMERIC(10,4),
    gross_pnl           NUMERIC(12,4),
    net_pnl             NUMERIC(12,4),
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_bt_run       ON backtest_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_bt_market    ON backtest_trades(market_id);
CREATE INDEX IF NOT EXISTS idx_bt_entry_ts  ON backtest_trades(entry_ts DESC);


-- ============================================================
-- EXTERNAL MARKET DATA
-- Traditional financial market prices for cross-asset analysis
-- ============================================================
CREATE TABLE IF NOT EXISTS external_market_data (
    id                  BIGSERIAL   PRIMARY KEY,
    price_ts            TIMESTAMPTZ NOT NULL,
    asset               TEXT        NOT NULL,   -- 'CORRA', 'WTI', 'CADUSD', 'TSX', etc.
    instrument          TEXT        NOT NULL,   -- 'futures', 'spot', 'ois', 'swap'
    tenor               TEXT,                   -- '1M', '3M', 'overnight', etc.
    price               NUMERIC(18,6),
    bid                 NUMERIC(18,6),
    ask                 NUMERIC(18,6),
    volume              NUMERIC(22,4),
    source              TEXT        NOT NULL,   -- 'boc_valet', 'fred', 'alpha_vantage', 'eia'
    source_series_id    TEXT,                   -- original series code

    UNIQUE (price_ts, asset, instrument, tenor, source)
);

CREATE INDEX IF NOT EXISTS idx_ext_asset_ts
    ON external_market_data(asset, price_ts DESC);
CREATE INDEX IF NOT EXISTS idx_ext_source ON external_market_data(source);


-- ============================================================
-- MACRO EVENTS (Canadian + Global)
-- For event-study analysis
-- ============================================================
CREATE TABLE IF NOT EXISTS macro_events (
    event_id            BIGSERIAL   PRIMARY KEY,
    event_ts            TIMESTAMPTZ NOT NULL,   -- exact release timestamp
    country             CHAR(2)     NOT NULL DEFAULT 'CA',
    event_type          TEXT        NOT NULL,   -- 'boc_rate_decision', 'cpi', 'employment', etc.
    description         TEXT,
    actual              NUMERIC(18,6),
    consensus           NUMERIC(18,6),
    previous            NUMERIC(18,6),
    surprise            NUMERIC(18,6) GENERATED ALWAYS AS (actual - consensus) STORED,
    surprise_std        NUMERIC(10,6),           -- standardised surprise (filled by analysis)
    source              TEXT        NOT NULL DEFAULT 'manual',
    kalshi_event_ticker TEXT        REFERENCES events(event_ticker),

    UNIQUE (event_ts, country, event_type)
);

CREATE INDEX IF NOT EXISTS idx_macro_ts      ON macro_events(event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_macro_country ON macro_events(country);
CREATE INDEX IF NOT EXISTS idx_macro_type    ON macro_events(event_type);


-- ============================================================
-- CROSS-ASSET SPREADS
-- Kalshi probability vs. traditional-market implied probability
-- ============================================================
CREATE TABLE IF NOT EXISTS cross_asset_spreads (
    id                  BIGSERIAL   PRIMARY KEY,
    spread_ts           TIMESTAMPTZ NOT NULL,
    market_id           TEXT        NOT NULL REFERENCES markets(market_id),
    asset               TEXT        NOT NULL,   -- paired traditional asset
    kalshi_probability  NUMERIC(6,4),           -- midpoint (yes_bid+yes_ask)/2
    trad_probability    NUMERIC(6,4),           -- derived from traditional market
    spread              NUMERIC(6,4) GENERATED ALWAYS AS
                            (kalshi_probability - trad_probability) STORED,
    trad_price          NUMERIC(18,6),          -- raw price of traditional instrument
    trad_instrument     TEXT,
    conversion_method   TEXT,                   -- how trad_probability was derived
    kalshi_liquidity    NUMERIC(16,2),          -- open interest at snapshot
    notes               TEXT,

    UNIQUE (spread_ts, market_id, asset)
);

CREATE INDEX IF NOT EXISTS idx_spread_market_ts
    ON cross_asset_spreads(market_id, spread_ts DESC);
CREATE INDEX IF NOT EXISTS idx_spread_asset ON cross_asset_spreads(asset);


-- ============================================================
-- INGESTION LOG
-- Track API data-pull runs to avoid redundant re-fetches
-- ============================================================
CREATE TABLE IF NOT EXISTS ingestion_log (
    id                  BIGSERIAL   PRIMARY KEY,
    run_ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    job_type            TEXT        NOT NULL,   -- 'historical_markets', 'candlesticks', 'trades', etc.
    target              TEXT,                   -- ticker or category scoped to
    period_interval     SMALLINT,
    rows_inserted       INTEGER     DEFAULT 0,
    rows_skipped        INTEGER     DEFAULT 0,
    status              TEXT        NOT NULL DEFAULT 'success', -- 'success' | 'partial' | 'error'
    error_message       TEXT,
    cursor_end          TEXT        -- last pagination cursor for resumability
);

CREATE INDEX IF NOT EXISTS idx_ingest_job ON ingestion_log(job_type, run_ts DESC);


-- ============================================================
-- HELPER VIEWS
-- ============================================================

-- Latest snapshot per market
CREATE OR REPLACE VIEW v_latest_snapshots AS
SELECT DISTINCT ON (market_id)
    market_id, snapshot_ts, yes_bid, yes_ask, no_bid, no_ask, last_price, volume, open_interest
FROM market_snapshots
ORDER BY market_id, snapshot_ts DESC;

-- Complement arbitrage quick view (YES bid + YES ask should sum < 1.00)
-- For a binary contract: buying YES at ask_A and NO at ask_B costs ask_A + ask_B.
-- If that < 1.00, gross edge exists.
CREATE OR REPLACE VIEW v_complement_candidates AS
SELECT
    m.market_id,
    m.ticker,
    m.event_ticker,
    s.yes_bid,
    s.yes_ask,
    s.no_bid,
    s.no_ask,
    (s.yes_ask + (1.0 - s.yes_bid)) AS cost_buy_yes_sell_no,
    (s.no_ask  + (1.0 - s.no_bid))  AS cost_buy_no_sell_yes,
    -- Cost to own both sides (buy YES ask + buy NO ask)
    (s.yes_ask + s.no_ask)           AS cost_both_sides,
    -- Gross edge if cost_both_sides < 1.00
    (1.0 - s.yes_ask - s.no_ask)    AS gross_edge_both,
    s.snapshot_ts
FROM markets m
JOIN v_latest_snapshots s USING (market_id)
WHERE m.status IN ('open','active')
  AND s.yes_ask IS NOT NULL
  AND s.no_ask IS NOT NULL
  AND (1.0 - s.yes_ask - s.no_ask) > 0;

-- Active open arbitrage opportunities summary
CREATE OR REPLACE VIEW v_live_opportunities AS
SELECT
    a.opportunity_id,
    a.detected_at,
    a.strategy_type,
    a.classification,
    a.markets_involved,
    a.gross_edge,
    a.total_fees,
    a.net_edge,
    a.max_net_profit,
    a.max_executable_contracts,
    EXTRACT(EPOCH FROM (NOW() - a.detected_at)) AS age_seconds
FROM arbitrage_opportunities a
WHERE a.status = 'open'
ORDER BY a.net_edge DESC;


-- ============================================================
-- OPERATIONS & RESEARCH TABLES
-- ============================================================

-- Data quality violations, written by analysis/integrity_audit.py.
-- One row per (check, run) so p10_system can show a time series of health.
CREATE TABLE IF NOT EXISTS data_quality_log (
    id          BIGSERIAL   PRIMARY KEY,
    check_name  TEXT        NOT NULL,
    severity    TEXT        NOT NULL DEFAULT 'info',
    count       BIGINT      NOT NULL DEFAULT 0,
    details     JSONB,
    run_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_dq_severity CHECK (severity IN ('ok', 'info', 'warning', 'error', 'critical'))
);

CREATE INDEX IF NOT EXISTS idx_dq_run       ON data_quality_log(run_at DESC);
CREATE INDEX IF NOT EXISTS idx_dq_check_run ON data_quality_log(check_name, run_at DESC);
CREATE INDEX IF NOT EXISTS idx_dq_severity  ON data_quality_log(severity, run_at DESC)
    WHERE severity IN ('warning', 'error', 'critical');

-- Backtest run summaries. backtest_trades holds the per-trade detail and
-- references arbitrage_opportunities; this table holds one row per run so the
-- dashboard can compare strategies without re-aggregating the trade log.
CREATE TABLE IF NOT EXISTS backtest_results (
    id            BIGSERIAL   PRIMARY KEY,
    strategy      TEXT        NOT NULL,
    start_date    DATE,
    end_date      DATE,
    initial_capital NUMERIC(14,2),
    total_return  NUMERIC(10,4),
    cagr          NUMERIC(10,4),
    sharpe        NUMERIC(10,4),
    sortino       NUMERIC(10,4),
    calmar        NUMERIC(10,4),
    max_dd        NUMERIC(10,4),
    win_rate      NUMERIC(6,4),
    profit_factor NUMERIC(10,4),
    n_trades      INTEGER     NOT NULL DEFAULT 0,
    params        JSONB,
    run_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bt_results_strategy ON backtest_results(strategy, run_at DESC);
CREATE INDEX IF NOT EXISTS idx_bt_results_run      ON backtest_results(run_at DESC);

-- Live cross-asset macro snapshots (BOC VALET + yfinance), written by
-- cross_asset/live_feed.py. Long format: one row per (timestamp, metric).
CREATE TABLE IF NOT EXISTS cross_asset_live (
    id          BIGSERIAL   PRIMARY KEY,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metric      TEXT        NOT NULL,
    value       DOUBLE PRECISION,
    UNIQUE (fetched_at, metric)
);

CREATE INDEX IF NOT EXISTS idx_cal_metric_ts ON cross_asset_live (metric, fetched_at DESC);


-- ============================================================
-- CROSS-ASSET MODEL SPREADS
-- Output of cross_asset/arb_scanner.py: Kalshi price vs a *model*-derived
-- probability, one row per (market, model, scan).
--
-- Distinct from cross_asset_spreads above, which pairs a Kalshi market with a
-- specific traditional *instrument*. Here the counterparty is a model, so the
-- row is keyed by model_name and carries the model's inputs for auditability.
--
-- These are RELATIVE VALUE signals, not arbitrage: a model disagreeing with
-- the market is not a riskless edge. price_source records whether the Kalshi
-- side came from a real quote ('l2_book') or a last trade ('last_trade');
-- last trades are not executable prices.
-- ============================================================
CREATE TABLE IF NOT EXISTS cross_asset_model_spreads (
    id              BIGSERIAL   PRIMARY KEY,
    scanned_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ticker          TEXT        NOT NULL,
    series_ticker   TEXT,
    title           TEXT,
    -- Kalshi side
    kalshi_yes_bid  NUMERIC(6,4),
    kalshi_yes_ask  NUMERIC(6,4),
    kalshi_midpoint NUMERIC(6,4),
    kalshi_volume   BIGINT,
    -- Model side
    model_name      TEXT,
    model_prob      NUMERIC(6,4),
    model_inputs    JSONB,
    -- Spread
    edge_pct        NUMERIC(8,4),   -- (kalshi_midpoint - model_prob) * 100
    direction       TEXT,           -- 'SELL_YES' | 'BUY_YES' | 'FLAT'
    -- Signal
    is_flagged      BOOLEAN     NOT NULL DEFAULT FALSE,
    price_source    TEXT,           -- 'l2_book' | 'last_trade'
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_cams_ticker     ON cross_asset_model_spreads(ticker);
CREATE INDEX IF NOT EXISTS idx_cams_scanned_at ON cross_asset_model_spreads(scanned_at DESC);
CREATE INDEX IF NOT EXISTS idx_cams_model      ON cross_asset_model_spreads(model_name, scanned_at DESC);
CREATE INDEX IF NOT EXISTS idx_cams_flagged    ON cross_asset_model_spreads(is_flagged, scanned_at DESC)
    WHERE is_flagged;
