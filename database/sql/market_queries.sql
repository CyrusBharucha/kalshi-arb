-- =============================================================================
-- database/sql/market_queries.sql
-- Kalshi Arbitrage Engine — Market Analytics
-- =============================================================================
-- Demonstrates: CTEs, window functions, joins, aggregations, ranking,
-- time-series metrics, and market microstructure analysis.
-- =============================================================================

-- =============================================================================
-- NOTE ON QUOTE AND VOLUME COLUMNS
-- The markets table stores contract *metadata* only (see database/schema.sql):
-- it has no yes_bid / yes_ask / volume / open_interest columns. Those live on
-- market_snapshots, one row per poll. Queries below therefore join markets to
-- the latest snapshot per market via a `latest_snap` CTE.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Market coverage summary — active markets by category
-- ---------------------------------------------------------------------------
WITH latest_snap AS (
    SELECT DISTINCT ON (market_id)
        market_id, yes_bid, yes_ask, volume, open_interest
    FROM market_snapshots
    ORDER BY market_id, snapshot_ts DESC
)
SELECT
    m.category,
    COUNT(*)                                        AS total_markets,
    COUNT(*) FILTER (WHERE m.status = 'active')     AS open_markets,
    COUNT(*) FILTER (WHERE m.status = 'finalized')  AS settled_markets,
    COUNT(*) FILTER (WHERE m.status = 'closed')     AS closed_markets,
    ROUND(AVG(s.volume)::NUMERIC, 2)                AS avg_volume,
    ROUND(SUM(s.volume)::NUMERIC, 2)                AS total_volume,
    ROUND(AVG(s.open_interest)::NUMERIC, 2)         AS avg_open_interest,
    MIN(m.open_time)                                AS earliest_open,
    MAX(m.close_time)                               AS latest_close
FROM markets m
LEFT JOIN latest_snap s ON s.market_id = m.market_id
GROUP BY m.category
ORDER BY total_markets DESC;


-- ---------------------------------------------------------------------------
-- 2. Market liquidity profile — spread and depth by volume tier
-- ---------------------------------------------------------------------------
WITH latest_snap AS (
    SELECT DISTINCT ON (market_id)
        market_id, yes_bid, yes_ask, volume, open_interest
    FROM market_snapshots
    ORDER BY market_id, snapshot_ts DESC
),
volume_tiers AS (
    SELECT
        m.market_id,
        m.title,
        m.category,
        s.volume,
        s.yes_bid,
        s.yes_ask,
        s.yes_ask - s.yes_bid                                AS spread,
        (s.yes_bid + s.yes_ask) / 2.0                        AS midpoint,
        NTILE(5) OVER (ORDER BY s.volume NULLS LAST)         AS volume_quintile
    FROM markets m
    JOIN latest_snap s ON s.market_id = m.market_id
    WHERE s.yes_bid IS NOT NULL
      AND s.yes_ask IS NOT NULL
      AND s.yes_bid > 0
      AND s.yes_ask > 0
      AND s.yes_ask > s.yes_bid
)
SELECT
    volume_quintile,
    COUNT(*)                                          AS market_count,
    ROUND(MIN(volume)::NUMERIC, 0)                    AS min_volume,
    ROUND(MAX(volume)::NUMERIC, 0)                    AS max_volume,
    ROUND(AVG(volume)::NUMERIC, 0)                    AS avg_volume,
    ROUND(AVG(spread)::NUMERIC, 4)                    AS avg_spread,
    ROUND(AVG(midpoint)::NUMERIC, 4)                  AS avg_midpoint,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY spread)::NUMERIC, 4) AS median_spread,
    ROUND(MIN(spread)::NUMERIC, 4)                    AS tightest_spread,
    ROUND(MAX(spread)::NUMERIC, 4)                    AS widest_spread
FROM volume_tiers
GROUP BY 1
ORDER BY 1;


-- ---------------------------------------------------------------------------
-- 3. Top markets by traded volume with relationship count
-- ---------------------------------------------------------------------------
WITH latest_snap AS (
    SELECT DISTINCT ON (market_id)
        market_id, yes_bid, yes_ask, volume, open_interest
    FROM market_snapshots
    ORDER BY market_id, snapshot_ts DESC
),
rel_counts AS (
    SELECT
        market_id,
        COUNT(*) AS rel_count
    FROM (
        SELECT market_id_1 AS market_id FROM contract_relationships
        UNION ALL
        SELECT market_id_2 AS market_id FROM contract_relationships
    ) sub
    GROUP BY market_id
)
SELECT
    m.market_id,
    m.title,
    m.category,
    m.status,
    ROUND(s.volume::NUMERIC, 0)              AS volume,
    ROUND(s.open_interest::NUMERIC, 0)       AS open_interest,
    ROUND(s.yes_bid::NUMERIC, 4)             AS yes_bid,
    ROUND(s.yes_ask::NUMERIC, 4)             AS yes_ask,
    ROUND((s.yes_ask - s.yes_bid)::NUMERIC, 4) AS spread,
    COALESCE(rc.rel_count, 0)               AS relationship_count,
    RANK() OVER (ORDER BY s.volume DESC NULLS LAST) AS volume_rank
FROM markets m
LEFT JOIN latest_snap s ON s.market_id = m.market_id
LEFT JOIN rel_counts rc ON rc.market_id = m.market_id
ORDER BY s.volume DESC NULLS LAST
LIMIT 100;


-- ---------------------------------------------------------------------------
-- 4. Contract relationships breakdown by type
-- ---------------------------------------------------------------------------
SELECT
    relationship_type,
    COUNT(*)                                       AS total_pairs,
    ROUND(AVG(confidence)::NUMERIC, 4)             AS avg_confidence,
    ROUND(MIN(confidence)::NUMERIC, 4)             AS min_confidence,
    ROUND(MAX(confidence)::NUMERIC, 4)             AS max_confidence,
    COUNT(*) FILTER (WHERE confidence >= 0.90)     AS high_confidence,
    COUNT(*) FILTER (WHERE confidence < 0.70)      AS low_confidence,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER ()::NUMERIC, 2) AS pct_of_total
FROM contract_relationships
GROUP BY relationship_type
ORDER BY total_pairs DESC;


-- ---------------------------------------------------------------------------
-- 5. Event market matrix — events with the most related markets
-- ---------------------------------------------------------------------------
WITH latest_snap AS (
    SELECT DISTINCT ON (market_id)
        market_id, yes_bid, yes_ask, volume, open_interest
    FROM market_snapshots
    ORDER BY market_id, snapshot_ts DESC
),
event_markets AS (
    SELECT
        m.event_ticker,
        e.title AS event_title,
        COUNT(DISTINCT m.market_id)                  AS market_count,
        COUNT(DISTINCT cr.id)                        AS relationship_count,
        ROUND(AVG(s.volume)::NUMERIC, 0)             AS avg_market_volume,
        ROUND(SUM(s.volume)::NUMERIC, 0)             AS total_event_volume,
        COUNT(*) FILTER (WHERE m.status = 'active')  AS open_count
    FROM markets m
    JOIN events e ON e.event_ticker = m.event_ticker
    LEFT JOIN latest_snap s ON s.market_id = m.market_id
    LEFT JOIN contract_relationships cr
        ON cr.market_id_1 = m.market_id OR cr.market_id_2 = m.market_id
    GROUP BY m.event_ticker, e.title
)
SELECT
    event_ticker,
    event_title,
    market_count,
    relationship_count,
    ROUND((relationship_count::FLOAT / NULLIF(market_count, 0))::NUMERIC, 2) AS rels_per_market,
    avg_market_volume,
    total_event_volume,
    open_count,
    DENSE_RANK() OVER (ORDER BY relationship_count DESC) AS rel_rank
FROM event_markets
ORDER BY relationship_count DESC
LIMIT 50;


-- ---------------------------------------------------------------------------
-- 6. Canadian market identification and stats
-- ---------------------------------------------------------------------------
-- Canadian relevance is classified at the *event* level (events.canadian_relevance),
-- not on markets -- one event's markets are all Canadian or all not, so storing
-- the flag per market would duplicate it across every strike.
WITH latest_snap AS (
    SELECT DISTINCT ON (market_id)
        market_id, yes_bid, yes_ask, volume, open_interest
    FROM market_snapshots
    ORDER BY market_id, snapshot_ts DESC
)
SELECT
    m.market_id,
    m.title,
    m.category,
    m.status,
    m.event_ticker,
    s.yes_bid,
    s.yes_ask,
    ROUND((s.yes_ask - s.yes_bid)::NUMERIC, 4) AS spread,
    ROUND(s.volume::NUMERIC, 0)                 AS volume,
    ROUND(s.open_interest::NUMERIC, 0)          AS open_interest,
    e.canadian_relevance,
    m.close_time
FROM markets m
JOIN events e ON e.event_ticker = m.event_ticker
LEFT JOIN latest_snap s ON s.market_id = m.market_id
-- canadian_relevance is a smallint score, not a boolean: 0 = not Canadian,
-- higher = stronger keyword evidence (see analysis/canadian_filter.py).
WHERE e.canadian_relevance > 0
ORDER BY s.volume DESC NULLS LAST;


-- ---------------------------------------------------------------------------
-- 7. Market price history and momentum (using snapshots)
-- ---------------------------------------------------------------------------
WITH price_history AS (
    SELECT
        market_id,
        snapshot_ts,
        yes_bid,
        yes_ask,
        (yes_bid + yes_ask) / 2.0                    AS mid,
        LAG((yes_bid + yes_ask) / 2.0)
            OVER (PARTITION BY market_id ORDER BY snapshot_ts) AS prev_mid,
        ROW_NUMBER() OVER (PARTITION BY market_id ORDER BY snapshot_ts DESC) AS recency
    FROM market_snapshots
)
SELECT
    ph.market_id,
    m.title,
    ph.snapshot_ts,
    ROUND(ph.mid::NUMERIC, 4)                         AS mid,
    ROUND(ph.prev_mid::NUMERIC, 4)                    AS prev_mid,
    ROUND((ph.mid - ph.prev_mid)::NUMERIC, 5)         AS price_change,
    ROUND(ph.yes_bid::NUMERIC, 4)                     AS yes_bid,
    ROUND(ph.yes_ask::NUMERIC, 4)                     AS yes_ask,
    ROUND((ph.yes_ask - ph.yes_bid)::NUMERIC, 4)      AS spread
FROM price_history ph
JOIN markets m ON m.market_id = ph.market_id
WHERE ph.recency = 1
  AND ph.prev_mid IS NOT NULL
ORDER BY ABS(ph.mid - ph.prev_mid) DESC
LIMIT 50;


-- ---------------------------------------------------------------------------
-- 8. Market microstructure: spread vs open interest cross-tab
-- ---------------------------------------------------------------------------
-- The markets table stores contract metadata only -- it carries no live quote
-- or open-interest columns (see database/schema.sql). The spread therefore
-- comes from the latest L2 snapshot per market, and traded volume stands in
-- for open interest.
WITH latest_book AS (
    SELECT DISTINCT ON (market_id)
        market_id, yes_best_bid, yes_best_ask
    FROM l2_snapshots
    WHERE yes_best_bid IS NOT NULL
      AND yes_best_ask IS NOT NULL
      AND yes_best_ask > yes_best_bid
    ORDER BY market_id, snapped_at DESC
),
traded AS (
    SELECT market_id, SUM(quantity) AS volume
    FROM trades
    GROUP BY market_id
),
classified AS (
    SELECT
        m.category,
        CASE
            WHEN lb.yes_best_ask - lb.yes_best_bid <= 0.02 THEN 'Tight (<=2c)'
            WHEN lb.yes_best_ask - lb.yes_best_bid <= 0.05 THEN 'Moderate (2-5c)'
            WHEN lb.yes_best_ask - lb.yes_best_bid <= 0.10 THEN 'Wide (5-10c)'
            ELSE                                                'Very Wide (>10c)'
        END AS spread_class,
        CASE
            WHEN t.volume >= 10000 THEN 'High volume (>=10k)'
            WHEN t.volume >= 1000  THEN 'Medium volume (1k-10k)'
            WHEN t.volume >= 100   THEN 'Low volume (100-1k)'
            ELSE                        'Minimal volume (<100)'
        END AS volume_class
    FROM markets m
    JOIN latest_book lb ON lb.market_id = m.market_id
    JOIN traded      t  ON t.market_id  = m.market_id
)
SELECT
    category,
    spread_class,
    volume_class,
    COUNT(*) AS market_count
FROM classified
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;


-- ---------------------------------------------------------------------------
-- 9. Settlement value distribution — how often do markets resolve YES?
-- ---------------------------------------------------------------------------
SELECT
    category,
    COUNT(*)                                                    AS settled_markets,
    COUNT(*) FILTER (WHERE settlement_value = 1.0)             AS resolved_yes,
    COUNT(*) FILTER (WHERE settlement_value = 0.0)             AS resolved_no,
    COUNT(*) FILTER (WHERE settlement_value NOT IN (0.0, 1.0)) AS partial,
    ROUND(100.0 * COUNT(*) FILTER (WHERE settlement_value = 1.0)
          / NULLIF(COUNT(*), 0)::NUMERIC, 2)                   AS yes_rate_pct,
    ROUND(AVG(settlement_value)::NUMERIC, 4)                   AS avg_settlement
FROM markets
WHERE status = 'settled'
  AND settlement_value IS NOT NULL
GROUP BY category
ORDER BY settled_markets DESC;
