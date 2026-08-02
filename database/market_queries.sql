-- database/market_queries.sql
-- =============================
-- Market/event discovery queries: category breakdown, Canadian markets,
-- relationship structure, and market metadata lookups.

-- ------------------------------------------------------------------------
-- 1. Canadian-relevant markets with latest event metadata
--    (canadian_relevance populated by analysis/canadian_filter.py,
--     persisted via server-side regex match -- see database/schema.sql)
-- ------------------------------------------------------------------------
SELECT
    e.event_ticker,
    e.title,
    e.canadian_relevance,
    e.category,
    COUNT(m.market_id)                     AS market_count,
    MIN(m.open_time)                       AS earliest_open,
    MAX(m.close_time)                      AS latest_close
FROM events e
JOIN markets m ON m.event_ticker = e.event_ticker
WHERE e.canadian_relevance >= 1
GROUP BY e.event_ticker, e.title, e.canadian_relevance, e.category
ORDER BY e.canadian_relevance DESC, market_count DESC
LIMIT 100;

-- ------------------------------------------------------------------------
-- 2. Market relationship breakdown by type, with average markets per group
-- ------------------------------------------------------------------------
SELECT
    relationship_type,
    COUNT(*)                                             AS relationship_count,
    COUNT(DISTINCT market_id_1) + COUNT(DISTINCT market_id_2) AS distinct_markets_touched,
    ROUND(AVG(confidence), 3)                             AS avg_confidence
FROM contract_relationships
GROUP BY relationship_type
ORDER BY relationship_count DESC;

-- ------------------------------------------------------------------------
-- 3. Events with the most relationship pairs (candidates for arb scanning)
--    CTE + window function ranking
-- ------------------------------------------------------------------------
WITH per_market_rel_count AS (
    SELECT market_id_1 AS market_id, COUNT(*) AS n
    FROM contract_relationships
    GROUP BY market_id_1
    UNION ALL
    SELECT market_id_2 AS market_id, COUNT(*) AS n
    FROM contract_relationships
    GROUP BY market_id_2
),
market_totals AS (
    SELECT market_id, SUM(n) AS total_relationships
    FROM per_market_rel_count
    GROUP BY market_id
)
SELECT
    m.ticker,
    m.title,
    m.event_ticker,
    mt.total_relationships,
    RANK() OVER (ORDER BY mt.total_relationships DESC) AS liquidity_rank
FROM market_totals mt
JOIN markets m ON m.market_id = mt.market_id
ORDER BY mt.total_relationships DESC
LIMIT 50;

-- ------------------------------------------------------------------------
-- 4. Category breakdown across events (NULLs = uncategorized by Kalshi)
-- ------------------------------------------------------------------------
SELECT
    COALESCE(category, '(uncategorized)') AS category,
    COUNT(*)                              AS event_count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_all_events
FROM events
GROUP BY category
ORDER BY event_count DESC;

-- ------------------------------------------------------------------------
-- 5. Markets closing soon with no relationship coverage yet
--    (data-quality / research backlog query)
-- ------------------------------------------------------------------------
SELECT
    m.ticker, m.title, m.close_time
FROM markets m
WHERE m.status = 'active'
  AND m.close_time BETWEEN NOW() AND NOW() + INTERVAL '7 days'
  AND NOT EXISTS (
      SELECT 1 FROM contract_relationships cr
      WHERE cr.market_id_1 = m.market_id OR cr.market_id_2 = m.market_id
  )
ORDER BY m.close_time
LIMIT 100;
