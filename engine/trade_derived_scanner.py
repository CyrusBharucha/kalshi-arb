"""
arbitrage/trade_derived_scanner.py
==================================
Historical arbitrage detection from the **executed trade tape** (Class C).

Why this exists
---------------
``arbitrage/historical_scanner.py`` works from candlestick/orderbook records, of
which this database holds relatively few. The ``trades`` table, by contrast,
holds tens of millions of prints going back years. It is the only dataset large
enough to answer the project's central question historically:

    "Has a mutually-exclusive pricing violation ever appeared on Kalshi?"

What Class C means -- and what it does not
------------------------------------------
A trade print is evidence that *someone* traded at that price. It is **not** a
quote, and it is **not** evidence that the price was available to us:

  * a print carries no depth, so we cannot know the executable size;
  * two prints in the same minute may be seconds apart, during which either
    price could have moved;
  * a thin or stale print can sit far from the real market.

So every opportunity written here is stamped ``classification = 'C'`` --
"derived from the trade tape, never confirmed against a book". Class C figures
must never be added to Class A/B totals or quoted as achievable profit. They
bound the problem from above: if no violation shows up even in Class C, none
existed; if violations do show up, Class A/B work is needed to find out whether
any of them were real.

Method
------
For a mutually-exclusive pair, at most one contract can settle YES, so a
coherent market prices them at::

    P(a) + P(b) <= 1

A violation is ``P(a) + P(b) > 1``: both YES legs could be sold for more than
the $1 that at most one of them will ever pay out. Trades are bucketed to the
minute and the last print in each bucket is taken as that minute's price, which
is the tightest time alignment the tape supports.

Fees are charged on both legs at Kalshi's taker schedule, and a slippage
allowance is applied, so ``net_edge`` is what would have remained after costs.

Usage::

    python run.py trade_scan
    from engine.trade_derived_scanner import run_trade_derived_scan
    stats = run_trade_derived_scan()
"""

from __future__ import annotations

import json
import logging
import math
from datetime import timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

log = logging.getLogger(__name__)

# Only write an opportunity if this much edge survives fees and slippage.
MIN_NET_EDGE = 0.005          # half a cent per contract

# Trades are aligned to this bucket before being compared.
BUCKET = "minute"

# Slippage allowance applied to the combined notional, in basis points.
SLIPPAGE_BPS = 10

# The tape carries no depth, so executable size is unknown. Class C rows record
# the traded quantity actually observed rather than inventing a fill size.
DEFAULT_QTY = 1.0


def kalshi_fee(prob: float) -> float:
    """Kalshi taker fee for one contract at probability ``prob``.

    Canonical formula: min(0.035, math.ceil(0.07 * p * (1-p) * 100) / 100) — matches
    execution/fees.py and ws_bridge.py exactly.
    """
    prob = max(0.0, min(1.0, float(prob)))
    raw = 0.07 * prob * (1.0 - prob)
    # Round up to the nearest cent so fees are always whole cents.
    ceiled = math.ceil(raw * 100) / 100
    return min(0.035, ceiled)


# ---------------------------------------------------------------------------
# Detection SQL
# ---------------------------------------------------------------------------
# Restricting to pairs that actually traded on both sides is what keeps this
# tractable: the relationship graph holds ~2M mutually-exclusive pairs, but only
# a few thousand have prints on both legs.

_SCAN_SQL = """
WITH me_pairs AS (
    SELECT cr.market_id_1 AS market_a,
           cr.market_id_2 AS market_b,
           cr.confidence
    FROM contract_relationships cr
    WHERE cr.relationship_type = 'mutually_exclusive'
      AND cr.confidence >= :min_confidence
      AND EXISTS (SELECT 1 FROM trades t WHERE t.market_id = cr.market_id_1)
      AND EXISTS (SELECT 1 FROM trades t WHERE t.market_id = cr.market_id_2)
),
involved AS (
    SELECT market_a AS market_id FROM me_pairs
    UNION
    SELECT market_b FROM me_pairs
),
-- Last print per market per bucket, plus the volume that traded in it.
bucketed AS (
    SELECT
        t.market_id,
        date_trunc(:bucket, t.trade_ts)                        AS bucket,
        (array_agg(t.price ORDER BY t.trade_ts DESC))[1]       AS last_price,
        SUM(t.quantity)                                        AS bucket_qty,
        COUNT(*)                                               AS bucket_trades,
        MAX(t.trade_ts)                                        AS last_trade_ts
    FROM trades t
    JOIN involved i ON i.market_id = t.market_id
    WHERE t.price IS NOT NULL
      AND t.price > 0 AND t.price < 1
    GROUP BY 1, 2
)
SELECT
    p.market_a,
    p.market_b,
    p.confidence,
    ba.bucket                                   AS observed_at,
    ba.last_price                               AS price_a,
    bb.last_price                               AS price_b,
    (ba.last_price + bb.last_price - 1.0)       AS gross_edge,
    ba.bucket_qty                               AS qty_a,
    bb.bucket_qty                               AS qty_b,
    ba.bucket_trades                            AS trades_a,
    bb.bucket_trades                            AS trades_b,
    ba.last_trade_ts                            AS last_ts_a,
    bb.last_trade_ts                            AS last_ts_b
FROM me_pairs p
JOIN bucketed ba ON ba.market_id = p.market_a
JOIN bucketed bb ON bb.market_id = p.market_b
                AND bb.bucket = ba.bucket
WHERE ba.last_price + bb.last_price > 1.0 + :min_gross
ORDER BY gross_edge DESC
LIMIT :limit
"""


def _get_engine():
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from database.repository import get_engine
    return get_engine()


def find_trade_derived_violations(
    engine=None,
    min_confidence: float = 0.85,
    min_gross: float = 0.0,
    limit: int = 100000,
    statement_timeout_ms: int = 900000,
) -> List[Dict[str, Any]]:
    """Return every mutually-exclusive pricing violation visible on the tape.

    Each result is a raw observation -- no fees applied yet.
    """
    engine = engine or _get_engine()
    with engine.connect() as conn:
        conn.execute(text(f"SET statement_timeout = {int(statement_timeout_ms)}"))
        rows = conn.execute(text(_SCAN_SQL), {
            "min_confidence": min_confidence,
            "min_gross": min_gross,
            "bucket": BUCKET,
            "limit": limit,
        }).fetchall()
    return [dict(r._mapping) for r in rows]


def build_opportunity(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a raw violation into an arbitrage_opportunities row, or None.

    Returns None when fees and slippage consume the edge -- which is the common
    case, and is itself the finding.
    """
    price_a = float(row["price_a"])
    price_b = float(row["price_b"])
    gross = round(price_a + price_b - 1.0, 6)
    if gross <= 0:
        return None

    fee_a = kalshi_fee(price_a)
    fee_b = kalshi_fee(price_b)
    total_fees = round(fee_a + fee_b, 6)
    slippage = round((price_a + price_b) * (SLIPPAGE_BPS / 10000.0), 6)
    net_edge = round(gross - total_fees - slippage, 6)

    if net_edge <= MIN_NET_EDGE:
        return None

    # The tape shows what traded, so use the smaller leg's volume as the most
    # optimistic size that could plausibly have been worked -- still theoretical.
    qty_a = float(row.get("qty_a") or 0)
    qty_b = float(row.get("qty_b") or 0)
    qty = min(qty_a, qty_b) if qty_a and qty_b else DEFAULT_QTY
    qty = max(qty, DEFAULT_QTY)

    observed_at = row["observed_at"]
    if observed_at is not None and observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)

    # Seconds between the two prints: the wider this is, the less the two
    # prices can be treated as simultaneous.
    skew_s = None
    if row.get("last_ts_a") and row.get("last_ts_b"):
        skew_s = abs((row["last_ts_a"] - row["last_ts_b"]).total_seconds())

    return {
        "detected_at":              observed_at,
        "strategy_type":            "mutually_exclusive",
        "classification":           "C",
        "markets_involved":         [row["market_a"], row["market_b"]],
        "prices_json": {
            "price_a": price_a,
            "price_b": price_b,
            "sum": round(price_a + price_b, 6),
            "fee_a": fee_a,
            "fee_b": fee_b,
            "qty_a": qty_a,
            "qty_b": qty_b,
            "trades_a": int(row.get("trades_a") or 0),
            "trades_b": int(row.get("trades_b") or 0),
            "print_skew_seconds": skew_s,
            "relationship_confidence": float(row.get("confidence") or 0),
        },
        "gross_edge":               gross,
        "total_fees":               total_fees,
        "estimated_slippage":       slippage,
        "net_edge":                 net_edge,
        "max_executable_contracts": qty,
        "max_gross_profit":         round(gross * qty, 4),
        "max_net_profit":           round(net_edge * qty, 4),
        "status":                   "historical",
        "closed_at":                None,
        "duration_seconds":         None,
        "notes": (
            "Class C: derived from the executed trade tape, not from an order "
            "book. Prices are last prints within a one-minute bucket "
            f"(skew {skew_s if skew_s is not None else 'unknown'}s). No depth "
            "confirmation -- not evidence of an executable opportunity."
        ),
    }


def save_opportunities(engine, opps: List[Dict[str, Any]]) -> int:
    """Insert Class C opportunities. Returns the number written."""
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
                        max_executable_contracts, max_gross_profit,
                        max_net_profit, status, closed_at, duration_seconds,
                        notes
                    ) VALUES (
                        :detected_at, :strategy_type, :classification,
                        :markets_involved, CAST(:prices_json AS jsonb),
                        :gross_edge, :total_fees, :estimated_slippage, :net_edge,
                        :max_executable_contracts, :max_gross_profit,
                        :max_net_profit, :status, :closed_at, :duration_seconds,
                        :notes
                    )
                    ON CONFLICT DO NOTHING
                """), {**opp, "prices_json": json.dumps(opp["prices_json"])})
                saved += 1
            except Exception as exc:
                log.warning("Class C insert failed: %s", exc)
    return saved


def purge_existing_class_c(engine) -> int:
    """Delete previously written Class C rows so a rescan is not double-counted.

    The table has no natural key for these, so a rescan would otherwise append
    duplicates and quietly inflate every count built on top of it.
    """
    with engine.begin() as conn:
        res = conn.execute(text("""
            DELETE FROM arbitrage_opportunities
            WHERE classification = 'C'
              AND strategy_type = 'mutually_exclusive'
              AND status = 'historical'
        """))
        return res.rowcount or 0


def run_trade_derived_scan(
    engine=None,
    min_confidence: float = 0.85,
    persist: bool = True,
    replace: bool = True,
) -> Dict[str, Any]:
    """Scan the trade tape for ME violations and record them as Class C.

    Returns a stats dict. ``violations_found`` counts raw crossings;
    ``opportunities_saved`` counts those that survived fees and slippage -- the
    gap between the two is the cost of trading.
    """
    engine = engine or _get_engine()
    log.info("Scanning trade tape for mutually-exclusive violations (Class C)...")

    raw = find_trade_derived_violations(engine, min_confidence=min_confidence)
    log.info("Raw violations on the tape: %d", len(raw))

    opps = [o for o in (build_opportunity(r) for r in raw) if o is not None]
    log.info("Surviving fees and slippage: %d", len(opps))

    purged = 0
    saved = 0
    if persist:
        if replace:
            purged = purge_existing_class_c(engine)
        saved = save_opportunities(engine, opps)
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO ingestion_log (job_type, rows_inserted,
                                               rows_skipped, status)
                    VALUES ('trade_derived_arb_scan', :ins, :skip, 'success')
                """), {"ins": saved, "skip": len(raw) - len(opps)})
        except Exception as exc:
            log.warning("ingestion_log write failed: %s", exc)

    gross_values = [float(r["gross_edge"]) for r in raw]
    net_values = [o["net_edge"] for o in opps]

    stats = {
        "pairs_scanned_note": "mutually-exclusive pairs with prints on both legs",
        "violations_found":      len(raw),
        "survived_fees":         len(opps),
        "opportunities_saved":   saved,
        "prior_class_c_purged":  purged,
        "max_gross_edge":        round(max(gross_values), 4) if gross_values else 0.0,
        "mean_gross_edge":       round(sum(gross_values) / len(gross_values), 4)
                                 if gross_values else 0.0,
        "max_net_edge":          round(max(net_values), 4) if net_values else 0.0,
        "classification":        "C",
        "caveat": (
            "Class C is trade-tape derived. It is an upper bound on what may "
            "have existed, not a measure of what was executable."
        ),
    }
    log.info("Trade-derived scan complete: %s", json.dumps(stats, default=str))
    return stats


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    print(json.dumps(run_trade_derived_scan(), indent=2, default=str))
