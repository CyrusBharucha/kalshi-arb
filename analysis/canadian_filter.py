"""
analysis/canadian_filter.py
===========================
Classifier for Canadian-relevant Kalshi markets.

"Canadian market" for this project means any market relevant to a Canadian
researcher or bettor - not only Canadian politics, but also:
  - Canadian politics and elections (federal + provincial)
  - Bank of Canada monetary policy
  - Canadian economic indicators (GDP, CPI, unemployment, housing)
  - Canadian sports teams in US leagues
  - Canadian-specific weather / geography
  - USD/CAD exchange rate
  - Companies headquartered in Canada
  - Events that directly affect Canadians (US/Canada trade policy, border, tariffs)

This is a filter applied to Synthesis/Kalshi market metadata for prioritization -
it does NOT limit the arbitrage scanner (which scans all markets regardless).
"""
from __future__ import annotations

import re
from typing import Dict, List, Any, Optional

# -- Pattern definitions --------------------------------------------------------

_POLITICAL = [
    r"\bcanad[ia]",          # Canada, Canadian
    r"\bOttawa\b",
    r"\bBank of Canada\b",   r"\bBoC\b",
    r"\bCarney\b",           r"\bPoilievre\b",   r"\bSingh\b.*\bNDP\b",
    r"\bNDP\b",              r"\bLiberal.*Party\b", r"\bConservative.*Party\b",
    r"\bHouse of Commons\b", r"\bParliament.*Canada\b",
    r"\bProvince\b.*Canada|Canada.*\bProvince\b",
    r"\bAlberta\b",  r"\bOntario\b",   r"\bQuebec\b",
    r"\bBC\b.*election|\belection.*\bBC\b",
    r"\bBritish Columbia\b",
    r"\bManitoba\b",  r"\bSaskatchewan\b",
    r"\bNova Scotia\b", r"\bNew Brunswick\b",
    r"\bNewfoundland\b", r"\bPrince Edward Island\b",
    r"\bNorthwest Territories\b", r"\bYukon\b", r"\bNunavut\b",
    r"\bCanada.*election|\belection.*Canada\b",
    r"\bfederal.*Canada|Canada.*federal\b",
    r"\bMinister.*Canada|Canada.*Minister\b",
    r"\bPrime Minister.*Canada|Canada.*Prime Minister\b",
]

_ECONOMIC = [
    r"\bCAD\b",              # currency
    r"\bUSD.?CAD\b",  r"\bCAD.?USD\b",
    r"\bCanadian.*dollar\b|\bdollar.*Canadian\b",
    r"\bBank of Canada\b",   r"\bboc rate\b",
    r"\bCanada.*CPI|CPI.*Canada\b",
    r"\bCanada.*GDP|GDP.*Canada\b",
    r"\bCanada.*inflation|inflation.*Canada\b",
    r"\bCanada.*unemployment|unemployment.*Canada\b",
    r"\bCanada.*housing|housing.*Canada\b",
    r"\bTSX\b",              # Toronto Stock Exchange
    r"\bS&P.?TSX\b",
    r"\bCanada.*tariff|tariff.*Canada\b",
    r"\bCanada.*trade deal|trade deal.*Canada\b",
    r"\bUSMCA\b",            # US-Mexico-Canada trade agreement
    r"\bNAFTA\b",
]

_SPORTS = [
    # NHL
    r"\bToronto Maple Leafs\b", r"\bVancouver Canucks\b",
    r"\bCalgary Flames\b",      r"\bEdmonton Oilers\b",
    r"\bMontreal Canadiens\b",  r"\bOttawa Senators\b",
    r"\bWinnipeg Jets\b",
    # NBA
    r"\bToronto Raptors\b",
    # MLB
    r"\bToronto Blue Jays\b",
    # MLS
    r"\bToronto FC\b",   r"\bCF Montreal\b",
    r"\bVancouver Whitecaps\b",
    # CFL
    r"\bCFL\b",
    # PWHL (women's hockey)
    r"\bPWHL\b",
]

_COMPANIES = [
    r"\bShopify\b", r"\bRoyal Bank\b", r"\bRBC\b",
    r"\bTD Bank\b", r"\bBMO\b", r"\bScotiabank\b", r"\bCIBC\b",
    r"\bSuncor\b",  r"\bCNR\b", r"\bCanadian National\b",
    r"\bBrookfield\b", r"\bEnbridge\b",
    r"\bCanadian Natural\b", r"\bCenovus\b",
    r"\bAir Canada\b", r"\bWestJet\b",
    r"\bLoblaws\b",    r"\bCostco Canada\b",
]

_GEO = [
    r"\bToronto\b",    r"\bVancouver\b",   r"\bMontreal\b",
    r"\bCalgary\b",    r"\bEdmonton\b",    r"\bWinnipeg\b",
    r"\bHalifax\b",    r"\bOttawa\b",
    r"\bArctic.*Canada|Canada.*Arctic\b",
    r"\bGreat Lakes\b",
]

_US_CANADA_RELATIONS = [
    r"\bCanada.*US\b|\bUS.*Canada\b",
    r"\b51st state\b.*Canada|Canada.*\b51st state\b",
    r"\bannexation.*Canada|Canada.*annexation\b",
    r"\bCanadian border\b",
    r"\bCanada.*tariff|tariff.*Canada\b",
]

ALL_PATTERNS = (
    _POLITICAL + _ECONOMIC + _SPORTS + _COMPANIES + _GEO + _US_CANADA_RELATIONS
)

_CANADIAN_RE = re.compile(
    "|".join(ALL_PATTERNS),
    re.IGNORECASE,
)

# Category-specific regexes for labeling
_CATEGORY_RES: List[tuple[str, re.Pattern]] = [
    ("politics",  re.compile("|".join(_POLITICAL), re.IGNORECASE)),
    ("economics", re.compile("|".join(_ECONOMIC),  re.IGNORECASE)),
    ("sports",    re.compile("|".join(_SPORTS),    re.IGNORECASE)),
    ("companies", re.compile("|".join(_COMPANIES), re.IGNORECASE)),
    ("geography", re.compile("|".join(_GEO),       re.IGNORECASE)),
    ("us_canada_relations", re.compile("|".join(_US_CANADA_RELATIONS), re.IGNORECASE)),
]


def is_canadian(text: str) -> bool:
    """Return True if the market text appears to be Canadian-relevant."""
    return bool(_CANADIAN_RE.search(text or ""))


def classify_market(
    market_id: str,
    title: str = "",
    event_title: str = "",
    labels: Optional[List[str]] = None,
    description: str = "",
) -> Dict[str, Any]:
    """
    Full classification for a single market.

    Returns:
        {
            "is_canadian": bool,
            "categories":  List[str],   # e.g. ["politics", "economics"]
            "confidence":  float,       # 0.0 - 1.0
            "matched_on":  str,         # which field triggered
        }
    """
    text_parts = {
        "market_id":   market_id,
        "title":       title,
        "event_title": event_title,
        "labels":      " ".join(labels or []),
        "description": description,
    }

    categories_found: List[str] = []
    matched_fields:   List[str] = []

    for field_name, text in text_parts.items():
        if _CANADIAN_RE.search(text):
            matched_fields.append(field_name)
            for cat_name, cat_re in _CATEGORY_RES:
                if cat_re.search(text) and cat_name not in categories_found:
                    categories_found.append(cat_name)

    is_ca = bool(matched_fields)
    # Confidence: higher when matched on title/event_title
    confidence = 0.0
    if "title" in matched_fields or "event_title" in matched_fields:
        confidence = 0.95
    elif "market_id" in matched_fields:
        confidence = 0.80
    elif "labels" in matched_fields:
        confidence = 0.85
    elif "description" in matched_fields:
        confidence = 0.70

    return {
        "is_canadian": is_ca,
        "categories":  categories_found,
        "confidence":  confidence,
        "matched_on":  ", ".join(matched_fields) if matched_fields else "",
    }


def filter_markets(markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter a list of market dicts to only Canadian-relevant ones.
    Each dict should have keys: market_id, title, event_title, labels.
    """
    results = []
    for mkt in markets:
        result = classify_market(
            market_id   = mkt.get("market_id", ""),
            title       = mkt.get("title", ""),
            event_title = mkt.get("event_title", ""),
            labels      = mkt.get("labels", []),
            description = mkt.get("description", ""),
        )
        if result["is_canadian"]:
            results.append({**mkt, **result})
    return results


def classify_all_from_db() -> List[Dict[str, Any]]:
    """
    Run the Canadian classifier against all markets in the local DB.
    Returns list of Canadian-relevant markets with classification metadata.
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from database.repository import session_scope
    from sqlalchemy import text

    with session_scope() as s:
        rows = s.execute(text("""
            SELECT m.market_id, m.title, e.title AS event_title,
                   m.floor_strike, m.status
            FROM markets m
            JOIN events e ON e.event_ticker = m.event_ticker
            LIMIT 1000000
        """)).fetchall()

    results = []
    for r in rows:
        c = classify_market(
            market_id   = r[0] or "",
            title       = r[1] or "",
            event_title = r[2] or "",
        )
        if c["is_canadian"]:
            results.append({
                "market_id":   r[0],
                "title":       r[1],
                "event_title": r[2],
                **c,
            })

    return results


if __name__ == "__main__":
    # Quick self-test
    test_cases = [
        ("KXBOCRATE-26NOV01", "Bank of Canada rate decision November 2026", "BOC", []),
        ("KXNBA-27-TOR", "Pro Basketball Champion - Toronto Raptors", "Pro Basketball Champion", []),
        ("KXPRESNOMD-28-GN", "Democratic nominee in 2028 - Gavin Newsom", "Democratic nominee", []),
        ("KXLEADERSOUT-27JAN01-MCARCAN", "World leaders out before 2027 - Mark Carney", "Leaders", []),
        ("KXBTCY-27JAN", "BTC price at year end", "Crypto", []),
        ("KXUSUNEMPRATE-26NOV", "US unemployment November", "US economy", []),
    ]
    print(f"{'Market ID':<40} {'Canadian':<10} {'Categories':<25} {'Confidence'}")
    print("-" * 100)
    for mid, title, event, labels in test_cases:
        r = classify_market(mid, title, event, labels)
        print(f"{mid:<40} {str(r['is_canadian']):<10} {str(r['categories']):<25} {r['confidence']:.2f}")
