# database/sql/ — Analytics SQL Layer

Pure SQL analytics, views, and schema maintenance for the Kalshi Arbitrage Engine.
All files use PostgreSQL-specific syntax and are intended to run against the production
`kalshi_arb` database.

> **Design principle**: SQL must be prominently visible on GitHub — not hidden behind
> ORM abstractions. Every analytical query is written as a standalone `.sql` file with
> comments explaining the business logic and SQL techniques used.

---

## Files

| File | Queries | Key SQL Features |
|------|---------|-----------------|
| `arbitrage_queries.sql` | 10 | CTEs, LAG/LEAD, PERCENTILE_CONT, UNNEST, CROSS JOIN LATERAL, RANK |
| `market_queries.sql` | 9 | NTILE, DENSE_RANK, PERCENTILE_CONT, LAG, UNNEST, window functions |
| `l2_orderbook_queries.sql` | 7 | JSONB operators, DISTINCT ON, LAG, time-bucketing, depth scoring |
| `historical_queries.sql` | 9 | OHLC construction, VWAP, FIRST_VALUE, cumulative sums, rolling windows |
| `performance_queries.sql` | 8 | pg_stat_user_tables, pg_size_pretty, GIN index usage, latency histograms |
| `views.sql` | 7 | CREATE VIEW, CREATE MATERIALIZED VIEW, LATERAL joins, quality scores |
| `indexes.sql` | 25 | CREATE INDEX CONCURRENTLY, GIN, composite, partial, unique indexes |

---

## Arbitrage Queries (`arbitrage_queries.sql`)

Opportunities detected by the live and historical scanners.

```sql
-- Best opportunities by net edge
SELECT opportunity_id, strategy_type, net_edge,
       net_edge * max_executable_contracts AS max_net_profit
FROM arbitrage_opportunities WHERE net_edge > 0
ORDER BY net_edge DESC LIMIT 100;

-- Rolling 7-day opportunity count
SELECT day, n_opps,
       SUM(n_opps) OVER (ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
           AS rolling_7d_opps
FROM (SELECT DATE_TRUNC('day', detected_at) AS day, COUNT(*) AS n_opps
      FROM arbitrage_opportunities GROUP BY 1) daily
ORDER BY day DESC;
```

---

## Market Queries (`market_queries.sql`)

Market universe analytics — volume tiers, spread, liquidity, settlement rates.

```sql
-- Volume tiers with NTILE
SELECT NTILE(5) OVER (ORDER BY volume NULLS LAST) AS volume_quintile,
       AVG(yes_ask - yes_bid) AS avg_spread
FROM markets WHERE yes_bid IS NOT NULL GROUP BY 1;
```

---

## L2 Order Book Queries (`l2_orderbook_queries.sql`)

Real-time book depth, spread evolution, VWAP estimates, and complement validation.

```sql
-- Book staleness: large gap detection using LAG
WITH gaps AS (
    SELECT market_id,
           EXTRACT(EPOCH FROM (snapped_at
               - LAG(snapped_at) OVER (PARTITION BY market_id ORDER BY snapped_at)))
               AS gap_seconds
    FROM l2_snapshots
)
SELECT market_id, MAX(gap_seconds) AS max_gap_s FROM gaps GROUP BY 1;
```

---

## Historical Queries (`historical_queries.sql`)

Trade analytics — OHLC construction, VWAP, price impact, inter-trade gaps.

```sql
-- VWAP per market per day
SELECT market_id, DATE_TRUNC('day', traded_at) AS day,
       SUM(price * quantity) / NULLIF(SUM(quantity), 0) AS vwap
FROM historical_trades GROUP BY 1, 2;
```

---

## Performance Queries (`performance_queries.sql`)

System health — ingestion monitoring, latency histograms, data quality scoring,
table bloat, index usage statistics.

```sql
-- Table sizes via pg_stat_user_tables
SELECT tablename, pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename))
FROM pg_stat_user_tables WHERE schemaname = 'public' ORDER BY 2 DESC;
```

---

## Views (`views.sql`)

Reusable views encapsulating complex joins. Materialized views power dashboard performance.

```sql
-- Refresh materialized views (run via cron or Streamlit admin page):
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_daily_arb_summary;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_market_quality_scores;
```

---

## Indexes (`indexes.sql`)

25 indexes covering all high-frequency query patterns. Use `CONCURRENTLY` to avoid
table locks during creation in production.

```sql
-- GIN index for array column search
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_arb_markets_gin
    ON arbitrage_opportunities USING GIN (markets_involved);

-- Partial index for high-confidence relationships
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rels_high_confidence
    ON contract_relationships (confidence DESC)
    WHERE confidence >= 0.90;
```

---

## Usage

These files are **not run automatically** — they are reference SQL for:
1. **GitHub visibility**: demonstrates SQL depth and analytical sophistication
2. **Manual exploration**: run in `psql` or a SQL editor against the live DB
3. **Dashboard queries**: adapted into `dashboard/data_layer.py` for Streamlit
4. **Documentation**: each file is self-documenting with `-- ----` comment blocks

To run against the local DB:
```bash
psql $DATABASE_URL -f database/sql/arbitrage_queries.sql
```
