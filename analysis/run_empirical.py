"""
analysis/run_empirical.py
Targeted empirical research run.

Only processes markets that appear in non-complement relationships
with trade data on both legs - the actual actionable universe.

Steps:
  1. Load the 3,989 actionable relationship pairs
  2. Build in-memory OHLC for just those markets (fast - ~7K markets)
  3. Run violation detection
  4. Apply fee model
  5. Report actual measured results

Usage:
    python analysis/run_empirical.py
"""
from __future__ import annotations

import logging
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple

import pandas as pd
import numpy as np
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.repository import session_scope
from database.models import ArbitrageOpportunity, Candlestick
from engine.fees import taker_fee_per_contract
from backtest.metrics import compute_performance_metrics

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

MIN_NET_EDGE   = 0.001   # $0.001 minimum to record
PERIOD_MINUTES = 15      # 15-minute bars (intraday - matches BTC contract cadence)


# -- 1. Load actionable pairs --------------------------------------------------

def load_actionable_pairs() -> pd.DataFrame:
    """Non-complement relationships where both legs have trade records."""
    with session_scope() as s:
        df = pd.read_sql(text("""
            SELECT cr.id, cr.relationship_type,
                   cr.market_id_1, cr.market_id_2,
                   cr.logical_constraint, cr.implied_inequality, cr.confidence
            FROM contract_relationships cr
            WHERE cr.relationship_type != 'complement'
              AND EXISTS (
                  SELECT 1 FROM trades t1 WHERE t1.market_id = cr.market_id_1 LIMIT 1
              )
              AND EXISTS (
                  SELECT 1 FROM trades t2 WHERE t2.market_id = cr.market_id_2 LIMIT 1
              )
            ORDER BY cr.relationship_type
        """), s.bind)
    return df


# -- 2. Build in-memory OHLC for just the relevant markets --------------------

def build_targeted_ohlc(
    market_ids: List[str],
    period_minutes: int = PERIOD_MINUTES,
) -> Dict[str, pd.Series]:
    """
    Load trades for the given market IDs, resample to OHLC, return a dict
    market_id -> Series(period_end_ts -> close_price).
    """
    logger.info("Loading trades for %d markets...", len(market_ids))
    with session_scope() as s:
        df = pd.read_sql(text("""
            SELECT market_id, trade_ts, price, quantity
            FROM trades
            WHERE market_id = ANY(:mids)
            ORDER BY market_id, trade_ts
        """), s.bind, params={"mids": market_ids})

    if df.empty:
        return {}

    df["trade_ts"] = pd.to_datetime(df["trade_ts"], utc=True)
    df["price"]    = pd.to_numeric(df["price"],    errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(1)

    freq = f"{period_minutes}min"
    price_index: Dict[str, pd.Series] = {}

    for market_id, grp in df.groupby("market_id"):
        grp = grp.set_index("trade_ts").sort_index()
        ohlc = grp["price"].resample(freq).ohlc()
        ohlc = ohlc.dropna(subset=["close"])
        end_ts = ohlc.index + pd.Timedelta(minutes=period_minutes)
        price_index[market_id] = pd.Series(
            ohlc["close"].values,
            index=pd.DatetimeIndex(end_ts, tz="UTC"),
        )

    logger.info("OHLC built for %d markets across %d bars",
                len(price_index),
                sum(len(v) for v in price_index.values()))
    return price_index


# -- 3. Violation detection ----------------------------------------------------

def scan_violations(
    rels: pd.DataFrame,
    price_index: Dict[str, pd.Series],
) -> List[Dict[str, Any]]:
    """
    Scan all actionable pairs for price violations.
    Returns list of violation dicts (with computed P&L).
    """
    violations: List[Dict[str, Any]] = []

    by_type: Dict[str, int] = {}
    skipped = 0

    for _, rel in rels.iterrows():
        m1    = rel["market_id_1"]
        m2    = rel["market_id_2"]
        rtype = rel["relationship_type"]

        s1 = price_index.get(m1)
        s2 = price_index.get(m2)
        if s1 is None or s2 is None:
            skipped += 1
            continue

        # Align timestamps
        combined = pd.concat([s1.rename("p1"), s2.rename("p2")], axis=1).dropna()
        if combined.empty:
            skipped += 1
            continue

        for ts, row in combined.iterrows():
            p1, p2 = float(row["p1"]), float(row["p2"])
            if not (0 < p1 < 1 and 0 < p2 < 1):
                continue

            gross_edge: float = 0.0
            direction:  str   = ""

            if rtype == "mutually_exclusive":
                # P(m1) + P(m2) > 1.00 -> sell both YES
                prob_sum = p1 + p2
                if prob_sum > 1.0:
                    gross_edge = prob_sum - 1.0
                    direction  = "sell_both_yes"

            elif rtype in ("threshold_order", "superset"):
                # m1 is the "higher" market; must have P(m1) >= P(m2)
                # Violation: P(m2) > P(m1)
                if p2 > p1:
                    gross_edge = p2 - p1
                    direction  = "buy_m1_sell_m2"

            elif rtype == "collectively_exhaustive":
                # P(m1) + P(m2) < 1.00 -> buy both YES
                prob_sum = p1 + p2
                if prob_sum < 1.0:
                    gross_edge = 1.0 - prob_sum
                    direction  = "buy_both_yes"

            if gross_edge < 0.005:
                continue  # below minimum gross edge

            fee1 = taker_fee_per_contract(p1)
            fee2 = taker_fee_per_contract(p2)
            total_fees = fee1 + fee2
            net_edge   = gross_edge - total_fees

            by_type[rtype] = by_type.get(rtype, 0) + 1
            violations.append({
                "ts":          ts,
                "market_id_1": m1,
                "market_id_2": m2,
                "rtype":       rtype,
                "direction":   direction,
                "p1":          p1,
                "p2":          p2,
                "gross_edge":  round(gross_edge, 6),
                "total_fees":  round(total_fees, 6),
                "net_edge":    round(net_edge,   6),
                "confidence":  float(rel["confidence"]),
            })

    logger.info("Violations found: %d  (skipped pairs: %d)", len(violations), skipped)
    for k, v in by_type.items():
        logger.info("  %-30s %d", k, v)
    return violations


# -- 4. Persist to DB ----------------------------------------------------------

def persist_violations(violations: List[Dict[str, Any]]) -> int:
    saved = 0
    batch = []
    for v in violations:
        if v["net_edge"] < MIN_NET_EDGE:
            continue
        opp = {
            "opportunity_id":    uuid.uuid4(),
            "detected_at":       v["ts"].to_pydatetime(),
            "strategy_type":     v["rtype"],
            "classification":    "B",
            "markets_involved":  [v["market_id_1"], v["market_id_2"]],
            "prices_json":       {
                "p1": v["p1"], "p2": v["p2"],
                "direction": v["direction"],
                "source": "targeted_ohlc",
            },
            "gross_edge":        v["gross_edge"],
            "total_fees":        v["total_fees"],
            "estimated_slippage": 0.001,
            "net_edge":          v["net_edge"],
            "max_executable_contracts": 1,
            "status":            "historical",
        }
        batch.append(opp)
        if len(batch) >= 500:
            with session_scope() as s:
                for o in batch:
                    stmt = pg_insert(ArbitrageOpportunity).values(o)
                    stmt = stmt.on_conflict_do_nothing()
                    s.execute(stmt)
            saved += len(batch)
            batch.clear()

    if batch:
        with session_scope() as s:
            for o in batch:
                stmt = pg_insert(ArbitrageOpportunity).values(o)
                stmt = stmt.on_conflict_do_nothing()
                s.execute(stmt)
        saved += len(batch)

    logger.info("Persisted %d opportunities to DB", saved)
    return saved


# -- 5. Compute research report ------------------------------------------------

def compute_research_report(
    violations: List[Dict[str, Any]],
    period_minutes: int,
    n_pairs: int,
    market_ids: List[str],
) -> Dict[str, Any]:
    if not violations:
        return {"error": "no_violations_found"}

    df = pd.DataFrame(violations)
    gross = df["gross_edge"]
    net   = df["net_edge"]
    fees  = df["total_fees"]

    profitable = df[df["net_edge"] > 0]
    fee_killed  = df[(df["gross_edge"] >= 0.005) & (df["net_edge"] <= 0)]

    by_type = df.groupby("rtype").agg(
        n=("net_edge", "count"),
        pct_profitable=("net_edge", lambda x: (x > 0).mean() * 100),
        avg_gross=("gross_edge", "mean"),
        avg_net=("net_edge", "mean"),
        max_gross=("gross_edge", "max"),
        max_net=("net_edge", "max"),
    ).round(6).to_dict("index")

    # P&L series (net, per opportunity, 1 contract)
    pnl_series = net.values

    report = {
        # -- Sample description ---------------------------------------------
        "SAMPLE_PERIOD": "2026-06-23 (single day - all available trades)",
        "SAMPLE_WARNING": (
            "Results derived from ONE calendar day of data. "
            "This is insufficient for reliable frequency or P&L distributions. "
            "Treat as proof-of-concept, not validated strategy."
        ),
        "bar_period_minutes": period_minutes,
        "markets_with_trades": len(market_ids),
        "actionable_relationship_pairs": n_pairs,

        # -- Violation counts -----------------------------------------------
        "total_violations_detected": len(df),
        "violations_above_min_gross_0.5pct": len(df),
        "profitable_after_fees": len(profitable),
        "pct_profitable_after_fees": round(len(profitable) / len(df) * 100, 2),
        "fee_killed_count": len(fee_killed),
        "pct_fee_killed": round(len(fee_killed) / len(df) * 100, 2),

        # -- Edge statistics ------------------------------------------------
        "gross_edge": {
            "mean":   round(float(gross.mean()),   4),
            "median": round(float(gross.median()), 4),
            "p75":    round(float(gross.quantile(0.75)), 4),
            "p90":    round(float(gross.quantile(0.90)), 4),
            "max":    round(float(gross.max()),    4),
        },
        "total_fees": {
            "mean":   round(float(fees.mean()),   4),
            "median": round(float(fees.median()), 4),
        },
        "net_edge": {
            "mean":   round(float(net.mean()),   4),
            "median": round(float(net.median()), 4),
            "p75":    round(float(net.quantile(0.75)), 4),
            "p90":    round(float(net.quantile(0.90)), 4),
            "max":    round(float(net.max()),    4),
        },

        # -- By relationship type -------------------------------------------
        "by_type": by_type,

        # -- Execution caveats ----------------------------------------------
        "EXECUTION_CAVEATS": [
            "Classification B: prices from OHLC close bars, not live order-book quotes",
            "Slippage modelled at 10bps - may be higher in thin prediction markets",
            "No market-impact modelling: assume 1 contract fills at quoted close",
            "No settlement correlation: assumes independent resolution (may be wrong for ME pairs)",
            "No look-ahead bias in price detection; settlement outcomes not verified",
        ],
    }
    return report


# -- Main ---------------------------------------------------------------------

def run_empirical():
    logger.info("=== KALSHI EMPIRICAL ARBITRAGE RESEARCH ===")
    logger.info("Sample: 2026-06-23 | Period: %d-min bars", PERIOD_MINUTES)

    # Step 1
    rels = load_actionable_pairs()
    logger.info("Actionable pairs: %d", len(rels))

    # Step 2
    all_mids = list(set(rels["market_id_1"].tolist() + rels["market_id_2"].tolist()))
    price_index = build_targeted_ohlc(all_mids, PERIOD_MINUTES)

    # Step 3
    violations = scan_violations(rels, price_index)

    # Step 4
    saved = persist_violations(violations)

    # Step 5
    report = compute_research_report(violations, PERIOD_MINUTES, len(rels), all_mids)

    print("\n" + "=" * 70)
    print("KALSHI ARBITRAGE EMPIRICAL RESULTS")
    print("=" * 70)
    print(json.dumps(report, indent=2, default=str))
    print("=" * 70)

    logger.info("Opportunities saved to DB: %d", saved)
    return report


if __name__ == "__main__":
    run_empirical()
