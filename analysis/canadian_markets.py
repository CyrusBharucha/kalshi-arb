"""
analysis/canadian_markets.py
Phase 8: Identify and classify Canadian-relevant Kalshi markets.

Scans the full market universe for BOC, Canadian CPI, CAD/USD, WTI, TSX
markets and cross-references against contract_relationships to find
cross-asset arbitrage candidates.

Usage:
    python analysis/canadian_markets.py
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Any, List

import pandas as pd
from sqlalchemy import text

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.repository import session_scope

logger = logging.getLogger(__name__)


# -- Keyword patterns for Canadian relevance ------------------------------------

CANADIAN_PATTERNS = {
    "boc_rate": re.compile(
        r'\b(boc|bank of canada|banque du canada|overnight rate|corra|'
        r'rate cut|rate hike|rate hold|kxboc|fedhike)\b',
        re.IGNORECASE,
    ),
    "canadian_cpi": re.compile(
        r'\b(canada cpi|canadian cpi|ca cpi|kxcacpi|inflation canada|'
        r'statscan|statistics canada)\b',
        re.IGNORECASE,
    ),
    "cadusd": re.compile(
        r'\b(cadusd|usdcad|cad/usd|usd/cad|canadian dollar|loonie|kxcadusd)\b',
        re.IGNORECASE,
    ),
    "wti_oil": re.compile(
        r'\b(wti|west texas|crude oil|oil price|kxwti|barrel|nymex)\b',
        re.IGNORECASE,
    ),
    "tsx": re.compile(
        r'\b(tsx|s&p/tsx|toronto stock|tsx composite|kxtsx|canadian equit)\b',
        re.IGNORECASE,
    ),
    "fed_rate": re.compile(
        r'\b(fed rate|federal funds|fedhike|fomc|kxffr|kxfed)\b',
        re.IGNORECASE,
    ),
    "oil_general": re.compile(
        r'\b(oil|brent|opec|energy|kxenergy)\b',
        re.IGNORECASE,
    ),
}

# Wealthsimple Predict availability - manually verified as of August 2026.
# WS Predict is a subset of Kalshi; exact mapping varies. Mark conservatively.
WEALTHSIMPLE_CONFIRMED = {
    # Add actual tickers here once WS availability is verified
    # e.g. "FEDHIKE-26DEC31": True
}


def classify_market(ticker: str, title: str, category: str = "") -> List[str]:
    """Return list of Canadian category tags for a market."""
    text_to_search = f"{ticker} {title} {category}".lower()
    tags = []
    for tag, pattern in CANADIAN_PATTERNS.items():
        if pattern.search(text_to_search):
            tags.append(tag)
    return tags


def load_canadian_markets() -> pd.DataFrame:
    """
    Scan all active markets and flag those with Canadian relevance.
    Returns a DataFrame with classification columns.
    """
    logger.info("Loading active markets for Canadian classification...")
    with session_scope() as s:
        df = pd.read_sql(text("""
            SELECT m.market_id, m.ticker, m.title, m.category,
                   m.status, m.event_ticker,
                   m.floor_strike, m.cap_strike,
                   e.geographic_region,
                   COUNT(t.trade_id) AS trade_count
            FROM markets m
            LEFT JOIN events e ON e.event_ticker = m.event_ticker
            LEFT JOIN trades t ON t.market_id = m.market_id
            WHERE m.status = 'active'
            GROUP BY m.market_id, m.ticker, m.title, m.category,
                     m.status, m.event_ticker, m.floor_strike, m.cap_strike,
                     e.geographic_region
        """), s.bind)

    logger.info("Loaded %d active markets", len(df))

    # Apply classification
    df["canadian_tags"] = df.apply(
        lambda r: classify_market(
            r.get("ticker", ""),
            r.get("title", ""),
            r.get("category", "") or ""
        ),
        axis=1,
    )
    df["canadian_relevant"] = df["canadian_tags"].apply(lambda t: len(t) > 0)
    df["primary_category"]  = df["canadian_tags"].apply(
        lambda t: t[0] if t else None
    )

    # Cross-reference with contract_relationships
    with session_scope() as s:
        rel_tickers = set(pd.read_sql(text("""
            SELECT DISTINCT market_id_1 AS mid FROM contract_relationships
            WHERE relationship_type != 'complement'
            UNION
            SELECT DISTINCT market_id_2 FROM contract_relationships
            WHERE relationship_type != 'complement'
        """), s.bind)["mid"].tolist())

    df["in_relationship_graph"] = df["market_id"].isin(rel_tickers)
    df["cross_asset_candidate"] = df["canadian_relevant"] & df["in_relationship_graph"]
    df["wealthsimple_available"] = df["ticker"].isin(WEALTHSIMPLE_CONFIRMED).astype(bool)

    return df


def produce_canadian_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """Produce Phase 8 summary statistics."""
    canadian = df[df["canadian_relevant"]]
    cross_asset = df[df["cross_asset_candidate"]]

    summary = {
        "total_active_markets":       len(df),
        "canadian_relevant":          int(canadian["canadian_relevant"].sum()),
        "cross_asset_candidates":     int(cross_asset["cross_asset_candidate"].sum()),
        "wealthsimple_available":     int(df["wealthsimple_available"].sum()),
        "by_category": {},
        "top_markets_by_trades":      [],
    }

    for cat in CANADIAN_PATTERNS:
        mask = canadian["primary_category"] == cat
        summary["by_category"][cat] = int(mask.sum())

    # Top markets by trade activity
    top = canadian.nlargest(20, "trade_count")[
        ["ticker", "title", "primary_category", "trade_count",
         "in_relationship_graph", "wealthsimple_available"]
    ].to_dict("records")
    summary["top_markets_by_trades"] = top

    return summary


def update_canadian_relevance_in_db(df: pd.DataFrame) -> None:
    """
    Update events.canadian_relevance field for events that have
    Canadian-relevant markets.
    """
    canadian_events = df[df["canadian_relevant"]]["event_ticker"].unique().tolist()
    if not canadian_events:
        return

    try:
        with session_scope() as s:
            s.execute(text("""
                UPDATE events
                SET canadian_relevance = 1
                WHERE event_ticker = ANY(:evts)
            """), {"evts": canadian_events})
        logger.info("Updated canadian_relevance=1 for %d events", len(canadian_events))
    except Exception as exc:
        logger.warning("Failed to update canadian_relevance: %s", exc)


def run_canadian_analysis() -> Dict[str, Any]:
    df = load_canadian_markets()
    summary = produce_canadian_summary(df)
    update_canadian_relevance_in_db(df)

    logger.info("=== CANADIAN MARKETS ANALYSIS ===")
    logger.info("  Total active markets:   %d", summary["total_active_markets"])
    logger.info("  Canadian relevant:      %d", summary["canadian_relevant"])
    logger.info("  Cross-asset candidates: %d", summary["cross_asset_candidates"])
    logger.info("  By category:")
    for cat, n in summary["by_category"].items():
        logger.info("    %-25s %d", cat, n)
    logger.info("  Top markets by trades:")
    for m in summary["top_markets_by_trades"][:5]:
        logger.info("    %s (%s) trades=%s", m["ticker"], m["primary_category"], m["trade_count"])

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    import json
    result = run_canadian_analysis()
    print("\n=== CANADIAN MARKETS SUMMARY ===")
    print(json.dumps(result, indent=2, default=str))
