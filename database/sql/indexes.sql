-- =============================================================================
-- database/sql/indexes.sql
-- Kalshi Arbitrage Engine â€” Index Definitions
-- =============================================================================
-- All performance-critical indexes for the production schema.
-- Create with CONCURRENTLY in production to avoid table locks.
-- Verify with: SELECT * FROM pg_indexes WHERE schemaname = 'public';
-- =============================================================================


-- ---------------------------------------------------------------------------
-- markets
-- ---------------------------------------------------------------------------

-- Primary lookup and filtering
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_markets_status
    ON markets (status);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_markets_event_ticker
    ON markets (event_ticker);

-- Composite: keyset pagination in relationship_runner (status + event_ticker)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_markets_status_event_ticker
    ON markets (status, event_ticker, floor_strike NULLS LAST, ticker);

-- Settlement queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_markets_settlement
    ON markets (status, settlement_value) WHERE status = 'settled';

-- Close time lookups
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_markets_close_time
    ON markets (close_time);


-- ---------------------------------------------------------------------------
-- events
-- ---------------------------------------------------------------------------

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_events_event_ticker
    ON events (event_ticker);

-- Canadian filter. Canadian relevance is scored on the EVENT, not the market
-- (every market under a BoC event is Canadian), so the index lives here and
-- markets inherit it through the event_ticker join. Partial: the vast
-- majority of events score 0 and never need to be visited.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_events_canadian_relevance
    ON events (canadian_relevance, event_ticker)
    WHERE canadian_relevance >= 1;


-- ---------------------------------------------------------------------------
-- trades  (historical raw trade data)
-- ---------------------------------------------------------------------------

-- Time-series queries per market
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_trades_market_ts
    ON trades (market_id, trade_ts);

-- All trades within time range (ingestion pipeline)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_trades_ts
    ON trades (trade_ts);


-- ---------------------------------------------------------------------------
-- candlesticks
-- ---------------------------------------------------------------------------

-- Per-market OHLC lookups (most common query pattern)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_candlesticks_market_period
    ON candlesticks (market_id, period_interval, period_end_ts);

-- Global time-range scan
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_candlesticks_period_end
    ON candlesticks (period_end_ts);


-- ---------------------------------------------------------------------------
-- contract_relationships
-- ---------------------------------------------------------------------------

-- Standard bidirectional lookup (m1 or m2 = target market)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rels_market_id_1
    ON contract_relationships (market_id_1);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rels_market_id_2
    ON contract_relationships (market_id_2);

-- Relationship type filter
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rels_type
    ON contract_relationships (relationship_type);

-- Non-complement relationships (most used in arb scanning)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rels_non_complement
    ON contract_relationships (market_id_1, market_id_2)
    WHERE relationship_type != 'complement';

-- High-confidence pairs (confidence >= 0.9)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rels_high_confidence
    ON contract_relationships (confidence DESC)
    WHERE confidence >= 0.90;

-- Unique constraint index (prevent duplicate pairs)
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_rels_unique_pair
    ON contract_relationships (
        LEAST(market_id_1, market_id_2),
        GREATEST(market_id_1, market_id_2),
        relationship_type
    );


-- ---------------------------------------------------------------------------
-- arbitrage_opportunities
-- ---------------------------------------------------------------------------

-- Primary lookups
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_arb_detected_at
    ON arbitrage_opportunities (detected_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_arb_strategy_type
    ON arbitrage_opportunities (strategy_type);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_arb_status
    ON arbitrage_opportunities (status);

-- Best opportunities (high net_edge)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_arb_net_edge_desc
    ON arbitrage_opportunities (net_edge DESC)
    WHERE net_edge > 0;

-- Composite for dashboard queries (strategy + date + edge)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_arb_strategy_date
    ON arbitrage_opportunities (strategy_type, detected_at DESC);

-- Market membership â€” GIN index for the ARRAY column (markets_involved)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_arb_markets_gin
    ON arbitrage_opportunities USING GIN (markets_involved);


-- ---------------------------------------------------------------------------
-- l2_snapshots
-- ---------------------------------------------------------------------------

-- Primary: per-market time-series
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_l2_market_time
    ON l2_snapshots (market_id, snapped_at DESC);

-- Global recency queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_l2_snapped_at
    ON l2_snapshots (snapped_at DESC);

-- Sequence number gap detection
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_l2_market_seq
    ON l2_snapshots (market_id, sequence);


-- ---------------------------------------------------------------------------
-- market_snapshots  (mid-price snapshots used by empirical scanner)
-- ---------------------------------------------------------------------------

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_mktsnap_market_ts
    ON market_snapshots (market_id, snapshot_ts);

-- Volume ranking. Cumulative volume is a snapshot attribute, not a market
-- attribute, so "most active markets" is answered from the latest snapshot
-- per market rather than from markets directly.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_mktsnap_volume_desc
    ON market_snapshots (volume DESC NULLS LAST, market_id);


-- ---------------------------------------------------------------------------
-- backtest_trades
-- ---------------------------------------------------------------------------

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bt_market_id
    ON backtest_trades (market_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bt_entry_ts
    ON backtest_trades (entry_ts);

-- Equity-curve reconstruction walks one run's trades in entry order.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bt_run_entry
    ON backtest_trades (run_id, entry_ts);

-- Attribution back to the opportunity that generated the trade.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bt_opportunity
    ON backtest_trades (opportunity_id);


-- ---------------------------------------------------------------------------
-- Verification query â€” confirm all indexes exist
-- ---------------------------------------------------------------------------
-- Run this after applying indexes to verify:
--
-- SELECT
--     indexname,
--     tablename,
--     pg_size_pretty(pg_relation_size(indexname::REGCLASS)) AS index_size
-- FROM pg_indexes
-- WHERE schemaname = 'public'
-- ORDER BY tablename, indexname;
