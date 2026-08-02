-- =============================================================================
-- database/sql/performance_queries.sql
-- Kalshi Arbitrage Engine — System Performance & Operational Metrics
-- =============================================================================
-- Demonstrates: operational analytics, ingestion monitoring, latency tracking,
-- data quality scoring, and system health time-series.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Ingestion pipeline health — snapshots per minute over last 24 hours
-- ---------------------------------------------------------------------------
WITH time_buckets AS (
    SELECT
        DATE_TRUNC('minute', snapped_at) AS minute,
        COUNT(DISTINCT market_id)         AS markets_snapped,
        COUNT(*)                          AS total_snapshots
    FROM l2_snapshots
    WHERE snapped_at >= NOW() - INTERVAL '24 hours'
    GROUP BY 1
)
SELECT
    minute,
    markets_snapped,
    total_snapshots,
    ROUND(AVG(total_snapshots) OVER (
        ORDER BY minute
        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
    )::NUMERIC, 1)                        AS rolling_5m_avg,
    -- flag minutes with unusual activity (below 20% of rolling avg)
    CASE
        WHEN total_snapshots < 0.2 * AVG(total_snapshots) OVER (
            ORDER BY minute ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
        ) THEN TRUE
        ELSE FALSE
    END                                   AS possible_gap
FROM time_buckets
ORDER BY minute DESC
LIMIT 1440;  -- last 24 hours at 1-min granularity


-- ---------------------------------------------------------------------------
-- 2. Scanner latency histogram — how quickly are opportunities detected?
-- ---------------------------------------------------------------------------
WITH latency_data AS (
    SELECT
        strategy_type,
        EXTRACT(EPOCH FROM (
            detected_at - LAG(detected_at) OVER (
                PARTITION BY strategy_type
                ORDER BY detected_at
            )
        )) * 1000 AS gap_ms   -- convert to milliseconds
    FROM arbitrage_opportunities
    WHERE detected_at >= NOW() - INTERVAL '7 days'
),
bucketed AS (
    SELECT
        strategy_type,
        CASE
            WHEN gap_ms < 100   THEN '< 100ms'
            WHEN gap_ms < 500   THEN '100-500ms'
            WHEN gap_ms < 1000  THEN '0.5-1s'
            WHEN gap_ms < 5000  THEN '1-5s'
            WHEN gap_ms < 30000 THEN '5-30s'
            ELSE                     '> 30s'
        END AS latency_bucket,
        CASE
            WHEN gap_ms < 100   THEN 1
            WHEN gap_ms < 500   THEN 2
            WHEN gap_ms < 1000  THEN 3
            WHEN gap_ms < 5000  THEN 4
            WHEN gap_ms < 30000 THEN 5
            ELSE                     6
        END AS bucket_order
    FROM latency_data
    WHERE gap_ms IS NOT NULL AND gap_ms > 0
)
SELECT
    strategy_type,
    latency_bucket,
    COUNT(*)                                         AS count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY strategy_type)::NUMERIC, 2)
                                                     AS pct_of_strategy
FROM bucketed
GROUP BY strategy_type, latency_bucket, bucket_order
ORDER BY strategy_type, bucket_order;


-- ---------------------------------------------------------------------------
-- 3. Data quality score per market (composite)
-- ---------------------------------------------------------------------------
WITH quality_metrics AS (
    SELECT
        m.market_id,
        m.title,
        m.category,
        m.status,
        -- Snapshot recency (within last hour = 10pts)
        CASE WHEN MAX(s.snapped_at) >= NOW() - INTERVAL '1 hour'  THEN 10
             WHEN MAX(s.snapped_at) >= NOW() - INTERVAL '6 hours' THEN 6
             WHEN MAX(s.snapped_at) >= NOW() - INTERVAL '24 hours' THEN 3
             ELSE 0
        END AS recency_score,
        -- Snapshot density (>10 per hour = 10pts)
        CASE WHEN COUNT(s.snapped_at) / GREATEST(
                EXTRACT(EPOCH FROM (MAX(s.snapped_at) - MIN(s.snapped_at))) / 3600.0,
                1
             ) >= 10 THEN 10
             WHEN COUNT(s.snapped_at) / GREATEST(
                EXTRACT(EPOCH FROM (MAX(s.snapped_at) - MIN(s.snapped_at))) / 3600.0,
                1
             ) >= 2  THEN 5
             ELSE 0
        END AS density_score,
        -- Quote completeness (both sides quoted on every snapshot).
        -- Quotes live on l2_snapshots, not on markets: the markets table holds
        -- contract metadata only and has no yes_bid / yes_ask columns.
        CASE WHEN COUNT(s.snapshot_id) > 0
                  AND AVG(CASE WHEN s.yes_best_bid IS NOT NULL
                                AND s.yes_best_ask IS NOT NULL
                               THEN 1 ELSE 0 END) = 1
             THEN 10 ELSE 0
        END AS quote_completeness_score,
        -- Spread sanity (average spread < 20c = 10pts)
        CASE WHEN AVG(s.yes_best_ask - s.yes_best_bid) < 0.20 THEN 10
             WHEN AVG(s.yes_best_ask - s.yes_best_bid) IS NOT NULL THEN 5
             ELSE 0
        END AS spread_sanity_score,
        -- Historical trade data present. Counted in a scalar subquery rather
        -- than a second LEFT JOIN: joining both children of markets would
        -- multiply the snapshot rows by the trade rows and inflate every
        -- aggregate above.
        (SELECT COUNT(*) FROM trades t WHERE t.market_id = m.market_id) AS trade_count
    FROM markets m
    LEFT JOIN l2_snapshots s ON s.market_id = m.market_id
    WHERE m.status = 'active'
    GROUP BY m.market_id, m.title, m.category, m.status
)
SELECT
    market_id,
    title,
    category,
    recency_score,
    density_score,
    quote_completeness_score,
    spread_sanity_score,
    CASE WHEN trade_count >= 10 THEN 10
         WHEN trade_count >= 1  THEN 5
         ELSE 0
    END AS historical_data_score,
    -- Total quality score (0-50)
    recency_score + density_score + quote_completeness_score + spread_sanity_score +
    CASE WHEN trade_count >= 10 THEN 10 WHEN trade_count >= 1 THEN 5 ELSE 0 END
                                                  AS total_quality_score,
    DENSE_RANK() OVER (ORDER BY
        recency_score + density_score + quote_completeness_score + spread_sanity_score +
        CASE WHEN trade_count >= 10 THEN 10 WHEN trade_count >= 1 THEN 5 ELSE 0 END
    DESC)                                         AS quality_rank
FROM quality_metrics
ORDER BY total_quality_score DESC;


-- ---------------------------------------------------------------------------
-- 4. Relationship detection performance — confidence over time
-- ---------------------------------------------------------------------------
WITH confidence_cohorts AS (
    SELECT
        relationship_type,
        -- contract_relationships timestamps its rows as created_at; only
        -- arbitrage_opportunities has a detected_at.
        DATE_TRUNC('week', created_at)            AS week,
        COUNT(*)                                  AS new_relationships,
        ROUND(AVG(confidence)::NUMERIC, 4)        AS avg_confidence,
        ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY confidence)::NUMERIC, 4) AS p25_conf,
        ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY confidence)::NUMERIC, 4) AS p75_conf,
        COUNT(*) FILTER (WHERE confidence >= 0.90) AS high_conf_count,
        COUNT(*) FILTER (WHERE confidence < 0.60)  AS low_conf_count
    FROM contract_relationships
    WHERE created_at IS NOT NULL
    GROUP BY relationship_type, DATE_TRUNC('week', created_at)
)
SELECT
    *,
    SUM(new_relationships) OVER (
        PARTITION BY relationship_type
        ORDER BY week
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_count
FROM confidence_cohorts
ORDER BY relationship_type, week;


-- ---------------------------------------------------------------------------
-- 5. Opportunity → backtest execution rate
-- ---------------------------------------------------------------------------
-- Measures what fraction of detected arb opps were actually backtested.
-- ---------------------------------------------------------------------------
WITH opp_stats AS (
    SELECT
        strategy_type,
        DATE_TRUNC('day', detected_at) AS day,
        COUNT(DISTINCT opportunity_id) AS opps_detected
    FROM arbitrage_opportunities
    GROUP BY 1, 2
),
bt_stats AS (
    -- backtest_trades stores run_id, entry_ts and id; the strategy name lives
    -- on backtest_results and is joined in via run_id.
    SELECT
        br.strategy,
        DATE_TRUNC('day', bt.entry_ts) AS day,
        COUNT(DISTINCT bt.id)          AS trades_executed
    FROM backtest_trades bt
    LEFT JOIN backtest_results br ON br.id::TEXT = bt.run_id
    GROUP BY 1, 2
)
SELECT
    o.strategy_type,
    o.day,
    o.opps_detected,
    COALESCE(b.trades_executed, 0)              AS trades_executed,
    ROUND(100.0 * COALESCE(b.trades_executed, 0)
          / NULLIF(o.opps_detected, 0)::NUMERIC, 2) AS execution_rate_pct,
    ROUND(AVG(COALESCE(b.trades_executed, 0)) OVER (
        PARTITION BY o.strategy_type
        ORDER BY o.day
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    )::NUMERIC, 2)                              AS rolling_7d_avg_trades
FROM opp_stats o
LEFT JOIN bt_stats b ON b.strategy = o.strategy_type AND b.day = o.day
ORDER BY o.strategy_type, o.day;


-- ---------------------------------------------------------------------------
-- 6. System capacity metrics — table row counts and estimated sizes
-- ---------------------------------------------------------------------------
-- pg_stat_user_tables exposes the table name as relname (tablename belongs to
-- pg_tables) and carries a relid OID, which sizes should be measured from.
SELECT
    schemaname,
    relname                                  AS tablename,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
    pg_size_pretty(pg_relation_size(relid))       AS table_size,
    pg_size_pretty(pg_indexes_size(relid))        AS index_size,
    n_live_tup                                                               AS approx_rows,
    n_dead_tup                                                               AS dead_rows,
    last_vacuum,
    last_analyze,
    ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0)::NUMERIC, 2) AS bloat_pct
FROM pg_stat_user_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(relid) DESC;


-- ---------------------------------------------------------------------------
-- 7. Slow query patterns — which tables generate the most sequential scans?
-- ---------------------------------------------------------------------------
SELECT
    relname AS table_name,
    seq_scan,
    seq_tup_read,
    idx_scan,
    idx_tup_fetch,
    ROUND((seq_tup_read::FLOAT / NULLIF(seq_scan, 0))::NUMERIC, 0) AS avg_rows_per_seq_scan,
    CASE
        WHEN seq_scan > 10 AND idx_scan < seq_scan THEN 'MISSING INDEX CANDIDATE'
        WHEN seq_scan > 0 AND idx_scan = 0        THEN 'NO INDEX USED'
        ELSE 'OK'
    END AS index_health
FROM pg_stat_user_tables
WHERE schemaname = 'public'
ORDER BY seq_scan DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- 8. L2 cache miss / gap rate — how often do we lose book continuity?
-- ---------------------------------------------------------------------------
WITH sequence_data AS (
    SELECT
        market_id,
        sequence,
        snapped_at,
        LAG(sequence) OVER (PARTITION BY market_id ORDER BY snapped_at) AS prev_seq
    FROM l2_snapshots
    WHERE sequence IS NOT NULL
),
gaps_flagged AS (
    SELECT
        market_id,
        snapped_at,
        sequence,
        prev_seq,
        sequence - prev_seq AS seq_delta,
        CASE WHEN sequence - prev_seq > 1 THEN 1 ELSE 0 END AS is_gap
    FROM sequence_data
    WHERE prev_seq IS NOT NULL
)
SELECT
    market_id,
    COUNT(*)                                    AS total_sequence_steps,
    SUM(is_gap)                                 AS gap_count,
    ROUND(100.0 * SUM(is_gap) / COUNT(*)::NUMERIC, 2) AS gap_rate_pct,
    SUM(seq_delta - 1) FILTER (WHERE is_gap = 1) AS total_missed_msgs,
    ROUND(AVG(seq_delta - 1) FILTER (WHERE is_gap = 1)::NUMERIC, 1)
                                                AS avg_msgs_per_gap
FROM gaps_flagged
GROUP BY market_id
HAVING SUM(is_gap) > 0
ORDER BY gap_rate_pct DESC
LIMIT 50;
