"""
scripts/synthesis_l2_writer.py
================================
Connects to Synthesis WebSocket and snapshots the full L2 cache into
l2_snapshots every SNAP_INTERVAL seconds (default 60s).

This keeps l2_snapshots fresh for the live arb scanner and dashboard
without requiring the Kalshi account WebSocket.

Run:
    python scripts/synthesis_l2_writer.py
    python scripts/synthesis_l2_writer.py --interval 30
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from database.repository import get_engine
from feeds.orderbook_l2 import L2Cache
from feeds.synthesis_live import SynthesisWebSocketClient

log = logging.getLogger("synthesis_l2_writer")

SNAP_INTERVAL = 60      # seconds between snapshots to DB
STATUS_INTERVAL = 60    # seconds between status log lines


def _get_db_open_tickers(engine) -> List[str]:
    """Fallback: get open market IDs from our DB."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT market_id FROM markets WHERE status = 'open' LIMIT 2000"
            )).fetchall()
        return [r[0] for r in rows]
    except Exception as exc:
        log.warning("DB market lookup failed: %s", exc)
        return []


def _snapshot_cache(engine, cache: L2Cache, snapped_at: datetime) -> int:
    """Write current top-of-book for every market in cache to l2_snapshots."""
    snap = cache.snapshot()
    if not snap:
        return 0

    rows = []
    ts_str = snapped_at.isoformat()
    for market_id, book in snap.items():
        tob = cache.top_of_book(market_id)
        if tob is None:
            continue

        yes_bid = tob.get("yes_bid")
        yes_ask = tob.get("yes_ask")

        # Build compact book JSON (top 10 levels per side)
        book_data = {
            "yes_bids": [[lvl.price, lvl.quantity] for lvl in book.yes_bids(10)],
            "yes_asks": [[lvl.price, lvl.quantity] for lvl in book.yes_asks(10)],
            "no_bids":  [[lvl.price, lvl.quantity] for lvl in book.no_bids(10)],
            "no_asks":  [[lvl.price, lvl.quantity] for lvl in book.no_asks(10)],
            "source": "synthesis",
        }

        rows.append({
            "market_id":    market_id,
            "snapped_at":   ts_str,
            "yes_best_bid": float(yes_bid) if yes_bid is not None else None,
            "yes_best_ask": float(yes_ask) if yes_ask is not None else None,
            "book_json":    json.dumps(book_data),
            "sequence":     book.sequence,
        })

    if not rows:
        return 0

    inserted = 0
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO l2_snapshots
                    (market_id, snapped_at, yes_best_bid, yes_best_ask,
                     book_json, sequence)
                SELECT
                    :market_id,
                    CAST(:snapped_at AS TIMESTAMPTZ),
                    :yes_best_bid,
                    :yes_best_ask,
                    CAST(:book_json AS jsonb),
                    :sequence
                ON CONFLICT (market_id, snapped_at) DO NOTHING
            """), rows)
        inserted = len(rows)
    except Exception as exc:
        log.error("Snapshot insert failed: %s", exc)

    return inserted


def run(snap_interval: int = SNAP_INTERVAL):
    engine = get_engine()

    # Build market ticker list from DB open markets
    tickers = _get_db_open_tickers(engine)
    if not tickers:
        log.error("No market tickers found — cannot subscribe to Synthesis")
        return

    secret = os.environ.get("SYNTHESIS_SECRET_KEY")
    if not secret:
        raise RuntimeError("SYNTHESIS_SECRET_KEY not set")

    cache  = L2Cache()
    client = SynthesisWebSocketClient(secret, cache, markets=tickers)

    log.info("Starting Synthesis L2 writer — %d markets, snapshot every %ds",
             len(tickers), snap_interval)
    ws_thread = client.start()

    # Allow initial snapshot to arrive
    time.sleep(5)

    total_snapped = 0
    last_snap     = time.monotonic()
    last_status   = time.monotonic()
    start         = time.monotonic()

    try:
        while True:
            now = time.monotonic()

            if now - last_snap >= snap_interval:
                ts = datetime.now(timezone.utc)
                n  = _snapshot_cache(engine, cache, ts)
                total_snapped += n
                last_snap = now
                log.info("Snapshot: %d rows written (cache=%d markets, total=%d)",
                         n, len(cache), total_snapped)

            if now - last_status >= STATUS_INTERVAL:
                ws = client.stats
                log.info("Status: elapsed=%.0fs  markets_cached=%d  deltas=%d  "
                         "snapshots_written=%d  reconnects=%d",
                         now - start, len(cache),
                         ws["total_deltas"], total_snapped, ws["reconnects"])
                last_status = now

            time.sleep(5)

    except KeyboardInterrupt:
        log.info("Interrupted — shutting down")
    finally:
        client.stop()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Synthesis WebSocket → l2_snapshots writer")
    parser.add_argument("--interval", type=int, default=SNAP_INTERVAL,
                        help="Seconds between snapshots to DB (default 60)")
    args = parser.parse_args()
    run(snap_interval=args.interval)
