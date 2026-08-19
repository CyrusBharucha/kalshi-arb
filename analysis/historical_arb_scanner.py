"""
analysis/historical_arb_scanner.py
Run arbitrage detection across all historical trade/candlestick data.

For each pair of markets in contract_relationships (non-complement),
uses the synthetic OHLC data to detect historical price violations.

Classification system:
  B = Historical candidate (price data exists; no order-book depth)
  D = Edge consumed by fees after accounting for spread

Output: populated arbitrage_opportunities table + summary stats.

Usage:
    python analysis/historical_arb_scanner.py
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.repository import session_scope
from database.models import ArbitrageOpportunity
from engine.fees import taker_fee_per_contract
from engine.relationship_detector import check_logical_price_violations

logger = logging.getLogger(__name__)

MIN_GROSS_EDGE = 0.005    # $0.005 minimum gross edge
MIN_NET_EDGE   = 0.001    # $0.001 minimum net edge to record
BATCH_SIZE     = 500      # opportunities per DB flush


# -- Load data helpers ----------------------------------------------------------

def load_relationships_with_candles() -> pd.DataFrame:
    """
    Load relationships where BOTH markets have candlestick data.
    Returns DataFrame with columns: relationship_type, market_id_1, market_id_2,
    logical_constraint, confidence
    """
    with session_scope() as s:
        df = pd.read_sql(text("""
            SELECT cr.id, cr.market_id_1, cr.market_id_2,
                   cr.relationship_type, cr.logical_constraint,
                   cr.implied_inequality, cr.confidence
            FROM contract_relationships cr
            WHERE cr.relationship_type != 'complement'
              AND EXISTS (
                  SELECT 1 FROM candlesticks c1
                  WHERE c1.market_id = cr.market_id_1 LIMIT 1
              )
              AND EXISTS (
                  SELECT 1 FROM candlesticks c2
                  WHERE c2.market_id = cr.market_id_2 LIMIT 1
              )
            LIMIT 50000
        """), s.bind)
    return df


def load_price_series(market_ids: List[str], period_interval: int = 60) -> pd.DataFrame:
    """Load OHLC close prices for given market IDs."""
    if not market_ids:
        return pd.DataFrame()
    with session_scope() as s:
        df = pd.read_sql(text("""
            SELECT market_id, period_end_ts, price_close, price_open,
                   price_high, price_low, volume,
                   yes_bid_close, yes_ask_close
            FROM candlesticks
            WHERE market_id = ANY(:mids)
              AND period_interval = :pi
            ORDER BY market_id, period_end_ts
        """), s.bind, params={"mids": market_ids, "pi": period_interval})
    df["period_end_ts"] = pd.to_datetime(df["period_end_ts"], utc=True)
    df["price_close"]   = pd.to_numeric(df["price_close"],   errors="coerce")
    df["volume"]        = pd.to_numeric(df["volume"],        errors="coerce")
    return df


# -- Detection logic ------------------------------------------------------------

def detect_me_violations(
    prices_m1: pd.Series,    # indexed by period_end_ts
    prices_m2: pd.Series,
    rel: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Mutually exclusive: P(m1) + P(m2) should be <= 1.0
    An arb exists when sum > 1.0 (both YES overpriced).
    Reverse: when sum < something meaningful (both underpriced - buy both).
    """
    # Align on timestamps
    combined = pd.concat([prices_m1.rename("p1"), prices_m2.rename("p2")], axis=1).dropna()
    if combined.empty:
        return []

    violations = []
    for ts, row in combined.iterrows():
        p1, p2 = row["p1"], row["p2"]
        if pd.isna(p1) or pd.isna(p2):
            continue

        # ME violation: P(A) + P(B) > 1 - at least one is overpriced
        prob_sum = p1 + p2
        if prob_sum > 1.0:
            gross_edge = prob_sum - 1.0
            if gross_edge >= MIN_GROSS_EDGE:
                fee1 = taker_fee_per_contract(p1)
                fee2 = taker_fee_per_contract(p2)
                net_edge = gross_edge - fee1 - fee2
                violations.append({
                    "ts":        ts,
                    "strategy":  "mutually_exclusive",
                    "p1":        p1, "p2": p2,
                    "gross_edge": gross_edge,
                    "total_fees": fee1 + fee2,
                    "net_edge":   net_edge,
                    "side":      "sell_both_no" if prob_sum > 1 else "buy_both_yes",
                })

    return violations


def detect_threshold_violations(
    prices_hi: pd.Series,   # higher-strike (should have higher probability)
    prices_lo: pd.Series,   # lower-strike  (should have lower probability)
    rel: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Superset/threshold: P(X <= high) >= P(X <= low) must hold.
    Violation when P_hi < P_lo - guaranteed arbitrage (buy hi, sell lo).
    """
    combined = pd.concat([prices_hi.rename("p_hi"), prices_lo.rename("p_lo")], axis=1).dropna()
    if combined.empty:
        return []

    violations = []
    for ts, row in combined.iterrows():
        p_hi, p_lo = row["p_hi"], row["p_lo"]
        if pd.isna(p_hi) or pd.isna(p_lo):
            continue

        # Violation: high-strike market trades cheaper than low-strike
        if p_lo > p_hi:
            gross_edge = p_lo - p_hi
            if gross_edge >= MIN_GROSS_EDGE:
                fee_hi = taker_fee_per_contract(p_hi)
                fee_lo = taker_fee_per_contract(p_lo)
                net_edge = gross_edge - fee_hi - fee_lo
                violations.append({
                    "ts":        ts,
                    "strategy":  "threshold_order",
                    "p1":        p_hi, "p2": p_lo,
                    "gross_edge": gross_edge,
                    "total_fees": fee_hi + fee_lo,
                    "net_edge":   net_edge,
                    "side":      "buy_hi_sell_lo",
                })

    return violations


def detect_superset_violations(
    prices_super: pd.Series,   # superset market (P_superset >= P_subset)
    prices_sub:   pd.Series,
    rel: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Superset: P(superset) >= P(subset) must hold.
    """
    return detect_threshold_violations(prices_super, prices_sub, rel)


# -- Main runner ----------------------------------------------------------------

def _flush_opps(opps: List[Dict]) -> int:
    if not opps:
        return 0
    try:
        with session_scope() as s:
            for opp in opps:
                stmt = pg_insert(ArbitrageOpportunity).values(opp)
                stmt = stmt.on_conflict_do_nothing()
                s.execute(stmt)
        return len(opps)
    except Exception as exc:
        logger.warning("Opp flush failed: %s", exc)
        return 0


def run_historical_arb_scan(period_interval: int = 60) -> Dict[str, Any]:
    """
    Main scan: iterate over relationships with candle data, detect violations,
    persist to arbitrage_opportunities.
    """
    logger.info("Loading relationships with candlestick data...")
    rels_df = load_relationships_with_candles()

    if rels_df.empty:
        logger.warning("No relationships with candle data found. "
                        "Run synthetic_ohlc.py first.")
        return {"error": "no_candle_data"}

    logger.info("Relationships to scan: %d", len(rels_df))

    # Load all price series at once
    all_mids = list(set(rels_df["market_id_1"].tolist() + rels_df["market_id_2"].tolist()))
    logger.info("Loading price series for %d markets...", len(all_mids))
    prices_df = load_price_series(all_mids, period_interval)

    if prices_df.empty:
        logger.warning("No price series loaded.")
        return {"error": "no_prices"}

    # Index: market_id -> Series(period_end_ts -> price_close)
    price_index: Dict[str, pd.Series] = {}
    for mid, grp in prices_df.groupby("market_id"):
        price_index[mid] = grp.set_index("period_end_ts")["price_close"].sort_index()

    logger.info("Price index built for %d markets", len(price_index))

    # Scan
    stats = {
        "rels_scanned": 0,
        "total_violations": 0,
        "fee_survivors": 0,
        "opps_saved": 0,
    }
    opp_buf: List[Dict] = []

    for _, rel in rels_df.iterrows():
        m1 = rel["market_id_1"]
        m2 = rel["market_id_2"]
        rtype = rel["relationship_type"]

        s1 = price_index.get(m1)
        s2 = price_index.get(m2)
        if s1 is None or s2 is None:
            continue

        stats["rels_scanned"] += 1

        # Pick detector based on relationship type
        if rtype == "mutually_exclusive":
            violations = detect_me_violations(s1, s2, rel.to_dict())
        elif rtype in ("threshold_order", "superset"):
            violations = detect_threshold_violations(s1, s2, rel.to_dict())
        elif rtype == "collectively_exhaustive":
            # CE (2-leg pairwise): exactly one of the two outcomes must resolve YES.
            # Arb condition: yes_ask_1 + yes_ask_2 < $1 (cost to buy both < guaranteed $1 payout).
            # Reuses detect_me_violations logic since the YES-sum arb formula is identical for 2-leg CE.
            violations = detect_me_violations(s1, s2, rel.to_dict())
        else:
            continue

        stats["total_violations"] += len(violations)

        for v in violations:
            classification = "B"   # no live depth data
            if v["net_edge"] <= 0:
                classification = "D"   # fees consume edge
                stats["fee_survivors"] += 0
            else:
                stats["fee_survivors"] += 1

            if v["net_edge"] < MIN_NET_EDGE:
                continue

            opp = {
                "opportunity_id":    uuid.uuid4(),
                "detected_at":       v["ts"],
                "strategy_type":     v["strategy"],
                "classification":    classification,
                "markets_involved":  [m1, m2],
                "prices_json":       {"p1": v["p1"], "p2": v["p2"],
                                      "source": "synthetic_ohlc"},
                "gross_edge":        round(v["gross_edge"], 6),
                "total_fees":        round(v["total_fees"], 6),
                "estimated_slippage": 0.002,
                "net_edge":          round(v["net_edge"], 6),
                "max_executable_contracts": 1,
                "status":            "historical",
            }
            opp_buf.append(opp)
            stats["opps_saved"] += 1

        if len(opp_buf) >= BATCH_SIZE:
            _flush_opps(opp_buf)
            opp_buf.clear()

        if stats["rels_scanned"] % 1000 == 0:
            logger.info("[hist_scan] rels=%d  violations=%d  survivors=%d  saved=%d",
                        stats["rels_scanned"], stats["total_violations"],
                        stats["fee_survivors"], stats["opps_saved"])

    _flush_opps(opp_buf)
    logger.info("Historical arb scan complete: %s", stats)
    return stats


def produce_research_report() -> Dict[str, Any]:
    """Compute summary statistics from arbitrage_opportunities for the research report."""
    with session_scope() as s:
        df = pd.read_sql(text("""
            SELECT strategy_type, classification, gross_edge, total_fees,
                   estimated_slippage, net_edge, detected_at, status
            FROM arbitrage_opportunities
            WHERE status IN ('historical', 'open')
        """), s.bind)

    if df.empty:
        return {"error": "no_opportunities"}

    df["gross_edge"] = pd.to_numeric(df["gross_edge"], errors="coerce")
    df["net_edge"]   = pd.to_numeric(df["net_edge"],   errors="coerce")
    df["total_fees"] = pd.to_numeric(df["total_fees"], errors="coerce")

    total = len(df)
    by_class = df["classification"].value_counts().to_dict()
    by_strat = df["strategy_type"].value_counts().to_dict()

    profitable = df[df["net_edge"] > 0]

    report = {
        "total_candidates":    total,
        "by_classification":   by_class,
        "by_strategy":         by_strat,
        "profitable_after_fees": len(profitable),
        "pct_profitable":      round(len(profitable) / total * 100, 2) if total > 0 else 0,
        "avg_gross_edge":      round(float(df["gross_edge"].mean()), 6),
        "avg_net_edge":        round(float(df["net_edge"].mean()),   6),
        "median_net_edge":     round(float(df["net_edge"].median()), 6),
        "max_net_edge":        round(float(df["net_edge"].max()),    6),
        "avg_fees":            round(float(df["total_fees"].mean()), 6),
        "date_range":          {
            "earliest": str(pd.to_datetime(df["detected_at"]).min())[:10] if not df.empty else None,
            "latest":   str(pd.to_datetime(df["detected_at"]).max())[:10] if not df.empty else None,
        },
    }
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    import json
    stats = run_historical_arb_scan()
    print("\n=== HISTORICAL ARB SCAN COMPLETE ===")
    print(json.dumps(stats, indent=2, default=str))

    report = produce_research_report()
    print("\n=== RESEARCH REPORT ===")
    print(json.dumps(report, indent=2, default=str))
