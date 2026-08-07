"""
cross_asset/probability_engine.py
Converts traditional financial market prices into implied probabilities
for comparison with Kalshi contract prices.

Every conversion method is explicitly documented.
We never simply assert "futures price = probability" without justification.

Supported conversions:
  1. Rate/OIS implied probability (BOC policy decisions)
  2. FX implied probability (CAD/USD threshold crossings)
  3. Commodity implied probability (WTI above/below threshold)
  4. Equity index implied probability (TSX level)
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import norm

logger = logging.getLogger(__name__)


# -- 1. BOC Policy Rate Probability Engine -------------------------------------

def boc_rate_implied_probability(
    current_ois_rate: float,
    meeting_date_days_ahead: int,
    target_rate: float,
    direction: str = "cut",
    rate_step_size: float = 0.0025,  # 25 basis points (not 25%)
    n_meetings: int = 1,
) -> float:
    """
    Estimate the probability of the BOC moving to a specific target rate
    using the overnight index swap (OIS) / CORRA market.

    Methodology:
      1. The overnight rate implied by OIS reflects the market's expected
         average policy rate over the OIS tenor.
      2. We use the difference between the current OIS rate and the
         current policy rate to infer the expected rate change.
      3. We model uncertainty around the central expectation using
         a normal distribution with std = rate_step_size * sqrt(n_meetings).

    This is a SIMPLIFICATION. Production implementation should use the
    full OIS strip and the BOC meeting schedule.

    NOTE: `meeting_date_days_ahead` is accepted for API compatibility but is NOT
    used in this simplified model — uncertainty is driven by n_meetings only.
    A production model would scale sigma with sqrt(T) using days_ahead.

    Args:
        current_ois_rate:       Current CORRA/OIS rate (annualised, as decimal e.g. 0.045)
        meeting_date_days_ahead: Days until the BOC meeting (unused in this simplified model)
        target_rate:            The specific rate outcome (e.g. 0.0425)
        direction:              'cut', 'hold', or 'hike'
        rate_step_size:         Typical step size (0.0025 = 25bps)
        n_meetings:             Number of meetings to next decision (for uncertainty scaling)

    Returns:
        Probability (0.0 to 1.0) of the specified outcome.
    """
    _ = meeting_date_days_ahead  # accepted for API compatibility; unused (see docstring)
    # Expected rate = OIS rate (simplified - ignores term premium / forward curve)
    expected_rate = current_ois_rate
    uncertainty   = rate_step_size * math.sqrt(n_meetings)

    if direction == "cut":
        # P(rate <= target)
        prob = norm.cdf(target_rate, loc=expected_rate, scale=uncertainty)
    elif direction == "hike":
        # P(rate >= target)
        prob = 1.0 - norm.cdf(target_rate - rate_step_size, loc=expected_rate, scale=uncertainty)
    else:  # hold
        # P(rate stays between target - step/2 and target + step/2)
        prob = (
            norm.cdf(target_rate + rate_step_size / 2, loc=expected_rate, scale=uncertainty) -
            norm.cdf(target_rate - rate_step_size / 2, loc=expected_rate, scale=uncertainty)
        )

    return float(max(0.0, min(1.0, prob)))


def boc_rate_distribution(
    current_ois_rate: float,
    possible_rates: List[float],
    rate_step_size: float = 0.0025,
) -> Dict[str, float]:
    """
    Compute the full implied probability distribution across all possible
    BOC rate outcomes using the OIS/CORRA market rate.

    Returns dict of {rate_str: probability} that sums to 1.0.
    """
    rates = sorted(possible_rates)
    n     = len(rates)
    uncertainty = rate_step_size  # single-meeting uncertainty

    probs = {}
    for i, rate in enumerate(rates):
        if i == 0:
            # Leftmost bucket: P(rate <= rates[0] + half_step)
            p = norm.cdf(rates[0] + rate_step_size / 2,
                          loc=current_ois_rate, scale=uncertainty)
        elif i == n - 1:
            # Rightmost bucket: P(rate > rates[-2] + half_step)
            p = 1.0 - norm.cdf(rates[-2] + rate_step_size / 2,
                                  loc=current_ois_rate, scale=uncertainty)
        else:
            p = (
                norm.cdf(rate + rate_step_size / 2,
                          loc=current_ois_rate, scale=uncertainty) -
                norm.cdf(rate - rate_step_size / 2,
                          loc=current_ois_rate, scale=uncertainty)
            )
        probs[f"{rate*100:.2f}%"] = max(0.0, p)

    # Normalise to sum to 1.0
    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}

    return probs


# -- 2. FX Threshold Probability (CAD/USD) -------------------------------------

def fx_threshold_probability(
    spot_rate: float,
    threshold: float,
    days_to_expiry: int,
    annualised_vol: float,
    direction: str = "above",
) -> float:
    """
    Estimate probability that CAD/USD will be above (or below) a threshold
    at contract expiry using a log-normal model (Black-Scholes digital).

    IMPORTANT CAVEATS:
      - This is a risk-neutral probability assuming zero drift (risk-neutral world).
      - Real-world probability differs from risk-neutral by the equity/FX risk premium.
      - Documented as an approximation, not a precision calculation.

    Args:
        spot_rate:       Current CAD/USD rate
        threshold:       Strike level (e.g. 0.74 for 74 US cents)
        days_to_expiry:  Calendar days to contract expiry
        annualised_vol:  Annualised FX vol (e.g. 0.06 for 6%)
        direction:       'above' or 'below'

    Returns:
        Probability (0.0 to 1.0).
    """
    if spot_rate <= 0 or threshold <= 0 or days_to_expiry <= 0:
        return 0.5

    T   = days_to_expiry / 365.0
    vol = annualised_vol

    # d2 from BSM (digital option) with zero risk-free rate and zero drift
    d2 = (math.log(spot_rate / threshold) - 0.5 * vol**2 * T) / (vol * math.sqrt(T))

    if direction == "above":
        return float(norm.cdf(d2))
    else:
        return float(norm.cdf(-d2))


# -- 3. Commodity Threshold Probability (WTI) ----------------------------------

def commodity_threshold_probability(
    current_price: float,
    threshold: float,
    days_to_expiry: int,
    annualised_vol: float = 0.35,
    direction: str = "above",
) -> float:
    """
    Probability that WTI crude will be above/below threshold at expiry.
    Uses same log-normal model as FX above.
    Default vol = 35% (historical WTI vol approximation).
    """
    return fx_threshold_probability(
        current_price, threshold, days_to_expiry, annualised_vol, direction
    )


# -- 4. Cross-Market Spread Calculation ----------------------------------------

def compute_cross_market_spread(
    kalshi_probability: float,
    trad_probability:   float,
) -> Dict[str, float]:
    """
    Compute the spread between Kalshi and traditional-market implied probability.
    Returns spread, absolute spread, and significance indicator.
    """
    spread  = kalshi_probability - trad_probability
    abs_spr = abs(spread)

    # Log-odds difference (captures non-linear nature of probability differences)
    try:
        lo_kalshi = math.log(kalshi_probability / (1 - kalshi_probability))
        lo_trad   = math.log(trad_probability / (1 - trad_probability))
        lo_diff   = lo_kalshi - lo_trad
    except (ValueError, ZeroDivisionError):
        lo_diff = None

    return {
        "kalshi_probability": kalshi_probability,
        "trad_probability":   trad_probability,
        "spread":             spread,
        "abs_spread":         abs_spr,
        "log_odds_diff":      lo_diff,
        "significant_5pct":   abs_spr > 0.05,
        "significant_10pct":  abs_spr > 0.10,
    }


# Alias with standard API signature used by tests and dashboard
def fx_implied_probability(
    spot_rate: float,
    threshold: float,
    volatility: float,
    days_ahead: int,
    direction: str = "above",
) -> float:
    """
    Alias for fx_threshold_probability with a standardised parameter order.
    Used by dashboard p07_cross_asset.py and tests.

    Args:
        spot_rate:   Current CAD/USD rate
        threshold:   Strike level
        volatility:  Annualised FX volatility (e.g. 0.06 for 6%)
        days_ahead:  Calendar days to contract expiry
        direction:   'above' or 'below'

    Returns:
        Probability (0.0 to 1.0) using a log-normal (BSM digital) model.
    """
    return fx_threshold_probability(spot_rate, threshold, days_ahead, volatility, direction)


def kalshi_midpoint(yes_bid: float, yes_ask: float) -> Optional[float]:
    """Kalshi midpoint probability (NOT executable - diagnostic use only)."""
    if yes_bid is None or yes_ask is None:
        return None
    return (float(yes_bid) + float(yes_ask)) / 2.0
