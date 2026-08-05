"""
arbitrage/nested_contracts.py
Strategy 3: Nested / Logical Contract Violations

Detects situations where market prices violate probability monotonicity:
  P(X ≤ high) >= P(X ≤ low)   [MUST hold by logic]
  P(superset event) >= P(subset event)

When executable prices violate this, there is either:
  - A Class C relative-value opportunity (trade the spread, but payoff is conditional)
  - A Class A arbitrage (rare - requires the relationship to be fully locked in)

Examples:
  "Rate ≤ 4.50%" must be at least as likely as "Rate ≤ 4.25%"
  "BOC cuts ≥ 25bps" must be at least as likely as "BOC cuts ≥ 50bps"
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from engine.simulator import ExecutionSimulator
from engine.relationship_detector import (
    detect_threshold_order_relationships,
    check_logical_price_violations,
)

logger = logging.getLogger(__name__)
_sim = ExecutionSimulator()


def scan_threshold_violations(
    event_ticker: str,
    markets: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Given markets in a threshold-structured event, find all executable
    price violations of monotonicity constraints.

    Returns list of opportunity dicts.
    """
    if len(markets) < 2:
        return []

    # Build relationship list
    relationships = detect_threshold_order_relationships(markets)
    if not relationships:
        return []

    # Build price lookup
    prices = {}
    for m in markets:
        mid = m.get("market_id") or m.get("ticker", "")
        prices[mid] = {
            "yes_bid": _safe_float(m.get("yes_bid")),
            "yes_ask": _safe_float(m.get("yes_ask")),
            "depth":   _safe_float(m.get("open_interest")),
        }

    # Check for violations at midpoint (flag them; only confirmed by executable prices)
    midpoint_violations = check_logical_price_violations(relationships, prices)
    if not midpoint_violations:
        return []

    # Now evaluate each violation with executable prices
    opportunities = []
    for viol in midpoint_violations:
        sup_id = viol["market_1"]
        sub_id = viol["market_2"]
        sup_prices = prices.get(sup_id, {})
        sub_prices = prices.get(sub_id, {})

        sup_ask = sup_prices.get("yes_ask")
        sub_bid = sub_prices.get("yes_bid")
        sup_bid = sup_prices.get("yes_bid")
        sub_ask = sub_prices.get("yes_ask")

        if sup_ask is None or sub_bid is None:
            continue
        if sup_ask >= 1.0 or sub_bid <= 0:
            continue

        result = _sim.simulate_threshold_violation(
            superset_market={
                "market_id": sup_id,
                "yes_bid": sup_bid, "yes_ask": sup_ask,
                "depth": sup_prices.get("depth"),
            },
            subset_market={
                "market_id": sub_id,
                "yes_bid": sub_bid, "yes_ask": sub_ask,
                "depth": sub_prices.get("depth"),
            },
        )

        if result.net_edge > config.MIN_NET_EDGE:  # gate on net (after fees), not gross
            opp = result.to_db_dict()
            opp["event_ticker"] = event_ticker
            opp["violation_detail"] = viol["violation"]
            opportunities.append(opp)
            logger.info(
                "THRESHOLD VIOLATION: event=%s %s | gross=$%.4f net=$%.4f",
                event_ticker, viol["violation"],
                result.gross_edge, result.net_edge,
            )

    return opportunities


def scan_all_nested_violations(
    events_markets: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    Scan all events for threshold ordering violations.
    Returns all opportunities found, sorted by gross_edge.
    """
    all_opps = []
    for event_ticker, markets in events_markets.items():
        opps = scan_threshold_violations(event_ticker, markets)
        all_opps.extend(opps)

    all_opps.sort(key=lambda x: x.get("gross_edge", 0), reverse=True)
    logger.info(
        "Nested scan: %d events -> %d violations",
        len(events_markets), len(all_opps),
    )
    return all_opps


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
