"""
tests/test_me_ce_detection.py
================================
Targeted unit tests for markets/relationship_detector.py:
  - detect_mutually_exclusive_set(): pairwise ME with threshold guard
  - detect_collectively_exhaustive_set(): binary CE with floor_strike guard
  - detect_threshold_order_relationships(): threshold ladder ordering

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


def _market(mid, floor_strike=None):
    return {"market_id": mid, "floor_strike": floor_strike}


# ---------------------------------------------------------------------------
# detect_mutually_exclusive_set
# ---------------------------------------------------------------------------

class TestDetectMutuallyExclusiveSet:
    def test_empty_markets_returns_empty(self):
        from engine.relationship_detector import detect_mutually_exclusive_set
        assert detect_mutually_exclusive_set([], "EVT") == []

    def test_single_market_returns_empty(self):
        from engine.relationship_detector import detect_mutually_exclusive_set
        result = detect_mutually_exclusive_set([_market("A")], "EVT")
        assert result == []

    def test_two_markets_returns_one_pair(self):
        from engine.relationship_detector import detect_mutually_exclusive_set
        result = detect_mutually_exclusive_set([_market("A"), _market("B")], "EVT")
        assert len(result) == 1

    def test_three_markets_returns_three_pairs(self):
        from engine.relationship_detector import detect_mutually_exclusive_set
        markets = [_market("A"), _market("B"), _market("C")]
        result = detect_mutually_exclusive_set(markets, "EVT")
        assert len(result) == 3  # C(3,2)

    def test_relationship_type_is_mutually_exclusive(self):
        from engine.relationship_detector import detect_mutually_exclusive_set
        result = detect_mutually_exclusive_set([_market("A"), _market("B")], "EVT")
        assert result[0]["relationship_type"] == "mutually_exclusive"

    def test_confidence_in_result(self):
        from engine.relationship_detector import detect_mutually_exclusive_set
        result = detect_mutually_exclusive_set([_market("A"), _market("B")], "EVT")
        assert "confidence" in result[0]
        assert result[0]["confidence"] > 0

    def test_threshold_pairs_skipped(self):
        """Markets with distinct floor_strikes should not be ME."""
        from engine.relationship_detector import detect_mutually_exclusive_set
        m1 = _market("A", floor_strike=4.75)
        m2 = _market("B", floor_strike=5.0)
        result = detect_mutually_exclusive_set([m1, m2], "EVT")
        assert result == []

    def test_same_floor_strike_not_skipped(self):
        """Same floor_strike (or both None) → not a threshold pair → ME applies."""
        from engine.relationship_detector import detect_mutually_exclusive_set
        m1 = _market("A", floor_strike=5.0)
        m2 = _market("B", floor_strike=5.0)
        result = detect_mutually_exclusive_set([m1, m2], "EVT")
        assert len(result) == 1

    def test_market_ids_propagated(self):
        from engine.relationship_detector import detect_mutually_exclusive_set
        result = detect_mutually_exclusive_set([_market("ALPHA"), _market("BETA")], "EVT")
        ids = {result[0]["market_id_1"], result[0]["market_id_2"]}
        assert ids == {"ALPHA", "BETA"}


# ---------------------------------------------------------------------------
# detect_collectively_exhaustive_set
# ---------------------------------------------------------------------------

class TestDetectCollectivelyExhaustiveSet:
    def test_returns_none_for_empty(self):
        from engine.relationship_detector import detect_collectively_exhaustive_set
        assert detect_collectively_exhaustive_set([], "EVT") is None

    def test_returns_none_for_single_market(self):
        from engine.relationship_detector import detect_collectively_exhaustive_set
        assert detect_collectively_exhaustive_set([_market("A")], "EVT") is None

    def test_returns_dict_for_exactly_two(self):
        from engine.relationship_detector import detect_collectively_exhaustive_set
        result = detect_collectively_exhaustive_set([_market("A"), _market("B")], "EVT")
        assert isinstance(result, dict)

    def test_type_is_collectively_exhaustive(self):
        from engine.relationship_detector import detect_collectively_exhaustive_set
        result = detect_collectively_exhaustive_set([_market("A"), _market("B")], "EVT")
        assert result["relationship_type"] == "collectively_exhaustive"

    def test_market_ids_correct(self):
        from engine.relationship_detector import detect_collectively_exhaustive_set
        result = detect_collectively_exhaustive_set([_market("A"), _market("B")], "EVT")
        assert result["market_id_1"] == "A"
        assert result["market_id_2"] == "B"

    def test_returns_none_for_three_markets(self):
        """3+ markets → CE not detected (requires explicit verification)."""
        from engine.relationship_detector import detect_collectively_exhaustive_set
        markets = [_market("A"), _market("B"), _market("C")]
        assert detect_collectively_exhaustive_set(markets, "EVT") is None

    def test_filters_out_floor_strike_markets(self):
        """If one has floor_strike, only 1 discrete market → None."""
        from engine.relationship_detector import detect_collectively_exhaustive_set
        markets = [_market("A"), _market("B", floor_strike=5.0)]
        result = detect_collectively_exhaustive_set(markets, "EVT")
        assert result is None

    def test_confidence_present(self):
        from engine.relationship_detector import detect_collectively_exhaustive_set
        result = detect_collectively_exhaustive_set([_market("A"), _market("B")], "EVT")
        assert "confidence" in result
        assert result["confidence"] > 0


# ---------------------------------------------------------------------------
# detect_threshold_order_relationships
# ---------------------------------------------------------------------------

class TestDetectThresholdOrderRelationships:
    def test_empty_returns_empty(self):
        from engine.relationship_detector import detect_threshold_order_relationships
        assert detect_threshold_order_relationships([]) == []

    def test_single_market_no_pair(self):
        from engine.relationship_detector import detect_threshold_order_relationships
        result = detect_threshold_order_relationships([_market("A", floor_strike=5.0)])
        assert result == []

    def test_no_floor_strikes_no_threshold(self):
        from engine.relationship_detector import detect_threshold_order_relationships
        result = detect_threshold_order_relationships([_market("A"), _market("B")])
        assert result == []

    def test_two_markets_with_different_strikes(self):
        from engine.relationship_detector import detect_threshold_order_relationships
        m1 = _market("A", floor_strike=4.75)
        m2 = _market("B", floor_strike=5.0)
        result = detect_threshold_order_relationships([m1, m2])
        assert len(result) == 1

    def test_relationship_type_is_threshold_order(self):
        from engine.relationship_detector import detect_threshold_order_relationships
        m1 = _market("A", floor_strike=4.75)
        m2 = _market("B", floor_strike=5.0)
        result = detect_threshold_order_relationships([m1, m2])
        assert result[0]["relationship_type"] == "threshold_order"

    def test_three_markets_multiple_pairs(self):
        from engine.relationship_detector import detect_threshold_order_relationships
        markets = [
            _market("A", floor_strike=4.75),
            _market("B", floor_strike=5.0),
            _market("C", floor_strike=5.25),
        ]
        result = detect_threshold_order_relationships(markets)
        # Adjacent pairs only: (4.75,5.0) and (5.0,5.25) = 2 pairs
        assert len(result) >= 1
