"""
execution/fees.py
Kalshi fee model - exactly as documented.

Taker fee formula:
    fee = ceil(0.07 * P * (1 - P) * 100) / 100   per contract
    cap at $0.035 per contract

Maker fee: approximately 1/4 of taker (effective $0.00 for most small trades
after the rounding step, but non-zero for institutional sizes).

Settlement: no fee. Contracts settle at $1.00 or $0.00.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

# Fee constants
FEE_ALPHA   = 0.07      # coefficient in formula
FEE_CAP     = 0.035     # max taker fee per contract ($0.035)
MAKER_RATIO = 0.25      # maker fee ≈ 25% of taker


def taker_fee_per_contract(price: float) -> float:
    """
    Compute taker fee for a single contract at the given YES price.

    Canonical formula: min(0.035, math.ceil(0.07 * p * (1-p) * 100) / 100)

    Args:
        price: YES contract price in dollars (0.01 to 0.99)

    Returns:
        Fee in dollars per contract.

    >>> round(taker_fee_per_contract(0.50), 4)
    0.02
    >>> round(taker_fee_per_contract(0.20), 4)
    0.02
    >>> round(taker_fee_per_contract(0.10), 4)
    0.01
    """
    if price is None:
        raise TypeError("price must not be None")
    if not 0 < price < 1:
        raise ValueError(f"Price must be in (0, 1). Got: {price}")
    raw = FEE_ALPHA * price * (1.0 - price)
    # Canonical formula: ceil to the nearest cent ($0.01).
    fee = math.ceil(raw * 100) / 100
    return min(fee, FEE_CAP)


def maker_fee_per_contract(price: float) -> float:
    """
    Maker fee per contract (approximate - post April 2025).
    For most retail sizes, this rounds to $0.00.
    """
    if price is None:
        raise TypeError("price must not be None")
    raw = FEE_ALPHA * MAKER_RATIO * price * (1.0 - price)
    return math.ceil(raw * 100) / 100


def total_round_trip_fee(
    price_leg1: float,
    price_leg2: Optional[float] = None,
    contracts: float = 1.0,
    taker_both: bool = True,
) -> float:
    """
    Total fees for a round-trip arbitrage trade.

    For a complement trade: buy YES at ask (leg1) and buy NO at ask (leg2).
    Both are taker trades -> double taker fee applies.

    Args:
        price_leg1:   Price of first leg (YES ask).
        price_leg2:   Price of second leg (NO ask). If None, assumes binary complement.
        contracts:    Number of contracts.
        taker_both:   If True, both legs are taker. If False, leg2 is maker.

    Returns:
        Total fee in dollars.
    """
    fee1 = taker_fee_per_contract(price_leg1) * contracts
    if price_leg2 is None:
        # NO price = 1 - YES_bid; for complement, use the other side
        price_leg2 = 1.0 - price_leg1
    fee2 = (
        taker_fee_per_contract(price_leg2) if taker_both
        else maker_fee_per_contract(price_leg2)
    ) * contracts
    return fee1 + fee2


def fee_schedule_table() -> List[Dict[str, float]]:
    """Return a table of taker fees across price points - for documentation."""
    prices = [0.01, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50,
              0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.99]
    return [
        {
            "price":      p,
            "taker_fee":  taker_fee_per_contract(p),
            "maker_fee":  maker_fee_per_contract(p),
            "fee_pct_of_price": taker_fee_per_contract(p) / p * 100,
        }
        for p in prices
    ]


def compute_multi_leg_fees(legs: List[Dict]) -> float:
    """
    Compute total fees for an n-leg arbitrage strategy.

    Each leg dict:
      {"price": float, "contracts": float, "is_taker": bool}

    Returns total fee in dollars.
    """
    total = 0.0
    for leg in legs:
        price     = float(leg["price"])
        contracts = float(leg.get("contracts", 1.0))
        is_taker  = bool(leg.get("is_taker", True))
        fee_fn    = taker_fee_per_contract if is_taker else maker_fee_per_contract
        total    += fee_fn(price) * contracts
    return total
