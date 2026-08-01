-- database/views.sql
-- =====================
-- Reusable views and materialized views. Views avoid Streamlit re-writing
-- the same multi-join SQL on every page load; materialized views cache
-- expensive aggregates (refresh via cron / run.py, not on every request).

-- ------------------------------------------------------------------------
-- 1. v_canadian_markets - Canadian-relevant markets with event context
-- ------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_canadian_markets AS
SELECT
    m.market_id,
    m.ticker,
    m.title,
    m.status,
    m.close_time,
    e.event_ticker,
    e.canadian_relevance,
    e.category
FROM markets m
JOIN events e ON e.event_ticker = m.event_ticker
WHERE e.canadian_relevance >= 1;

-- ------------------------------------------------------------------------
-- 2. v_relationship_summary - per-market relationship counts
-- ------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_relationship_summary AS
SELECT
    market_id,
    COUNT(*)                                                AS relationship_count,
    COUNT(*) FILTER (WHERE relationship_type = 'complement') AS complement_count,
    COUNT(*) FILTER (WHERE relationship_type = 'mutually_exclusive') AS me_count,
    COUNT(*) FILTER (WHERE relationship_type = 'collectively_exhaustive') AS ce_count
FROM (
    SELECT market_id_1 AS market_id, relationship_type FROM contract_relationships
    UNION ALL
    SELECT market_id_2 AS market_id, relationship_type FROM contract_relationships
) unpacked
GROUP BY market_id;

-- ------------------------------------------------------------------------
-- 3. v_open_arbitrage - currently-open, positive-net-edge opportunities
--    (the query the live dashboard should hit, not raw table scans)
-- ------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_open_arbitrage AS
SELECT
    opportunity_id,
    detected_at,
    strategy_type,
    classification,
    markets_involved,
    net_edge,
    max_executable_contracts,
    max_net_profit
FROM arbitrage_opportunities
WHERE status = 'open'
  AND net_edge > 0
ORDER BY max_net_profit DESC NULLS LAST;

-- ------------------------------------------------------------------------
-- 4. mv_daily_market_stats - materialized: one row per market per day
--    Refresh with: REFRESH MATERIALIZED VIEW mv_daily_market_stats;
-- ------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_daily_market_stats AS
SELECT
    market_id,
    date_trunc('day', trade_ts)   AS day,
    COUNT(*)                      AS trade_count,
    SUM(quantity)                 AS total_volume,
    ROUND(SUM(price * quantity) / NULLIF(SUM(quantity), 0), 4) AS vwap,
    MIN(price)                    AS low,
    MAX(price)                    AS high
FROM trades
GROUP BY market_id, date_trunc('day', trade_ts)
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_daily_market_stats
    ON mv_daily_market_stats(market_id, day);

-- To populate/refresh (run after ingestion, not on page load):
--   REFRESH MATERIALIZED VIEW CONCURRENTLY mv_daily_market_stats;

-- ------------------------------------------------------------------------
-- 5. mv_table_sizes - cheap system-page lookup (pg_catalog is itself slow
--    on huge catalogs; caching avoids hitting it on every dashboard refresh)
-- ------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_table_sizes AS
SELECT
    schemaname,
    relname                                   AS table_name,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
    pg_total_relation_size(relid)             AS total_size_bytes,
    n_live_tup                                AS row_estimate
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC;

-- ------------------------------------------------------------------------
-- 6. mv_market_summary - one row per market: lifetime activity + latest book
--    Backs the market browser (p03) and overview KPIs without re-joining
--    trades and snapshots on every page load.
-- ------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_market_summary AS
SELECT
    m.market_id,
    m.ticker,
    m.title,
    m.status,
    m.category,
    m.event_ticker,
    m.close_time,
    COALESCE(e.canadian_relevance, 0)          AS canadian_relevance,
    (COALESCE(e.canadian_relevance, 0) >= 1)   AS is_canadian,
    COALESCE(t.trade_count, 0)                 AS trade_count,
    COALESCE(t.total_volume, 0)                AS total_volume,
    t.last_trade_ts,
    t.vwap,
    s.yes_bid,
    s.yes_ask,
    s.open_interest,
    (s.yes_ask - s.yes_bid)                    AS avg_spread,
    s.snapshot_ts                              AS last_snapshot_ts
FROM markets m
LEFT JOIN events e ON e.event_ticker = m.event_ticker
LEFT JOIN (
    SELECT
        market_id,
        COUNT(*)                                                  AS trade_count,
        SUM(quantity)                                             AS total_volume,
        MAX(trade_ts)                                             AS last_trade_ts,
        ROUND(SUM(price * quantity) / NULLIF(SUM(quantity), 0), 4) AS vwap
    FROM trades
    GROUP BY market_id
) t ON t.market_id = m.market_id
LEFT JOIN v_latest_snapshots s ON s.market_id = m.market_id
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_market_summary
    ON mv_market_summary(market_id);
CREATE INDEX IF NOT EXISTS idx_mv_market_summary_cad
    ON mv_market_summary(is_canadian, total_volume DESC);

-- ------------------------------------------------------------------------
-- 7. mv_daily_arb_stats - per day: how many opportunities, how good, how
--    much money was theoretically on the table. Drives the p05 timeline.
-- ------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_daily_arb_stats AS
SELECT
    date_trunc('day', detected_at)                    AS day,
    classification,
    strategy_type,
    COUNT(*)                                          AS opp_count,
    COUNT(*) FILTER (WHERE net_edge > 0)              AS positive_opp_count,
    ROUND(AVG(net_edge), 4)                           AS avg_edge,
    ROUND(MAX(net_edge), 4)                           AS max_edge,
    ROUND(SUM(max_net_profit), 4)                     AS total_pnl,
    ROUND(AVG(duration_seconds), 2)                   AS avg_duration_seconds
FROM arbitrage_opportunities
GROUP BY date_trunc('day', detected_at), classification, strategy_type
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_daily_arb_stats
    ON mv_daily_arb_stats(day, classification, strategy_type);

-- ------------------------------------------------------------------------
-- 8. mv_canadian_markets - Canadian-classified markets with latest prices.
--    Materialized because p08 filters and sorts the whole set on load.
-- ------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_canadian_markets AS
SELECT
    m.market_id,
    m.ticker,
    m.title,
    m.status,
    m.category,
    m.close_time,
    e.event_ticker,
    e.canadian_relevance,
    s.yes_bid,
    s.yes_ask,
    s.last_price,
    s.volume,
    s.open_interest,
    s.snapshot_ts
FROM markets m
JOIN events e ON e.event_ticker = m.event_ticker
LEFT JOIN v_latest_snapshots s ON s.market_id = m.market_id
WHERE e.canadian_relevance >= 1
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_canadian_markets
    ON mv_canadian_markets(market_id);

-- ------------------------------------------------------------------------
-- 9. mv_l2_quality - per ticker L2 feed health: coverage, spread, staleness
--    and sequence gaps. Feeds the data-quality panel on p10.
-- ------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_l2_quality AS
SELECT
    l.market_id,
    m.ticker,
    COUNT(*)                                             AS snapshot_count,
    ROUND(AVG(l.yes_best_ask - l.yes_best_bid), 4)       AS avg_spread,
    MIN(l.snapped_at)                                    AS first_update,
    MAX(l.snapped_at)                                    AS last_update,
    EXTRACT(EPOCH FROM (NOW() - MAX(l.snapped_at)))      AS staleness_seconds,
    MIN(l.sequence)                                      AS min_sequence,
    MAX(l.sequence)                                      AS max_sequence,
    -- A contiguous feed has (max - min + 1) distinct sequences. Anything
    -- less means we dropped messages between snapshots.
    GREATEST(
        (MAX(l.sequence) - MIN(l.sequence) + 1) - COUNT(DISTINCT l.sequence),
        0
    )                                                    AS sequence_gaps,
    ROUND(AVG(l.yes_bid_levels + l.yes_ask_levels), 2)   AS avg_yes_levels
FROM l2_snapshots l
JOIN markets m ON m.market_id = l.market_id
GROUP BY l.market_id, m.ticker
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_l2_quality
    ON mv_l2_quality(market_id);
