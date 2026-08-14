"""
tests/test_canadian_markets_extended.py
=========================================
Extended unit tests for analysis/canadian_markets.py:
  - classify_market(): returns list; matches each CANADIAN_PATTERNS key
  - CANADIAN_PATTERNS: dict with re.Pattern values
  - produce_canadian_summary(): requires correct DataFrame shape; returns dict

No DB, no network — all DB calls mocked.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(session_scope=mock_ss),
        "database.models": MagicMock(),
    }):
        yield


# ---------------------------------------------------------------------------
# CANADIAN_PATTERNS constant
# ---------------------------------------------------------------------------

class TestCanadianPatterns:
    def test_is_dict(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        assert isinstance(CANADIAN_PATTERNS, dict)

    def test_nonempty(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        assert len(CANADIAN_PATTERNS) > 0

    def test_values_are_patterns(self):
        import re
        from analysis.canadian_markets import CANADIAN_PATTERNS
        for k, v in CANADIAN_PATTERNS.items():
            assert isinstance(v, type(re.compile(""))), f"{k} is not a compiled pattern"

    def test_keys_are_strings(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        for k in CANADIAN_PATTERNS:
            assert isinstance(k, str)


# ---------------------------------------------------------------------------
# classify_market (analysis/canadian_markets.py — simpler category tagger)
# ---------------------------------------------------------------------------

class TestClassifyMarketCategoryTagger:
    def test_returns_list(self):
        from analysis.canadian_markets import classify_market
        result = classify_market("MKT-X", "Generic title")
        assert isinstance(result, list)

    def test_empty_list_for_non_canadian(self):
        from analysis.canadian_markets import classify_market
        result = classify_market("KXNASDAQ-26", "S&P 500 level end of year")
        assert result == []

    def test_boc_rate_category_matched(self):
        from analysis.canadian_markets import classify_market
        result = classify_market("KXBOC-26", "Bank of Canada rate decision")
        assert "boc_rate" in result

    def test_categories_are_strings(self):
        from analysis.canadian_markets import classify_market
        result = classify_market("ANY", "Bank of Canada raises rates today")
        assert all(isinstance(tag, str) for tag in result)

    def test_multiple_categories_possible(self):
        from analysis.canadian_markets import classify_market
        # "Bank of Canada" touches boc_rate; "oil" touches oil_general
        result = classify_market("X", "Bank of Canada oil price impact")
        assert len(result) >= 2

    def test_case_insensitive(self):
        from analysis.canadian_markets import classify_market
        lower = classify_market("X", "bank of canada")
        upper = classify_market("X", "BANK OF CANADA")
        assert lower == upper

    def test_ticker_also_searched(self):
        from analysis.canadian_markets import classify_market
        # 'KXBOC-26NOV' contains 'kxboc' — matches boc_rate pattern
        result = classify_market("KXBOC-26NOV", "Generic market title")
        assert "boc_rate" in result


# ---------------------------------------------------------------------------
# produce_canadian_summary
# ---------------------------------------------------------------------------

def _make_summary_df(n_total=10, n_canadian=4, n_cross_asset=2, n_ws=1):
    """Create a minimal DataFrame matching the shape expected by produce_canadian_summary."""
    rows = []
    for i in range(n_total):
        is_can = i < n_canadian
        is_cross = i < n_cross_asset
        is_ws = i < n_ws
        rows.append({
            "market_id": f"MKT-{i:03d}",
            "ticker": f"KXTEST-{i:03d}",
            "title": "Bank of Canada" if is_can else f"Generic market {i}",
            "category": "Economics",
            "status": "open",
            "event_ticker": f"EVT-{i}",
            "floor_strike": None,
            "cap_strike": None,
            "trade_count": i * 10,
            "canadian_relevant": is_can,
            "primary_category": "boc_rate" if is_can else None,
            "in_relationship_graph": is_cross,
            "cross_asset_candidate": is_cross,
            "wealthsimple_available": is_ws,
        })
    return pd.DataFrame(rows)


class TestProduceCanadianSummary:
    def test_returns_dict(self):
        from analysis.canadian_markets import produce_canadian_summary
        df = _make_summary_df()
        result = produce_canadian_summary(df)
        assert isinstance(result, dict)

    def test_has_total_active_markets(self):
        from analysis.canadian_markets import produce_canadian_summary
        df = _make_summary_df(n_total=10)
        result = produce_canadian_summary(df)
        assert "total_active_markets" in result
        assert result["total_active_markets"] == 10

    def test_has_canadian_relevant_count(self):
        from analysis.canadian_markets import produce_canadian_summary
        df = _make_summary_df(n_total=10, n_canadian=4)
        result = produce_canadian_summary(df)
        assert "canadian_relevant" in result
        assert result["canadian_relevant"] == 4

    def test_has_cross_asset_candidates(self):
        from analysis.canadian_markets import produce_canadian_summary
        df = _make_summary_df(n_total=10, n_canadian=4, n_cross_asset=2)
        result = produce_canadian_summary(df)
        assert "cross_asset_candidates" in result
        assert result["cross_asset_candidates"] == 2

    def test_has_wealthsimple_available(self):
        from analysis.canadian_markets import produce_canadian_summary
        df = _make_summary_df(n_total=10, n_ws=1)
        result = produce_canadian_summary(df)
        assert "wealthsimple_available" in result
        assert result["wealthsimple_available"] == 1

    def test_has_by_category(self):
        from analysis.canadian_markets import produce_canadian_summary
        df = _make_summary_df()
        result = produce_canadian_summary(df)
        assert "by_category" in result
        assert isinstance(result["by_category"], dict)

    def test_has_top_markets(self):
        from analysis.canadian_markets import produce_canadian_summary
        df = _make_summary_df()
        result = produce_canadian_summary(df)
        assert "top_markets_by_trades" in result
        assert isinstance(result["top_markets_by_trades"], list)

    def test_by_category_has_pattern_keys(self):
        from analysis.canadian_markets import produce_canadian_summary, CANADIAN_PATTERNS
        df = _make_summary_df()
        result = produce_canadian_summary(df)
        for key in CANADIAN_PATTERNS:
            assert key in result["by_category"]

    def test_boc_rate_count_in_by_category(self):
        from analysis.canadian_markets import produce_canadian_summary
        df = _make_summary_df(n_total=10, n_canadian=4)
        # All 4 canadian rows have primary_category="boc_rate"
        result = produce_canadian_summary(df)
        assert result["by_category"]["boc_rate"] == 4

    def test_zero_canadian_gives_zero_counts(self):
        from analysis.canadian_markets import produce_canadian_summary
        df = _make_summary_df(n_total=5, n_canadian=0, n_cross_asset=0, n_ws=0)
        result = produce_canadian_summary(df)
        assert result["canadian_relevant"] == 0
        assert result["cross_asset_candidates"] == 0
