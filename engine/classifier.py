"""
markets/classifier.py
Classifies Kalshi markets: assigns Canadian relevance scores, market types,
and geographic regions. Runs automatically after every market ingestion.
"""

from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# -- Canadian relevance keywords ------------------------------------------------
# Scored 1-10; higher = more directly relevant to the Canadian cross-asset engine

CANADIAN_KEYWORDS: List[Tuple[List[str], int]] = [
    # Tier 1: Direct Canadian monetary policy
    (["bank of canada", "boc", "overnight rate target", "canadian interest rate",
      "corra", "policy rate canada"], 10),
    # Tier 2: Canadian inflation
    (["canada cpi", "canadian cpi", "canadian inflation", "canada inflation",
      "statscan", "statistics canada"], 9),
    # Tier 3: CAD / FX
    (["cad/usd", "usd/cad", "canadian dollar", "loonie", "cad fx"], 8),
    # Tier 4: Canadian economic indicators
    (["canada employment", "canadian employment", "canada jobs", "canada unemployment",
      "canada gdp", "canadian gdp", "canada housing"], 7),
    # Tier 5: Oil (strong Canadian macro relevance)
    (["wti", "crude oil", "oil price", "brent", "west texas"], 6),
    # Tier 6: General Canada mentions
    (["canada", "canadian", "toronto", "tsx", "s&p/tsx", "ottawa"], 5),
]

# Categories that are NOT relevant for cross-asset
NON_CANADIAN_CATEGORIES = {
    "sports", "entertainment", "celebrity", "gaming", "crypto",
    "weather", "social", "meme",
}


def score_canadian_relevance(market: Dict[str, Any]) -> int:
    """
    Score a market 0-10 for Canadian relevance.
    Uses title, subtitle, category, and event_ticker.
    """
    text = " ".join(filter(None, [
        (market.get("title") or "").lower(),
        (market.get("subtitle") or "").lower(),
        (market.get("category") or "").lower(),
        (market.get("event_ticker") or "").lower(),
        (market.get("series_ticker") or "").lower(),
    ]))

    # Quick reject: non-Canadian categories
    cat = (market.get("category") or "").lower()
    if any(ncat in cat for ncat in NON_CANADIAN_CATEGORIES):
        return 0

    best_score = 0
    for keywords, score in CANADIAN_KEYWORDS:
        if any(kw in text for kw in keywords):
            best_score = max(best_score, score)

    return best_score


def classify_market_type(market: Dict[str, Any]) -> Tuple[str, str]:
    """
    Returns (market_type, outcome_type).
    market_type: 'binary' | 'threshold' | 'range' | 'multivariate'
    outcome_type: 'yes_no' | 'threshold' | 'interval' | 'categorical'
    """
    title = (market.get("title") or "").lower()
    floor = market.get("floor_strike")
    cap   = market.get("cap_strike")

    # Multivariate
    if market.get("market_type") == "multivariate":
        return "multivariate", "categorical"

    # Range contract (has both floor and cap)
    if floor is not None and cap is not None:
        return "range", "interval"

    # Threshold (only floor or cap)
    if floor is not None or cap is not None:
        return "threshold", "threshold"

    # Binary
    return "binary", "yes_no"


def classify_geographic_region(market: Dict[str, Any]) -> str:
    """Assign a geographic region label."""
    text = " ".join(filter(None, [
        (market.get("title") or "").lower(),
        (market.get("category") or "").lower(),
        (market.get("event_ticker") or "").lower(),
    ]))
    if any(kw in text for kw in ["canada", "canadian", "boc", "tsx", "corra"]):
        return "Canada"
    if any(kw in text for kw in ["federal reserve", "fed rate", "fomc", "us jobs",
                                    "us cpi", "united states", "s&p 500", "nasdaq"]):
        return "US"
    if any(kw in text for kw in ["ecb", "european", "boe", "bank of england",
                                    "rba", "boj", "bank of japan"]):
        return "International"
    return "Global"


def classify_market(market: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full classification pass for a single market dict.
    Returns enriched dict with added fields.
    """
    canadian_score          = score_canadian_relevance(market)
    market_type, outcome    = classify_market_type(market)
    geo                     = classify_geographic_region(market)

    return {
        **market,
        "canadian_relevance": canadian_score,
        "market_type":        market_type,
        "outcome_type":       outcome,
        "geographic_region":  geo,
    }


def batch_classify(markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Classify a batch of market dicts."""
    return [classify_market(m) for m in markets]


def get_canadian_markets(markets: List[Dict[str, Any]], min_score: int = 5) -> List[Dict[str, Any]]:
    """Filter markets to only Canadian-relevant ones."""
    classified = batch_classify(markets)
    return [m for m in classified if m.get("canadian_relevance", 0) >= min_score]


def detect_event_structure(markets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Given all markets in a single event, infer its logical structure:
    - Are outcomes mutually exclusive?
    - Are they collectively exhaustive?
    - What is the strike ordering?
    """
    if not markets:
        return {}

    strikes = sorted([
        float(m["floor_strike"])
        for m in markets
        if m.get("floor_strike") is not None
    ])

    # Check if strikes are ordered (threshold family - must sum to 1 at boundaries)
    is_ordered_threshold = (
        len(strikes) > 1 and
        all(strikes[i] < strikes[i+1] for i in range(len(strikes)-1))
    )

    # For binary contracts: all YES outcomes are mutually exclusive unless otherwise stated
    all_binary = all(
        classify_market_type(m)[0] == "binary" for m in markets
    )

    return {
        "market_count":           len(markets),
        "has_threshold_structure": is_ordered_threshold,
        "strikes":                strikes,
        "all_binary":             all_binary,
        "likely_mutually_exclusive": True,   # default for Kalshi event families
        "likely_collectively_exhaustive": True,
    }
