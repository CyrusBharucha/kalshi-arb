-- database/arbitrage_queries.sql
-- ================================
-- Core arbitrage-analysis queries: best opportunities, frequency by market,
-- opportunity lifetime, and strategy breakdown.
--
-- All queries run against arbitrage_opportunities (see database/schema.sql).
-- classification: A = live L2-confirmed executable, B = top-of-book only,
--                  C = OHLC-derived, D = theoretical relationship violation.

-- ------------------------------------------------------------------------
-- 1. Best historical opportunities by net edge, Class A/B only
--    (never mix Class C/D theoretical hits into an "executable" ranking)
-- ------------------------------------------------------------------------
SELECT
    opportunity_id,
    detected_at,
    strategy_type,
    classification,
    markets_involved,
    gross_edge,
    total_fees,
    estimated_slippage,
    net_edge,
    max_executable_contracts,
    max_net_profit,
    status,
    duration_seconds
FROM arbitrage_opportunities
WHERE classification IN ('A', 'B')
  AND net_edge > 0
ORDER BY max_net_profit DESC NULLS LAST
LIMIT 50;

-- ------------------------------------------------------------------------
-- 2. Arbitrage frequency by strategy type and classification
--    (window function: share of total within each classification)
-- ------------------------------------------------------------------------
SELECT
    strategy_type,
    classification,
    COUNT(*)                                            AS opportunity_count,
    ROUND(AVG(net_edge), 4)                              AS avg_net_edge,
    ROUND(SUM(COUNT(*)) OVER (PARTITION BY classification)
          / SUM(COUNT(*)) OVER () * 100, 2)               AS pct_of_class
FROM arbitrage_opportunities
GROUP BY strategy_type, classification
ORDER BY classification, opportunity_count DESC;

-- ------------------------------------------------------------------------
-- 3. Average / median opportunity lifetime by strategy
--    (PERCENTILE_CONT for a true median, not just AVG)
-- ------------------------------------------------------------------------
SELECT
    strategy_type,
    COUNT(*) FILTER (WHERE duration_seconds IS NOT NULL) AS n_closed,
    ROUND(AVG(duration_seconds), 1)                       AS avg_duration_s,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY duration_seconds)::numeric, 1)            AS median_duration_s,
    ROUND(MAX(duration_seconds), 1)                       AS max_duration_s
FROM arbitrage_opportunities
WHERE status IN ('expired', 'settled')
GROUP BY strategy_type
ORDER BY avg_duration_s DESC NULLS LAST;

-- ------------------------------------------------------------------------
-- 4. Opportunity persistence: for each opportunity, how did net_edge move
--    relative to the PREVIOUS opportunity detected on the same market set?
--    Demonstrates LAG() over a natural ordering key.
-- ------------------------------------------------------------------------
SELECT
    opportunity_id,
    markets_involved,
    detected_at,
    net_edge,
    LAG(net_edge) OVER (
        PARTITION BY markets_involved ORDER BY detected_at
    )                                                      AS prev_net_edge,
    net_edge - LAG(net_edge) OVER (
        PARTITION BY markets_involved ORDER BY detected_at
    )                                                      AS edge_delta,
    LEAD(detected_at) OVER (
        PARTITION BY markets_involved ORDER BY detected_at
    ) - detected_at                                        AS time_to_next_reappearance
FROM arbitrage_opportunities
ORDER BY markets_involved, detected_at;

-- ------------------------------------------------------------------------
-- 5. Rolling 7-day opportunity count and average edge (window frame)
-- ------------------------------------------------------------------------
WITH daily AS (
    SELECT
        date_trunc('day', detected_at)  AS day,
        COUNT(*)                        AS n_opps,
        AVG(net_edge)                   AS avg_edge
    FROM arbitrage_opportunities
    GROUP BY 1
)
SELECT
    day,
    n_opps,
    ROUND(avg_edge, 4)                                     AS avg_edge,
    SUM(n_opps) OVER (
        ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    )                                                       AS rolling_7d_count,
    ROUND(AVG(avg_edge) OVER (
        ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ), 4)                                                   AS rolling_7d_avg_edge
FROM daily
ORDER BY day;

-- ------------------------------------------------------------------------
-- 6. Top markets by executable edge (RANK within each strategy type)
-- ------------------------------------------------------------------------
SELECT * FROM (
    SELECT
        strategy_type,
        markets_involved,
        net_edge,
        max_net_profit,
        RANK() OVER (
            PARTITION BY strategy_type ORDER BY max_net_profit DESC NULLS LAST
        ) AS rank_in_strategy
    FROM arbitrage_opportunities
    WHERE classification IN ('A', 'B')
) ranked
WHERE rank_in_strategy <= 10
ORDER BY strategy_type, rank_in_strategy;


-- ------------------------------------------------------------------------
-- 7. Opportunity lifetime via LEAD
--    Where closed_at was never written (scanner restart, crash), the next
--    detection on the same market set is the best available upper bound on
--    when the opportunity disappeared. LEAD supplies that neighbour.
-- ------------------------------------------------------------------------
WITH sequenced AS (
    SELECT
        opportunity_id,
        markets_involved,
        strategy_type,
        classification,
        detected_at,
        closed_at,
        net_edge,
        LEAD(detected_at) OVER (
            PARTITION BY markets_involved, strategy_type ORDER BY detected_at
        ) AS next_detected_at
    FROM arbitrage_opportunities
)
SELECT
    opportunity_id,
    markets_involved,
    strategy_type,
    classification,
    detected_at,
    COALESCE(closed_at, next_detected_at)                       AS disappeared_at,
    CASE WHEN closed_at IS NOT NULL THEN 'observed' ELSE 'inferred' END
                                                                AS lifetime_source,
    ROUND(EXTRACT(EPOCH FROM (
        COALESCE(closed_at, next_detected_at) - detected_at
    ))::numeric, 2)                                             AS lifetime_seconds,
    ROUND(net_edge, 4)                                          AS net_edge
FROM sequenced
WHERE COALESCE(closed_at, next_detected_at) IS NOT NULL
ORDER BY lifetime_seconds ASC NULLS LAST
LIMIT 200;

-- ------------------------------------------------------------------------
-- 8. Rolling 30-day hit rate
--    "Win" = the opportunity was still profitable after fees and slippage
--    at the moment it was closed out. The rolling frame smooths the daily
--    series so a single quiet day does not read as a regime change.
-- ------------------------------------------------------------------------
WITH daily AS (
    SELECT
        date_trunc('day', detected_at)::date          AS day,
        COUNT(*)                                      AS n_detected,
        COUNT(*) FILTER (WHERE net_edge > 0)          AS n_profitable,
        COUNT(*) FILTER (WHERE status = 'settled')    AS n_settled,
        SUM(max_net_profit) FILTER (WHERE net_edge > 0) AS gross_capture
    FROM arbitrage_opportunities
    WHERE classification IN ('A', 'B')
    GROUP BY 1
)
SELECT
    day,
    n_detected,
    n_profitable,
    ROUND(100.0 * n_profitable / NULLIF(n_detected, 0), 2)       AS daily_hit_rate_pct,
    SUM(n_detected) OVER w30                                     AS rolling_30d_detected,
    SUM(n_profitable) OVER w30                                   AS rolling_30d_profitable,
    ROUND(100.0 * SUM(n_profitable) OVER w30
          / NULLIF(SUM(n_detected) OVER w30, 0), 2)              AS rolling_30d_hit_rate_pct,
    ROUND(SUM(gross_capture) OVER w30, 2)                        AS rolling_30d_capture
FROM daily
WINDOW w30 AS (ORDER BY day RANGE BETWEEN INTERVAL '29 days' PRECEDING AND CURRENT ROW)
ORDER BY day;

-- ------------------------------------------------------------------------
-- 9. Fee sensitivity
--    Re-prices every historical opportunity under a grid of alternative
--    fee rates. The CROSS JOIN against a VALUES list is the whole trick:
--    one pass over the fact table answers "what fee level kills the edge?"
--    Kalshi's live fee is ~0.07 * C * P * (1-P), so the grid is expressed
--    as a multiplier on the fees we actually recorded.
-- ------------------------------------------------------------------------
WITH scenarios (label, fee_multiplier) AS (
    VALUES ('zero_fee', 0.00),
           ('half',     0.50),
           ('actual',   1.00),
           ('1.5x',     1.50),
           ('2x',       2.00)
),
repriced AS (
    SELECT
        s.label,
        s.fee_multiplier,
        o.classification,
        o.gross_edge
            - (o.total_fees * s.fee_multiplier)
            - COALESCE(o.estimated_slippage, 0)          AS scenario_net_edge,
        o.max_executable_contracts
    FROM arbitrage_opportunities o
    CROSS JOIN scenarios s
    WHERE o.gross_edge IS NOT NULL
      AND o.total_fees IS NOT NULL
)
SELECT
    label,
    classification,
    COUNT(*)                                                     AS n_opps,
    COUNT(*) FILTER (WHERE scenario_net_edge > 0)                AS n_still_profitable,
    ROUND(100.0 * COUNT(*) FILTER (WHERE scenario_net_edge > 0)
          / NULLIF(COUNT(*), 0), 2)                              AS pct_surviving,
    ROUND(AVG(scenario_net_edge), 4)                             AS avg_net_edge,
    ROUND(SUM(scenario_net_edge * max_executable_contracts)
          FILTER (WHERE scenario_net_edge > 0), 2)               AS total_capturable
FROM repriced
GROUP BY label, fee_multiplier, classification
ORDER BY fee_multiplier, classification;

-- ------------------------------------------------------------------------
-- 10. Class A/B/C/D breakdown with PERCENT_RANK
--     PERCENT_RANK is computed *within* each classification so an edge is
--     graded against its own evidence tier. Ranking a theoretical Class D
--     hit against L2-confirmed Class A fills would be meaningless.
-- ------------------------------------------------------------------------
WITH graded AS (
    SELECT
        opportunity_id,
        classification,
        strategy_type,
        detected_at,
        net_edge,
        max_net_profit,
        max_executable_contracts,
        PERCENT_RANK() OVER (
            PARTITION BY classification ORDER BY net_edge
        )                                                        AS edge_pctile_in_class,
        NTILE(4) OVER (
            PARTITION BY classification ORDER BY net_edge
        )                                                        AS edge_quartile
    FROM arbitrage_opportunities
    WHERE net_edge IS NOT NULL
)
SELECT
    classification,
    COUNT(*)                                                     AS n_opps,
    COUNT(DISTINCT strategy_type)                                AS n_strategies,
    ROUND(AVG(net_edge), 4)                                      AS avg_net_edge,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY net_edge)::numeric, 4)                          AS median_net_edge,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (
        ORDER BY net_edge)::numeric, 4)                          AS p95_net_edge,
    ROUND(AVG(max_executable_contracts), 2)                      AS avg_size,
    ROUND(SUM(max_net_profit), 2)                                AS total_net_profit,
    ROUND(AVG(net_edge) FILTER (WHERE edge_quartile = 4), 4)     AS avg_edge_top_quartile,
    MIN(detected_at)                                             AS first_seen,
    MAX(detected_at)                                             AS last_seen
FROM graded
GROUP BY classification
ORDER BY classification;

-- ------------------------------------------------------------------------
-- 11. Liquidity at detection time
--     Joins each opportunity back to the L2 book that was standing when it
--     fired, per leg. A large paper edge on a two-contract book is not a
--     trade -- this query is what separates the two.
-- ------------------------------------------------------------------------
WITH legs AS (
    SELECT
        o.opportunity_id,
        o.detected_at,
        o.classification,
        o.net_edge,
        o.max_executable_contracts,
        UNNEST(o.markets_involved) AS leg_market_id
    FROM arbitrage_opportunities o
    WHERE o.classification IN ('A', 'B')
),
leg_books AS (
    SELECT
        l.*,
        b.yes_best_bid,
        b.yes_best_ask,
        b.yes_ask_levels,
        b.yes_bid_levels,
        b.snapped_at,
        EXTRACT(EPOCH FROM (l.detected_at - b.snapped_at)) AS book_age_seconds
    FROM legs l
    LEFT JOIN LATERAL (
        SELECT s.yes_best_bid, s.yes_best_ask, s.yes_ask_levels,
               s.yes_bid_levels, s.snapped_at
        FROM l2_snapshots s
        WHERE s.market_id = l.leg_market_id
          AND s.snapped_at <= l.detected_at
        ORDER BY s.snapped_at DESC
        LIMIT 1
    ) b ON TRUE
)
SELECT
    opportunity_id,
    detected_at,
    classification,
    ROUND(net_edge, 4)                                           AS net_edge,
    max_executable_contracts,
    COUNT(*)                                                     AS n_legs,
    COUNT(*) FILTER (WHERE yes_best_ask IS NULL)                 AS legs_without_book,
    ROUND(MIN(yes_best_ask - yes_best_bid), 4)                   AS tightest_leg_spread,
    ROUND(MAX(yes_best_ask - yes_best_bid), 4)                   AS widest_leg_spread,
    MIN(yes_ask_levels)                                          AS shallowest_ask_depth,
    ROUND(MAX(book_age_seconds)::numeric, 2)                     AS stalest_leg_book_age_s
FROM leg_books
GROUP BY opportunity_id, detected_at, classification,
         net_edge, max_executable_contracts
ORDER BY detected_at DESC
LIMIT 200;

-- ------------------------------------------------------------------------
-- 12. Opportunities that disappeared before they could be executed
--     Anything whose whole life was shorter than a realistic round-trip
--     (order out, ack back, both legs filled) was never actually tradeable.
--     Reporting these separately keeps the headline capture number honest.
-- ------------------------------------------------------------------------
WITH latency_budget AS (
    -- Conservative retail round-trip: ~250ms out, ~250ms back, per leg.
    SELECT 0.5::numeric AS round_trip_seconds
),
lived AS (
    SELECT
        o.opportunity_id,
        o.detected_at,
        o.strategy_type,
        o.classification,
        o.net_edge,
        o.max_net_profit,
        ARRAY_LENGTH(o.markets_involved, 1)                      AS n_legs,
        COALESCE(
            o.duration_seconds,
            EXTRACT(EPOCH FROM (o.closed_at - o.detected_at))
        )                                                        AS lifetime_s
    FROM arbitrage_opportunities o
    WHERE o.net_edge > 0
      AND (o.closed_at IS NOT NULL OR o.duration_seconds IS NOT NULL)
)
SELECT
    l.classification,
    l.strategy_type,
    COUNT(*)                                                     AS n_profitable_opps,
    COUNT(*) FILTER (
        WHERE l.lifetime_s < lb.round_trip_seconds * l.n_legs
    )                                                            AS n_too_fast_to_trade,
    ROUND(100.0 * COUNT(*) FILTER (
        WHERE l.lifetime_s < lb.round_trip_seconds * l.n_legs
    ) / NULLIF(COUNT(*), 0), 2)                                  AS pct_unreachable,
    ROUND(SUM(l.max_net_profit), 2)                              AS headline_profit,
    ROUND(SUM(l.max_net_profit) FILTER (
        WHERE l.lifetime_s >= lb.round_trip_seconds * l.n_legs
    ), 2)                                                        AS reachable_profit
FROM lived l
CROSS JOIN latency_budget lb
GROUP BY l.classification, l.strategy_type
ORDER BY l.classification, pct_unreachable DESC;

