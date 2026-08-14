"""
tests/test_canadian_filter.py
==============================
Unit tests for analysis/canadian_filter.py:
  - is_canadian(text): regex-based Canadian relevance detection
  - classify_market(): full classification with categories + confidence
  - filter_markets(): list filtering

All tests are pure Python — no DB, no API, no mocking needed.
"""
from __future__ import annotations

import pytest


class TestIsCanadian:
    """is_canadian(text) — returns True if text matches a Canadian pattern."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.canadian_filter import is_canadian
        self.fn = is_canadian

    def test_canada_keyword_matches(self):
        assert self.fn("Will Canada implement tariffs?")

    def test_canadian_adjective_matches(self):
        assert self.fn("Canadian dollar falls")

    def test_tsx_matches(self):
        assert self.fn("TSX closes higher today")

    def test_boc_matches(self):
        assert self.fn("BoC rate decision")

    def test_bank_of_canada_matches(self):
        assert self.fn("Bank of Canada announces hold")

    def test_cad_currency_matches(self):
        assert self.fn("USD/CAD exchange rate update")

    def test_ontario_matches(self):
        assert self.fn("Ontario election results")

    def test_alberta_matches(self):
        assert self.fn("Alberta oil sands project")

    def test_empty_string_returns_false(self):
        assert not self.fn("")

    def test_none_returns_false(self):
        assert not self.fn(None)

    def test_unrelated_text_returns_false(self):
        assert not self.fn("Federal Reserve hikes rates in the US")

    def test_us_only_text_returns_false(self):
        assert not self.fn("S&P 500 closes at all-time high")

    def test_returns_bool(self):
        result = self.fn("Canada is great")
        assert isinstance(result, bool)


class TestClassifyMarket:
    """classify_market() returns a dict with is_canadian, categories, confidence, matched_on."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.canadian_filter import classify_market
        self.fn = classify_market

    def test_returns_dict(self):
        result = self.fn("KXTEST-YES")
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        result = self.fn("KXTEST-YES")
        for key in ("is_canadian", "categories", "confidence", "matched_on"):
            assert key in result

    def test_non_canadian_market_is_canadian_false(self):
        result = self.fn(
            market_id="KXUS-YES",
            title="Will US GDP grow?",
            event_title="US GDP",
        )
        assert result["is_canadian"] is False

    def test_canadian_title_is_canadian_true(self):
        result = self.fn(
            market_id="KXBOC-YES",
            title="Will the Bank of Canada cut rates?",
        )
        assert result["is_canadian"] is True

    def test_confidence_high_when_title_matches(self):
        result = self.fn(
            market_id="KXBOC-YES",
            title="Bank of Canada rate decision",
        )
        assert result["confidence"] >= 0.90

    def test_confidence_medium_when_market_id_matches(self):
        result = self.fn(
            market_id="CAD-USD-YES",  # word-boundary-clean CAD
            title="Exchange rate",
        )
        assert result["confidence"] >= 0.50

    def test_confidence_zero_for_non_canadian(self):
        result = self.fn(
            market_id="KXUS-YES",
            title="US presidential election",
        )
        assert result["confidence"] == 0.0

    def test_categories_is_list(self):
        result = self.fn("KXTEST")
        assert isinstance(result["categories"], list)

    def test_matched_on_is_string(self):
        result = self.fn("KXTEST")
        assert isinstance(result["matched_on"], str)

    def test_matched_on_empty_for_non_canadian(self):
        result = self.fn("KXUS-YES", title="US economy")
        assert result["matched_on"] == ""

    def test_event_title_triggers_is_canadian(self):
        result = self.fn(
            market_id="KXEVENT-YES",
            title="Will rates change?",
            event_title="Bank of Canada Decision",
        )
        assert result["is_canadian"] is True


class TestFilterMarkets:
    """filter_markets() returns only Canadian-relevant markets from a list."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.canadian_filter import filter_markets
        self.fn = filter_markets

    def _make(self, market_id, title="", event_title="", labels=None):
        return {
            "market_id": market_id,
            "title": title,
            "event_title": event_title,
            "labels": labels or [],
        }

    def test_returns_list(self):
        result = self.fn([])
        assert isinstance(result, list)

    def test_empty_input_returns_empty(self):
        assert self.fn([]) == []

    def test_filters_out_non_canadian(self):
        markets = [
            self._make("KXUS-YES", title="US election"),
            self._make("KXFR-YES", title="French election"),
        ]
        result = self.fn(markets)
        assert len(result) == 0

    def test_keeps_canadian_market(self):
        markets = [
            self._make("KXBOC-YES", title="Bank of Canada rate"),
            self._make("KXUS-YES", title="US Fed rate"),
        ]
        result = self.fn(markets)
        assert len(result) == 1
        assert result[0]["market_id"] == "KXBOC-YES"

    def test_all_canadian_all_returned(self):
        markets = [
            self._make("M1", title="Canadian election"),
            self._make("M2", title="Ontario budget"),
            self._make("M3", title="TSX performance"),
        ]
        result = self.fn(markets)
        assert len(result) == 3
