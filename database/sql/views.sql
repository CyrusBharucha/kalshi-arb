-- =============================================================================
-- database/sql/views.sql
-- Kalshi Arbitrage Engine — Materialized Views & Reporting Views
-- =============================================================================
-- Reusable views encapsulating complex joins and aggregations.
-- Refresh materialized views periodically for dashboard performance.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. vw_active_markets  — enriched view of currently open markets
-- ---------------------------------------------------------------------------
-- Quotes, volume and open interest are point-in-time observations and live in
-- market_snapshots, not on the market row itself; the LATERAL pulls the most
-- recent one per market. Canadian relevance is scored on the EVENT, so every
-- market inherits the flag through its event_ticker.
CREATE OR REPLACE VIEW vw_active_markets AS
SELECT
    m.market_id,
    m.ticker,
    m.event_ticker,
    m.title,
    m.category,
    m.status,
    s.yes_bid,
    s.yes_ask,
    ROUND((s.yes_ask - s.yes_bid)::NUMERIC, 4)                AS spread,
    ROUND(((s.yes_bid + s.yes_ask) / 2.0)::NUMERIC, 4)        AS midpoint,
    ROUND(s.volume::NUMERIC, 0)                               AS volume,
    ROUND(s.open_interest::NUMERIC, 0)                        AS open_interest,
    s.snapshot_ts                                             AS quote_as_of,
    m.close_time,
    EXTRACT(EPOCH FROM (m.close_time - NOW())) / 3600.0       AS hours_to_close,
    (COALESCE(e.canadian_relevance, 0) >= 1)                  AS is_canadian,
    e.canadian_relevance,
    e.title                                                   AS event_title
FROM markets m
JOIN events e ON e.event_ticker = m.event_ticker
LEFT JOIN LATERAL (
    SELECT ms.yes_bid, ms.yes_ask, ms.volume,
           ms.open_interest, ms.snapshot_ts
    FROM market_snapshots ms
    WHERE ms.market_id = m.market_id
    ORDER BY ms.snapshot_ts DESC
    LIMIT 1
) s ON TRUE
WHERE m.status IN ('open','active');


-- ---------------------------------------------------------------------------
-- 2. vw_relationship_coverage — which events have non-trivial relationships?
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_relationship_coverage AS
WITH event_rels AS (
    SELECT
        m.event_ticker,
        COUNT(DISTINCT cr.id)                                  AS total_rels,
        COUNT(DISTINCT cr.id) FILTER (WHERE cr.relationship_type != 'complement')
                                                               AS non_trivial_rels,
        COUNT(DISTINCT m.market_id)                            AS market_count
    FROM markets m
    LEFT JOIN contract_relationships cr
        ON cr.market_id_1 = m.market_id OR cr.market_id_2 = m.market_id
    GROUP BY m.event_ticker
)
SELECT
    er.event_ticker,
    e.title                  AS event_title,
    er.market_count,
    er.total_rels,
    er.non_trivial_rels,
    er.non_trivial_rels > 0  AS is_arb_candidate
FROM event_rels er
JOIN events e ON e.event_ticker = er.event_ticker;


-- ---------------------------------------------------------------------------
-- 3. vw_latest_l2  — most-recent L2 snapshot per market
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_latest_l2 AS
SELECT DISTINCT ON (market_id)
    market_id,
    snapped_at,
    yes_best_bid,
    yes_best_ask,
    ROUND((yes_best_ask - yes_best_bid)::NUMERIC, 4) AS spread,
    no_best_bid,
    no_best_ask,
    yes_bid_levels,
    yes_ask_levels,
    sequence
FROM l2_snapshots
ORDER BY market_id, snapped_at DESC;


-- ---------------------------------------------------------------------------
-- 4. vw_arb_opportunities_enriched — opportunities with market titles
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_arb_opportunities_enriched AS
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
    ao.net_edge * ao.max_executable_contracts          AS max_net_profit,
    ao.markets_involved,
    ao.status,
    -- Pull first market's details for context
    m.title                                            AS primary_market_title,
    m.category                                         AS primary_market_category,
    m.event_ticker
FROM arbitrage_opportunities ao
LEFT JOIN LATERAL (
    SELECT m.title, m.category, m.event_ticker
    FROM markets m
    WHERE m.market_id = ANY(ao.markets_involved::TEXT[])
    LIMIT 1
) m ON true;


-- ---------------------------------------------------------------------------
-- 5. vw_canadian_opportunities — Canadian-flagged arb candidates
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_canadian_opportunities AS
SELECT
    ao.opportunity_id,
    ao.strategy_type,
    ao.gross_edge,
    ao.net_edge,
    ao.detected_at,
    ao.markets_involved,
    COUNT(m_can.market_id)  AS canadian_market_count
FROM arbitrage_opportunities ao
JOIN LATERAL UNNEST(ao.markets_involved::TEXT[]) AS mid(market_id) ON true
JOIN markets m_can ON m_can.market_id = mid.market_id
JOIN events e_can ON e_can.event_ticker = m_can.event_ticker
                 AND e_can.canadian_relevance >= 1
GROUP BY ao.opportunity_id, ao.strategy_type, ao.gross_edge, ao.net_edge,
         ao.detected_at, ao.markets_involved
HAVING COUNT(m_can.market_id) > 0;


-- ---------------------------------------------------------------------------
-- 6. mv_daily_arb_summary — MATERIALIZED VIEW for daily stats
--    Refresh with: REFRESH MATERIALIZED VIEW mv_daily_arb_summary;
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_daily_arb_summary AS
SELECT
    DATE_TRUNC('day', detected_at)             AS day,
    strategy_type,
    COUNT(*)                                   AS opp_count,
    ROUND(AVG(gross_edge)::NUMERIC, 5)         AS avg_gross_edge,
    ROUND(AVG(net_edge)::NUMERIC, 5)           AS avg_net_edge,
    ROUND(MAX(net_edge)::NUMERIC, 5)           AS max_net_edge,
    ROUND(SUM(net_edge * max_executable_contracts)::NUMERIC, 4) AS total_value
FROM arbitrage_opportunities
WHERE detected_at IS NOT NULL
GROUP BY DATE_TRUNC('day', detected_at), strategy_type
WITH NO DATA;

-- Populate the materialized view on first load:
-- REFRESH MATERIALIZED VIEW mv_daily_arb_summary;

CREATE UNIQUE INDEX IF NOT EXISTS mv_daily_arb_summary_pk
    ON mv_daily_arb_summary (day, strategy_type);


-- ---------------------------------------------------------------------------
-- 7. mv_market_quality_scores — MATERIALIZED VIEW for dashboard quality scores
--    Refresh periodically (every 15 minutes) for Streamlit dashboard.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_market_quality_scores AS
WITH recent_snaps AS (
    SELECT
        market_id,
        MAX(snapped_at)                        AS last_snap,
        COUNT(*)                               AS snap_count,
        EXTRACT(EPOCH FROM (MAX(snapped_at) - MIN(snapped_at))) AS coverage_s,
        -- top-of-book from the most recent snapshot in the window
        (ARRAY_AGG(yes_best_bid ORDER BY snapped_at DESC))[1] AS yes_bid,
        (ARRAY_AGG(yes_best_ask ORDER BY snapped_at DESC))[1] AS yes_ask
    FROM l2_snapshots
    WHERE snapped_at >= NOW() - INTERVAL '7 days'
    GROUP BY market_id
)
SELECT
    m.market_id,
    m.title,
    m.category,
    m.status,
    COALESCE(rs.last_snap, '-infinity'::TIMESTAMPTZ)    AS last_snapshot,
    COALESCE(rs.snap_count, 0)                          AS snapshot_count_7d,
    -- Quality score 0-30 (recency 10 + density 10 + spread 10)
    CASE WHEN rs.last_snap >= NOW() - INTERVAL '1 hour'  THEN 10
         WHEN rs.last_snap >= NOW() - INTERVAL '6 hours' THEN 6
         WHEN rs.last_snap >= NOW() - INTERVAL '24 hours' THEN 3
         ELSE 0
    END +
    CASE WHEN COALESCE(rs.snap_count, 0) /
              GREATEST(COALESCE(rs.coverage_s, 1) / 3600.0, 1) >= 10 THEN 10
         WHEN COALESCE(rs.snap_count, 0) /
              GREATEST(COALESCE(rs.coverage_s, 1) / 3600.0, 1) >= 2  THEN 5
         ELSE 0
    END +
    CASE WHEN rs.yes_ask IS NOT NULL AND rs.yes_bid IS NOT NULL
              AND (rs.yes_ask - rs.yes_bid) < 0.10 THEN 10
         WHEN rs.yes_ask IS NOT NULL AND rs.yes_bid IS NOT NULL THEN 5
         ELSE 0
    END                                               AS quality_score
FROM markets m
LEFT JOIN recent_snaps rs ON rs.market_id = m.market_id
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS mv_market_quality_scores_pk
    ON mv_market_quality_scores (market_id);
