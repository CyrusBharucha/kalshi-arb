"""
arbitrage/yes_no.py
Strategy 1: YES/NO Complement Arbitrage

For any binary Kalshi contract:
  - Settlement pays $1.00 to one side.
  - YES_price + NO_price should equal $1.00 at fair value.
  - YES_ask + NO_ask < $1.00  ->  buying both is cheaper than guaranteed $1.00 payoff.

This is the cleanest form of Kalshi arbitrage and the most likely to be executable.

Key insight: we NEVER use midpoint prices to claim arbitrage.
We use YES_ask to buy YES and NO_ask to buy NO.
NO_ask = 1 - YES_bid  (Kalshi shows YES bids only; NO ask is derived).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from engine.simulator import ExecutionSimulator
from engine.fees import taker_fee_per_contract

logger = logging.getLogger(__name__)
_sim = ExecutionSimulator()


def check_complement_arb(
    market: Dict[str, Any],
    order_book: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Evaluate a single market for YES/NO complement arbitrage.

    Args:
        market:     Market dict with yes_bid, yes_ask (from snapshot or API).
        order_book: Optional order book dict with depth info.
                    {"yes_bids": [(price, qty), ...], "yes_asks": [(price, qty), ...]}

    Returns:
        ExecutionResult.to_db_dict() if an opportunity exists (gross_edge > 0),
        else None.
    """
    yes_bid = _safe_float(market.get("yes_bid"))
    yes_ask = _safe_float(market.get("yes_ask"))

    if yes_bid is None or yes_ask is None:
        return None
    if yes_bid <= 0 or yes_ask <= 0 or yes_ask >= 1.0:
        return None

    # NO ask = 1 - YES bid  (if I want to buy NO, I cross the YES bid)
    no_ask = round(1.0 - yes_bid, 6)

    # Gross edge check (quick reject - must be positive before paying costs)
    gross_edge = 1.0 - yes_ask - no_ask
    if gross_edge <= config.MIN_GROSS_EDGE:
        return None

    # Extract depth from order book if available
    yes_depth = _best_ask_depth(order_book, "yes") if order_book else None
    no_depth  = _best_bid_depth(order_book, "yes") if order_book else None

    result = _sim.simulate_complement_arb(
        market_id = market.get("market_id") or market.get("ticker", ""),
        yes_ask   = yes_ask,
        no_ask    = no_ask,
        yes_depth = yes_depth,
        no_depth  = no_depth,
    )

    if result.net_edge <= config.MIN_NET_EDGE:
        return None

    logger.info(
        "COMPLEMENT ARB: %s | gross=$%.4f fees=$%.4f net=$%.4f max_qty=%.0f class=%s",
        market.get("ticker"), result.gross_edge, result.total_fees,
        result.net_edge, result.max_executable, result.classification,
    )
    return result.to_db_dict()


def scan_complement_arb(
    markets: List[Dict[str, Any]],
    order_books: Optional[Dict[str, Dict]] = None,
) -> List[Dict[str, Any]]:
    """
    Scan a list of markets for complement arbitrage.
    Returns list of opportunity dicts sorted by net_edge descending.
    """
    opportunities = []
    for m in markets:
        ticker = m.get("market_id") or m.get("ticker", "")
        ob = (order_books or {}).get(ticker)
        result = check_complement_arb(m, ob)
        if result:
            opportunities.append(result)

    opportunities.sort(key=lambda x: x.get("net_edge", 0), reverse=True)
    logger.info(
        "Complement scan: %d markets -> %d opportunities",
        len(markets), len(opportunities),
    )
    return opportunities


# -- Historical scanner (uses candlestick OHLC) --------------------------------

def scan_historical_complement_arb(
    candlestick_df,
) -> List[Dict[str, Any]]:
    """
    Run complement arbitrage detection over historical candlestick data.

    candlestick_df columns: market_id, period_end_ts, yes_bid_close, yes_ask_close
    (uses close prices of each candle as the representative price)

    Returns list of historical opportunity records.
    IMPORTANT: classified as Type B - no depth confirmation.
    """
    if candlestick_df is None or candlestick_df.empty:
        return []

    opportunities = []
    for _, row in candlestick_df.iterrows():
        yes_ask = row.get("yes_ask_close")
        yes_bid = row.get("yes_bid_close")
        if yes_ask is None or yes_bid is None:
            continue
        try:
            yes_ask = float(yes_ask)
            yes_bid = float(yes_bid)
        except (TypeError, ValueError):
            continue

        if yes_bid <= 0 or yes_ask <= 0 or yes_ask >= 1.0:
            continue

        no_ask = round(1.0 - yes_bid, 6)
        gross_edge = 1.0 - yes_ask - no_ask

        if gross_edge <= config.MIN_GROSS_EDGE:
            continue

        # Compute fees at close prices
        fee_yes = taker_fee_per_contract(yes_ask)
        fee_no  = taker_fee_per_contract(no_ask)
        total_fees = fee_yes + fee_no
        slippage   = (yes_ask + no_ask) * 10 / 10000   # 10 bps - no depth
        net_edge   = gross_edge - total_fees - slippage

        opportunities.append({
            "strategy_type":           "yes_no_complement",
            "classification":          "B",    # historical - no depth confirmation
            "markets_involved":        [str(row["market_id"])],
            "prices_json":             {
                str(row["market_id"]): {
                    "yes_ask": yes_ask, "no_ask": no_ask,
                    "period_end_ts": str(row.get("period_end_ts", "")),
                }
            },
            "gross_edge":              round(gross_edge, 6),
            "total_fees":              round(total_fees, 6),
            "estimated_slippage":      round(slippage, 6),
            "net_edge":                round(net_edge, 6),
            "max_executable_contracts": 10.0,  # unknown; conservative
            "max_gross_profit":        round(gross_edge * 10, 4),
            "max_net_profit":          round(net_edge * 10, 4),
            "notes":                   "Historical candlestick - no depth data (Class B).",
        })

    logger.info(
        "Historical complement scan: %d candles -> %d candidates",
        len(candlestick_df), len(opportunities),
    )
    return opportunities


# -- Helpers --------------------------------------------------------------------

def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _best_ask_depth(ob: Dict, side: str) -> Optional[float]:
    """Return quantity at best ask level from order book."""
    asks = ob.get("yes_asks", [])
    if asks:
        return float(asks[0][1]) if len(asks[0]) > 1 else None
    return None


def _best_bid_depth(ob: Dict, side: str) -> Optional[float]:
    """Return quantity at best bid level (= NO sell side)."""
    bids = ob.get("yes_bids", [])
    if bids:
        return float(bids[0][1]) if len(bids[0]) > 1 else None
    return None
