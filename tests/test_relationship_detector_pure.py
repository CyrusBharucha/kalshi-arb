"""
tests/test_relationship_detector_pure.py
==========================================
Unit tests for pure helper functions in markets/relationship_detector.py:
  - _midpoint(): None when bid/ask missing, average otherwise
  - _deduplicate(): removes duplicate (m1, m2, type) triples
  - _detect_threshold_direction(): 'leq' vs 'geq'
  - _markets_have_ordered_strikes(): bool check
  - detect_complement_relationship(): self-referential complement

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


# ---------------------------------------------------------------------------
# _midpoint
# ---------------------------------------------------------------------------

class TestMidpoint:
    def test_no_bid_returns_none(self):
        from engine.relationship_detector import _midpoint
        assert _midpoint({"yes_ask": 0.55}) is None

    def test_no_ask_returns_none(self):
        from engine.relationship_detector import _midpoint
        assert _midpoint({"yes_bid": 0.40}) is None

    def test_empty_dict_returns_none(self):
        from engine.relationship_detector import _midpoint
        assert _midpoint({}) is None

    def test_correct_average(self):
        from engine.relationship_detector import _midpoint
        result = _midpoint({"yes_bid": 0.40, "yes_ask": 0.50})
        assert result == pytest.approx(0.45)

    def test_equal_bid_ask(self):
        from engine.relationship_detector import _midpoint
        result = _midpoint({"yes_bid": 0.55, "yes_ask": 0.55})
        assert result == pytest.approx(0.55)

    def test_returns_float(self):
        from engine.relationship_detector import _midpoint
        result = _midpoint({"yes_bid": 0.40, "yes_ask": 0.60})
        assert isinstance(result, float)

    def test_string_values_converted(self):
        from engine.relationship_detector import _midpoint
        result = _midpoint({"yes_bid": "0.40", "yes_ask": "0.60"})
        assert result == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# _deduplicate
# ---------------------------------------------------------------------------

class TestDeduplicate:
    def _rel(self, m1, m2, rtype, conf=0.9):
        return {"market_id_1": m1, "market_id_2": m2, "relationship_type": rtype,
                "confidence": conf}

    def test_empty_list(self):
        from engine.relationship_detector import _deduplicate
        assert _deduplicate([]) == []

    def test_no_duplicates_unchanged(self):
        from engine.relationship_detector import _deduplicate
        rels = [
            self._rel("A", "B", "complement"),
            self._rel("A", "C", "mutually_exclusive"),
        ]
        result = _deduplicate(rels)
        assert len(result) == 2

    def test_exact_duplicate_removed(self):
        from engine.relationship_detector import _deduplicate
        rels = [
            self._rel("A", "B", "complement"),
            self._rel("A", "B", "complement"),
        ]
        result = _deduplicate(rels)
        assert len(result) == 1

    def test_same_markets_different_type_kept(self):
        from engine.relationship_detector import _deduplicate
        rels = [
            self._rel("A", "B", "complement"),
            self._rel("A", "B", "mutually_exclusive"),
        ]
        result = _deduplicate(rels)
        assert len(result) == 2

    def test_first_occurrence_kept(self):
        from engine.relationship_detector import _deduplicate
        rels = [
            self._rel("A", "B", "complement", conf=0.9),
            self._rel("A", "B", "complement", conf=0.5),
        ]
        result = _deduplicate(rels)
        assert result[0]["confidence"] == 0.9

    def test_multiple_duplicates(self):
        from engine.relationship_detector import _deduplicate
        rels = [self._rel("X", "Y", "me")] * 5
        result = _deduplicate(rels)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _detect_threshold_direction
# ---------------------------------------------------------------------------

class TestDetectThresholdDirection:
    def test_returns_string(self):
        from engine.relationship_detector import _detect_threshold_direction
        result = _detect_threshold_direction({"ticker": "KXBOCRATE-26"})
        assert isinstance(result, str)

    def test_default_is_leq(self):
        from engine.relationship_detector import _detect_threshold_direction
        result = _detect_threshold_direction({"ticker": "KXTEST-PLAIN-26"})
        assert result == "leq"

    def test_empty_market_is_leq(self):
        from engine.relationship_detector import _detect_threshold_direction
        result = _detect_threshold_direction({})
        assert result == "leq"

    def test_geq_in_title_returns_geq(self):
        from engine.relationship_detector import _detect_threshold_direction
        result = _detect_threshold_direction({"ticker": "X", "title": "Will exceed 5.0%?"})
        assert result == "geq"

    def test_above_keyword_returns_geq(self):
        from engine.relationship_detector import _detect_threshold_direction
        result = _detect_threshold_direction({"ticker": "X", "title": "Will go above 100?"})
        assert result == "geq"


# ---------------------------------------------------------------------------
# _markets_have_ordered_strikes
# ---------------------------------------------------------------------------

class TestMarketsHaveOrderedStrikes:
    def test_both_none_returns_false(self):
        from engine.relationship_detector import _markets_have_ordered_strikes
        assert _markets_have_ordered_strikes({}, {}) is False

    def test_one_none_returns_false(self):
        from engine.relationship_detector import _markets_have_ordered_strikes
        assert _markets_have_ordered_strikes(
            {"floor_strike": 5.0}, {}
        ) is False

    def test_same_strike_returns_false(self):
        from engine.relationship_detector import _markets_have_ordered_strikes
        assert _markets_have_ordered_strikes(
            {"floor_strike": 5.0}, {"floor_strike": 5.0}
        ) is False

    def test_different_strikes_returns_true(self):
        from engine.relationship_detector import _markets_have_ordered_strikes
        assert _markets_have_ordered_strikes(
            {"floor_strike": 4.75}, {"floor_strike": 5.0}
        ) is True

    def test_string_strikes_converted(self):
        from engine.relationship_detector import _markets_have_ordered_strikes
        assert _markets_have_ordered_strikes(
            {"floor_strike": "4.75"}, {"floor_strike": "5.00"}
        ) is True

    def test_invalid_strike_returns_false(self):
        from engine.relationship_detector import _markets_have_ordered_strikes
        assert _markets_have_ordered_strikes(
            {"floor_strike": "bad"}, {"floor_strike": 5.0}
        ) is False


# ---------------------------------------------------------------------------
# detect_complement_relationship
# ---------------------------------------------------------------------------

class TestDetectComplementRelationship:
    def test_returns_dict_for_binary(self):
        from engine.relationship_detector import detect_complement_relationship
        result = detect_complement_relationship({"market_id": "MKT-001", "market_type": "binary"})
        assert isinstance(result, dict)

    def test_uses_market_id(self):
        from engine.relationship_detector import detect_complement_relationship
        result = detect_complement_relationship({"market_id": "MKT-001"})
        assert result["market_id_1"] == "MKT-001"
        assert result["market_id_2"] == "MKT-001"

    def test_falls_back_to_ticker(self):
        from engine.relationship_detector import detect_complement_relationship
        result = detect_complement_relationship({"ticker": "KXTEST-26"})
        assert result["market_id_1"] == "KXTEST-26"

    def test_type_is_complement(self):
        from engine.relationship_detector import detect_complement_relationship
        result = detect_complement_relationship({"market_id": "X"})
        assert result["relationship_type"] == "complement"

    def test_confidence_one(self):
        from engine.relationship_detector import detect_complement_relationship
        result = detect_complement_relationship({"market_id": "X"})
        assert result["confidence"] == 1.0

    def test_no_market_id_returns_none(self):
        from engine.relationship_detector import detect_complement_relationship
        result = detect_complement_relationship({})
        assert result is None

    def test_non_binary_market_type_returns_none(self):
        from engine.relationship_detector import detect_complement_relationship
        result = detect_complement_relationship({"market_id": "X", "market_type": "scalar"})
        assert result is None
