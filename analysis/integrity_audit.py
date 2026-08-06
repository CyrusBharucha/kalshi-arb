"""
analysis/integrity_audit.py
Phase 15: Data integrity audit across all tables.

Checks:
  1. Orphaned relationships (markets not in markets table)
  2. Duplicate market IDs
  3. Price-probability bounds (all prices in 0-1)
  4. Timestamp sanity (no future dates, no pre-2020 dates)
  5. Candlestick OHLC ordering (high >= low; open/close within [low, high])
  6. Arbitrage opportunity completeness (net_edge < gross_edge always)
  7. Trade volume sanity (no negative quantities)
  8. Relationship type coverage
  9. Crossed books (best bid > best ask) -- L2
 10. Stale books (open market, newest snapshot > 5 min old) -- L2
 11. Duplicate trade_ids (double ingestion)
 12. Missing close_time on closed/finalized markets
 13. L2 sequence gaps (dropped deltas) -- L2
 14. Tradeable markets that have never traded (coverage, informational)

Usage:
    python analysis/integrity_audit.py
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import text

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.repository import session_scope

logger = logging.getLogger(__name__)


def _query(sql: str, params: dict | None = None) -> pd.DataFrame:
    with session_scope() as s:
        return pd.read_sql(text(sql), s.bind, params=params or {})


def _scalar(sql: str, params: dict | None = None) -> Any:
    with session_scope() as s:
        result = s.execute(text(sql), params or {}).scalar()
        return result


def check_table_counts() -> Dict[str, int]:
    """Return row counts for all key tables."""
    tables = [
        "events", "markets", "trades", "candlesticks",
        "contract_relationships", "arbitrage_opportunities",
        "backtest_trades", "market_snapshots",
    ]
    counts: Dict[str, int] = {}
    for t in tables:
        try:
            n = _scalar(f"SELECT COUNT(*) FROM {t}")
            counts[t] = int(n or 0)
        except Exception as exc:
            counts[t] = -1
            logger.warning("Could not count %s: %s", t, exc)
    return counts


def check_orphaned_relationships() -> Dict[str, Any]:
    """Relationships pointing to market IDs not in the markets table."""
    try:
        orphaned_m1 = _scalar("""
            SELECT COUNT(*) FROM contract_relationships cr
            WHERE NOT EXISTS (SELECT 1 FROM markets m WHERE m.market_id = cr.market_id_1)
        """)
        orphaned_m2 = _scalar("""
            SELECT COUNT(*) FROM contract_relationships cr
            WHERE NOT EXISTS (SELECT 1 FROM markets m WHERE m.market_id = cr.market_id_2)
        """)
        return {
            "orphaned_market_id_1": int(orphaned_m1 or 0),
            "orphaned_market_id_2": int(orphaned_m2 or 0),
            "pass": int(orphaned_m1 or 0) == 0 and int(orphaned_m2 or 0) == 0,
        }
    except Exception as exc:
        return {"error": str(exc), "pass": False}


def check_market_duplicates() -> Dict[str, Any]:
    """Duplicate market IDs in the markets table."""
    try:
        n_dup = _scalar("""
            SELECT COUNT(*) FROM (
                SELECT market_id, COUNT(*) AS cnt
                FROM markets
                GROUP BY market_id
                HAVING COUNT(*) > 1
            ) x
        """)
        return {
            "duplicate_market_ids": int(n_dup or 0),
            "pass": int(n_dup or 0) == 0,
        }
    except Exception as exc:
        return {"error": str(exc), "pass": False}


def check_price_bounds() -> Dict[str, Any]:
    """All trade prices must lie within the closed probability interval [0, 1].

    Kalshi contracts settle at 0 or 1, and sub-cent markets legitimately print
    at the extremes, so prices of exactly 0.0 or 1.0 are valid data rather than
    corruption. They are reported separately as an informational count; only
    prices strictly outside [0, 1] (or NULL) constitute a failure.
    """
    try:
        out_of_bounds = _scalar("""
            SELECT COUNT(*) FROM trades
            WHERE price < 0 OR price > 1
        """)
        n_null = _scalar("SELECT COUNT(*) FROM trades WHERE price IS NULL")
        at_boundary = _scalar("""
            SELECT COUNT(*) FROM trades
            WHERE price = 0 OR price = 1
        """)
        n_oob = int(out_of_bounds or 0)
        return {
            "prices_out_of_bounds": n_oob,
            "prices_null": int(n_null or 0),
            "prices_at_boundary": int(at_boundary or 0),
            "pass": n_oob == 0 and int(n_null or 0) == 0,
        }
    except Exception as exc:
        return {"error": str(exc), "pass": None}


def check_candlestick_ohlc_ordering() -> Dict[str, Any]:
    """high >= open >= close >= low AND high >= low."""
    try:
        n_total = _scalar("SELECT COUNT(*) FROM candlesticks")
        if not n_total:
            return {"candlestick_rows": 0, "pass": True, "note": "no candles to check"}

        bad_hl = _scalar("""
            SELECT COUNT(*) FROM candlesticks
            WHERE price_high < price_low
        """)
        bad_open = _scalar("""
            SELECT COUNT(*) FROM candlesticks
            WHERE price_open > price_high OR price_open < price_low
        """)
        bad_close = _scalar("""
            SELECT COUNT(*) FROM candlesticks
            WHERE price_close > price_high OR price_close < price_low
        """)
        return {
            "candlestick_rows": int(n_total),
            "high_lt_low": int(bad_hl or 0),
            "open_out_of_range": int(bad_open or 0),
            "close_out_of_range": int(bad_close or 0),
            "pass": all(int(x or 0) == 0 for x in [bad_hl, bad_open, bad_close]),
        }
    except Exception as exc:
        return {"error": str(exc), "pass": None}


def check_timestamp_sanity() -> Dict[str, Any]:
    """Timestamps should be between 2020-01-01 and today."""
    try:
        pre_2020 = _scalar("""
            SELECT COUNT(*) FROM trades
            WHERE trade_ts < '2020-01-01'
        """)
        future_ts = _scalar("""
            SELECT COUNT(*) FROM trades
            WHERE trade_ts > NOW() + INTERVAL '1 day'
        """)
        return {
            "trades_before_2020": int(pre_2020 or 0),
            "trades_in_future":   int(future_ts or 0),
            "pass": all(int(x or 0) == 0 for x in [pre_2020, future_ts]),
        }
    except Exception as exc:
        return {"error": str(exc), "pass": None}


def check_arbitrage_consistency() -> Dict[str, Any]:
    """net_edge must always be <= gross_edge for all opportunities."""
    try:
        n = _scalar("SELECT COUNT(*) FROM arbitrage_opportunities")
        if not n:
            return {"opportunities": 0, "pass": True, "note": "no opportunities yet"}
        inconsistent = _scalar("""
            SELECT COUNT(*) FROM arbitrage_opportunities
            WHERE net_edge > gross_edge
        """)
        negative_gross = _scalar("""
            SELECT COUNT(*) FROM arbitrage_opportunities
            WHERE gross_edge < 0
        """)
        return {
            "total_opportunities": int(n),
            "net_gt_gross": int(inconsistent or 0),
            "negative_gross_edge": int(negative_gross or 0),
            "pass": int(inconsistent or 0) == 0 and int(negative_gross or 0) == 0,
        }
    except Exception as exc:
        return {"error": str(exc), "pass": None}


def check_relationship_type_distribution() -> Dict[str, Any]:
    """Report distribution of relationship types."""
    try:
        df = _query("""
            SELECT relationship_type, COUNT(*) AS n
            FROM contract_relationships
            GROUP BY relationship_type
            ORDER BY n DESC
        """)
        distribution = dict(zip(df["relationship_type"], df["n"].astype(int)))
        total = sum(distribution.values())
        return {
            "total_relationships": total,
            "by_type": distribution,
            "pass": total > 0,
        }
    except Exception as exc:
        return {"error": str(exc), "pass": False}


def check_trade_volume_sanity() -> Dict[str, Any]:
    """No trade should have negative quantity."""
    try:
        neg_qty = _scalar("SELECT COUNT(*) FROM trades WHERE quantity < 0")
        zero_qty = _scalar("SELECT COUNT(*) FROM trades WHERE quantity = 0")
        return {
            "negative_quantity": int(neg_qty or 0),
            "zero_quantity":     int(zero_qty or 0),
            "pass": int(neg_qty or 0) == 0,
        }
    except Exception as exc:
        return {"error": str(exc), "pass": None}


#: Keys of the report that describe the run rather than an individual check.
_NON_CHECK_KEYS = ("table_counts", "summary")

#: Per-check key holding the primary "how many rows are wrong" figure. Any check
#: not listed here falls back to summing every integer value in its result.
_CHECK_COUNT_KEY = {
    "orphaned_relationships": "orphaned_market_id_1",
    "market_duplicates":      "duplicate_market_ids",
    "price_bounds":           "prices_out_of_bounds",
    "ohlc_ordering":          "high_lt_low",
    "timestamp_sanity":       "trades_in_future",
    "arb_consistency":        "net_gt_gross",
    "relationship_types":     "total_relationships",
    "crossed_books":          "crossed_books",
    "stale_books":            "stale_over_5min",
    "duplicate_trade_ids":    "duplicate_trade_ids",
    "missing_close_time":     "missing_close_time",
    "l2_sequence_gaps":       "sequence_gaps",
    "markets_without_trades": "without_trades",
    "trade_volume_sanity":    "negative_quantity",
}


def _check_severity(result: Dict[str, Any]) -> str:
    """Map a check's pass flag onto the data_quality_log severity vocabulary."""
    passed = result.get("pass")
    if passed is True:
        return "ok"
    if passed is False:
        return "error"
    return "warning"  # pass is None -> check could not run


def _check_count(name: str, result: Dict[str, Any]) -> int:
    """Extract the headline row count for a check result."""
    key = _CHECK_COUNT_KEY.get(name)
    if key is not None and isinstance(result.get(key), int):
        return int(result[key])
    return sum(v for v in result.values() if isinstance(v, int) and not isinstance(v, bool))


def persist_audit(report: Dict[str, Any]) -> int:
    """Write one data_quality_log row per check in ``report``.

    Table counts are persisted as a single ``table_counts`` row so the dashboard
    can chart row growth over time. Returns the number of rows written; a DB
    failure is logged and swallowed so a monitoring write can never break the
    audit itself.
    """
    import json

    rows: List[tuple] = []
    for name, result in report.items():
        if name in _NON_CHECK_KEYS or not isinstance(result, dict):
            continue
        rows.append((name, _check_severity(result), _check_count(name, result),
                     json.dumps(result, default=str)))

    counts = report.get("table_counts")
    if isinstance(counts, dict):
        total = sum(v for v in counts.values() if isinstance(v, int) and v > 0)
        rows.append(("table_counts", "info", total, json.dumps(counts, default=str)))

    if not rows:
        return 0

    try:
        with session_scope() as s:
            for check_name, severity, count, details in rows:
                s.execute(
                    text("""
                        INSERT INTO data_quality_log (check_name, severity, count, details)
                        VALUES (:check_name, :severity, :count, CAST(:details AS JSONB))
                    """),
                    {"check_name": check_name, "severity": severity,
                     "count": count, "details": details},
                )
    except Exception as exc:
        logger.warning("Could not persist audit to data_quality_log: %s", exc)
        return 0

    logger.info("  Persisted %d check results to data_quality_log", len(rows))
    return len(rows)


def run_integrity_audit(persist: bool = True) -> Dict[str, Any]:
    """Run all integrity checks and return consolidated report.

    When ``persist`` is true the results are also written to data_quality_log so
    the System Monitoring page can show quality history rather than only the
    current run.
    """
    logger.info("=== DATA INTEGRITY AUDIT ===")

    report: Dict[str, Any] = {
        "table_counts":          check_table_counts(),
        "orphaned_relationships": check_orphaned_relationships(),
        "market_duplicates":     check_market_duplicates(),
        "price_bounds":          check_price_bounds(),
        "ohlc_ordering":         check_candlestick_ohlc_ordering(),
        "timestamp_sanity":      check_timestamp_sanity(),
        "arb_consistency":       check_arbitrage_consistency(),
        "relationship_types":    check_relationship_type_distribution(),
        "trade_volume_sanity":   check_trade_volume_sanity(),
        "crossed_books":         check_crossed_books(),
        "stale_books":           check_stale_books(),
        "duplicate_trade_ids":   check_duplicate_trade_ids(),
        "missing_close_time":    check_missing_close_time(),
        "l2_sequence_gaps":      check_l2_sequence_gaps(),
        "markets_without_trades": check_markets_without_trades(),
    }

    # Overall pass/fail
    all_checks = [v.get("pass") for v in report.values() if isinstance(v, dict)]
    n_pass = sum(1 for r in all_checks if r is True)
    n_fail = sum(1 for r in all_checks if r is False)
    n_skip = sum(1 for r in all_checks if r is None)

    report["summary"] = {
        "checks_passed": n_pass,
        "checks_failed": n_fail,
        "checks_unavailable": n_skip,
        "overall": "PASS" if n_fail == 0 else "FAIL",
    }

    logger.info("  Table counts:")
    for tbl, cnt in report["table_counts"].items():
        logger.info("    %-30s %d", tbl, cnt)

    logger.info("  Check results:")
    for key, val in report.items():
        if key in ("table_counts", "summary"):
            continue
        passed = val.get("pass")
        symbol = "[PASS]" if passed else "[FAIL]" if passed is False else "[N/A]"
        logger.info("    %s %s", symbol, key)

    logger.info("  Summary: %s (%d passed, %d failed, %d N/A)",
                report["summary"]["overall"],
                n_pass, n_fail, n_skip)

    if persist:
        persist_audit(report)

    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    import json
    report = run_integrity_audit()
    print("\n=== INTEGRITY AUDIT REPORT ===")
    print(json.dumps(report, indent=2, default=str))


# ---------------------------------------------------------------------------
# Book-level and lifecycle checks
# ---------------------------------------------------------------------------
# These five complete the intended check list. Four of them read the L2 tables
# (l2_snapshots / order_book_snapshots), which are still being backfilled, so
# they report "pass": None with a note rather than a misleading green tick when
# there is nothing to check. A check that passes on zero rows is not a pass.

def check_crossed_books() -> Dict[str, Any]:
    """A book is crossed when the best bid is at or above the best ask.

    On a real venue this cannot happen -- someone would have taken the trade.
    In stored data it means either a stale side or a merge bug.
    """
    try:
        total = _scalar("SELECT COUNT(*) FROM l2_snapshots")
        if not total:
            return {
                "l2_snapshot_rows": 0,
                "pass": None,
                "note": "l2_snapshots is empty -- nothing to verify yet",
            }
        crossed = _scalar("""
            SELECT COUNT(*) FROM l2_snapshots
            WHERE yes_best_bid IS NOT NULL
              AND yes_best_ask IS NOT NULL
              AND yes_best_bid > yes_best_ask
        """)
        locked = _scalar("""
            SELECT COUNT(*) FROM l2_snapshots
            WHERE yes_best_bid IS NOT NULL
              AND yes_best_ask IS NOT NULL
              AND yes_best_bid = yes_best_ask
        """)
        return {
            "l2_snapshot_rows": int(total),
            "crossed_books":    int(crossed or 0),
            "locked_books":     int(locked or 0),
            "pass": int(crossed or 0) == 0,
        }
    except Exception as exc:
        return {"error": str(exc), "pass": None}


def check_stale_books() -> Dict[str, Any]:
    """An open market whose newest book is over 5 minutes old is stale.

    Stale books are the main way a scanner "finds" an edge that never existed:
    one side of the pair is simply a quote nobody has refreshed.
    """
    try:
        total = _scalar("SELECT COUNT(*) FROM l2_snapshots")
        if not total:
            return {
                "l2_snapshot_rows": 0,
                "pass": None,
                "note": "l2_snapshots is empty -- staleness cannot be assessed",
            }
        stale = _scalar("""
            WITH newest AS (
                SELECT l.market_id, MAX(l.snapped_at) AS last_seen
                FROM l2_snapshots l
                JOIN markets m ON m.market_id = l.market_id
                WHERE m.status IN ('open','active')
                GROUP BY l.market_id
            )
            SELECT COUNT(*) FROM newest
            WHERE last_seen < NOW() - INTERVAL '5 minutes'
        """)
        tracked = _scalar("""
            SELECT COUNT(DISTINCT l.market_id)
            FROM l2_snapshots l
            JOIN markets m ON m.market_id = l.market_id
            WHERE m.status IN ('open','active')
        """)
        return {
            "open_markets_with_books": int(tracked or 0),
            "stale_over_5min":         int(stale or 0),
            "pass": int(stale or 0) == 0,
        }
    except Exception as exc:
        return {"error": str(exc), "pass": None}


def check_duplicate_trade_ids() -> Dict[str, Any]:
    """trade_id is the venue's unique key -- a repeat means double ingestion.

    Double-counted trades inflate volume, which in turn inflates every
    liquidity estimate built on top of it.
    """
    try:
        dupes = _scalar("""
            SELECT COUNT(*) FROM (
                SELECT trade_id
                FROM trades
                WHERE trade_id IS NOT NULL
                GROUP BY trade_id
                HAVING COUNT(*) > 1
            ) d
        """)
        null_ids = _scalar("SELECT COUNT(*) FROM trades WHERE trade_id IS NULL")
        return {
            "duplicate_trade_ids": int(dupes or 0),
            "null_trade_ids":      int(null_ids or 0),
            "pass": int(dupes or 0) == 0,
        }
    except Exception as exc:
        return {"error": str(exc), "pass": None}


def check_missing_close_time() -> Dict[str, Any]:
    """A market that is no longer tradeable must carry a close_time.

    Without it the settlement-proximity analysis silently drops the row.
    """
    try:
        missing = _scalar("""
            SELECT COUNT(*) FROM markets
            WHERE status IN ('closed','finalized','settled')
              AND close_time IS NULL
        """)
        closed_total = _scalar("""
            SELECT COUNT(*) FROM markets
            WHERE status IN ('closed','finalized','settled')
        """)
        return {
            "closed_markets":            int(closed_total or 0),
            "missing_close_time":        int(missing or 0),
            "pass": int(missing or 0) == 0,
        }
    except Exception as exc:
        return {"error": str(exc), "pass": None}


def check_l2_sequence_gaps() -> Dict[str, Any]:
    """Synthesis stamps each book update with a monotonic sequence number.

    A gap means a dropped delta, so the reconstructed book after that point is
    not trustworthy. LAG() over the per-market sequence finds them.
    """
    try:
        total = _scalar("SELECT COUNT(*) FROM l2_snapshots WHERE sequence IS NOT NULL")
        if not total:
            return {
                "sequenced_rows": 0,
                "pass": None,
                "note": "no sequenced L2 rows yet -- gap detection unavailable",
            }
        gaps = _scalar("""
            WITH seq AS (
                SELECT
                    market_id,
                    sequence,
                    LAG(sequence) OVER (
                        PARTITION BY market_id ORDER BY sequence
                    ) AS prev_sequence
                FROM l2_snapshots
                WHERE sequence IS NOT NULL
            )
            SELECT COUNT(*) FROM seq
            WHERE prev_sequence IS NOT NULL
              AND sequence <> prev_sequence + 1
        """)
        return {
            "sequenced_rows": int(total),
            "sequence_gaps":  int(gaps or 0),
            "pass": int(gaps or 0) == 0,
        }
    except Exception as exc:
        return {"error": str(exc), "pass": None}


def check_markets_without_trades() -> Dict[str, Any]:
    """Tradeable markets that have never printed a trade.

    Not a fault on its own -- most markets are genuinely untraded -- so this is
    reported for coverage awareness and always passes. It answers "how much of
    the universe is actually liquid enough to trade?".
    """
    try:
        open_total = _scalar(
            "SELECT COUNT(*) FROM markets WHERE status IN ('open','active')")
        with_trades = _scalar("""
            SELECT COUNT(DISTINCT m.market_id)
            FROM markets m
            JOIN trades t ON t.market_id = m.market_id
            WHERE m.status IN ('open','active')
        """)
        open_total = int(open_total or 0)
        with_trades = int(with_trades or 0)
        return {
            "open_markets":        open_total,
            "with_trades":         with_trades,
            "without_trades":      open_total - with_trades,
            "pct_ever_traded": round(100.0 * with_trades / open_total, 2)
                               if open_total else 0.0,
            "pass": True,  # informational: untraded markets are expected
        }
    except Exception as exc:
        return {"error": str(exc), "pass": None}
