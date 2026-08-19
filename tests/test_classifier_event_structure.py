"""
tests/test_classifier_event_structure.py
==========================================
Unit tests for markets/classifier.detect_event_structure().

Tests:
  - Empty market list → empty dict
  - market_count matches input length
  - strikes extracted and sorted correctly
  - has_threshold_structure True when ordered strikes
  - has_threshold_structure False when strikes unordered or only 1
  - all_binary True when all binary markets
  - likely_mutually_exclusive / collectively_exhaustive defaults
  - markets with None floor_strike excluded from strikes

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
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
    }):
        yield


def _market(mid="MKT-A", floor_strike=None, title="Will X?",
            category="financials", subtitle=""):
    return {
        "market_id": mid,
        "floor_strike": floor_strike,
        "title": title,
        "category": category,
        "subtitle": subtitle,
        "sub_title": subtitle,
    }


class TestDetectEventStructureEmpty:
    def test_empty_list_returns_empty_dict(self):
        from engine.classifier import detect_event_structure
        assert detect_event_structure([]) == {}

    def test_empty_dict_is_falsy(self):
        from engine.classifier import detect_event_structure
        assert not detect_event_structure([])


class TestDetectEventStructureMarketCount:
    def test_single_market(self):
        from engine.classifier import detect_event_structure
        result = detect_event_structure([_market()])
        assert result["market_count"] == 1

    def test_three_markets(self):
        from engine.classifier import detect_event_structure
        markets = [_market("A"), _market("B"), _market("C")]
        result = detect_event_structure(markets)
        assert result["market_count"] == 3


class TestDetectEventStructureStrikes:
    def test_no_floor_strikes_empty_strikes(self):
        from engine.classifier import detect_event_structure
        markets = [_market("A"), _market("B")]
        result = detect_event_structure(markets)
        assert result["strikes"] == []

    def test_strikes_sorted_ascending(self):
        from engine.classifier import detect_event_structure
        markets = [
            _market("A", floor_strike=5.0),
            _market("B", floor_strike=3.0),
            _market("C", floor_strike=4.0),
        ]
        result = detect_event_structure(markets)
        assert result["strikes"] == [3.0, 4.0, 5.0]

    def test_none_floor_strike_excluded(self):
        from engine.classifier import detect_event_structure
        markets = [
            _market("A", floor_strike=5.0),
            _market("B", floor_strike=None),
            _market("C", floor_strike=3.0),
        ]
        result = detect_event_structure(markets)
        assert len(result["strikes"]) == 2
        assert None not in result["strikes"]

    def test_single_strike_included(self):
        from engine.classifier import detect_event_structure
        markets = [_market("A", floor_strike=2.5)]
        result = detect_event_structure(markets)
        assert result["strikes"] == [2.5]


class TestDetectEventStructureThreshold:
    def test_ordered_strikes_has_threshold_structure(self):
        from engine.classifier import detect_event_structure
        markets = [
            _market("A", floor_strike=2.0),
            _market("B", floor_strike=3.0),
            _market("C", floor_strike=4.0),
        ]
        result = detect_event_structure(markets)
        assert result["has_threshold_structure"] is True

    def test_single_strike_no_threshold_structure(self):
        from engine.classifier import detect_event_structure
        markets = [_market("A", floor_strike=5.0)]
        result = detect_event_structure(markets)
        assert result["has_threshold_structure"] is False

    def test_no_strikes_no_threshold_structure(self):
        from engine.classifier import detect_event_structure
        markets = [_market("A"), _market("B")]
        result = detect_event_structure(markets)
        assert result["has_threshold_structure"] is False

    def test_duplicate_strikes_no_threshold_structure(self):
        from engine.classifier import detect_event_structure
        markets = [
            _market("A", floor_strike=3.0),
            _market("B", floor_strike=3.0),
        ]
        result = detect_event_structure(markets)
        assert result["has_threshold_structure"] is False


class TestDetectEventStructureAllBinary:
    def test_binary_markets_all_binary_true(self):
        from engine.classifier import detect_event_structure
        markets = [
            _market("A", title="Will X?"),
            _market("B", title="Will Y?"),
        ]
        result = detect_event_structure(markets)
        assert result["all_binary"] is True

    def test_has_required_keys(self):
        from engine.classifier import detect_event_structure
        result = detect_event_structure([_market()])
        for key in ("market_count", "has_threshold_structure", "strikes",
                    "all_binary", "likely_mutually_exclusive",
                    "likely_collectively_exhaustive"):
            assert key in result

    def test_likely_me_default_true(self):
        from engine.classifier import detect_event_structure
        result = detect_event_structure([_market()])
        assert result["likely_mutually_exclusive"] is True

    def test_likely_ce_default_true(self):
        from engine.classifier import detect_event_structure
        result = detect_event_structure([_market()])
        assert result["likely_collectively_exhaustive"] is True
