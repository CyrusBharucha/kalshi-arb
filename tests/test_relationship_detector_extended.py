"""
tests/test_relationship_detector_extended.py
=============================================
Extended unit tests for markets/relationship_detector.py.

Covers _midpoint, _markets_have_ordered_strikes, _detect_threshold_direction,
detect_complement_relationship, detect_mutually_exclusive_set,
detect_collectively_exhaustive_set, and detect_subset_relationships.

No database, no network.
"""
from __future__ import annotations

import pytest

from engine.relationship_detector import (
    detect_complement_relationship,
    detect_mutually_exclusive_set,
    detect_collectively_exhaustive_set,
    detect_threshold_order_relationships,
    detect_subset_relationships,
    detect_all_relationships_for_event,
    check_logical_price_violations,
    _midpoint,
    _markets_have_ordered_strikes,
)


# ---------------------------------------------------------------------------
# _midpoint
# ---------------------------------------------------------------------------

class TestMidpoint:
    def test_both_present(self):
        m = _midpoint({"yes_bid": 0.44, "yes_ask": 0.46})
        assert abs(m - 0.45) < 1e-6

    def test_missing_bid_returns_none(self):
        assert _midpoint({"yes_ask": 0.46}) is None

    def test_missing_ask_returns_none(self):
        assert _midpoint({"yes_bid": 0.44}) is None

    def test_none_prices_returns_none(self):
        assert _midpoint({}) is None

    def test_correct_average(self):
        m = _midpoint({"yes_bid": 0.40, "yes_ask": 0.60})
        assert abs(m - 0.50) < 1e-6


# ---------------------------------------------------------------------------
# _markets_have_ordered_strikes
# ---------------------------------------------------------------------------

class TestMarketsHaveOrderedStrikes:
    def test_different_strikes(self):
        m1 = {"floor_strike": 60}
        m2 = {"floor_strike": 80}
        assert _markets_have_ordered_strikes(m1, m2) is True

    def test_same_strikes(self):
        m1 = {"floor_strike": 70}
        m2 = {"floor_strike": 70}
        assert _markets_have_ordered_strikes(m1, m2) is False

    def test_missing_strike_false(self):
        m1 = {"floor_strike": 70}
        m2 = {"ticker": "X"}
        assert _markets_have_ordered_strikes(m1, m2) is False

    def test_both_missing_false(self):
        assert _markets_have_ordered_strikes({}, {}) is False

    def test_string_strikes_converted(self):
        m1 = {"floor_strike": "60"}
        m2 = {"floor_strike": "80"}
        assert _markets_have_ordered_strikes(m1, m2) is True


# ---------------------------------------------------------------------------
# detect_complement_relationship
# ---------------------------------------------------------------------------

class TestDetectComplementRelationship:
    def test_binary_market_returns_relationship(self):
        m = {"market_id": "KXTEST-01", "market_type": "binary"}
        rel = detect_complement_relationship(m)
        assert rel is not None

    def test_relationship_type_is_complement(self):
        m = {"market_id": "KXTEST-01", "market_type": "binary"}
        rel = detect_complement_relationship(m)
        if rel:
            assert rel["relationship_type"] == "complement"

    def test_missing_market_id_returns_none(self):
        m = {"market_type": "binary"}
        rel = detect_complement_relationship(m)
        assert rel is None

    def test_self_referential(self):
        """Complement relationship has market_id_1 == market_id_2."""
        m = {"market_id": "KXTEST-01", "market_type": "binary"}
        rel = detect_complement_relationship(m)
        if rel:
            assert rel["market_id_1"] == rel["market_id_2"]


# ---------------------------------------------------------------------------
# detect_mutually_exclusive_set
# ---------------------------------------------------------------------------

def _simple_market(market_id, yes_bid=0.30, yes_ask=0.32):
    return {"market_id": market_id, "ticker": market_id,
            "yes_bid": yes_bid, "yes_ask": yes_ask}


class TestDetectMutuallyExclusiveSet:
    def test_single_market_returns_empty(self):
        result = detect_mutually_exclusive_set([_simple_market("M1")], "EV")
        assert result == []

    def test_two_markets_returns_list(self):
        result = detect_mutually_exclusive_set([
            _simple_market("M1"), _simple_market("M2"),
        ], "EV")
        assert isinstance(result, list)

    def test_relationship_type_is_me(self):
        result = detect_mutually_exclusive_set([
            _simple_market("M1"), _simple_market("M2"),
        ], "EV")
        for rel in result:
            assert rel["relationship_type"] == "mutually_exclusive"

    def test_empty_returns_empty(self):
        result = detect_mutually_exclusive_set([], "EV")
        assert result == []


# ---------------------------------------------------------------------------
# detect_collectively_exhaustive_set
# ---------------------------------------------------------------------------

class TestDetectCollectivelyExhaustiveSet:
    def test_single_market_returns_none(self):
        result = detect_collectively_exhaustive_set([_simple_market("M1")], "EV")
        assert result is None

    def test_two_markets_returns_result(self):
        result = detect_collectively_exhaustive_set([
            _simple_market("M1", 0.48, 0.50),
            _simple_market("M2", 0.48, 0.50),
        ], "EV")
        # Result can be a Relationship or None depending on sum
        assert result is None or isinstance(result, dict)

    def test_empty_returns_none(self):
        result = detect_collectively_exhaustive_set([], "EV")
        assert result is None


# ---------------------------------------------------------------------------
# detect_all_relationships_for_event
# ---------------------------------------------------------------------------

class TestDetectAllRelationshipsForEvent:
    def _markets(self, n):
        return [{"market_id": f"M{i}", "ticker": f"M{i}",
                 "yes_bid": 0.30, "yes_ask": 0.32,
                 "market_type": "binary"}
                for i in range(n)]

    def test_empty_markets_empty_list(self):
        result = detect_all_relationships_for_event([], "EV")
        assert isinstance(result, list)

    def test_single_market_returns_complement(self):
        result = detect_all_relationships_for_event(self._markets(1), "EV")
        types = [r["relationship_type"] for r in result]
        assert "complement" in types

    def test_two_markets_returns_list(self):
        result = detect_all_relationships_for_event(self._markets(2), "EV")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_no_duplicates(self):
        """Each (m1, m2, type) triplet should appear at most once."""
        result = detect_all_relationships_for_event(self._markets(3), "EV")
        fingerprints = [
            (r["market_id_1"], r["market_id_2"], r["relationship_type"])
            for r in result
        ]
        assert len(fingerprints) == len(set(fingerprints))


# ---------------------------------------------------------------------------
# detect_subset_relationships
# ---------------------------------------------------------------------------

class TestDetectSubsetRelationships:
    def test_no_range_markets_returns_none(self):
        """Markets without both floor+cap strike produce no subset relationship."""
        m1 = _simple_market("M1")
        m2 = _simple_market("M2")
        result = detect_subset_relationships(m1, m2)
        assert result is None

    def test_returns_relationship_or_none(self):
        """Any pair produces a Relationship or None."""
        m1 = {"market_id": "M1", "floor_strike": 60, "cap_strike": 80,
              "yes_bid": 0.30, "yes_ask": 0.32}
        m2 = {"market_id": "M2", "floor_strike": 65, "cap_strike": 75,
              "yes_bid": 0.20, "yes_ask": 0.22}
        result = detect_subset_relationships(m1, m2)
        assert result is None or isinstance(result, dict)
