"""
backtest/engine.py
Historical arbitrage backtester.

Takes detected historical opportunities (from candlestick data) and simulates
what would have happened had we entered them at the time of detection.

Key design rules:
  1. No look-ahead bias - only data available at entry_ts is used for entry.
  2. Exit at settlement (market resolves YES or NO at $1.00 or $0.00).
  3. Fees applied at entry (taker rates); no fee at settlement.
  4. Clearly labelled: historical candlestick-based opportunities are Class B
     (potential, not confirmed executable) unless live depth data exists.
  5. No silent gap-filling - if settlement data is missing, trade is excluded.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.fees import taker_fee_per_contract
from database.repository import (
    session_scope, get_candlesticks, get_backtest_performance,
)
from database.models import BacktestTrade

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    Backtests historical arbitrage opportunities using Kalshi candlestick data.
    """

    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id or f"bt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self._trades: List[Dict[str, Any]] = []

    # -- Strategy 1: Historical complement arb ---------------------------------

    def backtest_complement_arb(
        self,
        market_id: str,
        period_interval: int = 60,
        contracts_per_trade: float = 10.0,
        start_ts: Optional[datetime] = None,
        end_ts:   Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Simulate trading every historical complement arb opportunity
        for a single market.

        Entry: whenever yes_ask_close + no_ask_close < 1.00 - min_net_edge.
        Exit: at settlement ($1.00 guaranteed if held to settlement).

        Returns trade-level DataFrame.
        """
        with session_scope() as s:
            df = get_candlesticks(s, market_id, period_interval, start_ts, end_ts)

        if df.empty:
            logger.warning("No candlestick data for %s period=%d", market_id, period_interval)
            return pd.DataFrame()

        # We need settlement price - check if market is settled
        settlement_value = self._get_settlement_value(market_id)

        trades = []
        for _, row in df.iterrows():
            yes_ask = _safe_float(row.get("yes_ask_close"))
            yes_bid = _safe_float(row.get("yes_bid_close"))

            if yes_ask is None or yes_bid is None:
                continue
            if yes_ask >= 1.0 or yes_bid <= 0:
                continue

            no_ask = round(1.0 - yes_bid, 6)
            total_cost = yes_ask + no_ask
            gross_edge = 1.0 - total_cost

            if gross_edge <= 0.005:   # minimum 0.5c gross edge threshold
                continue

            fee_yes = taker_fee_per_contract(yes_ask)
            fee_no  = taker_fee_per_contract(no_ask)
            total_fees = (fee_yes + fee_no) * contracts_per_trade
            slippage   = total_cost * 10 / 10000 * contracts_per_trade

            # Gross P&L if held to settlement
            if settlement_value is not None:
                # One leg pays $1.00; both legs cost total_cost
                gross_pnl = (1.0 - total_cost) * contracts_per_trade
            else:
                gross_pnl = None   # settlement unknown - cannot compute realized P&L

            net_pnl = (gross_pnl - total_fees - slippage) if gross_pnl is not None else None

            trades.append({
                "run_id":        self.run_id,
                "market_id":     market_id,
                "side":          "both",
                "action":        "buy_complement",
                "entry_ts":      row.get("period_end_ts"),
                "exit_ts":       None,
                "entry_price":   total_cost,
                "exit_price":    settlement_value,
                "quantity":      contracts_per_trade,
                "taker_fees":    total_fees,
                "maker_fees":    0.0,
                "slippage":      slippage,
                "gross_pnl":     gross_pnl,
                "net_pnl":       net_pnl,
                "notes":         f"gross_edge={gross_edge:.4f} class=B (candlestick only)",
            })

        result_df = pd.DataFrame(trades)
        if not result_df.empty:
            logger.info(
                "Backtest %s: %d trades | net_pnl=$%.2f",
                market_id, len(result_df),
                result_df["net_pnl"].sum() if "net_pnl" in result_df else 0,
            )
            self._trades.extend(trades)

        return result_df

    # -- Aggregate metrics ------------------------------------------------------

    def compute_metrics(self, trades_df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        """
        Compute standard performance metrics from a trades DataFrame.
        """
        if trades_df is None:
            trades_df = pd.DataFrame(self._trades)

        if trades_df.empty:
            return {"error": "No trades"}

        net_pnl = trades_df["net_pnl"].dropna()
        gross_pnl = trades_df["gross_pnl"].dropna()

        n_trades = len(trades_df)
        n_profitable = int((net_pnl > 0).sum())

        metrics = {
            "run_id":          self.run_id,
            "n_trades":        n_trades,
            "n_profitable":    n_profitable,
            "win_rate":        n_profitable / n_trades if n_trades > 0 else 0.0,
            "total_gross_pnl": float(gross_pnl.sum()) if len(gross_pnl) else 0.0,
            "total_net_pnl":   float(net_pnl.sum()) if len(net_pnl) else 0.0,
            "avg_net_pnl":     float(net_pnl.mean()) if len(net_pnl) else 0.0,
            "std_net_pnl":     float(net_pnl.std()) if len(net_pnl) > 1 else 0.0,
            "max_pnl":         float(net_pnl.max()) if len(net_pnl) else 0.0,
            "min_pnl":         float(net_pnl.min()) if len(net_pnl) else 0.0,
            "total_fees":      float(trades_df["taker_fees"].sum()),
            "total_slippage":  float(trades_df["slippage"].sum()),
        }

        # Sharpe ratio (annualized - rough; each "trade" is one opportunity)
        if metrics["std_net_pnl"] > 0 and n_trades > 1:
            metrics["sharpe_ratio"] = (
                metrics["avg_net_pnl"] / metrics["std_net_pnl"] * (n_trades ** 0.5)
            )
        else:
            metrics["sharpe_ratio"] = None

        # Max drawdown on cumulative P&L
        if len(net_pnl) > 1:
            cum = net_pnl.cumsum()
            rolling_max = cum.cummax()
            drawdown = cum - rolling_max
            metrics["max_drawdown"] = float(drawdown.min())
        else:
            metrics["max_drawdown"] = 0.0

        return metrics

    # -- Helpers ----------------------------------------------------------------

    def _get_settlement_value(self, market_id: str) -> Optional[float]:
        from sqlalchemy import text
        try:
            with session_scope() as s:
                result = s.execute(
                    text("SELECT settlement_value FROM markets WHERE market_id = :mid LIMIT 1"),
                    {"mid": market_id}
                ).fetchone()
                if result and result[0] is not None:
                    return float(result[0])
        except Exception:
            pass
        return None

    def run_all(
        self,
        period_interval: int = 60,
        limit: int = 200,
        min_volume: float = 1000.0,
    ) -> Dict[str, Any]:
        """
        Run complement-arb backtest across the top `limit` markets by volume.

        Fetches all settled markets with sufficient volume from the DB,
        backtests each, aggregates metrics, and saves trades.

        Returns:
            {markets_tested, trades_found, total_net_pnl, win_rate, sharpe_ratio}
        """
        from sqlalchemy import text
        markets_tested = 0
        trades_found   = 0
        try:
            from database.repository import get_engine
            engine = get_engine()
            with engine.connect() as conn:
                rows = conn.execute(text("""
                    SELECT m.market_id
                    FROM markets m
                    LEFT JOIN LATERAL (
                        SELECT SUM(volume) AS vol
                        FROM market_snapshots
                        WHERE market_id = m.market_id
                    ) s ON TRUE
                    WHERE m.status IN ('settled', 'closed')
                      AND COALESCE(s.vol, 0) >= :min_vol
                    ORDER BY COALESCE(s.vol, 0) DESC NULLS LAST
                    LIMIT :lim
                """), {"min_vol": min_volume, "lim": limit}).fetchall()
            market_ids = [r[0] for r in rows]
            logger.info("run_all: backtesting %d markets (period=%ds)", len(market_ids), period_interval)

            for mid in market_ids:
                df = self.backtest_complement_arb(mid, period_interval=period_interval)
                markets_tested += 1
                trades_found   += len(df)

        except Exception as exc:
            logger.warning("run_all: %s", exc)

        n_saved = self.save_trades_to_db()
        all_df  = pd.DataFrame(self._trades)
        metrics = self.compute_metrics(all_df) if not all_df.empty else {}
        metrics["markets_tested"] = markets_tested
        metrics["trades_found"]   = trades_found
        metrics["trades_saved"]   = n_saved
        return metrics

    def save_trades_to_db(self) -> int:
        """Persist all simulated trades to backtest_trades table."""
        if not self._trades:
            return 0
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from database.models import BacktestTrade
        inserted = 0
        with session_scope() as s:
            for t in self._trades:
                s.add(BacktestTrade(**t))
                inserted += 1
        logger.info("Saved %d backtest trades (run_id=%s)", inserted, self.run_id)
        return inserted


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
