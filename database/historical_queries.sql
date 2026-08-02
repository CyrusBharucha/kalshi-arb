-- database/historical_queries.sql
-- ===================================
-- Historical trade/L2 analysis: spread behaviour, volume trends, and
-- time-series patterns using LAG/LEAD and rolling windows.
--
-- NOTE: order_book_snapshots is currently empty (0 rows). L2 snapshots
-- are written periodically by scripts/synthesis_l2_writer.py and stored
-- in the l2_snapshots table. Queries here are written against the intended
-- schema and will populate once the writer has been running.

-- ------------------------------------------------------------------------
-- 1. Trade price movement per market: tick-by-tick delta via LAG()
-- ------------------------------------------------------------------------
SELECT
    market_id,
    trade_ts,
    price,
    quantity,
    LAG(price) OVER (PARTITION BY market_id ORDER BY trade_ts)  AS prev_price,
    price - LAG(price) OVER (PARTITION BY market_id ORDER BY trade_ts) AS price_change,
    LEAD(trade_ts) OVER (PARTITION BY market_id ORDER BY trade_ts) - trade_ts AS time_to_next_trade
FROM trades
WHERE market_id = :market_id   -- bind a specific market_id
ORDER BY trade_ts;

-- ------------------------------------------------------------------------
-- 2. Daily trade volume and VWAP per market (window aggregation)
-- ------------------------------------------------------------------------
SELECT
    market_id,
    date_trunc('day', trade_ts)                          AS day,
    COUNT(*)                                              AS trade_count,
    SUM(quantity)                                         AS total_qty,
    ROUND(SUM(price * quantity) / NULLIF(SUM(quantity), 0), 4) AS vwap,
    MIN(price)                                            AS low,
    MAX(price)                                            AS high
FROM trades
GROUP BY market_id, date_trunc('day', trade_ts)
ORDER BY day DESC, total_qty DESC
LIMIT 100;

-- ------------------------------------------------------------------------
-- 3. Rolling 20-trade realized volatility proxy (stddev of price changes)
-- ------------------------------------------------------------------------
WITH deltas AS (
    SELECT
        market_id,
        trade_ts,
        price - LAG(price) OVER (PARTITION BY market_id ORDER BY trade_ts) AS delta
    FROM trades
)
SELECT
    market_id,
    trade_ts,
    ROUND(STDDEV(delta) OVER (
        PARTITION BY market_id ORDER BY trade_ts
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    )::numeric, 5) AS rolling_20trade_vol
FROM deltas
WHERE delta IS NOT NULL
ORDER BY market_id, trade_ts;

-- ------------------------------------------------------------------------
-- 4. L2 spread history for a market (once order_book_snapshots is loaded)
--    Shows best bid/ask reconstruction from per-level rows.
-- ------------------------------------------------------------------------
WITH best_levels AS (
    SELECT
        market_id,
        snapshot_ts,
        MAX(price) FILTER (WHERE side = 'bid') AS best_bid,
        MIN(price) FILTER (WHERE side = 'ask') AS best_ask
    FROM order_book_snapshots
    WHERE market_id = :market_id
    GROUP BY market_id, snapshot_ts
)
SELECT
    market_id,
    snapshot_ts,
    best_bid,
    best_ask,
    best_ask - best_bid                                    AS spread,
    (best_bid + best_ask) / 2.0                             AS mid,
    LAG(best_ask - best_bid) OVER (ORDER BY snapshot_ts)    AS prev_spread
FROM best_levels
ORDER BY snapshot_ts;

-- ------------------------------------------------------------------------
-- 5. Markets with the most trading activity historically (liquidity proxy)
-- ------------------------------------------------------------------------
SELECT
    m.ticker,
    m.title,
    COUNT(t.trade_id)                                       AS trade_count,
    SUM(t.quantity)                                          AS total_volume,
    MIN(t.trade_ts)                                          AS first_trade,
    MAX(t.trade_ts)                                          AS last_trade
FROM trades t
JOIN markets m ON m.market_id = t.market_id
GROUP BY m.ticker, m.title
ORDER BY trade_count DESC
LIMIT 50;
