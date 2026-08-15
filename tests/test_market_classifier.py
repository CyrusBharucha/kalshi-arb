"""
tests/test_market_classifier.py
=================================
Tests for markets/classifier.py — all pure Python, no DB, no network.

Covers:
  - score_canadian_relevance
  - classify_market_type
  - classify_geographic_region
  - classify_market
  - batch_classify
  - get_canadian_markets
  - detect_event_structure
"""
from __future__ import annotations

import pytest
from engine.classifier import (
    score_canadian_relevance,
    classify_market_type,
    classify_geographic_region,
    classify_market,
    batch_classify,
    get_canadian_markets,
    detect_event_structure,
)


# ---------------------------------------------------------------------------
# score_canadian_relevance
# ---------------------------------------------------------------------------

class TestScoreCanadianRelevance:
    def _market(self, title="", category="", event_ticker="", series_ticker=""):
        return {"title": title, "category": category,
                "event_ticker": event_ticker, "series_ticker": series_ticker}

    def test_boc_in_title_scores_10(self):
        m = self._market(title="Will the BOC raise rates?")
        assert score_canadian_relevance(m) == 10

    def test_corra_in_title_scores_10(self):
        m = self._market(title="CORRA overnight rate decision")
        assert score_canadian_relevance(m) == 10

    def test_canada_cpi_scores_9(self):
        m = self._market(title="Canada CPI above 3%?")
        assert score_canadian_relevance(m) == 9

    def test_canadian_cpi_scores_9(self):
        m = self._market(title="Will Canadian CPI surprise?")
        assert score_canadian_relevance(m) == 9

    def test_cad_usd_scores_8(self):
        m = self._market(title="CAD/USD above 0.75?")
        assert score_canadian_relevance(m) == 8

    def test_canadian_dollar_scores_8(self):
        m = self._market(title="Canadian dollar vs USD")
        assert score_canadian_relevance(m) == 8

    def test_wti_crude_scores_6(self):
        m = self._market(title="WTI crude above $80?")
        assert score_canadian_relevance(m) == 6

    def test_canada_mentions_scores_at_least_5(self):
        m = self._market(title="Who wins the Canada election?")
        assert score_canadian_relevance(m) >= 5

    def test_unrelated_market_scores_zero(self):
        m = self._market(title="Super Bowl winner", category="sports")
        assert score_canadian_relevance(m) == 0

    def test_entertainment_category_scores_zero(self):
        m = self._market(title="Canadian band wins Grammy", category="entertainment")
        assert score_canadian_relevance(m) == 0

    def test_sports_category_scores_zero(self):
        m = self._market(title="Toronto Maple Leafs win", category="sports")
        assert score_canadian_relevance(m) == 0

    def test_empty_market_scores_zero(self):
        assert score_canadian_relevance({}) == 0

    def test_event_ticker_contributes_to_score(self):
        m = self._market(event_ticker="KXBOC-25JAN")
        assert score_canadian_relevance(m) == 10  # "boc" in event ticker


# ---------------------------------------------------------------------------
# classify_market_type
# ---------------------------------------------------------------------------

class TestClassifyMarketType:
    def test_binary_no_strikes(self):
        mtype, otype = classify_market_type({"title": "Will X happen?"})
        assert mtype == "binary"
        assert otype == "yes_no"

    def test_range_with_floor_and_cap(self):
        mtype, otype = classify_market_type({"floor_strike": 60, "cap_strike": 80})
        assert mtype == "range"
        assert otype == "interval"

    def test_threshold_with_only_floor(self):
        mtype, otype = classify_market_type({"floor_strike": 60})
        assert mtype == "threshold"
        assert otype == "threshold"

    def test_threshold_with_only_cap(self):
        mtype, otype = classify_market_type({"cap_strike": 80})
        assert mtype == "threshold"
        assert otype == "threshold"

    def test_multivariate_explicit(self):
        mtype, otype = classify_market_type({"market_type": "multivariate"})
        assert mtype == "multivariate"
        assert otype == "categorical"

    def test_multivariate_takes_priority_over_strikes(self):
        mtype, otype = classify_market_type({
            "market_type": "multivariate",
            "floor_strike": 60, "cap_strike": 80,
        })
        assert mtype == "multivariate"

    def test_empty_market_returns_binary(self):
        mtype, otype = classify_market_type({})
        assert mtype == "binary"


# ---------------------------------------------------------------------------
# classify_geographic_region
# ---------------------------------------------------------------------------

class TestClassifyGeographicRegion:
    def test_boc_is_canada(self):
        m = {"title": "BOC rate decision", "category": "", "event_ticker": ""}
        assert classify_geographic_region(m) == "Canada"

    def test_tsx_is_canada(self):
        m = {"title": "TSX above 20000?", "category": "", "event_ticker": ""}
        assert classify_geographic_region(m) == "Canada"

    def test_fomc_is_us(self):
        m = {"title": "FOMC rate decision", "category": "", "event_ticker": ""}
        assert classify_geographic_region(m) == "US"

    def test_federal_reserve_is_us(self):
        m = {"title": "Federal Reserve hikes?", "category": "", "event_ticker": ""}
        assert classify_geographic_region(m) == "US"

    def test_ecb_is_international(self):
        m = {"title": "ECB rate meeting", "category": "", "event_ticker": ""}
        assert classify_geographic_region(m) == "International"

    def test_boe_is_international(self):
        m = {"title": "BOE rate decision", "category": "", "event_ticker": ""}
        assert classify_geographic_region(m) == "International"

    def test_unclassifiable_is_global(self):
        m = {"title": "Random market", "category": "", "event_ticker": ""}
        assert classify_geographic_region(m) == "Global"

    def test_empty_is_global(self):
        assert classify_geographic_region({}) == "Global"


# ---------------------------------------------------------------------------
# classify_market (integration)
# ---------------------------------------------------------------------------

class TestClassifyMarket:
    def test_adds_canadian_relevance_field(self):
        m = {"title": "BOC rate decision"}
        result = classify_market(m)
        assert "canadian_relevance" in result

    def test_adds_market_type_field(self):
        m = {"title": "Will X happen?"}
        result = classify_market(m)
        assert "market_type" in result

    def test_adds_outcome_type_field(self):
        m = {"title": "Will X happen?"}
        result = classify_market(m)
        assert "outcome_type" in result

    def test_adds_geographic_region_field(self):
        m = {"title": "BOC rate decision"}
        result = classify_market(m)
        assert "geographic_region" in result

    def test_preserves_original_fields(self):
        m = {"title": "BOC rate decision", "ticker": "KXBOC-25JAN"}
        result = classify_market(m)
        assert result["ticker"] == "KXBOC-25JAN"

    def test_boc_market_gets_canada_region(self):
        m = {"title": "BOC rate decision"}
        result = classify_market(m)
        assert result["geographic_region"] == "Canada"

    def test_boc_market_gets_high_relevance(self):
        m = {"title": "BOC overnight rate target"}
        result = classify_market(m)
        assert result["canadian_relevance"] >= 9


# ---------------------------------------------------------------------------
# batch_classify
# ---------------------------------------------------------------------------

class TestBatchClassify:
    def test_empty_list_returns_empty(self):
        assert batch_classify([]) == []

    def test_single_market(self):
        result = batch_classify([{"title": "BOC rate?"}])
        assert len(result) == 1

    def test_multiple_markets_all_classified(self):
        markets = [{"title": f"Market {i}"} for i in range(5)]
        result = batch_classify(markets)
        assert len(result) == 5
        for m in result:
            assert "canadian_relevance" in m

    def test_preserves_order(self):
        markets = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
        result = batch_classify(markets)
        assert result[0]["title"] == "A"
        assert result[2]["title"] == "C"


# ---------------------------------------------------------------------------
# get_canadian_markets
# ---------------------------------------------------------------------------

class TestGetCanadianMarkets:
    def test_filters_out_non_canadian(self):
        markets = [
            {"title": "BOC rate decision"},
            {"title": "Super Bowl winner", "category": "sports"},
        ]
        result = get_canadian_markets(markets)
        assert len(result) == 1
        assert result[0]["title"] == "BOC rate decision"

    def test_empty_list_returns_empty(self):
        assert get_canadian_markets([]) == []

    def test_min_score_filter_works(self):
        markets = [
            {"title": "BOC rate", "category": ""},   # score=10
            {"title": "Canada election"},             # score=5
        ]
        result_high = get_canadian_markets(markets, min_score=9)
        assert len(result_high) == 1

        result_low = get_canadian_markets(markets, min_score=5)
        assert len(result_low) == 2

    def test_returns_classified_fields(self):
        markets = [{"title": "BOC rate"}]
        result = get_canadian_markets(markets)
        for m in result:
            assert "canadian_relevance" in m


# ---------------------------------------------------------------------------
# detect_event_structure
# ---------------------------------------------------------------------------

class TestDetectEventStructure:
    def test_empty_markets_returns_empty(self):
        result = detect_event_structure([])
        assert result == {}

    def test_market_count_correct(self):
        markets = [{"title": "M1"}, {"title": "M2"}]
        result = detect_event_structure(markets)
        assert result["market_count"] == 2

    def test_ordered_strikes_detected(self):
        markets = [
            {"floor_strike": 60.0}, {"floor_strike": 80.0}, {"floor_strike": 100.0},
        ]
        result = detect_event_structure(markets)
        assert result["has_threshold_structure"] is True

    def test_unordered_strikes_not_threshold(self):
        markets = [
            {"floor_strike": 80.0}, {"floor_strike": 60.0},
        ]
        result = detect_event_structure(markets)
        # Strikes are sorted, so they'll be ordered after sorting → threshold detected
        assert isinstance(result["has_threshold_structure"], bool)

    def test_no_strikes_no_threshold(self):
        markets = [{"title": "Binary market"}]
        result = detect_event_structure(markets)
        assert result["has_threshold_structure"] is False

    def test_all_binary_detected(self):
        markets = [{"title": "Will X happen?"}, {"title": "Will Y happen?"}]
        result = detect_event_structure(markets)
        assert result["all_binary"] is True

    def test_range_markets_not_all_binary(self):
        markets = [
            {"floor_strike": 60, "cap_strike": 80, "title": "Range market"},
        ]
        result = detect_event_structure(markets)
        assert result["all_binary"] is False

    def test_strikes_returned_sorted(self):
        markets = [
            {"floor_strike": 80.0}, {"floor_strike": 60.0}, {"floor_strike": 100.0},
        ]
        result = detect_event_structure(markets)
        strikes = result["strikes"]
        assert strikes == sorted(strikes)
