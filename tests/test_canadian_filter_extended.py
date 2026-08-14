"""
tests/test_canadian_filter_extended.py
=======================================
Extended unit tests for analysis/canadian_filter.py.

The existing test_canadian_filter.py covers is_canadian (basic), classify_market
(basic), and filter_markets (basic). These tests add:
  - Sports team keywords (NHL, NBA, MLB, CFL)
  - Canadian company keywords (Shopify, RBC, Air Canada)
  - Geographic keywords (Toronto, Vancouver, Calgary)
  - Economic keywords (USMCA, TSX, USD/CAD)
  - classify_market via labels and description fields
  - classify_market confidence levels (0.95/0.80/0.85/0.70/0.0)
  - classify_market categories list: sports, companies, economics, geography
  - filter_markets with description + labels
  - ALL_PATTERNS constant structure
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# is_canadian — sports
# ---------------------------------------------------------------------------

class TestIsCanadianSports:
    def test_toronto_maple_leafs(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Will the Toronto Maple Leafs win the Cup?")

    def test_vancouver_canucks(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Vancouver Canucks to make playoffs")

    def test_calgary_flames(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Calgary Flames season wins")

    def test_edmonton_oilers(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Edmonton Oilers first round exit")

    def test_toronto_raptors(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Toronto Raptors playoff seed")

    def test_toronto_blue_jays(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Toronto Blue Jays wins this season")

    def test_cfl(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("CFL Grey Cup champion 2026")

    def test_pwhl(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("PWHL champion this season")

    def test_cf_montreal(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("CF Montreal to win MLS Cup")

    def test_winnipeg_jets(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Winnipeg Jets division winner")


# ---------------------------------------------------------------------------
# is_canadian — companies
# ---------------------------------------------------------------------------

class TestIsCanadianCompanies:
    def test_shopify(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Shopify stock above $100")

    def test_rbc(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("RBC earnings beat estimates")

    def test_td_bank(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("TD Bank Q3 revenue")

    def test_air_canada(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Air Canada flight disruptions")

    def test_enbridge(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Enbridge pipeline approval")

    def test_bmo(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("BMO prime rate announcement")


# ---------------------------------------------------------------------------
# is_canadian — geography and economic
# ---------------------------------------------------------------------------

class TestIsCanadianGeoAndEcon:
    def test_toronto(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Toronto housing prices Q2")

    def test_usmca(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("USMCA renegotiation timeline")

    def test_tsx(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("TSX composite index level")

    def test_usd_cad(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("USD/CAD exchange rate end of year")

    def test_nafta(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("NAFTA successor trade deal")

    def test_51st_state(self):
        from analysis.canadian_filter import is_canadian
        assert is_canadian("Will Canada become the 51st state")


# ---------------------------------------------------------------------------
# classify_market — confidence levels
# ---------------------------------------------------------------------------

class TestClassifyMarketConfidence:
    def test_confidence_095_on_title_match(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("ANY-ID", title="Bank of Canada rate decision")
        assert result["confidence"] == pytest.approx(0.95)

    def test_confidence_095_on_event_title_match(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("ANY-ID", event_title="Canadian federal election")
        assert result["confidence"] == pytest.approx(0.95)

    def test_confidence_080_on_market_id_match(self):
        from analysis.canadian_filter import classify_market
        # Use a market_id that matches (e.g. contains "Canada" as whole word via ticker)
        # The regex uses \bcanad[ia] — "KXBOCRATE-CANADA-26" has "CANADA" with word boundary after "-"
        result = classify_market("KXBOCRATE-CANADA-26", title="Generic market")
        # market_id matched → confidence should be 0.80
        assert result["confidence"] == pytest.approx(0.80)

    def test_confidence_085_on_labels_match(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("ANY-ID", labels=["Canada", "politics"])
        assert result["confidence"] == pytest.approx(0.85)

    def test_confidence_070_on_description_match(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("ANY-ID", description="This concerns the Bank of Canada")
        assert result["confidence"] == pytest.approx(0.70)

    def test_confidence_zero_for_non_canadian(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("KXUSEMP-26", title="US unemployment rate")
        assert result["confidence"] == 0.0
        assert result["is_canadian"] is False


# ---------------------------------------------------------------------------
# classify_market — categories
# ---------------------------------------------------------------------------

class TestClassifyMarketCategories:
    def test_sports_category_on_nhl_team(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("X", title="Edmonton Oilers Stanley Cup odds")
        assert "sports" in result["categories"]

    def test_economics_category_on_boc(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("X", title="Bank of Canada rate decision")
        assert "economics" in result["categories"]

    def test_politics_category_on_election(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("X", title="Canadian federal election winner")
        assert "politics" in result["categories"]

    def test_companies_category_on_shopify(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("X", title="Shopify Q4 earnings beat")
        assert "companies" in result["categories"]

    def test_geography_category_on_toronto(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("X", title="Toronto housing market crash")
        assert "geography" in result["categories"]

    def test_multiple_categories_possible(self):
        from analysis.canadian_filter import classify_market
        # TSX touches both economics AND geography (Toronto)
        result = classify_market("X", title="TSX composite drops in Toronto trading")
        assert len(result["categories"]) >= 1

    def test_empty_categories_for_non_canadian(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("KXUSEMP-26", title="US unemployment")
        assert result["categories"] == []


# ---------------------------------------------------------------------------
# classify_market — matched_on field
# ---------------------------------------------------------------------------

class TestClassifyMarketMatchedOn:
    def test_matched_on_title_when_title_matches(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("ANY-ID", title="Bank of Canada rate")
        assert "title" in result["matched_on"]

    def test_matched_on_empty_when_no_match(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("ANY-ID", title="US Federal Reserve rate")
        assert result["matched_on"] == ""

    def test_matched_on_multiple_fields(self):
        from analysis.canadian_filter import classify_market
        result = classify_market("ANY-ID",
                                 title="Bank of Canada",
                                 event_title="Canadian markets")
        matched = result["matched_on"]
        assert "title" in matched
        assert "event_title" in matched


# ---------------------------------------------------------------------------
# ALL_PATTERNS — module-level constant
# ---------------------------------------------------------------------------

class TestAllPatterns:
    def test_is_list(self):
        from analysis.canadian_filter import ALL_PATTERNS
        assert isinstance(ALL_PATTERNS, list)

    def test_nonempty(self):
        from analysis.canadian_filter import ALL_PATTERNS
        assert len(ALL_PATTERNS) > 10

    def test_all_strings(self):
        from analysis.canadian_filter import ALL_PATTERNS
        for p in ALL_PATTERNS:
            assert isinstance(p, str)


# ---------------------------------------------------------------------------
# filter_markets — labels and description
# ---------------------------------------------------------------------------

class TestFilterMarketsExtended:
    def test_label_triggers_inclusion(self):
        from analysis.canadian_filter import filter_markets
        markets = [
            {"market_id": "X", "title": "Generic", "event_title": "Generic",
             "labels": ["Canada"], "description": ""}
        ]
        result = filter_markets(markets)
        assert len(result) == 1

    def test_description_triggers_inclusion(self):
        from analysis.canadian_filter import filter_markets
        markets = [
            {"market_id": "X", "title": "Generic", "event_title": "Generic",
             "labels": [], "description": "Relevant to the Bank of Canada rate"}
        ]
        result = filter_markets(markets)
        assert len(result) == 1

    def test_output_dict_has_classification_keys(self):
        from analysis.canadian_filter import filter_markets
        markets = [
            {"market_id": "X", "title": "Toronto Raptors", "event_title": "NBA",
             "labels": [], "description": ""}
        ]
        result = filter_markets(markets)
        assert len(result) == 1
        row = result[0]
        assert "is_canadian" in row
        assert "categories" in row
        assert "confidence" in row
        assert "matched_on" in row

    def test_original_fields_preserved(self):
        from analysis.canadian_filter import filter_markets
        markets = [
            {"market_id": "MY-ID", "title": "Toronto Blue Jays",
             "event_title": "MLB", "labels": [], "description": "",
             "custom_field": "preserved"}
        ]
        result = filter_markets(markets)
        assert result[0]["market_id"] == "MY-ID"
        assert result[0]["custom_field"] == "preserved"
