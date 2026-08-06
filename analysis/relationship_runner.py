"""
analysis/relationship_runner.py
Run the relationship detector across the full Kalshi market universe.

Reads open markets from PostgreSQL, groups by event, runs all detectors,
persists results to contract_relationships, then identifies the subset of
markets that are arb candidates for historical price ingestion.

Design:
  - Streams events in pages of 500 to stay within RAM
  - Only processes events with ≥ 2 open markets (single-market events have
    only the trivial complement relationship - no price constraint to exploit)
  - Bulk-upserts relationships in batches of 2 000
  - Prints a summary at the end

Usage:
    python analysis/relationship_runner.py
    # or via run.py:
    python run.py relationships
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.repository import session_scope
from database.models import ContractRelationship
from sqlalchemy.dialects.postgresql import insert as pg_insert
from engine.relationship_detector import detect_all_relationships_for_event

logger = logging.getLogger(__name__)

# -- Tuning ---------------------------------------------------------------------
MKT_PAGE        = 50_000   # markets fetched per DB query (keyset pagination)
REL_BATCH_SIZE  = 5_000    # relationships upserted per transaction
LOG_EVERY       = 5_000    # log progress every N events


# -- Stats ----------------------------------------------------------------------

@dataclass
class RunStats:
    events_seen:      int = 0
    events_with_multi: int = 0
    rels_found:       int = 0
    rels_written:     int = 0
    start:            float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return time.monotonic() - self.start

    def summary(self) -> str:
        return (
            f"events_seen={self.events_seen:,}  "
            f"multi_market_events={self.events_with_multi:,}  "
            f"rels_found={self.rels_found:,}  "
            f"rels_written={self.rels_written:,}  "
            f"elapsed={self.elapsed():.0f}s"
        )


# -- Bulk upsert ----------------------------------------------------------------

def _flush_rels(batch: List[Dict[str, Any]], stats: RunStats) -> None:
    if not batch:
        return
    # Strip any extra keys not in the model
    allowed = {
        "market_id_1", "market_id_2", "relationship_type",
        "logical_constraint", "implied_inequality", "confidence",
    }
    cleaned = [{k: v for k, v in r.items() if k in allowed} for r in batch]
    try:
        with session_scope() as s:
            stmt = pg_insert(ContractRelationship).values(cleaned)
            stmt = stmt.on_conflict_do_update(
                index_elements=["market_id_1", "market_id_2", "relationship_type"],
                set_={
                    "logical_constraint": stmt.excluded.logical_constraint,
                    "implied_inequality": stmt.excluded.implied_inequality,
                    "confidence":         stmt.excluded.confidence,
                },
            )
            s.execute(stmt)
        stats.rels_written += len(cleaned)
    except Exception as exc:
        logger.warning("Rel batch flush failed: %s - skipping %d rels", exc, len(batch))


# -- Per-event processing -------------------------------------------------------

MAX_ME_MARKETS = 30   # skip pairwise ME for events with more markets (O(n²) too slow)

def _process_event(
    event_ticker: str,
    markets: List[Dict],
    stats: "RunStats",
    candidate_events: set,
    rel_batch: List[Dict],
) -> None:
    """Detect relationships for one event and accumulate into rel_batch."""
    # For large events skip the O(n²) mutually_exclusive detection to stay fast
    if len(markets) > MAX_ME_MARKETS:
        from engine.relationship_detector import (
            detect_complement_relationship,
            detect_threshold_order_relationships,
            detect_collectively_exhaustive_set,
        )
        rels = []
        for m in markets:
            cr = detect_complement_relationship(m)
            if cr:
                rels.append(cr)
        rels.extend(detect_threshold_order_relationships(markets))
        ce = detect_collectively_exhaustive_set(markets, event_ticker)
        if ce:
            rels.append(ce)
    else:
        rels = detect_all_relationships_for_event(markets, event_ticker)

    non_trivial = [r for r in rels if r["relationship_type"] != "complement"]
    if non_trivial:
        candidate_events.add(event_ticker)
    stats.rels_found += len(rels)
    rel_batch.extend(rels)


# -- Main runner ----------------------------------------------------------------

def run_relationship_detector(
    status_filter: str = "open",
) -> Tuple[RunStats, List[str]]:
    """
    Run the relationship detector across all markets with the given status.

    Returns
    -------
    stats       : RunStats summary
    candidate_tickers : sorted list of market tickers selected as arb candidates
    """
    stats = RunStats()

    # We'll collect candidate event_tickers here (events with ≥2 markets
    # AND at least one non-trivial relationship).
    candidate_events: set = set()

    # -- Step 1: Stream all active markets ordered by event_ticker -------------
    # Keyset pagination: no GROUP BY, no OFFSET - just scan the composite index
    # in (status, event_ticker) order. Group events in Python.
    logger.info("Streaming %s markets ordered by event_ticker (keyset pagination)...",
                status_filter)

    from collections import defaultdict as _dd

    last_event_ticker = ""   # keyset cursor
    rel_batch: List[Dict[str, Any]] = []
    pending_event: str = ""
    pending_markets: List[Dict] = []

    while True:
        with session_scope() as s:
            mkt_rows = s.execute(text("""
                SELECT market_id, ticker, event_ticker, floor_strike, cap_strike,
                       market_type, outcome_type, title
                FROM markets
                WHERE status = :status
                  AND event_ticker > :cursor
                ORDER BY event_ticker, floor_strike NULLS LAST, ticker
                LIMIT :lim
            """), {
                "status": status_filter,
                "cursor": last_event_ticker,
                "lim":    MKT_PAGE,
            }).fetchall()

        if not mkt_rows:
            # Process any remaining pending event
            if pending_markets and len(pending_markets) >= 2:
                _process_event(pending_event, pending_markets,
                               stats, candidate_events, rel_batch)
            break

        # The last row's event_ticker might be incomplete (more rows exist)
        # - hold it back until we see a new event_ticker in the next page.
        last_row_et = mkt_rows[-1].event_ticker

        for row in mkt_rows:
            et = row.event_ticker
            m  = dict(row._mapping)

            if et != pending_event:
                # Flush previous event
                if pending_markets:
                    stats.events_seen += 1
                    if len(pending_markets) >= 2:
                        stats.events_with_multi += 1
                        _process_event(pending_event, pending_markets,
                                       stats, candidate_events, rel_batch)
                        if len(rel_batch) >= REL_BATCH_SIZE:
                            _flush_rels(rel_batch, stats)
                            rel_batch.clear()
                        if stats.events_seen % LOG_EVERY == 0:
                            logger.info("[rel_detect] %s", stats.summary())
                pending_event  = et
                pending_markets = []

            pending_markets.append(m)

        # Advance cursor to last complete event (not the one still in progress)
        # If the full page had only one event_ticker, advance anyway
        last_event_ticker = last_row_et

    # Flush remainder
    _flush_rels(rel_batch, stats)
    rel_batch.clear()

    # -- Step 2: (events_seen already counts all events from the stream) ----------
    # No additional COUNT(*) needed - every event was seen in the keyset scan.

    # -- Step 3: Collect candidate market tickers -------------------------------
    candidate_tickers: List[str] = []
    if candidate_events:
        with session_scope() as s:
            rows = s.execute(text("""
                SELECT ticker
                FROM markets
                WHERE event_ticker = ANY(:evts) AND status = :status
                ORDER BY ticker
            """), {"evts": list(candidate_events), "status": status_filter}).fetchall()
        candidate_tickers = [r[0] for r in rows]

    logger.info("Relationship detection complete. %s", stats.summary())
    logger.info("Candidate events: %d  ->  %d candidate market tickers",
                len(candidate_events), len(candidate_tickers))

    return stats, candidate_tickers


def get_candidate_summary() -> Dict[str, Any]:
    """
    Query the contract_relationships table for a summary of what's been detected.
    Safe to call any time after run_relationship_detector() has been run.
    """
    with session_scope() as s:
        rows = s.execute(text("""
            SELECT relationship_type, COUNT(*) AS n
            FROM contract_relationships
            GROUP BY relationship_type
            ORDER BY n DESC
        """)).fetchall()

        total = s.execute(text("SELECT COUNT(*) FROM contract_relationships")).scalar()

        # Events/markets that appear in any non-complement relationship
        candidate_count = s.execute(text("""
            SELECT COUNT(DISTINCT market_id_1)
            FROM contract_relationships
            WHERE relationship_type != 'complement'
        """)).scalar()

    return {
        "total_relationships":   total,
        "by_type":               {r[0]: r[1] for r in rows},
        "candidate_markets":     candidate_count,
    }


# -- Entry point ----------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    stats, candidates = run_relationship_detector(status_filter="active")
    summary = get_candidate_summary()
    print("\n=== RELATIONSHIP DETECTION RESULTS ===")
    print(f"  Total relationships stored : {summary['total_relationships']:,}")
    print(f"  Breakdown by type          :")
    for rtype, n in summary["by_type"].items():
        print(f"    {rtype:<30s} {n:>10,}")
    print(f"  Candidate markets (for historical pull): {summary['candidate_markets']:,}")
    print(f"  Sample tickers: {candidates[:10]}")
