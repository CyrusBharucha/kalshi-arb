-- database/l2_schema.sql
-- ======================
-- PostgreSQL schema for L2 orderbook storage.
--
-- Design rationale
-- ----------------
-- At 3,450 msg/sec, writing every raw delta to Postgres is ~298M rows/day —
-- impractical for a research system.  Instead we use a two-tier strategy:
--
--   1. l2_snapshots   — periodic full-book snapshots (every N seconds per market)
--                       Compact JSONB representation; used for back-testing and
--                       replay.  Target: once per market per ~60 seconds.
--
--   2. l2_deltas_raw  — optional hot table for a SHORT rolling window (1 hour)
--                       partitioned by hour; auto-dropped after retention period.
--                       Only enabled if continuous tick-by-tick research is needed.
--                       Disabled by default to avoid storage explosion.
--
-- Snapshot format (l2_snapshots.book_json):
--   {
--     "yes_bids": [[price, qty], ...],   -- descending
--     "yes_asks": [[price, qty], ...],   -- ascending
--     "no_bids":  [[price, qty], ...],
--     "no_asks":  [[price, qty], ...]
--   }

-- ── 1. Periodic L2 snapshots ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS l2_snapshots (
    snapshot_id     BIGSERIAL PRIMARY KEY,
    market_id       TEXT        NOT NULL,
    snapped_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sequence        BIGINT      NOT NULL,
    delta_count     INT         NOT NULL DEFAULT 0,

    -- Top-of-book (denormalised for fast queries)
    yes_best_bid    NUMERIC(6,4),
    yes_best_ask    NUMERIC(6,4),
    no_best_bid     NUMERIC(6,4),
    no_best_ask     NUMERIC(6,4),

    -- Full depth as compact JSONB
    book_json       JSONB       NOT NULL,

    -- Depth counts (for quick aggregation without unpacking JSON)
    yes_bid_levels  SMALLINT    NOT NULL DEFAULT 0,
    yes_ask_levels  SMALLINT    NOT NULL DEFAULT 0,
    no_bid_levels   SMALLINT    NOT NULL DEFAULT 0,
    no_ask_levels   SMALLINT    NOT NULL DEFAULT 0
);

-- Index for time-series queries per market
CREATE INDEX IF NOT EXISTS l2_snapshots_market_time
    ON l2_snapshots (market_id, snapped_at DESC);

-- Index for scanning all snapshots in a time window
CREATE INDEX IF NOT EXISTS l2_snapshots_time
    ON l2_snapshots (snapped_at DESC);


-- ── 2. Optional hot delta table (partitioned by hour) ────────────────────────
-- Enable only if sub-second tick research is needed.
-- Each partition covers one hour; the retention job drops partitions older than
-- RETENTION_HOURS.

CREATE TABLE IF NOT EXISTS l2_deltas_raw (
    delta_id        BIGSERIAL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    market_id       TEXT        NOT NULL,
    sequence        BIGINT      NOT NULL,
    price           NUMERIC(6,4) NOT NULL,
    amount          NUMERIC(14,4) NOT NULL,
    side            TEXT        NOT NULL,   -- 'yes' | 'no'
    yes_best_bid    NUMERIC(6,4),
    yes_best_ask    NUMERIC(6,4),
    no_best_bid     NUMERIC(6,4),
    no_best_ask     NUMERIC(6,4)
) PARTITION BY RANGE (received_at);

-- Create partitions programmatically via Python (see database/l2_partition_manager.py)
-- Example: CREATE TABLE l2_deltas_raw_2026082400
--              PARTITION OF l2_deltas_raw
--              FOR VALUES FROM ('2026-08-24 00:00:00+00') TO ('2026-08-24 01:00:00+00');

CREATE INDEX IF NOT EXISTS l2_deltas_raw_market_time
    ON l2_deltas_raw (market_id, received_at DESC);


-- ── 3. Snapshot retention function ──────────────────────────────────────────

CREATE OR REPLACE FUNCTION drop_old_l2_snapshots(retention_days INT DEFAULT 7)
RETURNS INT AS $$
DECLARE
    deleted INT;
BEGIN
    DELETE FROM l2_snapshots
    WHERE snapped_at < now() - (retention_days || ' days')::INTERVAL;
    GET DIAGNOSTICS deleted = ROW_COUNT;
    RETURN deleted;
END;
$$ LANGUAGE plpgsql;


-- ── 4. Helper view: latest book per market ───────────────────────────────────

CREATE OR REPLACE VIEW l2_latest AS
SELECT DISTINCT ON (market_id)
    market_id,
    snapped_at,
    sequence,
    yes_best_bid,
    yes_best_ask,
    no_best_bid,
    no_best_ask,
    book_json,
    yes_bid_levels,
    yes_ask_levels,
    no_bid_levels,
    no_ask_levels
FROM l2_snapshots
ORDER BY market_id, snapped_at DESC;
