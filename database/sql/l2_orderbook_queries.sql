-- =============================================================================
-- database/sql/l2_orderbook_queries.sql
-- Kalshi Arbitrage Engine — L2 Order Book Analytics
-- =============================================================================
-- Demonstrates: time-series aggregation, depth analysis, VWAP approximation,
-- rolling statistics, and book quality metrics.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. L2 snapshot coverage — which markets have the deepest books?
-- ---------------------------------------------------------------------------
SELECT
    s.market_id,
    m.title,
    m.category,
    COUNT(*)                                        AS snapshot_count,
    MIN(s.snapped_at)                               AS first_snapshot,
    MAX(s.snapped_at)                               AS last_snapshot,
    EXTRACT(EPOCH FROM (MAX(s.snapped_at) - MIN(s.snapped_at))) / 3600.0
                                                    AS coverage_hours,
    ROUND(AVG(s.yes_best_bid)::NUMERIC, 4)          AS avg_yes_bid,
    ROUND(AVG(s.yes_best_ask)::NUMERIC, 4)          AS avg_yes_ask,
    ROUND(AVG(s.yes_best_ask - s.yes_best_bid)::NUMERIC, 4) AS avg_spread
FROM l2_snapshots s
JOIN markets m ON m.market_id = s.market_id
GROUP BY s.market_id, m.title, m.category
HAVING COUNT(*) >= 10
ORDER BY snapshot_count DESC
LIMIT 50;


-- ---------------------------------------------------------------------------
-- 2. Order book spread time series (1-hour buckets)
-- ---------------------------------------------------------------------------
SELECT
    market_id,
    DATE_TRUNC('hour', snapped_at)                 AS hour,
    COUNT(*)                                        AS snapshots,
    ROUND(AVG(yes_best_bid)::NUMERIC, 4)            AS avg_bid,
    ROUND(AVG(yes_best_ask)::NUMERIC, 4)            AS avg_ask,
    ROUND(AVG(yes_best_ask - yes_best_bid)::NUMERIC, 4) AS avg_spread,
    ROUND(MIN(yes_best_ask - yes_best_bid)::NUMERIC, 4) AS min_spread,
    ROUND(MAX(yes_best_ask - yes_best_bid)::NUMERIC, 4) AS max_spread,
    ROUND(STDDEV(yes_best_ask - yes_best_bid)::NUMERIC, 5) AS spread_vol
FROM l2_snapshots
WHERE yes_best_bid IS NOT NULL
  AND yes_best_ask IS NOT NULL
  AND yes_best_ask > yes_best_bid
GROUP BY market_id, DATE_TRUNC('hour', snapped_at)
ORDER BY market_id, hour;


-- ---------------------------------------------------------------------------
-- 3. Depth at best quote — average quantity at best bid/ask
-- ---------------------------------------------------------------------------
-- Parses book_json JSONB to extract first-level quantities.
-- book_json format: {"yes_bids": [[price, qty], ...], "yes_asks": [[price, qty], ...]}
-- ---------------------------------------------------------------------------
-- Wrapped in an outer SELECT because ORDER BY ranks on the *sum* of two output
-- columns: a bare alias is allowed in ORDER BY, but an expression over aliases
-- is not, so the aggregates are computed once in the inner query and summed here.
SELECT *
FROM (
    SELECT
        market_id,
        COUNT(*)                                              AS snapshots,
        ROUND(AVG(
            (book_json->'yes_bids'->0->>1)::FLOAT
        )::NUMERIC, 2)                                       AS avg_best_bid_qty,
        ROUND(AVG(
            (book_json->'yes_asks'->0->>1)::FLOAT
        )::NUMERIC, 2)                                       AS avg_best_ask_qty,
        ROUND(AVG(
            JSONB_ARRAY_LENGTH(book_json->'yes_bids')
        )::NUMERIC, 1)                                       AS avg_bid_levels,
        ROUND(AVG(
            JSONB_ARRAY_LENGTH(book_json->'yes_asks')
        )::NUMERIC, 1)                                       AS avg_ask_levels
    FROM l2_snapshots
    WHERE book_json IS NOT NULL
      AND book_json != '{}'::JSONB
      AND JSONB_ARRAY_LENGTH(book_json->'yes_bids') > 0
      AND JSONB_ARRAY_LENGTH(book_json->'yes_asks') > 0
    GROUP BY market_id
    HAVING COUNT(*) >= 5
) depth
ORDER BY avg_best_bid_qty + avg_best_ask_qty DESC
LIMIT 30;


-- ---------------------------------------------------------------------------
-- 4. Book staleness detection — markets with large snapshot gaps
-- ---------------------------------------------------------------------------
WITH snapshot_gaps AS (
    SELECT
        market_id,
        snapped_at,
        LAG(snapped_at) OVER (PARTITION BY market_id ORDER BY snapped_at) AS prev_snap,
        EXTRACT(EPOCH FROM (
            snapped_at - LAG(snapped_at) OVER (PARTITION BY market_id ORDER BY snapped_at)
        )) AS gap_seconds
    FROM l2_snapshots
)
SELECT
    market_id,
    COUNT(*) FILTER (WHERE gap_seconds > 120)   AS gaps_over_2min,
    COUNT(*) FILTER (WHERE gap_seconds > 300)   AS gaps_over_5min,
    COUNT(*) FILTER (WHERE gap_seconds > 600)   AS gaps_over_10min,
    ROUND(MAX(gap_seconds)::NUMERIC, 1)         AS max_gap_seconds,
    ROUND(AVG(gap_seconds)::NUMERIC, 1)         AS avg_gap_seconds
FROM snapshot_gaps
WHERE gap_seconds IS NOT NULL
GROUP BY market_id
HAVING COUNT(*) FILTER (WHERE gap_seconds > 120) > 0
ORDER BY gaps_over_2min DESC;


-- ---------------------------------------------------------------------------
-- 5. VWAP approximation at top-of-book (limited depth)
-- ---------------------------------------------------------------------------
-- Uses first two levels from book_json for a two-level VWAP estimate.
-- ---------------------------------------------------------------------------
WITH parsed AS (
    SELECT
        market_id,
        snapped_at,
        yes_best_bid,
        yes_best_ask,
        (book_json->'yes_asks'->0->0)::TEXT::FLOAT AS ask1_price,
        (book_json->'yes_asks'->0->1)::TEXT::FLOAT AS ask1_qty,
        (book_json->'yes_asks'->1->0)::TEXT::FLOAT AS ask2_price,
        (book_json->'yes_asks'->1->1)::TEXT::FLOAT AS ask2_qty
    FROM l2_snapshots
    WHERE book_json IS NOT NULL
      AND JSONB_ARRAY_LENGTH(book_json->'yes_asks') >= 2
)
SELECT
    market_id,
    DATE_TRUNC('hour', snapped_at) AS hour,
    ROUND(AVG(ask1_price)::NUMERIC, 4)                          AS avg_ask1_price,
    ROUND(AVG(ask1_qty)::NUMERIC, 1)                            AS avg_ask1_qty,
    ROUND(AVG(ask2_price)::NUMERIC, 4)                          AS avg_ask2_price,
    ROUND(AVG(ask2_qty)::NUMERIC, 1)                            AS avg_ask2_qty,
    -- 2-level VWAP estimate for 100-contract fill
    ROUND(AVG(
        CASE
            WHEN ask1_qty >= 100 THEN ask1_price
            WHEN ask1_qty + ask2_qty > 0
            THEN (ask1_price * ask1_qty + ask2_price * LEAST(100 - ask1_qty, ask2_qty))
                 / LEAST(ask1_qty + ask2_qty, 100)
            ELSE ask1_price
        END
    )::NUMERIC, 4)                                              AS avg_vwap_100ct
FROM parsed
GROUP BY market_id, DATE_TRUNC('hour', snapped_at)
ORDER BY market_id, hour;


-- ---------------------------------------------------------------------------
-- 6. L2 depth quality score per market
--    Score = (avg_bid_levels + avg_ask_levels) × snapshot_density
-- ---------------------------------------------------------------------------
WITH depth_stats AS (
    SELECT
        market_id,
        COUNT(*)                             AS snapshot_count,
        AVG(JSONB_ARRAY_LENGTH(COALESCE(book_json->'yes_bids', '[]'::JSONB))) AS avg_bid_levels,
        AVG(JSONB_ARRAY_LENGTH(COALESCE(book_json->'yes_asks', '[]'::JSONB))) AS avg_ask_levels,
        EXTRACT(EPOCH FROM MAX(snapped_at) - MIN(snapped_at)) AS coverage_s
    FROM l2_snapshots
    WHERE book_json IS NOT NULL
    GROUP BY market_id
    HAVING COUNT(*) >= 5
),
density AS (
    SELECT
        *,
        -- snapshots per minute of coverage
        CASE WHEN coverage_s > 0
             THEN snapshot_count / (coverage_s / 60.0)
             ELSE 0
        END AS snaps_per_minute
    FROM depth_stats
)
SELECT
    d.market_id,
    m.title,
    ROUND(d.avg_bid_levels::NUMERIC, 1)      AS avg_bid_levels,
    ROUND(d.avg_ask_levels::NUMERIC, 1)      AS avg_ask_levels,
    ROUND(d.snaps_per_minute::NUMERIC, 3)    AS snaps_per_min,
    d.snapshot_count,
    -- composite depth quality score (0-100)
    ROUND(LEAST(100,
        (d.avg_bid_levels + d.avg_ask_levels) * 5 * LEAST(1.0, d.snaps_per_minute)
    )::NUMERIC, 1)                           AS depth_quality_score,
    DENSE_RANK() OVER (ORDER BY
        (d.avg_bid_levels + d.avg_ask_levels) * d.snaps_per_minute DESC
    )                                        AS depth_rank
FROM density d
JOIN markets m ON m.market_id = d.market_id
ORDER BY depth_quality_score DESC
LIMIT 50;


-- ---------------------------------------------------------------------------
-- 7. Cross-book complement validation
--    For complement pairs: yes_bid + no_ask should be close to 1.00
-- ---------------------------------------------------------------------------
WITH complement_pairs AS (
    SELECT
        cr.market_id_1,
        cr.market_id_2
    FROM contract_relationships cr
    WHERE cr.relationship_type = 'complement'
    LIMIT 500
),
latest_snaps AS (
    SELECT DISTINCT ON (market_id)
        market_id,
        snapped_at,
        yes_best_bid,
        yes_best_ask
    FROM l2_snapshots
    WHERE yes_best_bid IS NOT NULL
      AND yes_best_ask IS NOT NULL
    ORDER BY market_id, snapped_at DESC
)
SELECT
    cp.market_id_1,
    cp.market_id_2,
    s1.yes_best_bid                           AS m1_yes_bid,
    s1.yes_best_ask                           AS m1_yes_ask,
    s2.yes_best_bid                           AS m2_yes_bid,
    s2.yes_best_ask                           AS m2_yes_ask,
    -- complement: YES_bid + NO_ask ≈ 1 - spread
    ROUND((s1.yes_best_bid + (1 - s2.yes_best_ask))::NUMERIC, 4) AS combined_bid,
    ROUND((s1.yes_best_ask + (1 - s2.yes_best_bid))::NUMERIC, 4) AS combined_ask,
    -- gap from parity (should be negative for no-arb)
    ROUND((1 - s1.yes_best_ask - (1 - s2.yes_best_bid))::NUMERIC, 4) AS gross_edge_yes,
    ROUND((1 - s2.yes_best_ask - (1 - s1.yes_best_bid))::NUMERIC, 4) AS gross_edge_no
FROM complement_pairs cp
JOIN latest_snaps s1 ON s1.market_id = cp.market_id_1
JOIN latest_snaps s2 ON s2.market_id = cp.market_id_2
-- Postgres has no abs(interval), so the two books are required to be within
-- five minutes of each other in whichever direction they differ. Comparing
-- stale books across legs would manufacture edges that never coexisted.
WHERE GREATEST(s1.snapped_at, s2.snapped_at)
    - LEAST(s1.snapped_at, s2.snapped_at) < INTERVAL '5 minutes'
ORDER BY ABS(
    LEAST(
        1 - s1.yes_best_ask - (1 - s2.yes_best_bid),
        1 - s2.yes_best_ask - (1 - s1.yes_best_bid)
    )
) DESC
LIMIT 20;
