-- database/performance_queries.sql
-- ===================================
-- Backtest performance analytics: returns, drawdown, win rate, and
-- capital utilization, computed directly in SQL from backtest_trades.

-- ------------------------------------------------------------------------
-- 1. Per-run summary stats: total return, win rate, profit factor
-- ------------------------------------------------------------------------
SELECT
    run_id,
    COUNT(*)                                              AS n_trades,
    COUNT(*) FILTER (WHERE net_pnl > 0)              AS n_winners,
    COUNT(*) FILTER (WHERE net_pnl < 0)              AS n_losers,
    ROUND(100.0 * COUNT(*) FILTER (WHERE net_pnl > 0)
          / NULLIF(COUNT(*), 0), 2)                        AS win_rate_pct,
    ROUND(SUM(net_pnl), 4)                            AS total_pnl,
    ROUND(SUM(net_pnl) FILTER (WHERE net_pnl > 0), 4) AS gross_profit,
    ROUND(ABS(SUM(net_pnl) FILTER (WHERE net_pnl < 0)), 4) AS gross_loss,
    ROUND(
        SUM(net_pnl) FILTER (WHERE net_pnl > 0)
        / NULLIF(ABS(SUM(net_pnl) FILTER (WHERE net_pnl < 0)), 0)
    , 3)                                                    AS profit_factor,
    ROUND(AVG(net_pnl), 4)                             AS avg_trade,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY net_pnl)::numeric, 4)                 AS median_trade
FROM backtest_trades
GROUP BY run_id
ORDER BY total_pnl DESC;

-- ------------------------------------------------------------------------
-- 2. Equity curve + running max + drawdown (window functions)
-- ------------------------------------------------------------------------
WITH ordered AS (
    SELECT
        run_id,
        entry_ts,
        net_pnl,
        SUM(net_pnl) OVER (
            PARTITION BY run_id ORDER BY entry_ts
        ) AS cumulative_pnl
    FROM backtest_trades
    WHERE run_id = :run_id
)
SELECT
    entry_ts,
    net_pnl,
    cumulative_pnl,
    MAX(cumulative_pnl) OVER (ORDER BY entry_ts)            AS running_peak,
    cumulative_pnl - MAX(cumulative_pnl) OVER (ORDER BY entry_ts) AS drawdown
FROM ordered
ORDER BY entry_ts;

-- ------------------------------------------------------------------------
-- 3. Max drawdown per run (single scalar per run_id)
-- ------------------------------------------------------------------------
WITH ordered AS (
    SELECT
        run_id,
        entry_ts,
        SUM(net_pnl) OVER (
            PARTITION BY run_id ORDER BY entry_ts
        ) AS cumulative_pnl
    FROM backtest_trades
),
drawdowns AS (
    SELECT
        run_id,
        cumulative_pnl - MAX(cumulative_pnl) OVER (
            PARTITION BY run_id ORDER BY entry_ts
        ) AS drawdown
    FROM ordered
)
SELECT
    run_id,
    ROUND(MIN(drawdown), 4) AS max_drawdown
FROM drawdowns
GROUP BY run_id
ORDER BY max_drawdown ASC;

-- ------------------------------------------------------------------------
-- 4. Trade holding-period distribution (avg / median / p90)
-- ------------------------------------------------------------------------
SELECT
    run_id,
    ROUND(AVG(EXTRACT(EPOCH FROM (exit_ts - entry_ts)))::numeric, 1)   AS avg_hold_s,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (exit_ts - entry_ts)))::numeric, 1) AS median_hold_s,
    ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (exit_ts - entry_ts)))::numeric, 1) AS p90_hold_s
FROM backtest_trades
WHERE exit_ts IS NOT NULL
GROUP BY run_id;

-- ------------------------------------------------------------------------
-- 5. Capital utilization: overlapping open positions over time
--    (classic "concurrent intervals" problem via generate_series + LATERAL)
-- ------------------------------------------------------------------------
SELECT
    run_id,
    date_trunc('hour', entry_ts)                            AS hour,
    COUNT(*)                                                 AS trades_opened,
    SUM(quantity)                                       AS capital_deployed
FROM backtest_trades
GROUP BY run_id, date_trunc('hour', entry_ts)
ORDER BY hour;
