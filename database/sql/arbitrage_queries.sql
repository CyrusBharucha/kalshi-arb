-- =============================================================================
-- database/sql/arbitrage_queries.sql
-- Kalshi Arbitrage Engine — Arbitrage Analytics
-- =============================================================================
-- Demonstrates: CTEs, window functions, LAG/LEAD, time-series aggregation,
-- ranking, rolling statistics, and opportunity lifecycle analysis.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Best arbitrage opportunities by net edge (with market details)
-- ---------------------------------------------------------------------------
SELECT
    ao.opportunity_id,
    ao.strategy_type,
    ao.classification,
    ao.detected_at,
    ao.closed_at,
    EXTRACT(EPOCH FROM (ao.closed_at - ao.detected_at)) AS duration_seconds,
    ao.gross_edge,
    ao.total_fees,
    ao.net_edge,
    ao.max_executable_contracts,
    ao.net_edge * ao.max_executable_contracts                       AS max_net_profit,
    ao.markets_involved,
    ao.status
FROM arbitrage_opportunities ao
WHERE ao.net_edge > 0
ORDER BY ao.net_edge DESC
LIMIT 100;


-- ---------------------------------------------------------------------------
-- 2. Opportunity frequency by strategy and day of week
--    (window: last 90 days)
-- ---------------------------------------------------------------------------
WITH daily_counts AS (
    SELECT
        strategy_type,
        DATE_TRUNC('day', detected_at) AS day,
        TO_CHAR(detected_at, 'Day')    AS dow_label,
        EXTRACT(DOW FROM detected_at)  AS dow_num,
        COUNT(*)                       AS n_opps,
        AVG(net_edge)                  AS avg_net_edge,
        MAX(net_edge)                  AS max_net_edge,
        SUM(net_edge * max_executable_contracts) AS total_value
    FROM arbitrage_opportunities
    WHERE detected_at >= NOW() - INTERVAL '90 days'
    GROUP BY 1, 2, 3, 4
)
SELECT
    strategy_type,
    dow_num,
    TRIM(dow_label)            AS day_of_week,
    SUM(n_opps)                AS total_opportunities,
    ROUND(AVG(n_opps), 2)     AS avg_per_day,
    ROUND(AVG(avg_net_edge)::NUMERIC, 5) AS avg_net_edge,
    ROUND(MAX(max_net_edge)::NUMERIC, 5) AS max_net_edge,
    ROUND(SUM(total_value)::NUMERIC, 4)  AS total_executable_value
FROM daily_counts
GROUP BY 1, 2, 3
ORDER BY dow_num, total_opportunities DESC;


-- ---------------------------------------------------------------------------
-- 3. Opportunity lifetime distribution (percentiles)
-- ---------------------------------------------------------------------------
WITH lifetimes AS (
    SELECT
        strategy_type,
        classification,
        EXTRACT(EPOCH FROM (closed_at - detected_at)) AS duration_s
    FROM arbitrage_opportunities
    WHERE closed_at IS NOT NULL
      AND detected_at IS NOT NULL
      AND closed_at > detected_at
)
SELECT
    strategy_type,
    classification,
    COUNT(*)                                          AS count,
    ROUND(AVG(duration_s)::NUMERIC, 1)                AS avg_duration_s,
    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY duration_s)::NUMERIC, 1) AS p25_s,
    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_s)::NUMERIC, 1) AS median_s,
    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY duration_s)::NUMERIC, 1) AS p75_s,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_s)::NUMERIC, 1) AS p95_s,
    ROUND(MAX(duration_s)::NUMERIC, 1)                AS max_duration_s
FROM lifetimes
GROUP BY 1, 2
ORDER BY 1, 2;


-- ---------------------------------------------------------------------------
-- 4. Rolling 7-day opportunity count and average edge
-- ---------------------------------------------------------------------------
WITH daily AS (
    SELECT
        DATE_TRUNC('day', detected_at) AS day,
        COUNT(*)                       AS n_opps,
        AVG(net_edge)                  AS avg_edge,
        MAX(net_edge)                  AS best_edge,
        SUM(max_executable_contracts)  AS total_qty
    FROM arbitrage_opportunities
    GROUP BY 1
)
SELECT
    day,
    n_opps,
    ROUND(avg_edge::NUMERIC, 5)          AS avg_edge,
    ROUND(best_edge::NUMERIC, 5)         AS best_edge,
    total_qty,
    SUM(n_opps)  OVER w7                 AS rolling_7d_opps,
    ROUND(AVG(avg_edge) OVER w7::NUMERIC, 5) AS rolling_7d_avg_edge,
    SUM(total_qty) OVER w7              AS rolling_7d_qty
FROM daily
WINDOW w7 AS (ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
ORDER BY day DESC;


-- ---------------------------------------------------------------------------
-- 5. Opportunity edge distribution (histogram buckets)
-- ---------------------------------------------------------------------------
WITH bucketed AS (
    SELECT
        strategy_type,
        CASE
            WHEN net_edge < 0.005  THEN '0-0.5¢'
            WHEN net_edge < 0.010  THEN '0.5-1¢'
            WHEN net_edge < 0.020  THEN '1-2¢'
            WHEN net_edge < 0.050  THEN '2-5¢'
            WHEN net_edge < 0.100  THEN '5-10¢'
            WHEN net_edge < 0.200  THEN '10-20¢'
            ELSE                        '20¢+'
        END AS edge_bucket,
        CASE
            WHEN net_edge < 0.005  THEN 1
            WHEN net_edge < 0.010  THEN 2
            WHEN net_edge < 0.020  THEN 3
            WHEN net_edge < 0.050  THEN 4
            WHEN net_edge < 0.100  THEN 5
            WHEN net_edge < 0.200  THEN 6
            ELSE                        7
        END AS bucket_order
    FROM arbitrage_opportunities
    WHERE net_edge IS NOT NULL
)
SELECT
    strategy_type,
    edge_bucket,
    COUNT(*) AS opportunity_count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY strategy_type), 2) AS pct_of_strategy
FROM bucketed
-- Group by the bucket's sort key, not by COUNT(*): an aggregate cannot appear
-- in GROUP BY. bucket_order is functionally dependent on edge_bucket, so this
-- adds no extra groups and lets ORDER BY sort buckets by magnitude.
GROUP BY strategy_type, edge_bucket, bucket_order
ORDER BY strategy_type, bucket_order;


-- ---------------------------------------------------------------------------
-- 6. Market appearance frequency — which markets appear in most opportunities
-- ---------------------------------------------------------------------------
WITH market_list AS (
    SELECT
        ao.opportunity_id,
        ao.strategy_type,
        ao.net_edge,
        ao.detected_at,
        UNNEST(ao.markets_involved::TEXT[]) AS market_id
    FROM arbitrage_opportunities ao
)
SELECT
    ml.market_id,
    m.title,
    m.event_ticker,
    COUNT(*)                                      AS appearances,
    COUNT(DISTINCT ml.strategy_type)              AS strategy_types,
    ROUND(AVG(ml.net_edge)::NUMERIC, 5)           AS avg_net_edge,
    MAX(ml.net_edge)                              AS best_edge,
    MIN(ml.detected_at)                           AS first_seen,
    MAX(ml.detected_at)                           AS last_seen
FROM market_list ml
LEFT JOIN markets m ON m.market_id = ml.market_id
GROUP BY ml.market_id, m.title, m.event_ticker
ORDER BY appearances DESC
LIMIT 50;


-- ---------------------------------------------------------------------------
-- 7. Strategy performance summary with fee impact
-- ---------------------------------------------------------------------------
SELECT
    strategy_type,
    classification,
    COUNT(*)                                       AS total_opps,
    ROUND(AVG(gross_edge)::NUMERIC, 5)             AS avg_gross_edge,
    ROUND(AVG(total_fees)::NUMERIC, 5)             AS avg_total_fees,
    ROUND(AVG(net_edge)::NUMERIC, 5)               AS avg_net_edge,
    ROUND(100.0 * AVG(total_fees / NULLIF(gross_edge, 0))::NUMERIC, 2) AS avg_fee_pct_of_gross,
    ROUND(MAX(net_edge)::NUMERIC, 5)               AS best_net_edge,
    ROUND(SUM(net_edge * max_executable_contracts)::NUMERIC, 4) AS total_executable_value,
    ROUND(AVG(max_executable_contracts)::NUMERIC, 1) AS avg_qty
FROM arbitrage_opportunities
WHERE gross_edge > 0
GROUP BY 1, 2
ORDER BY 1, 2;


-- ---------------------------------------------------------------------------
-- 8. Settlement-proximity analysis — do arb opportunities cluster near expiry?
-- ---------------------------------------------------------------------------
WITH proximity AS (
    SELECT
        ao.opportunity_id,
        ao.strategy_type,
        ao.net_edge,
        m.close_time::TIMESTAMPTZ AS close_time,
        ao.detected_at,
        EXTRACT(EPOCH FROM (m.close_time::TIMESTAMPTZ - ao.detected_at)) / 3600.0 AS hours_to_close
    FROM arbitrage_opportunities ao
    CROSS JOIN UNNEST(ao.markets_involved::TEXT[]) AS mid(market_id)
    JOIN markets m ON m.market_id = mid.market_id
    WHERE m.close_time IS NOT NULL
      AND ao.detected_at IS NOT NULL
)
-- The bucket and its sort key are derived together in a sub-select so the
-- outer ORDER BY can sort chronologically. Sorting on the label alone would
-- order the windows alphabetically ('1-24 hours' before 'Last hour'), and an
-- output alias cannot be used inside an ORDER BY expression.
SELECT
    strategy_type,
    time_window,
    COUNT(*)                              AS opportunity_count,
    ROUND(AVG(net_edge)::NUMERIC, 5)      AS avg_net_edge,
    ROUND(MAX(net_edge)::NUMERIC, 5)      AS max_net_edge
FROM (
    SELECT
        strategy_type,
        net_edge,
        CASE
            WHEN hours_to_close < 1    THEN 'Last hour'
            WHEN hours_to_close < 24   THEN '1-24 hours'
            WHEN hours_to_close < 168  THEN '1-7 days'
            WHEN hours_to_close < 720  THEN '1-4 weeks'
            ELSE                            '> 1 month'
        END AS time_window,
        CASE
            WHEN hours_to_close < 1    THEN 1
            WHEN hours_to_close < 24   THEN 2
            WHEN hours_to_close < 168  THEN 3
            WHEN hours_to_close < 720  THEN 4
            ELSE                            5
        END AS window_order
    FROM proximity
    WHERE hours_to_close >= 0
) w
GROUP BY strategy_type, time_window, window_order
ORDER BY strategy_type, window_order;


-- ---------------------------------------------------------------------------
-- 9. Consecutive opportunity streak (LAG/LEAD) — persistence tracking
-- ---------------------------------------------------------------------------
WITH opp_ordered AS (
    SELECT
        opportunity_id,
        strategy_type,
        detected_at,
        net_edge,
        ROW_NUMBER() OVER (PARTITION BY strategy_type ORDER BY detected_at) AS rn,
        LAG(detected_at) OVER (PARTITION BY strategy_type ORDER BY detected_at)  AS prev_ts,
        LEAD(detected_at) OVER (PARTITION BY strategy_type ORDER BY detected_at) AS next_ts
    FROM arbitrage_opportunities
    WHERE net_edge > 0
),
gaps AS (
    SELECT
        *,
        EXTRACT(EPOCH FROM (detected_at - prev_ts)) AS gap_from_prev_s
    FROM opp_ordered
)
SELECT
    strategy_type,
    COUNT(*) FILTER (WHERE gap_from_prev_s < 120)  AS rapid_follow_ons,   -- < 2 min gap
    COUNT(*) FILTER (WHERE gap_from_prev_s < 3600) AS within_hour,
    ROUND(AVG(gap_from_prev_s) FILTER (WHERE gap_from_prev_s IS NOT NULL)::NUMERIC, 1)
                                                    AS avg_gap_s,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP
        (ORDER BY gap_from_prev_s) FILTER (WHERE gap_from_prev_s IS NOT NULL)::NUMERIC, 1)
                                                    AS median_gap_s
FROM gaps
GROUP BY 1
ORDER BY 1;


-- ---------------------------------------------------------------------------
-- 10. Top events by total arbitrage value (aggregated across markets)
-- ---------------------------------------------------------------------------
WITH event_value AS (
    SELECT
        e.event_ticker,
        e.title                                              AS event_title,
        COUNT(DISTINCT ao.opportunity_id)                    AS opportunity_count,
        SUM(ao.net_edge * ao.max_executable_contracts)       AS total_value,
        AVG(ao.net_edge)                                     AS avg_edge,
        MAX(ao.net_edge)                                     AS best_edge,
        MIN(ao.detected_at)                                  AS first_arb_at,
        MAX(ao.detected_at)                                  AS last_arb_at,
        COUNT(DISTINCT ao.strategy_type)                     AS distinct_strategies
    FROM arbitrage_opportunities ao
    CROSS JOIN UNNEST(ao.markets_involved::TEXT[]) AS mid(market_id)
    JOIN markets m   ON m.market_id     = mid.market_id
    JOIN events  e   ON e.event_ticker  = m.event_ticker
    GROUP BY e.event_ticker, e.title
)
SELECT
    event_ticker,
    event_title,
    opportunity_count,
    ROUND(total_value::NUMERIC, 4)    AS total_executable_value,
    ROUND(avg_edge::NUMERIC, 5)       AS avg_net_edge,
    ROUND(best_edge::NUMERIC, 5)      AS best_net_edge,
    distinct_strategies,
    first_arb_at,
    last_arb_at,
    RANK() OVER (ORDER BY total_value DESC NULLS LAST) AS value_rank
FROM event_value
ORDER BY total_value DESC NULLS LAST
LIMIT 30;
