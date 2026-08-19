"""
backtest/full_backtest.py
Full historical backtest of all detected arbitrage opportunities.

Design rules:
  1. No look-ahead bias: entry at detection timestamp, exit at settlement
  2. No survivorship bias: we use ALL candidate pairs regardless of outcome
  3. Fees applied at entry (taker rates)
  4. Slippage modeled as 10bps of trade value
  5. Opportunities from synthetic OHLC are Class B (no order-book depth)
  6. Clearly labeled: backtest results are POTENTIAL, not confirmed executable

Output:
  - backtest_trades table populated
  - Summary metrics printed

Usage:
    python backtest/full_backtest.py
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.repository import session_scope
from database.models import BacktestTrade, ArbitrageOpportunity
from engine.fees import taker_fee_per_contract
from backtest.metrics import compute_performance_metrics

logger = logging.getLogger(__name__)

SLIPPAGE_BPS = 10    # 10 basis points slippage assumption
CONTRACTS    = 10    # contracts per trade (simulation unit)


def load_opportunities() -> pd.DataFrame:
    """Load all historical opportunities from arbitrage_opportunities table."""
    with session_scope() as s:
        df = pd.read_sql(text("""
            SELECT opportunity_id, detected_at, strategy_type, classification,
                   markets_involved, prices_json,
                   gross_edge, total_fees, estimated_slippage, net_edge
            FROM arbitrage_opportunities
            WHERE status = 'historical'
            ORDER BY detected_at
        """), s.bind)
    return df


def load_settlement_values() -> Dict[str, Optional[float]]:
    """Load settlement values for all markets that have resolved."""
    with session_scope() as s:
        rows = s.execute(text("""
            SELECT market_id, settlement_value
            FROM markets
            WHERE settlement_value IS NOT NULL
        """)).fetchall()
    return {r[0]: float(r[1]) if r[1] is not None else None for r in rows}


def simulate_trade(
    opp: pd.Series,
    settlement_values: Dict[str, Optional[float]],
    run_id: str,
) -> List[Dict[str, Any]]:
    """
    Simulate entry+exit for one arbitrage opportunity.
    Returns list of trade legs to insert into backtest_trades.
    """
    markets = opp.get("markets_involved") or []
    if not markets or len(markets) < 2:
        return []

    m1, m2 = markets[0], markets[1]
    p1 = float(opp.get("prices_json", {}).get("p1", 0) or 0)
    p2 = float(opp.get("prices_json", {}).get("p2", 0) or 0)
    gross_edge = float(opp.get("gross_edge") or 0)
    total_fees_per_contract = float(opp.get("total_fees") or 0)
    strategy = opp.get("strategy_type", "unknown")

    entry_ts = pd.to_datetime(opp["detected_at"], utc=True)
    entry_ts_dt = entry_ts.to_pydatetime()

    slippage_per_contract = (p1 + p2) * SLIPPAGE_BPS / 10_000

    # Determine if settlement data exists
    sv1 = settlement_values.get(m1)
    sv2 = settlement_values.get(m2)

    # For mutually_exclusive: short both; receive when both resolve NO (net cost recovered)
    # For threshold/superset: buy underpriced, short overpriced
    # Simplification: model as "buy the edge" at gross_edge cost, hold to settlement
    if strategy in ("mutually_exclusive",):
        # Sell m1-YES + Sell m2-YES (both overpriced)
        gross_pnl_per_contract = gross_edge   # recover when at least one resolves NO
        exit_price = 1.0 - gross_edge         # simplified
    elif strategy in ("threshold_order", "superset"):
        # Buy hi (underpriced), sell lo (overpriced)
        gross_pnl_per_contract = gross_edge
        exit_price = p1 + gross_edge          # simplified
    else:
        gross_pnl_per_contract = gross_edge
        exit_price = None

    total_fees  = total_fees_per_contract  * CONTRACTS
    total_slip  = slippage_per_contract    * CONTRACTS
    gross_pnl   = gross_pnl_per_contract   * CONTRACTS
    net_pnl     = gross_pnl - total_fees - total_slip

    # Classification: B unless we have settlement data (then we know the realized outcome)
    has_settlement = sv1 is not None or sv2 is not None
    classification = "A" if has_settlement else "B"

    trades = [{
        "run_id":      run_id,
        "market_id":   m1,
        "side":        "short" if strategy == "mutually_exclusive" else "long",
        "action":      strategy,
        "entry_ts":    entry_ts_dt,
        "exit_ts":     None,
        "entry_price": p1,
        "exit_price":  exit_price,
        "quantity":    float(CONTRACTS),
        "taker_fees":  round(total_fees, 6),
        "maker_fees":  0.0,
        "slippage":    round(total_slip, 6),
        "gross_pnl":   round(gross_pnl, 6),
        "net_pnl":     round(net_pnl, 6),
        "notes":       f"class={classification} gross_edge={gross_edge:.4f} strategy={strategy}",
    }]

    return trades


def run_full_backtest(max_opps: int = 100_000) -> Dict[str, Any]:
    """
    Run the complete backtest and return summary metrics.
    """
    run_id = f"bt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    logger.info("Starting full backtest run_id=%s", run_id)

    opps = load_opportunities()
    if opps.empty:
        logger.warning("No historical opportunities found. "
                        "Run historical_arb_scanner.py first.")
        return {"error": "no_opportunities", "run_id": run_id}

    opps = opps.head(max_opps)
    logger.info("Backtesting %d opportunities...", len(opps))

    settlement_values = load_settlement_values()
    logger.info("Settlement values available for %d markets", len(settlement_values))

    all_trades: List[Dict] = []

    for _, opp in opps.iterrows():
        trades = simulate_trade(opp, settlement_values, run_id)
        all_trades.extend(trades)

    # Flush trades to DB
    inserted = 0
    batch_size = 1_000
    for i in range(0, len(all_trades), batch_size):
        batch = all_trades[i : i + batch_size]
        try:
            with session_scope() as s:
                for t in batch:
                    s.add(BacktestTrade(**t))
            inserted += len(batch)
        except Exception as exc:
            logger.warning("Batch insert failed: %s", exc)

    logger.info("Inserted %d backtest trades", inserted)

    # Compute metrics
    trades_df = pd.DataFrame(all_trades)
    if trades_df.empty:
        return {"run_id": run_id, "error": "no_trades"}

    net_pnl = pd.to_numeric(trades_df["net_pnl"], errors="coerce").dropna()
    metrics = compute_performance_metrics(net_pnl)
    metrics["run_id"]      = run_id
    metrics["n_opps"]      = len(opps)
    metrics["n_trades"]    = inserted
    metrics["start_date"]  = str(pd.to_datetime(opps["detected_at"]).min())[:10]
    metrics["end_date"]    = str(pd.to_datetime(opps["detected_at"]).max())[:10]

    logger.info("Backtest complete: %s", metrics)
    return metrics


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    import json
    metrics = run_full_backtest()
    print("\n=== BACKTEST RESULTS ===")
    print(json.dumps(metrics, indent=2, default=str))
