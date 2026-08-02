-- database/analytics.sql
-- =========================
-- Cross-cutting analytics: L2 depth analysis, data quality checks, and
-- Canadian vs non-Canadian comparison. Complements arbitrage_queries.sql
-- and historical_queries.sql with checks that don't fit either category.

-- ------------------------------------------------------------------------
-- 1. Data quality: crossed books (bid > ask) -- should never happen
-- ------------------------------------------------------------------------
WITH best_levels AS (
    SELECT
        market_id, snapshot_ts,
        MAX(price) FILTER (WHERE side = 'bid') AS best_bid,
        MIN(price) FILTER (WHERE side = 'ask') AS best_ask
    FROM order_book_snapshots
    GROUP BY market_id, snapshot_ts
)
SELECT market_id, snapshot_ts, best_bid, best_ask
FROM best_levels
WHERE best_bid > best_ask;

-- ------------------------------------------------------------------------
-- 2. Data quality: negative or impossible prices/quantities across tables
-- ------------------------------------------------------------------------
SELECT 'trades' AS source, trade_id::text AS row_id, price, quantity
FROM trades
WHERE price < 0 OR price > 1 OR quantity < 0
UNION ALL
SELECT 'order_book_snapshots', id::text, price, quantity
FROM order_book_snapshots
WHERE price < 0 OR price > 1 OR quantity < 0;

-- ------------------------------------------------------------------------
-- 3. Data quality: duplicate trades (same market/ts/price/qty/side)
-- ------------------------------------------------------------------------
SELECT market_id, trade_ts, price, quantity, taker_side, COUNT(*) AS dup_count
FROM trades
GROUP BY market_id, trade_ts, price, quantity, taker_side
HAVING COUNT(*) > 1
ORDER BY dup_count DESC
LIMIT 100;

-- ------------------------------------------------------------------------
-- 4. Data quality: orphan relationships (market_id no longer in markets)
-- ------------------------------------------------------------------------
SELECT cr.id, cr.market_id_1, cr.market_id_2
FROM contract_relationships cr
LEFT JOIN markets m1 ON m1.market_id = cr.market_id_1
LEFT JOIN markets m2 ON m2.market_id = cr.market_id_2
WHERE m1.market_id IS NULL OR m2.market_id IS NULL
LIMIT 100;

-- ------------------------------------------------------------------------
-- 5. Canadian vs non-Canadian: relationship + arbitrage coverage compare
-- ------------------------------------------------------------------------
WITH tagged AS (
    SELECT
        m.market_id,
        CASE WHEN e.canadian_relevance >= 1 THEN 'canadian' ELSE 'other' END AS bucket
    FROM markets m
    JOIN events e ON e.event_ticker = m.event_ticker
)
SELECT
    t.bucket,
    COUNT(DISTINCT t.market_id)                                    AS markets,
    COUNT(DISTINCT cr.id)                             AS relationships,
    ROUND(COUNT(DISTINCT cr.id)::numeric
          / NULLIF(COUNT(DISTINCT t.market_id), 0), 4)             AS relationships_per_market
FROM tagged t
LEFT JOIN contract_relationships cr
    ON cr.market_id_1 = t.market_id OR cr.market_id_2 = t.market_id
GROUP BY t.bucket;

-- ------------------------------------------------------------------------
-- 6. Ingestion health: rows ingested per job per day (from ingestion_log)
-- ------------------------------------------------------------------------
SELECT
    job_type,
    date_trunc('day', run_ts) AS day,
    COUNT(*)                  AS runs,
    SUM(rows_inserted)       AS total_rows,
    SUM(rows_skipped)          AS total_failed
FROM ingestion_log
GROUP BY job_type, date_trunc('day', run_ts)
ORDER BY day DESC, job_type;


-- ========================================================================
-- MARKET MICROSTRUCTURE
-- The queries below characterise trading activity and book quality.
-- They are the analytical counterpart to the data-quality checks above.
-- ========================================================================

-- ------------------------------------------------------------------------
-- 7. Rolling 7-day traded volume per market
--    CTE aggregates to daily grain, then a windowed frame sums the
--    trailing 7 days *within each market*. RANGE (not ROWS) is deliberate:
--    a market with no trades on a given day must not silently shorten the
--    lookback window.
-- ------------------------------------------------------------------------
WITH daily_volume AS (
    SELECT
        market_id,
        date_trunc('day', trade_ts)::date AS trade_day,
        SUM(quantity)                     AS contracts,
        SUM(price * quantity)             AS notional,
        COUNT(*)                          AS n_trades
    FROM trades
    GROUP BY market_id, date_trunc('day', trade_ts)::date
)
SELECT
    market_id,
    trade_day,
    contracts,
    n_trades,
    SUM(contracts) OVER (
        PARTITION BY market_id ORDER BY trade_day
        RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW
    )                                                   AS rolling_7d_contracts,
    ROUND(AVG(contracts) OVER (
        PARTITION BY market_id ORDER BY trade_day
        RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW
    ), 2)                                               AS rolling_7d_avg_contracts,
    ROUND(notional, 2)                                  AS daily_notional
FROM daily_volume
ORDER BY market_id, trade_day;

-- ------------------------------------------------------------------------
-- 8. Hourly trade-activity heatmap (day-of-week x hour-of-day, UTC)
--    PERCENT_RANK turns raw counts into a 0-1 intensity that the dashboard
--    maps straight onto a colour scale without a second normalising pass.
-- ------------------------------------------------------------------------
WITH bucketed AS (
    SELECT
        EXTRACT(DOW  FROM trade_ts)::int AS day_of_week,
        EXTRACT(HOUR FROM trade_ts)::int AS hour_of_day,
        COUNT(*)                         AS n_trades,
        SUM(quantity)                    AS contracts
    FROM trades
    WHERE trade_ts >= NOW() - INTERVAL '90 days'
    GROUP BY 1, 2
)
SELECT
    day_of_week,
    hour_of_day,
    n_trades,
    contracts,
    ROUND(PERCENT_RANK() OVER (ORDER BY n_trades)::numeric, 4) AS intensity,
    ROUND(100.0 * n_trades / SUM(n_trades) OVER (), 3)         AS pct_of_all_trades
FROM bucketed
ORDER BY day_of_week, hour_of_day;

-- ------------------------------------------------------------------------
-- 9. Bid-ask spread trend with LAG
--    Each L2 snapshot is compared with that market's previous snapshot, so
--    widening/tightening reads as a signed delta rather than a raw level.
--    A named WINDOW clause avoids repeating the OVER spec five times.
-- ------------------------------------------------------------------------
WITH spreads AS (
    SELECT
        market_id,
        snapped_at,
        yes_best_ask - yes_best_bid                    AS spread,
        (yes_best_ask + yes_best_bid) / 2.0            AS mid
    FROM l2_snapshots
    WHERE yes_best_bid IS NOT NULL
      AND yes_best_ask IS NOT NULL
)
SELECT
    market_id,
    snapped_at,
    ROUND(spread, 4)                                        AS spread,
    ROUND(mid, 4)                                           AS mid,
    ROUND(LAG(spread) OVER w, 4)                            AS prev_spread,
    ROUND(spread - LAG(spread) OVER w, 4)                   AS spread_delta,
    snapped_at - LAG(snapped_at) OVER w                     AS time_since_prev,
    ROUND(AVG(spread) OVER (
        PARTITION BY market_id ORDER BY snapped_at
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ), 4)                                                   AS spread_ma20,
    ROUND(100.0 * spread / NULLIF(mid, 0), 2)               AS relative_spread_pct
FROM spreads
WINDOW w AS (PARTITION BY market_id ORDER BY snapped_at)
ORDER BY market_id, snapped_at;

-- ------------------------------------------------------------------------
-- 10. VWAP per market per day, plus the day-over-day VWAP move
--     VWAP is the volume-weighted execution benchmark; comparing it with
--     the day's closing print highlights sessions where size traded away
--     from where the market finished.
-- ------------------------------------------------------------------------
WITH daily AS (
    SELECT
        t.market_id,
        date_trunc('day', t.trade_ts)::date              AS trade_day,
        SUM(t.price * t.quantity) / NULLIF(SUM(t.quantity), 0) AS vwap,
        SUM(t.quantity)                                  AS contracts,
        COUNT(*)                                         AS n_trades,
        MIN(t.price)                                     AS low_price,
        MAX(t.price)                                     AS high_price,
        (ARRAY_AGG(t.price ORDER BY t.trade_ts DESC))[1] AS close_price,
        (ARRAY_AGG(t.price ORDER BY t.trade_ts ASC))[1]  AS open_price
    FROM trades t
    GROUP BY t.market_id, date_trunc('day', t.trade_ts)::date
)
SELECT
    d.market_id,
    m.title,
    d.trade_day,
    ROUND(d.vwap, 4)                                        AS vwap,
    ROUND(d.open_price, 4)                                  AS open_price,
    ROUND(d.close_price, 4)                                 AS close_price,
    ROUND(d.high_price - d.low_price, 4)                    AS day_range,
    d.contracts,
    d.n_trades,
    ROUND(d.close_price - d.vwap, 4)                        AS close_minus_vwap,
    ROUND(d.vwap - LAG(d.vwap) OVER (
        PARTITION BY d.market_id ORDER BY d.trade_day
    ), 4)                                                   AS vwap_change
FROM daily d
JOIN markets m ON m.market_id = d.market_id
ORDER BY d.market_id, d.trade_day;

-- ------------------------------------------------------------------------
-- 11. Microstructure: effective spread and post-trade price impact
--     Effective spread = 2 * |trade_price - mid| (Lee-Ready convention).
--     Price impact     = signed mid move over the 5 minutes after the fill.
--     Each trade is matched to the last L2 snapshot at or before it via a
--     LATERAL top-1 lookup -- far cheaper than a BETWEEN range JOIN.
-- ------------------------------------------------------------------------
WITH quoted AS (
    SELECT
        t.trade_id,
        t.market_id,
        t.trade_ts,
        t.price,
        t.quantity,
        t.taker_side,
        b.mid_at_trade,
        b.spread_at_trade
    FROM trades t
    CROSS JOIN LATERAL (
        SELECT
            (s.yes_best_bid + s.yes_best_ask) / 2.0 AS mid_at_trade,
            s.yes_best_ask - s.yes_best_bid         AS spread_at_trade
        FROM l2_snapshots s
        WHERE s.market_id = t.market_id
          AND s.snapped_at <= t.trade_ts
          AND s.yes_best_bid IS NOT NULL
          AND s.yes_best_ask IS NOT NULL
        ORDER BY s.snapped_at DESC
        LIMIT 1
    ) b
    WHERE t.trade_ts >= NOW() - INTERVAL '30 days'
),
impacted AS (
    SELECT
        q.*,
        f.mid_after
    FROM quoted q
    LEFT JOIN LATERAL (
        SELECT (s.yes_best_bid + s.yes_best_ask) / 2.0 AS mid_after
        FROM l2_snapshots s
        WHERE s.market_id = q.market_id
          AND s.snapped_at >= q.trade_ts + INTERVAL '5 minutes'
          AND s.yes_best_bid IS NOT NULL
          AND s.yes_best_ask IS NOT NULL
        ORDER BY s.snapped_at ASC
        LIMIT 1
    ) f ON TRUE
)
SELECT
    market_id,
    COUNT(*)                                                    AS n_trades,
    ROUND(AVG(2 * ABS(price - mid_at_trade)), 5)                AS avg_effective_spread,
    ROUND(AVG(spread_at_trade), 5)                              AS avg_quoted_spread,
    ROUND(AVG(2 * ABS(price - mid_at_trade))
          / NULLIF(AVG(spread_at_trade), 0), 3)                 AS effective_to_quoted_ratio,
    ROUND(AVG(
        CASE WHEN taker_side = 'yes' THEN  (mid_after - mid_at_trade)
             WHEN taker_side = 'no'  THEN -(mid_after - mid_at_trade)
        END
    ), 5)                                                        AS avg_signed_impact_5m,
    ROUND(SUM(quantity), 2)                                      AS total_contracts
FROM impacted
WHERE mid_at_trade IS NOT NULL
GROUP BY market_id
HAVING COUNT(*) >= 20
ORDER BY avg_effective_spread DESC;

-- ------------------------------------------------------------------------
-- 12. Opportunity persistence: how long does an arb actually survive?
--     Buckets closed opportunities by lifetime so the research page can
--     state plainly what fraction last long enough to be routable.
-- ------------------------------------------------------------------------
WITH lifetimes AS (
    SELECT
        classification,
        strategy_type,
        COALESCE(
            duration_seconds,
            EXTRACT(EPOCH FROM (closed_at - detected_at))
        ) AS lifetime_s
    FROM arbitrage_opportunities
    WHERE closed_at IS NOT NULL OR duration_seconds IS NOT NULL
),
bucketed AS (
    SELECT
        classification,
        strategy_type,
        lifetime_s,
        CASE
            WHEN lifetime_s <    1 THEN '0. sub-second'
            WHEN lifetime_s <    5 THEN '1. 1-5s'
            WHEN lifetime_s <   30 THEN '2. 5-30s'
            WHEN lifetime_s <  300 THEN '3. 30s-5m'
            WHEN lifetime_s < 3600 THEN '4. 5m-1h'
            ELSE                        '5. 1h+'
        END AS lifetime_bucket
    FROM lifetimes
)
SELECT
    classification,
    lifetime_bucket,
    COUNT(*)                                                    AS n_opps,
    ROUND(AVG(lifetime_s)::numeric, 2)                          AS avg_lifetime_s,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY lifetime_s)::numeric, 2)                       AS median_lifetime_s,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (
        PARTITION BY classification), 2)                        AS pct_within_class
FROM bucketed
GROUP BY classification, lifetime_bucket
ORDER BY classification, lifetime_bucket;

-- ------------------------------------------------------------------------
-- 13. Top markets by edge frequency
--     markets_involved is TEXT[], so UNNEST flattens each opportunity into
--     one row per leg before ranking. DENSE_RANK keeps ties on the same
--     rank, which matters when many markets share an opportunity count.
-- ------------------------------------------------------------------------
WITH legs AS (
    SELECT
        UNNEST(o.markets_involved) AS market_id,
        o.classification,
        o.net_edge,
        o.max_net_profit,
        o.detected_at
    FROM arbitrage_opportunities o
    WHERE o.net_edge > 0
),
per_market AS (
    SELECT
        l.market_id,
        COUNT(*)                                              AS n_opportunities,
        COUNT(*) FILTER (WHERE l.classification = 'A')        AS n_class_a,
        ROUND(AVG(l.net_edge), 4)                             AS avg_net_edge,
        ROUND(MAX(l.net_edge), 4)                             AS best_net_edge,
        ROUND(SUM(l.max_net_profit), 2)                       AS cumulative_net_profit,
        MIN(l.detected_at)                                    AS first_seen,
        MAX(l.detected_at)                                    AS last_seen
    FROM legs l
    GROUP BY l.market_id
)
SELECT
    p.market_id,
    m.title,
    m.category,
    p.n_opportunities,
    p.n_class_a,
    p.avg_net_edge,
    p.best_net_edge,
    p.cumulative_net_profit,
    p.first_seen,
    p.last_seen,
    DENSE_RANK() OVER (ORDER BY p.n_opportunities DESC)                 AS freq_rank,
    DENSE_RANK() OVER (ORDER BY p.cumulative_net_profit DESC NULLS LAST) AS profit_rank
FROM per_market p
LEFT JOIN markets m ON m.market_id = p.market_id
ORDER BY p.n_opportunities DESC
LIMIT 50;

