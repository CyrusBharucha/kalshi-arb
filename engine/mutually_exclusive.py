"""
arbitrage/mutually_exclusive.py
Strategy 2: Mutually Exclusive & Collectively Exhaustive Outcome Arbitrage

For events where exactly one of N outcomes must occur (ME + CE):
    sum(P(outcome_i)) = 1.00

Using executable ask prices, if:
    sum(YES_ask_i) < 1.00   ->  gross edge exists

This is the generalization of the complement trade to multi-outcome events.
Examples: BOC raises 0/25/50bps, CPI above/at/below threshold.

We NEVER use midpoints. We buy YES at ask for every outcome.
The total cost is sum(YES_ask_i); the guaranteed return is $1.00.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from engine.simulator import ExecutionSimulator
from engine.fees import taker_fee_per_contract

logger = logging.getLogger(__name__)
_sim = ExecutionSimulator()


def check_me_arb(
    event_ticker: str,
    markets: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Check a set of markets for ME+CE arbitrage (buy-all-YES strategy).

    Arb condition: Σ YES_asks < $1.00 across N mutually exclusive, collectively
    exhaustive outcomes. Buying all YES contracts guarantees a $1.00 payout.

    Note: the live scanner (ws_bridge.py) also runs the complementary ME arb
    (buy all NOs when Σ NO_asks < N−1) which applies to ME-only sets. This
    function covers the historical-scanner path only.

    Args:
        event_ticker: Parent event identifier.
        markets:      List of market dicts, each with yes_ask (executable ask price).

    Returns:
        Opportunity dict if net_edge > 0, else None.
    """
    valid = []
    for m in markets:
        ask = _safe_float(m.get("yes_ask"))
        if ask is None or ask <= 0 or ask >= 1.0:
            continue
        valid.append({
            "market_id": m.get("market_id") or m.get("ticker", ""),
            "yes_ask":   ask,
            "depth":     _safe_float(m.get("open_interest")),   # use OI as depth proxy
        })

    if len(valid) < 2:
        return None

    total_ask = sum(m["yes_ask"] for m in valid)
    gross_edge = 1.0 - total_ask

    if gross_edge <= config.MIN_GROSS_EDGE:
        return None

    result = _sim.simulate_mutually_exclusive_arb(valid)

    if result.net_edge <= config.MIN_NET_EDGE:
        return None

    logger.info(
        "ME ARB: event=%s N=%d total_ask=%.4f gross=$%.4f net=$%.4f class=%s",
        event_ticker, len(valid), total_ask,
        result.gross_edge, result.net_edge, result.classification,
    )
    return result.to_db_dict()


def scan_me_arb(
    events_markets: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    Scan across all events for ME/CE arbitrage.

    Args:
        events_markets: {event_ticker: [market_dict, ...]}

    Returns:
        List of opportunity dicts.
    """
    opportunities = []
    for event_ticker, markets in events_markets.items():
        if len(markets) < 2:
            continue
        result = check_me_arb(event_ticker, markets)
        if result:
            result["event_ticker"] = event_ticker
            opportunities.append(result)

    opportunities.sort(key=lambda x: x.get("net_edge", 0), reverse=True)
    logger.info(
        "ME scan: %d events -> %d opportunities",
        len(events_markets), len(opportunities),
    )
    return opportunities


def check_overpriced_set(
    event_ticker: str,
    markets: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Check if the complete ME set is overpriced:
    sum(YES_bid_i) > 1.00  ->  sell YES on all outcomes.
    One settles at $1.00; rest at $0.00.
    Revenue = sum(YES_bid) > $1.00 = guaranteed payout -> net short profit.

    (Less common than underpriced, but logically symmetric.)
    """
    valid = []
    for m in markets:
        bid = _safe_float(m.get("yes_bid"))
        if bid is None or bid <= 0:
            continue
        valid.append({
            "market_id": m.get("market_id") or m.get("ticker", ""),
            "yes_bid":   bid,
        })

    if len(valid) < 2:
        return None

    total_bid  = sum(m["yes_bid"] for m in valid)
    gross_edge = total_bid - 1.0   # revenue from selling all minus $1 guaranteed payout

    if gross_edge <= config.MIN_GROSS_EDGE:
        return None

    total_fees = sum(taker_fee_per_contract(m["yes_bid"]) for m in valid)
    slippage   = total_bid * 10 / 10000
    net_edge   = gross_edge - total_fees - slippage

    if net_edge <= config.MIN_NET_EDGE:
        return None

    max_qty = 10.0
    return {
        "strategy_type":           "me_overpriced",
        "classification":          "B",
        "markets_involved":        [m["market_id"] for m in valid],
        "prices_json":             {m["market_id"]: {"yes_bid": m["yes_bid"]} for m in valid},
        "gross_edge":              round(gross_edge, 6),
        "total_fees":              round(total_fees, 6),
        "estimated_slippage":      round(slippage, 6),
        "net_edge":                round(net_edge, 6),
        "max_executable_contracts": max_qty,
        "max_gross_profit":        round(gross_edge * max_qty, 4),
        "max_net_profit":          round(net_edge * max_qty, 4),
        "event_ticker":            event_ticker,
        "notes": f"Overpriced ME set: sum(bids)={total_bid:.4f} > 1.00",
    }


def analyze_event_probability_sum(
    event_ticker: str,
    markets: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Calculate probability sum diagnostics for an event.
    Returns summary dict with both bid-sum and ask-sum.
    Does not make trading recommendations - just diagnostics.
    """
    asks = [_safe_float(m.get("yes_ask")) for m in markets]
    bids = [_safe_float(m.get("yes_bid")) for m in markets]
    mids = []
    for a, b in zip(asks, bids):
        if a is not None and b is not None:
            mids.append((a + b) / 2.0)
        else:
            mids.append(None)

    valid_asks = [x for x in asks if x is not None]
    valid_bids = [x for x in bids if x is not None]
    valid_mids = [x for x in mids if x is not None]

    return {
        "event_ticker":    event_ticker,
        "n_markets":       len(markets),
        "n_valid":         len(valid_asks),
        "sum_asks":        sum(valid_asks) if valid_asks else None,
        "sum_bids":        sum(valid_bids) if valid_bids else None,
        "sum_mids":        sum(valid_mids) if valid_mids else None,
        "gross_edge_buyside":  1.0 - sum(valid_asks) if valid_asks else None,
        "gross_edge_sellside": sum(valid_bids) - 1.0 if valid_bids else None,
        "underpriced":     sum(valid_asks) < 1.0 if valid_asks else False,
        "overpriced":      sum(valid_bids) > 1.0 if valid_bids else False,
    }


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
