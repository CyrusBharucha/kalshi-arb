-- =============================================================================
-- database/sql/historical_queries.sql
-- Kalshi Arbitrage Engine — Historical Data & Trade Analytics
-- =============================================================================
-- Demonstrates: time-bucketing, OHLC construction, trade aggregation,
-- multi-CTE pipelines, LAG/LEAD, PERCENTILE_CONT, and rolling windows.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Historical trade OHLC — 15-minute candles per market
-- ---------------------------------------------------------------------------
WITH bucketed AS (
    SELECT
        market_id,
        DATE_TRUNC('minute', trade_ts) -
            (EXTRACT(MINUTE FROM trade_ts)::INT % 15) * INTERVAL '1 minute'
                                                AS bucket,
        price,
        quantity
    FROM trades
    WHERE trade_ts IS NOT NULL
)
SELECT
    market_id,
    bucket                                      AS open_time,
    bucket + INTERVAL '15 minutes'              AS close_time,
    FIRST_VALUE(price) OVER (PARTITION BY market_id, bucket ORDER BY bucket)
                                                AS open_price,
    MAX(price)  OVER (PARTITION BY market_id, bucket)  AS high_price,
    MIN(price)  OVER (PARTITION BY market_id, bucket)  AS low_price,
    LAST_VALUE(price)  OVER (
        PARTITION BY market_id, bucket
        ORDER BY bucket
        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    )                                           AS close_price,
    SUM(quantity) OVER (PARTITION BY market_id, bucket) AS total_volume,
    COUNT(*)      OVER (PARTITION BY market_id, bucket) AS trade_count
FROM bucketed
GROUP BY market_id, bucket, price, quantity
ORDER BY market_id, bucket;


-- ---------------------------------------------------------------------------
-- 2. Volume-weighted average price (VWAP) by market and day
-- ---------------------------------------------------------------------------
SELECT
    market_id,
    DATE_TRUNC('day', trade_ts)              AS trade_day,
    COUNT(*)                                  AS trade_count,
    SUM(quantity)                             AS total_qty,
    ROUND(SUM(price * quantity) / NULLIF(SUM(quantity), 0)::NUMERIC, 6) AS vwap,
    ROUND(MIN(price)::NUMERIC, 4)             AS low,
    ROUND(MAX(price)::NUMERIC, 4)             AS high,
    ROUND(FIRST_VALUE(price) OVER (
        PARTITION BY market_id, DATE_TRUNC('day', trade_ts)
        ORDER BY trade_ts
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )::NUMERIC, 4)                            AS open_price,
    ROUND(LAST_VALUE(price) OVER (
        PARTITION BY market_id, DATE_TRUNC('day', trade_ts)
        ORDER BY trade_ts
        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    )::NUMERIC, 4)                            AS close_price
FROM trades
WHERE quantity > 0
  AND price > 0
GROUP BY market_id, DATE_TRUNC('day', trade_ts), price, quantity, trade_ts
ORDER BY market_id, trade_day;


-- ---------------------------------------------------------------------------
-- 3. Trade size distribution — large trades (block-size analysis)
-- ---------------------------------------------------------------------------
WITH trade_classes AS (
    SELECT
        market_id,
        trade_ts,
        price,
        quantity,
        CASE
            WHEN quantity >= 1000 THEN 'Block (≥1000)'
            WHEN quantity >= 100  THEN 'Large (100-999)'
            WHEN quantity >= 10   THEN 'Medium (10-99)'
            ELSE                       'Small (<10)'
        END AS size_class,
        CASE
            WHEN quantity >= 1000 THEN 4
            WHEN quantity >= 100  THEN 3
            WHEN quantity >= 10   THEN 2
            ELSE                       1
        END AS size_order
    FROM trades
)
SELECT
    market_id,
    size_class,
    size_order,
    COUNT(*)                               AS trade_count,
    SUM(quantity)                          AS total_qty,
    ROUND(AVG(price)::NUMERIC, 4)          AS avg_price,
    ROUND(SUM(price * quantity)::NUMERIC, 2) AS notional_value,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY market_id)::NUMERIC, 2)
                                           AS pct_of_trades,
    ROUND(100.0 * SUM(quantity) / SUM(SUM(quantity)) OVER (PARTITION BY market_id)::NUMERIC, 2)
                                           AS pct_of_volume
FROM trade_classes
GROUP BY market_id, size_class, size_order
ORDER BY market_id, size_order DESC;


-- ---------------------------------------------------------------------------
-- 4. Inter-trade duration (tick-to-tick time delta)
-- ---------------------------------------------------------------------------
WITH gaps AS (
    SELECT
        market_id,
        trade_ts,
        LAG(trade_ts) OVER (PARTITION BY market_id ORDER BY trade_ts) AS prev_trade,
        EXTRACT(EPOCH FROM (
            trade_ts - LAG(trade_ts) OVER (PARTITION BY market_id ORDER BY trade_ts)
        )) AS gap_seconds,
        price,
        LAG(price) OVER (PARTITION BY market_id ORDER BY trade_ts) AS prev_price
    FROM trades
)
SELECT
    market_id,
    COUNT(*)                                               AS gap_count,
    ROUND(AVG(gap_seconds)::NUMERIC, 2)                   AS avg_gap_s,
    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY gap_seconds)::NUMERIC, 2)
                                                           AS median_gap_s,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY gap_seconds)::NUMERIC, 2)
                                                           AS p95_gap_s,
    ROUND(MAX(gap_seconds)::NUMERIC, 1)                   AS max_gap_s,
    -- price impact of the gap
    ROUND(AVG(ABS(price - prev_price))::NUMERIC, 5)       AS avg_price_move,
    ROUND(MAX(ABS(price - prev_price))::NUMERIC, 5)       AS max_price_move
FROM gaps
WHERE gap_seconds IS NOT NULL
GROUP BY market_id
HAVING COUNT(*) >= 5
ORDER BY median_gap_s;


-- ---------------------------------------------------------------------------
-- 5. Cumulative trade volume over time (running total)
-- ---------------------------------------------------------------------------
WITH daily_volume AS (
    SELECT
        market_id,
        DATE_TRUNC('day', trade_ts) AS trade_day,
        SUM(quantity)                AS daily_qty,
        COUNT(*)                     AS daily_trades
    FROM trades
    GROUP BY market_id, DATE_TRUNC('day', trade_ts)
)
SELECT
    market_id,
    trade_day,
    daily_qty,
    daily_trades,
    SUM(daily_qty)    OVER (PARTITION BY market_id ORDER BY trade_day) AS cum_volume,
    SUM(daily_trades) OVER (PARTITION BY market_id ORDER BY trade_day) AS cum_trades,
    LAG(daily_qty)    OVER (PARTITION BY market_id ORDER BY trade_day) AS prev_day_qty,
    ROUND(100.0 * (daily_qty - LAG(daily_qty) OVER
        (PARTITION BY market_id ORDER BY trade_day))
        / NULLIF(LAG(daily_qty) OVER (PARTITION BY market_id ORDER BY trade_day), 0)
    ::NUMERIC, 2)                                                        AS vol_pct_chg
FROM daily_volume
ORDER BY market_id, trade_day;


-- ---------------------------------------------------------------------------
-- 6. Market price impact analysis — trades that moved the market
-- ---------------------------------------------------------------------------
WITH price_moves AS (
    SELECT
        ht.market_id,
        ht.trade_ts,
        ht.price,
        ht.quantity,
        ht.taker_side,
        LAG(ht.price) OVER (PARTITION BY ht.market_id ORDER BY ht.trade_ts) AS prev_price,
        ABS(ht.price - LAG(ht.price) OVER (PARTITION BY ht.market_id ORDER BY ht.trade_ts))
                                                          AS price_impact
    FROM trades ht
),
significant AS (
    SELECT *
    FROM price_moves
    WHERE price_impact >= 0.01   -- at least 1¢ move
      AND quantity > 0
)
SELECT
    market_id,
    COUNT(*)                                      AS large_moves,
    ROUND(AVG(quantity)::NUMERIC, 1)              AS avg_qty_at_move,
    ROUND(AVG(price_impact)::NUMERIC, 5)          AS avg_price_impact,
    ROUND(MAX(price_impact)::NUMERIC, 5)          AS max_price_impact,
    COUNT(*) FILTER (WHERE taker_side = 'yes')    AS yes_taker_moves,
    COUNT(*) FILTER (WHERE taker_side = 'no')     AS no_taker_moves,
    -- Kyle's lambda: price_impact per unit of volume (simplified)
    ROUND(SUM(price_impact) / NULLIF(SUM(quantity), 0)::NUMERIC, 8)
                                                  AS kyle_lambda_approx
FROM significant
GROUP BY market_id
HAVING COUNT(*) >= 3
ORDER BY avg_price_impact DESC;


-- ---------------------------------------------------------------------------
-- 7. Historical opportunity detection — arbitrage windows from tick data
-- ---------------------------------------------------------------------------
-- Identifies periods where the complement pair price diverged from 1.0
-- by looking at synchronized timestamps within a 60-second window.
-- ---------------------------------------------------------------------------
WITH complement_snapshots AS (
    SELECT
        cr.market_id_1,
        cr.market_id_2,
        s1.snapshot_ts,
        s1.yes_bid   AS m1_bid,
        s1.yes_ask   AS m1_ask,
        s2.yes_bid   AS m2_bid,
        s2.yes_ask   AS m2_ask,
        -- Complement arbitrage: BUY YES in m1, BUY YES in m2 (sum < 1)
        -- or SELL YES in m1, SELL YES in m2 (sum > 1)
        (s1.yes_ask + s2.yes_ask) - 1.0             AS ce_arb_cost,   -- negative = arb
        1.0 - (s1.yes_bid + s2.yes_bid)             AS me_arb_gain,   -- positive = arb
        EXTRACT(EPOCH FROM (s1.snapshot_ts - s2.snapshot_ts)) AS lag_s
    FROM contract_relationships cr
    JOIN market_snapshots s1 ON s1.market_id = cr.market_id_1
    JOIN market_snapshots s2 ON s2.market_id = cr.market_id_2
        AND ABS(EXTRACT(EPOCH FROM (s1.snapshot_ts - s2.snapshot_ts))) < 60
    WHERE cr.relationship_type = 'complement'
)
-- Wrapped so ORDER BY can rank on the sum of two aggregate aliases: an alias
-- may stand alone in ORDER BY, but not inside an expression.
SELECT *
FROM (
    SELECT
        market_id_1,
        market_id_2,
        COUNT(*) FILTER (WHERE ce_arb_cost < -0.005)    AS ce_arb_windows,
        COUNT(*) FILTER (WHERE me_arb_gain > 0.005)     AS me_arb_windows,
        ROUND(AVG(CASE WHEN ce_arb_cost < -0.005 THEN ABS(ce_arb_cost) END)::NUMERIC, 5)
                                                         AS avg_ce_edge,
        ROUND(AVG(CASE WHEN me_arb_gain > 0.005 THEN me_arb_gain END)::NUMERIC, 5)
                                                         AS avg_me_edge,
        ROUND(MIN(ce_arb_cost)::NUMERIC, 5)             AS best_ce_edge,
        ROUND(MAX(me_arb_gain)::NUMERIC, 5)             AS best_me_edge
    FROM complement_snapshots
    GROUP BY market_id_1, market_id_2
    HAVING
        COUNT(*) FILTER (WHERE ce_arb_cost < -0.005) > 0
        OR COUNT(*) FILTER (WHERE me_arb_gain > 0.005) > 0
) w
ORDER BY ce_arb_windows + me_arb_windows DESC;


-- ---------------------------------------------------------------------------
-- 8. Backtest trade performance by settlement outcome
-- ---------------------------------------------------------------------------
WITH trade_outcomes AS (
    SELECT
        bt.id                               AS trade_id,
        bt.market_id,
        -- backtest_trades records the run, not the strategy name; the strategy
        -- lives on backtest_results and is joined in below.
        br.strategy,
        bt.side,
        bt.entry_price,
        bt.exit_price,
        bt.quantity,
        bt.net_pnl                          AS pnl,
        (COALESCE(bt.taker_fees, 0) + COALESCE(bt.maker_fees, 0)) AS fee,
        bt.entry_ts                         AS trade_ts,
        m.settlement_value,
        m.category,
        -- Was trade directionally correct vs settlement?
        CASE
            WHEN bt.side = 'yes' AND m.settlement_value = 1.0 THEN 'correct'
            WHEN bt.side = 'no'  AND m.settlement_value = 0.0 THEN 'correct'
            WHEN bt.side = 'yes' AND m.settlement_value = 0.0 THEN 'incorrect'
            WHEN bt.side = 'no'  AND m.settlement_value = 1.0 THEN 'incorrect'
            ELSE 'unknown'
        END AS direction_accuracy
    FROM backtest_trades bt
    JOIN markets m ON m.market_id = bt.market_id
    LEFT JOIN backtest_results br ON br.id::TEXT = bt.run_id
    WHERE m.settlement_value IS NOT NULL
)
SELECT
    strategy,
    direction_accuracy,
    COUNT(*)                                        AS trade_count,
    ROUND(AVG(pnl)::NUMERIC, 5)                    AS avg_pnl,
    ROUND(SUM(pnl)::NUMERIC, 4)                    AS total_pnl,
    ROUND(AVG(fee)::NUMERIC, 6)                    AS avg_fee,
    ROUND(AVG(entry_price)::NUMERIC, 4)            AS avg_entry,
    ROUND(AVG(exit_price)::NUMERIC, 4)             AS avg_exit,
    ROUND(AVG(quantity)::NUMERIC, 1)               AS avg_qty
FROM trade_outcomes
GROUP BY strategy, direction_accuracy
ORDER BY strategy, direction_accuracy;


-- ---------------------------------------------------------------------------
-- 9. Settlement-day volume surge — detect anomalous trading activity
-- ---------------------------------------------------------------------------
WITH market_daily AS (
    SELECT
        ht.market_id,
        DATE_TRUNC('day', ht.trade_ts)   AS trade_day,
        SUM(ht.quantity)                   AS daily_qty,
        COUNT(*)                           AS daily_trades,
        m.close_time::DATE                 AS settlement_day
    FROM trades ht
    JOIN markets m ON m.market_id = ht.market_id
    GROUP BY ht.market_id, DATE_TRUNC('day', ht.trade_ts), m.close_time::DATE
),
with_avg AS (
    SELECT
        *,
        AVG(daily_qty) OVER (
            PARTITION BY market_id
            ORDER BY trade_day
            ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
        ) AS rolling_7d_avg_qty,
        trade_day::DATE = settlement_day AS is_settlement_day
    FROM market_daily
)
SELECT
    market_id,
    trade_day,
    daily_qty,
    ROUND(rolling_7d_avg_qty::NUMERIC, 1)         AS rolling_avg,
    ROUND(daily_qty / NULLIF(rolling_7d_avg_qty, 0)::NUMERIC, 2)
                                                   AS volume_ratio,
    is_settlement_day
FROM with_avg
WHERE rolling_7d_avg_qty IS NOT NULL
  AND daily_qty / NULLIF(rolling_7d_avg_qty, 0) >= 2.0
ORDER BY volume_ratio DESC
LIMIT 50;
