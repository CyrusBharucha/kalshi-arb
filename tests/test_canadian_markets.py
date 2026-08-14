"""
tests/test_canadian_markets.py
===============================
Unit tests for analysis/canadian_markets.py:
  - CANADIAN_PATTERNS: all 7 category keys present, values are compiled regexes
  - WEALTHSIMPLE_CONFIRMED: is a dict
  - classify_market(): returns list of matching category tags
  - produce_canadian_summary(): output shape and key invariants

All tests are pure Python — no DB, no API, no mocking needed.
"""
from __future__ import annotations

import re
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Patch session_scope at import time (module uses DB in load_canadian_markets)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    """Prevent any DB call from running during import."""
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


# ---------------------------------------------------------------------------
# CANADIAN_PATTERNS
# ---------------------------------------------------------------------------

class TestCanadianPatterns:
    """CANADIAN_PATTERNS constant structure."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        self.patterns = CANADIAN_PATTERNS

    def test_is_dict(self):
        assert isinstance(self.patterns, dict)

    def test_has_seven_categories(self):
        assert len(self.patterns) == 7

    def test_expected_keys_present(self):
        expected = {
            "boc_rate", "canadian_cpi", "cadusd",
            "wti_oil", "tsx", "fed_rate", "oil_general",
        }
        assert set(self.patterns.keys()) == expected

    def test_values_are_compiled_regexes(self):
        for key, val in self.patterns.items():
            assert isinstance(val, re.Pattern), f"{key} is not a compiled regex"

    def test_all_patterns_are_case_insensitive(self):
        for key, val in self.patterns.items():
            assert val.flags & re.IGNORECASE, f"{key} pattern is not IGNORECASE"

    def test_boc_rate_matches_bank_of_canada(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        assert CANADIAN_PATTERNS["boc_rate"].search("Bank of Canada rate decision")

    def test_canadian_cpi_matches(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        assert CANADIAN_PATTERNS["canadian_cpi"].search("Canadian CPI data released")

    def test_cadusd_matches_loonie(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        assert CANADIAN_PATTERNS["cadusd"].search("loonie drops vs USD")

    def test_wti_oil_matches_barrel(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        assert CANADIAN_PATTERNS["wti_oil"].search("Oil price per barrel")

    def test_tsx_matches(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        assert CANADIAN_PATTERNS["tsx"].search("TSX closes higher")

    def test_fed_rate_matches_fomc(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        assert CANADIAN_PATTERNS["fed_rate"].search("FOMC decision pending")

    def test_oil_general_matches_brent(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        assert CANADIAN_PATTERNS["oil_general"].search("Brent crude rises")


# ---------------------------------------------------------------------------
# WEALTHSIMPLE_CONFIRMED
# ---------------------------------------------------------------------------

class TestWealthsimpleConfirmed:
    """WEALTHSIMPLE_CONFIRMED constant."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.canadian_markets import WEALTHSIMPLE_CONFIRMED
        self.ws = WEALTHSIMPLE_CONFIRMED

    def test_is_dict(self):
        assert isinstance(self.ws, dict)


# ---------------------------------------------------------------------------
# classify_market()
# ---------------------------------------------------------------------------

class TestClassifyMarket:
    """classify_market(ticker, title, category) returns list of category tags."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.canadian_markets import classify_market
        self.fn = classify_market

    def test_returns_list(self):
        result = self.fn("KXTEST-YES", "some title")
        assert isinstance(result, list)

    def test_empty_list_for_unrelated_market(self):
        result = self.fn("KXUS-YES", "Will US GDP grow?", "economics")
        # Should not trigger any Canadian pattern (no BOC/Canada/TSX etc.)
        assert "boc_rate" not in result
        assert "tsx" not in result

    def test_boc_rate_tag_returned_for_boc(self):
        result = self.fn("KXBOC-YES", "Bank of Canada rate decision", "")
        assert "boc_rate" in result

    def test_tsx_tag_returned_for_tsx_title(self):
        result = self.fn("KXTSX-YES", "TSX composite close price", "")
        assert "tsx" in result

    def test_cadusd_tag_returned_for_loonie(self):
        result = self.fn("KXCAD-YES", "Will the loonie rise?", "fx")
        assert "cadusd" in result

    def test_wti_oil_tag_returned_for_barrel(self):
        result = self.fn("KXWTI-YES", "Oil price per barrel end of month", "energy")
        assert "wti_oil" in result

    def test_multiple_tags_possible(self):
        # A WTI market also matches oil_general
        result = self.fn("KXWTI-YES", "WTI crude barrel price", "energy")
        assert "wti_oil" in result
        assert "oil_general" in result

    def test_category_text_searched_too(self):
        result = self.fn("KXTEST", "generic title", "TSX composite")
        assert "tsx" in result

    def test_ticker_searched(self):
        result = self.fn("KXBOC-YES", "market title", "")
        assert "boc_rate" in result  # KXBOC triggers boc_rate via kxboc pattern

    def test_case_insensitive_title(self):
        result = self.fn("KXTEST", "BANK OF CANADA decision", "")
        assert "boc_rate" in result

    def test_fed_rate_tag(self):
        result = self.fn("KXFED-YES", "FOMC rate decision", "")
        assert "fed_rate" in result

    def test_canadian_cpi_tag(self):
        result = self.fn("KXCACPI-YES", "Canada CPI data", "")
        assert "canadian_cpi" in result


# ---------------------------------------------------------------------------
# produce_canadian_summary()
# ---------------------------------------------------------------------------

def _make_df(rows):
    """Helper to build a minimal DataFrame matching expected schema."""
    return pd.DataFrame(rows, columns=[
        "market_id", "ticker", "title", "category",
        "status", "event_ticker", "floor_strike", "cap_strike",
        "geographic_region", "trade_count",
        "canadian_tags", "canadian_relevant", "primary_category",
        "in_relationship_graph", "cross_asset_candidate", "wealthsimple_available",
    ])


class TestProduceCanadianSummary:
    """produce_canadian_summary(df) returns a dict with required keys."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.canadian_markets import produce_canadian_summary
        self.fn = produce_canadian_summary

    def _build(self, *rows):
        return pd.DataFrame(rows)

    def _base_row(self, market_id="M1", canadian_relevant=False, cross_asset=False,
                  wealthsimple=False, primary_cat=None, trade_count=0,
                  ticker="KXTEST-YES", title="Test Market"):
        return {
            "market_id": market_id,
            "ticker": ticker,
            "title": title,
            "category": "",
            "status": "active",
            "event_ticker": "EV1",
            "floor_strike": None,
            "cap_strike": None,
            "geographic_region": None,
            "trade_count": trade_count,
            "canadian_tags": [primary_cat] if primary_cat else [],
            "canadian_relevant": canadian_relevant,
            "primary_category": primary_cat,
            "in_relationship_graph": False,
            "cross_asset_candidate": cross_asset,
            "wealthsimple_available": wealthsimple,
        }

    def test_returns_dict(self):
        df = pd.DataFrame([self._base_row()])
        result = self.fn(df)
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        df = pd.DataFrame([self._base_row()])
        result = self.fn(df)
        for key in ("total_active_markets", "canadian_relevant",
                    "cross_asset_candidates", "wealthsimple_available",
                    "by_category", "top_markets_by_trades"):
            assert key in result, f"Missing key: {key}"

    def test_total_active_markets_correct(self):
        df = pd.DataFrame([self._base_row("M1"), self._base_row("M2")])
        result = self.fn(df)
        assert result["total_active_markets"] == 2

    def test_canadian_relevant_count(self):
        df = pd.DataFrame([
            self._base_row("M1", canadian_relevant=True, primary_cat="boc_rate"),
            self._base_row("M2", canadian_relevant=False),
        ])
        result = self.fn(df)
        assert result["canadian_relevant"] == 1

    def test_cross_asset_candidates_count(self):
        df = pd.DataFrame([
            self._base_row("M1", canadian_relevant=True, cross_asset=True, primary_cat="tsx"),
            self._base_row("M2", canadian_relevant=True, primary_cat="tsx"),
        ])
        result = self.fn(df)
        assert result["cross_asset_candidates"] == 1

    def test_wealthsimple_available_count(self):
        df = pd.DataFrame([
            self._base_row("M1", wealthsimple=True),
            self._base_row("M2", wealthsimple=False),
        ])
        result = self.fn(df)
        assert result["wealthsimple_available"] == 1

    def test_by_category_is_dict(self):
        df = pd.DataFrame([self._base_row()])
        result = self.fn(df)
        assert isinstance(result["by_category"], dict)

    def test_by_category_has_all_keys(self):
        from analysis.canadian_markets import CANADIAN_PATTERNS
        df = pd.DataFrame([self._base_row()])
        result = self.fn(df)
        for cat in CANADIAN_PATTERNS:
            assert cat in result["by_category"]

    def test_top_markets_is_list(self):
        df = pd.DataFrame([self._base_row()])
        result = self.fn(df)
        assert isinstance(result["top_markets_by_trades"], list)

    def test_empty_df_returns_zeros(self):
        # Build a completely empty df but with the right columns
        cols = ["market_id", "ticker", "title", "category", "status",
                "event_ticker", "floor_strike", "cap_strike", "geographic_region",
                "trade_count", "canadian_tags", "canadian_relevant",
                "primary_category", "in_relationship_graph",
                "cross_asset_candidate", "wealthsimple_available"]
        df = pd.DataFrame(columns=cols)
        # Convert bool columns
        for col in ["canadian_relevant", "cross_asset_candidate", "wealthsimple_available"]:
            df[col] = df[col].astype(bool)
        df["trade_count"] = df["trade_count"].astype(int)
        result = self.fn(df)
        assert result["total_active_markets"] == 0
        assert result["canadian_relevant"] == 0
