"""
markets/relationship_detector.py
Detects logical relationships between Kalshi markets within events.

Relationship types:
  complement          - YES + NO of same binary = guaranteed $1.00 payout
  mutually_exclusive  - at most one outcome can resolve YES
  collectively_exhaustive - at least one must resolve YES (sum >= 1)
  superset            - P(A) >= P(B) when A covers a larger outcome set than B
  threshold_order     - monotone probability along a strike ladder

Direction convention
--------------------
Kalshi has two threshold market families:

  "at-most X" / "below X" / "≤ X":
      Higher strike = more outcomes qualify = MORE likely.
      P(rate ≤ 4.50%) ≥ P(rate ≤ 4.25%)
      direction_flag = "leq"

  "exceed X" / "above X" / "reach X" / "> X":
      Higher strike = harder to achieve = LESS likely.
      P(streams ≥ 3.5B) ≤ P(streams ≥ 2.8B)
      direction_flag = "geq"

Every threshold relationship stores a direction_flag so scanners know the
expected probability ordering and can detect genuine violations without
accidentally flagging correct pricing as an error.

False-positive prevention
-------------------------
Three known false-positive sources (fixed here):

  1. Nested thresholds as ME - streaming milestones, release-date tiers.
     Fix: skip ME detection for any pair where BOTH markets have ordered
     floor_strike values (those are superset/threshold markets, not ME).

  2. Direction errors - "exceed X" markets have the OPPOSITE ordering to
     "at-most X" markets.  A scanner assuming "high strike ↔ high prob"
     generates spurious violations for "exceed" markets.
     Fix: detect direction from ticker/title; store in direction_flag.

  3. Partial-set CE - two teams out of 30 flagged as collectively exhaustive.
     Fix: CE requires the markets list to plausibly cover the COMPLETE
     outcome space; default to requiring exactly 2 markets with no
     floor_strike, or all markets summing near 1.0 in the relationship
     context.
"""

from __future__ import annotations

import itertools
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# -- Type definition ------------------------------------------------------------
Relationship = Dict[str, Any]


# -- Market direction detection -------------------------------------------------

# Patterns that indicate "≤ X / below X / at-most X" convention
# Higher strike -> more likely
_LEQ_PATTERNS = re.compile(
    r'\b(below|under|at most|no more than|at or below|<=|≤)\b'
    r'|[-_](B|bel|low)\d',          # ticker suffix: -B132500 = "Below 132500"
    re.IGNORECASE,
)

# Patterns that indicate "> X / exceed / reach / above X" convention
# Higher strike -> LESS likely
_GEQ_PATTERNS = re.compile(
    r'\b(above|over|exceeds?|at least|reaches?|no less than|>=|≥|at or above)\b'
    r'|[-_](T|top|high|over|reach)\d',
    re.IGNORECASE,
)


def _detect_threshold_direction(market: Dict[str, Any]) -> str:
    """
    Return 'leq' (higher strike = more likely) or 'geq' (higher strike = less
    likely) by examining the market ticker and title.

    Defaults to 'leq' when ambiguous - this is the safe default for rate
    markets which are the most common threshold type.
    """
    ticker = str(market.get("ticker") or market.get("market_id") or "")
    title  = str(market.get("title") or "")
    text   = ticker + " " + title

    leq_match = _LEQ_PATTERNS.search(text)
    geq_match = _GEQ_PATTERNS.search(text)

    if geq_match and not leq_match:
        return "geq"
    # Specific ticker suffixes that definitively signal exceed markets
    # e.g. KXARTISTSTREAMSY-*-2.8B, KXAMZNA-28JANHEAD-1600000
    # Heuristic: pure numeric suffix with no B/T prefix on the suffix segment
    # is ambiguous - default to leq
    return "leq"


def _markets_have_ordered_strikes(
    m1: Dict[str, Any],
    m2: Dict[str, Any],
) -> bool:
    """True if both markets have distinct, valid numeric floor_strikes.

    Returns True only when BOTH legs carry a numeric floor_strike AND those
    strikes differ — i.e. they form a proper ordered threshold pair.
    Returns False when either strike is missing, non-numeric, or equal.
    """
    s1 = m1.get("floor_strike")
    s2 = m2.get("floor_strike")
    if s1 is None or s2 is None:
        return False
    try:
        f1, f2 = float(s1), float(s2)
    except (TypeError, ValueError):
        return False
    return f1 != f2


# Cumulative deadline markets: "Will X happen by <date>?". Every such market in
# a series is nested inside the next one out -- if it happens by November it has
# also happened by December -- so their probabilities form a monotone ladder and
# may legitimately sum well above 1.
_DEADLINE_PATTERN = re.compile(
    r"\b(by|before|on or before|prior to)\b\s+"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{1,2}[/-]|\d{4})",
    re.IGNORECASE,
)


def _is_deadline_market(market: Dict[str, Any]) -> bool:
    """True if the market asks whether something happens *by* a cutoff date."""
    title = str(market.get("title") or "")
    return bool(_DEADLINE_PATTERN.search(title))


def _markets_have_ordered_dates(
    m1: Dict[str, Any],
    m2: Dict[str, Any],
) -> bool:
    """True if both markets are cumulative deadline markets with distinct cutoffs.

    These are the date-ladder analogue of a strike ladder. They carry no
    ``floor_strike``, so ``_markets_have_ordered_strikes`` cannot see them, and
    before this guard existed they were all classified mutually exclusive.

    That mislabelling was not harmless: "GTA6 released by Dec 31 2026" at $0.92
    and "released by Jul 1 2027" at $0.97 sum to $1.89, which the arbitrage
    scanners then reported as a huge ME violation. The prices were correct and
    the relationship label was wrong.
    """
    if not (_is_deadline_market(m1) and _is_deadline_market(m2)):
        return False
    c1 = m1.get("close_time")
    c2 = m2.get("close_time")
    if c1 is None or c2 is None:
        return False
    return c1 != c2


# -- Core relationship detectors ------------------------------------------------

def detect_complement_relationship(market: Dict[str, Any]) -> Optional[Relationship]:
    """
    Every binary market: YES_price + NO_price = $1.00 by Kalshi construction.
    Self-referential - market_id_1 == market_id_2.
    """
    mid = market.get("market_id") or market.get("ticker")
    if not mid:
        return None
    # Every Kalshi binary has a YES+NO complement — include "threshold" markets
    # (which are classified as threshold due to floor_strike but still binary).
    if market.get("market_type", "binary") not in ("binary", "yes_no", "threshold"):
        return None
    return {
        "market_id_1":        mid,
        "market_id_2":        mid,
        "relationship_type":  "complement",
        "logical_constraint": "YES_price + NO_price = $1.00",
        "implied_inequality": "P(YES) + P(NO) = 1",
        "confidence":         1.0,
    }


def detect_mutually_exclusive_set(
    markets: List[Dict[str, Any]],
    event_ticker: str,
) -> List[Relationship]:
    """
    Pairwise ME detection: at most one outcome can resolve YES.

    FALSE-POSITIVE GUARD: If BOTH markets in a pair have distinct floor_strike
    values, they belong to a threshold ladder (superset/threshold relations are
    detected separately).  Such pairs are NOT ME in the classical sense - one
    can logically imply the other - so they are skipped here.

    A second guard covers cumulative deadline ladders ("Will X happen by
    <date>?"), which carry no floor_strike but are just as nested: happening by
    November implies happening by December.

    Example skipped: "artist reaches 2.8B streams" + "reaches 3.5B streams"
    Example skipped: "GTA6 released by Nov 30 2026" + "released by Jul 1 2027"
    Example kept:    "England wins" + "Ghana wins" (both floor_strike=None)
    """
    rels = []
    for m1, m2 in itertools.combinations(markets, 2):
        # GUARD: skip pairs that form a ladder rather than a partition --
        # either a strike ladder (distinct floor_strike) or a cumulative
        # deadline ladder ("by <date>" with distinct close_time).
        if _markets_have_ordered_strikes(m1, m2):
            continue
        if _markets_have_ordered_dates(m1, m2):
            continue

        id1 = m1.get("market_id") or m1.get("ticker")
        id2 = m2.get("market_id") or m2.get("ticker")
        rels.append({
            "market_id_1":        id1,
            "market_id_2":        id2,
            "relationship_type":  "mutually_exclusive",
            "logical_constraint": (
                f"At most one of {{{id1}, {id2}}} can resolve YES"
            ),
            "implied_inequality": f"P({id1}) + P({id2}) <= 1",
            "confidence":         0.9,
        })
    return rels


def detect_collectively_exhaustive_set(
    markets: List[Dict[str, Any]],
    event_ticker: str,
) -> Optional[Relationship]:
    """
    CE detection: at least one market in the set MUST resolve YES.

    FALSE-POSITIVE GUARD: CE is only valid when the market set plausibly covers
    the COMPLETE outcome space.  We enforce two requirements:

      1. Exactly 2 markets in the set (binary-choice events are the clearest
         CE case: e.g., Democrat wins OR Republican wins a two-candidate race).

      2. Neither market has a floor_strike value.  Markets with floor_strike
         are threshold-ladder markets and generally do NOT form CE pairs by
         themselves.

    Events with 3+ markets may still be CE but require explicit verification
    (e.g., all party outcomes in a known two-party race).  Those are handled
    by the relationship_runner for events where ALL markets sum to exactly 1.
    """
    # Filter out markets with floor_strike (threshold markets)
    discrete_markets = [m for m in markets if m.get("floor_strike") is None]

    # Only classify CE for exactly 2 discrete-outcome markets
    if len(discrete_markets) != 2:
        return None

    ids = [m.get("market_id") or m.get("ticker") for m in discrete_markets]
    return {
        "market_id_1":        ids[0],
        "market_id_2":        ids[1],
        "relationship_type":  "collectively_exhaustive",
        "logical_constraint": (
            f"Exactly one of {{{ids[0]}, {ids[1]}}} must resolve YES"
        ),
        "implied_inequality": f"P({ids[0]}) + P({ids[1]}) = 1",
        "confidence":         0.85,
    }


def detect_threshold_order_relationships(
    markets: List[Dict[str, Any]],
) -> List[Relationship]:
    """
    Monotone probability along a strike ladder.

    Direction is detected per market family:

      "leq" (at-most X / below X):
          Higher strike -> MORE likely.
          market_id_1 = hi_id, market_id_2 = lo_id.
          Constraint: P(m1) >= P(m2).

      "geq" (exceed X / above X / reach X):
          Higher strike -> LESS likely.
          market_id_1 = lo_id (easier threshold = more likely),
          market_id_2 = hi_id.
          Constraint: P(m1) >= P(m2).

    In both cases market_id_1 is always the MORE LIKELY market.
    The scanner can therefore use a single rule: P(m1) >= P(m2).
    """
    # Filter to markets with a floor_strike that is both non-None AND numeric.
    threshold_markets = []
    for m in markets:
        raw = m.get("floor_strike")
        if raw is None:
            continue
        try:
            float(raw)
        except (TypeError, ValueError):
            logger.debug(
                "Skipping market %s: non-numeric floor_strike %r",
                m.get("market_id") or m.get("ticker"), raw,
            )
            continue
        threshold_markets.append(m)

    if len(threshold_markets) < 2:
        return []

    # Detect direction from the first market (whole event uses same convention)
    direction = _detect_threshold_direction(threshold_markets[0])

    sorted_markets = sorted(
        threshold_markets,
        key=lambda m: float(m["floor_strike"]),
    )

    rels = []
    for i in range(len(sorted_markets) - 1):
        lo_mkt = sorted_markets[i]
        hi_mkt = sorted_markets[i + 1]
        lo_id  = lo_mkt.get("market_id") or lo_mkt.get("ticker")
        hi_id  = hi_mkt.get("market_id") or hi_mkt.get("ticker")
        lo_s   = float(lo_mkt["floor_strike"])
        hi_s   = float(hi_mkt["floor_strike"])

        if direction == "leq":
            # Higher strike = more likely -> m1=hi, m2=lo, P(m1)>=P(m2)
            more_likely_id   = hi_id
            less_likely_id   = lo_id
            constraint_str   = (
                f"P(X ≤ {hi_s}) >= P(X ≤ {lo_s}) [monotone probability, leq]"
            )
        else:
            # "geq": higher strike = HARDER = LESS likely -> m1=lo (easier), m2=hi
            more_likely_id   = lo_id
            less_likely_id   = hi_id
            constraint_str   = (
                f"P(X >= {lo_s}) >= P(X >= {hi_s}) [monotone probability, geq]"
            )

        rels.append({
            "market_id_1":        more_likely_id,
            "market_id_2":        less_likely_id,
            "relationship_type":  "threshold_order",
            "logical_constraint": constraint_str,
            "implied_inequality": f"P({more_likely_id}) >= P({less_likely_id})",
            "confidence":         1.0,
            "direction_flag":     direction,   # "leq" or "geq"
        })
    return rels


def detect_subset_relationships(
    market_a: Dict[str, Any],
    market_b: Dict[str, Any],
) -> Optional[Relationship]:
    """
    Detect superset relationship for non-adjacent threshold pairs.
    market_a is superset (more likely), market_b is subset.
    Requires floor_strike values; uses the same direction detection.
    """
    fa = market_a.get("floor_strike")
    fb = market_b.get("floor_strike")
    if fa is None or fb is None:
        return None

    try:
        fa_f = float(fa)
        fb_f = float(fb)
    except (TypeError, ValueError):
        return None

    if fa_f == fb_f:
        return None

    direction = _detect_threshold_direction(market_a)
    mid_a = market_a.get("market_id") or market_a.get("ticker")
    mid_b = market_b.get("market_id") or market_b.get("ticker")

    if direction == "leq":
        # Higher strike = more likely; A is superset only if fa_f > fb_f
        if fa_f <= fb_f:
            return None
        return {
            "market_id_1":        mid_a,   # superset = more likely
            "market_id_2":        mid_b,
            "relationship_type":  "superset",
            "logical_constraint": (
                f"[X ≤ {fb_f}] ⊆ [X ≤ {fa_f}] -> P({mid_a}) >= P({mid_b})"
            ),
            "implied_inequality": f"P({mid_a}) >= P({mid_b})",
            "confidence":         1.0,
            "direction_flag":     "leq",
        }
    else:
        # "geq": lower strike = more likely; A is superset only if fa_f < fb_f
        if fa_f >= fb_f:
            return None
        return {
            "market_id_1":        mid_a,   # lower strike = more likely = superset
            "market_id_2":        mid_b,
            "relationship_type":  "superset",
            "logical_constraint": (
                f"[X >= {fa_f}] ⊇ [X >= {fb_f}] -> P({mid_a}) >= P({mid_b})"
            ),
            "implied_inequality": f"P({mid_a}) >= P({mid_b})",
            "confidence":         1.0,
            "direction_flag":     "geq",
        }


# -- Event-level discovery ------------------------------------------------------

def detect_all_relationships_for_event(
    markets: List[Dict[str, Any]],
    event_ticker: str,
) -> List[Relationship]:
    """Run all detectors for all markets in an event."""
    all_rels: List[Relationship] = []

    for m in markets:
        rel = detect_complement_relationship(m)
        if rel:
            all_rels.append(rel)

    threshold_rels = detect_threshold_order_relationships(markets)
    all_rels.extend(threshold_rels)

    if len(markets) >= 2:
        me_rels = detect_mutually_exclusive_set(markets, event_ticker)
        all_rels.extend(me_rels)

    ce_rel = detect_collectively_exhaustive_set(markets, event_ticker)
    if ce_rel:
        all_rels.append(ce_rel)

    for m_a, m_b in itertools.combinations(markets, 2):
        for ma, mb in [(m_a, m_b), (m_b, m_a)]:
            rel = detect_subset_relationships(ma, mb)
            if rel:
                all_rels.append(rel)

    return _deduplicate(all_rels)


# -- Violation checker ----------------------------------------------------------

def check_logical_price_violations(
    relationships: List[Relationship],
    prices: Dict[str, Dict[str, float]],
) -> List[Dict[str, Any]]:
    """
    Detect price violations against logical constraints.

    For threshold_order / superset:
        market_id_1 is always the MORE LIKELY market (after direction correction).
        Violation: P(m1) < P(m2).
        We use midpoints; bids used as conservative check for ME.

    For mutually_exclusive:
        Violation: P(m1) + P(m2) > 1.0 (even at bid level).

    None prices skip the check rather than raising.
    """
    violations = []
    for rel in relationships:
        id1   = rel["market_id_1"]
        id2   = rel["market_id_2"]
        p1    = prices.get(id1) or {}
        p2    = prices.get(id2) or {}
        rtype = rel["relationship_type"]

        if rtype in ("threshold_order", "superset"):
            # m1 is always the more-likely market; violation = P(m1) < P(m2)
            mid1 = _midpoint(p1)
            mid2 = _midpoint(p2)
            if mid1 is not None and mid2 is not None and mid1 < mid2:
                violations.append({
                    "type":       rtype,
                    "market_1":   id1,
                    "market_2":   id2,
                    "mid_1":      mid1,
                    "mid_2":      mid2,
                    "violation":  f"P({id1})={mid1:.4f} < P({id2})={mid2:.4f} (m1 should be more likely)",
                    "magnitude":  mid2 - mid1,
                    "relationship": rel,
                })

        elif rtype == "mutually_exclusive":
            bid1 = p1.get("yes_bid")
            bid2 = p2.get("yes_bid")
            if bid1 is None or bid2 is None:
                continue
            prob_sum = float(bid1) + float(bid2)
            if prob_sum > 1.0:
                violations.append({
                    "type":      rtype,
                    "market_1":  id1,
                    "market_2":  id2,
                    "bid_1":     bid1,
                    "bid_2":     bid2,
                    "violation": f"P({id1})+P({id2})={prob_sum:.4f} > 1.0",
                    "magnitude": prob_sum - 1.0,
                    "relationship": rel,
                })

        elif rtype == "collectively_exhaustive":
            # CE violation: sum < 1 -> buy both YES for less than $1
            ask1 = p1.get("yes_ask")
            ask2 = p2.get("yes_ask")
            if ask1 is None or ask2 is None:
                continue
            cost = float(ask1) + float(ask2)
            if cost < 1.0:
                violations.append({
                    "type":      rtype,
                    "market_1":  id1,
                    "market_2":  id2,
                    "ask_1":     ask1,
                    "ask_2":     ask2,
                    "violation": f"Buy cost {cost:.4f} < 1.0 (guaranteed payout)",
                    "magnitude": 1.0 - cost,
                    "relationship": rel,
                })

    return violations


# -- Helpers --------------------------------------------------------------------

def _midpoint(prices: Dict[str, float]) -> Optional[float]:
    bid = prices.get("yes_bid")
    ask = prices.get("yes_ask")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def _deduplicate(rels: List[Relationship]) -> List[Relationship]:
    seen: set = set()
    out:  List[Relationship] = []
    for r in rels:
        key = (r["market_id_1"], r["market_id_2"], r["relationship_type"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out
