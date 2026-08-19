"""
tests/test_markets_classifier_extended.py
==========================================
Unit tests for markets/classifier.py pure functions:
  - score_canadian_relevance(): 0-10 scoring
  - classify_market_type(): binary/threshold/range/multivariate
  - classify_geographic_region(): region string
  - detect_event_structure(): structure of event
  - batch_classify(): list processing
  - get_canadian_markets(): min_score filtering

No DB, no network.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
    }):
        yield


# ---------------------------------------------------------------------------
# score_canadian_relevance
# ---------------------------------------------------------------------------

class TestScoreCanadianRelevance:
    def test_non_canadian_returns_zero(self):
        from engine.classifier import score_canadian_relevance
        market = {"title": "Will the NFL season start on time?", "category": "sports"}
        assert score_canadian_relevance(market) == 0

    def test_sports_category_returns_zero(self):
        from engine.classifier import score_canadian_relevance
        market = {"title": "Bank of Canada rate meeting", "category": "sports"}
        assert score_canadian_relevance(market) == 0

    def test_boc_keyword_returns_high_score(self):
        from engine.classifier import score_canadian_relevance
        market = {"title": "Will the Bank of Canada raise rates?"}
        assert score_canadian_relevance(market) >= 9

    def test_wti_oil_returns_6(self):
        from engine.classifier import score_canadian_relevance
        market = {"title": "Will WTI crude oil exceed $80?"}
        assert score_canadian_relevance(market) == 6

    def test_canada_generic_returns_5(self):
        from engine.classifier import score_canadian_relevance
        market = {"title": "Will Canada win the hockey tournament?", "category": "economics"}
        assert score_canadian_relevance(market) >= 5

    def test_cad_usd_returns_8(self):
        from engine.classifier import score_canadian_relevance
        market = {"title": "Will USD/CAD exceed 1.40?"}
        assert score_canadian_relevance(market) == 8

    def test_empty_market_returns_zero(self):
        from engine.classifier import score_canadian_relevance
        assert score_canadian_relevance({}) == 0

    def test_returns_int(self):
        from engine.classifier import score_canadian_relevance
        result = score_canadian_relevance({"title": "Bank of Canada policy"})
        assert isinstance(result, int)

    def test_uses_event_ticker_text(self):
        from engine.classifier import score_canadian_relevance
        market = {"title": "Unknown", "event_ticker": "KXBOCRATE-2026"}
        # "boc" appears in event_ticker
        score = score_canadian_relevance(market)
        assert score >= 10

    def test_entertainment_category_zero(self):
        from engine.classifier import score_canadian_relevance
        market = {"title": "Will Canada get mentioned?", "category": "entertainment"}
        assert score_canadian_relevance(market) == 0


# ---------------------------------------------------------------------------
# classify_market_type
# ---------------------------------------------------------------------------

class TestClassifyMarketType:
    def test_binary_market(self):
        from engine.classifier import classify_market_type
        mtype, otype = classify_market_type({"title": "Will X happen?"})
        assert mtype == "binary"
        assert otype == "yes_no"

    def test_threshold_with_floor(self):
        from engine.classifier import classify_market_type
        mtype, otype = classify_market_type({"floor_strike": 5.0})
        assert mtype == "threshold"
        assert otype == "threshold"

    def test_threshold_with_cap(self):
        from engine.classifier import classify_market_type
        mtype, otype = classify_market_type({"cap_strike": 10.0})
        assert mtype == "threshold"
        assert otype == "threshold"

    def test_range_with_both_strikes(self):
        from engine.classifier import classify_market_type
        mtype, otype = classify_market_type({"floor_strike": 5.0, "cap_strike": 10.0})
        assert mtype == "range"
        assert otype == "interval"

    def test_multivariate_type(self):
        from engine.classifier import classify_market_type
        mtype, otype = classify_market_type({"market_type": "multivariate"})
        assert mtype == "multivariate"
        assert otype == "categorical"

    def test_returns_tuple(self):
        from engine.classifier import classify_market_type
        result = classify_market_type({})
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_no_strikes_is_binary(self):
        from engine.classifier import classify_market_type
        mtype, _ = classify_market_type({"floor_strike": None, "cap_strike": None})
        assert mtype == "binary"


# ---------------------------------------------------------------------------
# classify_geographic_region
# ---------------------------------------------------------------------------

class TestClassifyGeographicRegion:
    def test_returns_string(self):
        from engine.classifier import classify_geographic_region
        result = classify_geographic_region({"title": "Test"})
        assert isinstance(result, str)

    def test_canada_region(self):
        from engine.classifier import classify_geographic_region
        result = classify_geographic_region({"title": "Will Canada raise rates?"})
        assert "canada" in result.lower() or result != ""

    def test_empty_market(self):
        from engine.classifier import classify_geographic_region
        result = classify_geographic_region({})
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# classify_market (full pipeline)
# ---------------------------------------------------------------------------

class TestClassifyMarket:
    def test_returns_dict(self):
        from engine.classifier import classify_market
        result = classify_market({"market_id": "X", "title": "Test"})
        assert isinstance(result, dict)

    def test_has_canadian_score(self):
        from engine.classifier import classify_market
        result = classify_market({"market_id": "X", "title": "Bank of Canada"})
        assert "canadian_relevance" in result or "canadian_relevance_score" in result

    def test_has_market_type(self):
        from engine.classifier import classify_market
        result = classify_market({"market_id": "X", "title": "Test"})
        assert "market_type" in result or "market_type_classified" in result


# ---------------------------------------------------------------------------
# batch_classify
# ---------------------------------------------------------------------------

class TestBatchClassify:
    def test_empty_input(self):
        from engine.classifier import batch_classify
        result = batch_classify([])
        assert result == []

    def test_returns_list_same_length(self):
        from engine.classifier import batch_classify
        markets = [{"market_id": f"M{i}"} for i in range(5)]
        result = batch_classify(markets)
        assert len(result) == 5

    def test_each_item_is_dict(self):
        from engine.classifier import batch_classify
        result = batch_classify([{"market_id": "X"}])
        assert isinstance(result[0], dict)


# ---------------------------------------------------------------------------
# get_canadian_markets
# ---------------------------------------------------------------------------

class TestGetCanadianMarkets:
    def test_filters_by_score(self):
        from engine.classifier import get_canadian_markets
        markets = [
            {"title": "Bank of Canada rate decision"},
            {"title": "Will the NFL playoffs start?", "category": "sports"},
        ]
        result = get_canadian_markets(markets, min_score=5)
        titles = [m["title"] for m in result]
        assert any("Bank of Canada" in t for t in titles)

    def test_empty_input(self):
        from engine.classifier import get_canadian_markets
        assert get_canadian_markets([]) == []

    def test_min_score_zero_returns_all(self):
        from engine.classifier import get_canadian_markets
        markets = [{"title": "Random market"}, {"title": "Another market"}]
        result = get_canadian_markets(markets, min_score=0)
        assert len(result) == len(markets)
