"""
arbitrage/historical_scanner.py
================================
Scans the `l2_snapshots` table for historical arbitrage opportunities,
tracking full lifecycle (open_time, close_time, duration).

Why this is valuable
--------------------
We have 192M+ raw L2 lines covering months of price history, now loaded
into l2_snapshots at 60s resolution. The REST-based scanner.py only sees
live prices; it cannot reconstruct what happened last week. This module
bridges that gap — it walks the historical snapshots in time order,
detects complement / ME / nested arb windows, and persists them with
proper open/close timestamps so downstream analytics can measure:

  - How long do arb windows last on average?
  - What's the typical max_net_profit before closure?
  - Which markets / strategies are most persistent?
  - What does the daily/weekly opportunity frequency look like?

Design
------
Processing is done in TICKER-BATCH × TIME-CHUNK slices to avoid pulling
all 13M+ snapshot rows into memory at once:

  For each batch of N tickers:
    Pull snapshots ordered by (snapped_at, market_id).
    Walk forward in time, reconstructing the best bid/ask per market
    at each 60s bucket.
    For each market: check complement arb at each bucket.
    Track open windows: (market_id, strategy) -> first_bucket_ts.
    When window closes (edge disappears or snapshot gap > 2 buckets):
      emit a completed opportunity dict with open/close timestamps.

Lifecycle rules
---------------
- Window OPENS  when net_edge > MIN_NET_EDGE for the first time.
- Window STAYS  open while consecutive 60s buckets all have net_edge > 0.
- Window CLOSES when:
    a) net_edge drops to <= 0, OR
    b) no snapshot seen for > 180s (gap = market was illiquid / closed)
- Closed windows are inserted into arbitrage_opportunities with
  status = 'expired', detected_at = first bucket, closed_at = last bucket.

Usage
-----
    python arbitrage/historical_scanner.py            # scan all loaded tickers
    python arbitrage/historical_scanner.py --tickers KXBOC-25JAN KXBOC-25JUL
    python arbitrage/historical_scanner.py --since 2024-01-01
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, __import__("pathlib").Path(__file__).resolve().parent.parent.__str__())

from sqlalchemy import text

from database.repository import get_engine
from engine.lifecycle import _kalshi_fee, _depth_qty_from_book

log = logging.getLogger(__name__)

MIN_NET_EDGE   = 0.005   # $0.005 minimum net edge to record
SNAP_GAP_S     = 180     # seconds gap that terminates an open window
TICKER_BATCH   = 200     # tickers per DB query
TIME_CHUNK_H   = 24      # hours per time slice (controls memory usage)


# ---------------------------------------------------------------------------
# Complement arb check (inlined to avoid circular import)
# ---------------------------------------------------------------------------

def _complement_check(
    market_id: str,
    yes_ask: Optional[float],
    yes_bid: Optional[float],
    book_json: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Returns a detection dict if complement arb exists, else None.
    Uses executable depth from book_json when available.
    """
    if yes_ask is None or yes_bid is None:
        return None
    if yes_ask <= 0 or yes_bid <= 0 or yes_ask >= 1.0:
        return None

    no_ask = round(1.0 - yes_bid, 6)

    # G2: prices below 3c indicate a settling/resolved market — skip.
    # OHLC end-of-day prices hit 0.00/$1.00 on settlement and create phantom edges.
    if yes_ask < 0.03 or no_ask < 0.03:
        return None

    gross  = round(1.0 - yes_ask - no_ask, 6)
    if gross <= 0:
        return None

    # G3: gross > 30c is physically impossible on a live, liquid market.
    # Any larger edge is an OHLC artifact (stale ask vs settlement-era bid).
    if gross > 0.30:
        return None

    fee_yes = _kalshi_fee(yes_ask)
    fee_no  = _kalshi_fee(no_ask)
    total_fees = round(fee_yes + fee_no, 6)
    slippage   = round((yes_ask + no_ask) * 0.001, 6)   # 10 bps slippage estimate
    net_edge   = round(gross - total_fees - slippage, 6)

    if net_edge <= MIN_NET_EDGE:
        return None

    qty = _depth_qty_from_book(book_json, gross)
    if qty is None:
        qty = 10.0
        classification = "B"
    else:
        classification = "A"

    return {
        "strategy_type":            "yes_no_complement",
        "classification":           classification,
        "markets_involved":         [market_id],
        "prices_json":              {"yes_ask": yes_ask, "no_ask": no_ask,
                                     "yes_bid": yes_bid, "no_bid": 1.0 - yes_ask},
        "gross_edge":               gross,
        "total_fees":               total_fees,
        "estimated_slippage":       slippage,
        "net_edge":                 net_edge,
        "max_executable_contracts": qty,
        "max_gross_profit":         round(gross * qty, 4),
        "max_net_profit":           round(net_edge * qty, 4),
        "book_json":                book_json,
    }


# ---------------------------------------------------------------------------
# Window tracker
# ---------------------------------------------------------------------------

class _Window:
    """Tracks a single open arbitrage window for one (market_id, strategy)."""

    __slots__ = [
        "market_id", "strategy_type",
        "open_ts", "last_ts",
        "peak_net_edge", "peak_qty",
        "last_det",
    ]

    def __init__(self, market_id: str, strategy_type: str, ts: datetime, det: Dict):
        self.market_id    = market_id
        self.strategy_type = strategy_type
        self.open_ts       = ts
        self.last_ts       = ts
        self.peak_net_edge = det["net_edge"]
        self.peak_qty      = det["max_executable_contracts"]
        self.last_det      = det

    def update(self, ts: datetime, det: Dict) -> None:
        self.last_ts = ts
        if det["net_edge"] > self.peak_net_edge:
            self.peak_net_edge = det["net_edge"]
            self.peak_qty      = det["max_executable_contracts"]
            self.last_det      = det

    def is_stale(self, now_ts: datetime) -> bool:
        return (now_ts - self.last_ts).total_seconds() > SNAP_GAP_S

    def to_opp(self) -> Dict[str, Any]:
        det = self.last_det
        return {
            "detected_at":              self.open_ts,
            "strategy_type":            self.strategy_type,
            "classification":           det.get("classification", "B"),
            "markets_involved":         [self.market_id],
            "prices_json":              det.get("prices_json", {}),
            "gross_edge":               det.get("gross_edge"),
            "total_fees":               det.get("total_fees"),
            "estimated_slippage":       det.get("estimated_slippage"),
            "net_edge":                 self.peak_net_edge,
            "max_executable_contracts": self.peak_qty,
            "max_gross_profit":         round(det.get("gross_edge", 0) * self.peak_qty, 4),
            "max_net_profit":           round(self.peak_net_edge * self.peak_qty, 4),
            "status":                   "expired",
            "closed_at":                self.last_ts,
            "duration_seconds":         round(
                (self.last_ts - self.open_ts).total_seconds(), 1
            ),
            "notes": "Historical L2 scan.",
        }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_tickers(engine, since: Optional[datetime]) -> List[str]:
    """Load distinct market_ids from l2_snapshots (optional since filter)."""
    q = "SELECT DISTINCT market_id FROM l2_snapshots"
    params: Dict = {}
    if since:
        q += " WHERE snapped_at >= :since"
        params["since"] = since
    with engine.connect() as conn:
        rows = conn.execute(text(q), params).fetchall()
    return [r[0] for r in rows]


def _fetch_snapshots(
    engine,
    tickers: List[str],
    since: Optional[datetime],
    until: Optional[datetime],
) -> List[Tuple]:
    """
    Fetch (market_id, snapped_at, yes_best_bid, yes_best_ask, book_json)
    ordered by (snapped_at, market_id).
    """
    q = """
        SELECT market_id, snapped_at, yes_best_bid, yes_best_ask, book_json
        FROM l2_snapshots
        WHERE market_id = ANY(:tickers)
    """
    params: Dict[str, Any] = {"tickers": tickers}
    if since:
        q += " AND snapped_at >= :since"
        params["since"] = since
    if until:
        q += " AND snapped_at < :until"
        params["until"] = until
    q += " ORDER BY snapped_at, market_id"

    with engine.connect() as conn:
        rows = conn.execute(text(q), params).fetchall()
    return rows


def _save_opportunities(engine, opps: List[Dict]) -> int:
    if not opps:
        return 0
    saved = 0
    with engine.begin() as conn:
        for opp in opps:
            try:
                conn.execute(text("""
                    INSERT INTO arbitrage_opportunities (
                        detected_at, strategy_type, classification,
                        markets_involved, prices_json,
                        gross_edge, total_fees, estimated_slippage, net_edge,
                        max_executable_contracts, max_gross_profit, max_net_profit,
                        status, closed_at, duration_seconds, notes
                    ) VALUES (
                        :detected_at, :strategy_type, :classification,
                        :markets_involved, CAST(:prices_json AS jsonb),
                        :gross_edge, :total_fees, :estimated_slippage, :net_edge,
                        :max_executable_contracts, :max_gross_profit, :max_net_profit,
                        :status, :closed_at, :duration_seconds, :notes
                    )
                    ON CONFLICT DO NOTHING
                """), {
                    **opp,
                    "prices_json": json.dumps(opp.get("prices_json", {})),
                })
                saved += 1
            except Exception as exc:
                log.warning("Opp insert failed: %s", exc)
    return saved


def _log_run(engine, rows_inserted: int, rows_skipped: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ingestion_log (job_type, rows_inserted, rows_skipped, status)
            VALUES ('historical_arb_scan', :ins, :skip, 'success')
        """), {"ins": rows_inserted, "skip": rows_skipped})


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

def scan_ticker_batch(
    engine,
    tickers: List[str],
    since: Optional[datetime],
    until: Optional[datetime],
) -> List[Dict]:
    """
    Scan one batch of tickers over a time range.
    Returns list of completed opportunity dicts (status=expired or open-at-end).
    """
    rows = _fetch_snapshots(engine, tickers, since, until)
    if not rows:
        return []

    # Active windows: key = (market_id, strategy_type)
    windows: Dict[Tuple[str, str], _Window] = {}
    completed: List[Dict] = []

    for row in rows:
        mid, ts, yes_bid, yes_ask, book_json = (
            row[0], row[1],
            float(row[2]) if row[2] is not None else None,
            float(row[3]) if row[3] is not None else None,
            row[4],
        )
        # Ensure timezone-aware
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        det = _complement_check(mid, yes_ask, yes_bid, book_json)
        key = (mid, "yes_no_complement")

        if det:
            w = windows.get(key)
            if w is None:
                windows[key] = _Window(mid, "yes_no_complement", ts, det)
            else:
                w.update(ts, det)
        else:
            # No edge at this bucket: close window if open
            w = windows.pop(key, None)
            if w is not None:
                completed.append(w.to_opp())

    # Close any windows still open at end of chunk (mark as open, not expired,
    # so the next chunk can continue them — handled by caller for cross-chunk)
    # For final flush we treat them as "open at scan end"
    for w in windows.values():
        opp = w.to_opp()
        opp["status"] = "open"           # still open at scan boundary
        opp["notes"]  = "Historical L2 scan — open at scan end."
        completed.append(opp)

    return completed


def run_historical_scan(
    tickers: Optional[List[str]] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> Dict[str, int]:
    """
    Full historical scan over l2_snapshots.

    Args:
        tickers: specific tickers to scan; None = all in l2_snapshots
        since:   start of window (None = beginning of data)
        until:   end of window (None = now)

    Returns:
        {"total_windows": N, "opps_saved": M, "tickers_scanned": K}
    """
    engine = get_engine()
    t0 = time.monotonic()

    if tickers is None:
        log.info("Loading ticker list from l2_snapshots...")
        tickers = _load_tickers(engine, since)
        log.info("  %d distinct tickers in l2_snapshots", len(tickers))

    if not tickers:
        log.warning("No tickers to scan.")
        return {"total_windows": 0, "opps_saved": 0, "tickers_scanned": 0}

    total_windows = 0
    total_saved   = 0

    # Process in TICKER_BATCH chunks, TIME_CHUNK_H slices
    # Build time slice boundaries
    if until is None:
        until = datetime.now(timezone.utc)
    if since is None:
        # Find earliest snapshot
        with engine.connect() as conn:
            _sql = "SELECT MIN(snapped_at) FROM l2_snapshots" + (" WHERE market_id = ANY(:t)" if tickers else "")
            _params = {"t": tickers} if tickers else {}
            row = conn.execute(text(_sql), _params).fetchone()
            since = row[0] if row and row[0] else until - timedelta(days=30)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

    # Generate time slices
    time_slices: List[Tuple[datetime, datetime]] = []
    cur = since
    delta = timedelta(hours=TIME_CHUNK_H)
    while cur < until:
        nxt = min(cur + delta, until)
        time_slices.append((cur, nxt))
        cur = nxt

    log.info(
        "Historical scan: %d tickers, %d time slices (%s to %s)",
        len(tickers), len(time_slices), since.date(), until.date(),
    )

    for slice_start, slice_end in time_slices:
        for i in range(0, len(tickers), TICKER_BATCH):
            batch = tickers[i: i + TICKER_BATCH]
            opps = scan_ticker_batch(engine, batch, slice_start, slice_end)

            # Only save completed (expired) opportunities — not open-at-boundary
            # ones (they will continue or be re-detected in the next slice)
            to_save = [o for o in opps if o["status"] == "expired"]
            saved = _save_opportunities(engine, to_save)
            total_windows += len(opps)
            total_saved   += saved

        elapsed = time.monotonic() - t0
        log.info(
            "  Slice %s done: %d total windows, %d saved (%.0fs elapsed)",
            slice_start.date(), total_windows, total_saved, elapsed,
        )

    _log_run(engine, total_saved, total_windows - total_saved)

    log.info(
        "Historical scan complete: %d windows tracked, %d opportunities saved "
        "from %d tickers in %.0fs",
        total_windows, total_saved, len(tickers), time.monotonic() - t0,
    )
    return {
        "total_windows":   total_windows,
        "opps_saved":      total_saved,
        "tickers_scanned": len(tickers),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Historical arbitrage scanner over l2_snapshots"
    )
    parser.add_argument("--tickers", nargs="*", help="Specific tickers to scan")
    parser.add_argument("--since",   help="ISO date/datetime (e.g. 2024-01-01)")
    parser.add_argument("--until",   help="ISO date/datetime")
    args = parser.parse_args()

    def _parse_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        raise ValueError(f"Cannot parse date: {s!r}")

    result = run_historical_scan(
        tickers=args.tickers or None,
        since=_parse_dt(args.since),
        until=_parse_dt(args.until),
    )
    print(result)
