"""
data/l2_snapshot_writer.py
==========================
Periodically writes L2 book snapshots to PostgreSQL (l2_snapshots table).

Usage
-----
    from feeds.l2_snapshot_writer import L2SnapshotWriter
    writer = L2SnapshotWriter(session_factory, cache, interval_seconds=60)
    writer.start()          # background thread
    ...
    writer.stop()

Storage design
--------------
One row per (market_id, snapshot_time).  At 60s interval, ~9,000 markets
gives 9,000 rows/min = 540K rows/hour = 13M rows/day.  With compression
and 7-day retention this is ~<5 GB, acceptable for a research system.

Optionally call writer.flush(market_id) to force an immediate snapshot
for a specific market (e.g. before a known event).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 60     # seconds between snapshots per market
_SNAPSHOT_BATCH   = 200    # markets per DB round-trip


class L2SnapshotWriter:
    """Background thread that drains L2Cache -> l2_snapshots table."""

    def __init__(
        self,
        session_factory: Callable,      # returns a SQLAlchemy session context
        cache,                          # L2Cache instance
        interval_seconds: float = _DEFAULT_INTERVAL,
    ):
        self._session_factory = session_factory
        self._cache           = cache
        self._interval        = interval_seconds
        self._stop_evt        = threading.Event()
        self._last_snap: Dict[str, float] = {}   # market_id -> last snap time
        self._stats = {
            "snapshots_written": 0,
            "write_errors":      0,
        }

    def start(self) -> threading.Thread:
        t = threading.Thread(
            target=self._loop,
            name="l2-snapshot-writer",
            daemon=True,
        )
        t.start()
        logger.info("L2SnapshotWriter started (interval=%.0fs)", self._interval)
        return t

    def stop(self):
        self._stop_evt.set()

    @property
    def stats(self) -> Dict:
        return dict(self._stats)

    # -- Internal ---------------------------------------------------------------

    def _loop(self):
        while not self._stop_evt.is_set():
            try:
                self._snapshot_due_markets()
            except Exception as exc:
                logger.warning("Snapshot loop error: %s", exc)
            self._stop_evt.wait(timeout=5)   # wake every 5s to check due markets

    def _snapshot_due_markets(self):
        now   = time.time()
        books = self._cache.snapshot()
        batch = []
        for mid, book in books.items():
            last = self._last_snap.get(mid, 0.0)
            if now - last >= self._interval:
                batch.append((mid, book))
                self._last_snap[mid] = now
                if len(batch) >= _SNAPSHOT_BATCH:
                    self._write_batch(batch)
                    batch = []
        if batch:
            self._write_batch(batch)

    def _write_batch(self, batch):
        rows = [_book_to_row(mid, book) for mid, book in batch]
        try:
            with self._session_factory() as session:
                session.execute(
                    _INSERT_SQL,
                    rows,
                )
                session.commit()
            self._stats["snapshots_written"] += len(rows)
        except Exception as exc:
            self._stats["write_errors"] += 1
            logger.warning("l2_snapshots write error: %s", exc)

    def flush(self, market_id: str):
        """Force immediate snapshot for one market."""
        book = self._cache.get(market_id)
        if book:
            self._write_batch([(market_id, book)])
            self._last_snap[market_id] = time.time()


def _book_to_row(market_id: str, book) -> Dict[str, Any]:
    """Convert an L2Book to a dict suitable for the l2_snapshots INSERT."""
    yb = book.yes.best_bid_level()
    ya = book.yes.best_ask_level()
    nb = book.no.best_bid_level()
    na = book.no.best_ask_level()

    book_json = {
        "yes_bids": [[p, q] for p, q in sorted(book.yes.bids.items(), reverse=True)],
        "yes_asks": [[p, q] for p, q in sorted(book.yes.asks.items())],
        "no_bids":  [[p, q] for p, q in sorted(book.no.bids.items(), reverse=True)],
        "no_asks":  [[p, q] for p, q in sorted(book.no.asks.items())],
    }

    return {
        "market_id":      market_id,
        "sequence":       book.sequence,
        "delta_count":    book.delta_count,
        "yes_best_bid":   yb.price if yb else None,
        "yes_best_ask":   ya.price if ya else None,
        "no_best_bid":    nb.price if nb else None,
        "no_best_ask":    na.price if na else None,
        "book_json":      json.dumps(book_json),
        "yes_bid_levels": len(book.yes.bids),
        "yes_ask_levels": len(book.yes.asks),
        "no_bid_levels":  len(book.no.bids),
        "no_ask_levels":  len(book.no.asks),
    }


_INSERT_SQL = """
    INSERT INTO l2_snapshots (
        market_id, sequence, delta_count,
        yes_best_bid, yes_best_ask, no_best_bid, no_best_ask,
        book_json,
        yes_bid_levels, yes_ask_levels, no_bid_levels, no_ask_levels
    ) VALUES (
        :market_id, :sequence, :delta_count,
        :yes_best_bid, :yes_best_ask, :no_best_bid, :no_best_ask,
        :book_json::jsonb,
        :yes_bid_levels, :yes_ask_levels, :no_bid_levels, :no_ask_levels
    )
"""
