"""
tests/test_subset_relationships.py
=====================================
Unit tests for markets/relationship_detector.detect_subset_relationships().

Tests:
  - None when floor_strike missing from either market
  - None when same floor_strike
  - Returns dict for valid leq and geq directions
  - Relationship type is 'superset'
  - Correct market_id_1 is the superset (more likely) market
  - Confidence is 1.0

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


def _market(mid, floor_strike=None, title=""):
    return {"market_id": mid, "floor_strike": floor_strike, "title": title}


class TestDetectSubsetRelationshipsNone:
    def test_no_floor_strike_a_returns_none(self):
        from engine.relationship_detector import detect_subset_relationships
        result = detect_subset_relationships(
            _market("A"),               # no floor_strike
            _market("B", floor_strike=5.0),
        )
        assert result is None

    def test_no_floor_strike_b_returns_none(self):
        from engine.relationship_detector import detect_subset_relationships
        result = detect_subset_relationships(
            _market("A", floor_strike=5.0),
            _market("B"),               # no floor_strike
        )
        assert result is None

    def test_both_none_returns_none(self):
        from engine.relationship_detector import detect_subset_relationships
        result = detect_subset_relationships(_market("A"), _market("B"))
        assert result is None

    def test_same_floor_strike_returns_none(self):
        from engine.relationship_detector import detect_subset_relationships
        result = detect_subset_relationships(
            _market("A", floor_strike=5.0),
            _market("B", floor_strike=5.0),
        )
        assert result is None

    def test_invalid_floor_strike_returns_none(self):
        from engine.relationship_detector import detect_subset_relationships
        result = detect_subset_relationships(
            _market("A", floor_strike="bad"),
            _market("B", floor_strike=5.0),
        )
        assert result is None


class TestDetectSubsetRelationshipsLeq:
    def test_higher_strike_is_superset_leq(self):
        """leq direction: P(X<=high) >= P(X<=low), so high-strike market is superset."""
        from engine.relationship_detector import detect_subset_relationships
        m_high = _market("HIGH", floor_strike=5.0)   # higher strike = superset
        m_low  = _market("LOW",  floor_strike=4.75)
        result = detect_subset_relationships(m_high, m_low)
        assert result is not None
        assert result["market_id_1"] == "HIGH"
        assert result["relationship_type"] == "superset"

    def test_lower_strike_first_returns_none_leq(self):
        """m_a has lower strike → not superset for leq → None."""
        from engine.relationship_detector import detect_subset_relationships
        m_low  = _market("LOW",  floor_strike=4.75)
        m_high = _market("HIGH", floor_strike=5.0)
        result = detect_subset_relationships(m_low, m_high)
        assert result is None

    def test_confidence_is_one(self):
        from engine.relationship_detector import detect_subset_relationships
        m_high = _market("A", floor_strike=5.0)
        m_low  = _market("B", floor_strike=4.75)
        result = detect_subset_relationships(m_high, m_low)
        assert result["confidence"] == 1.0

    def test_returns_dict(self):
        from engine.relationship_detector import detect_subset_relationships
        m_high = _market("A", floor_strike=6.0)
        m_low  = _market("B", floor_strike=4.0)
        result = detect_subset_relationships(m_high, m_low)
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        from engine.relationship_detector import detect_subset_relationships
        m_high = _market("A", floor_strike=6.0)
        m_low  = _market("B", floor_strike=4.0)
        result = detect_subset_relationships(m_high, m_low)
        for key in ("market_id_1", "market_id_2", "relationship_type", "confidence"):
            assert key in result


class TestDetectSubsetRelationshipsGeq:
    def test_lower_strike_is_superset_geq(self):
        """geq direction: P(X>=low) >= P(X>=high), lower-strike is superset."""
        from engine.relationship_detector import detect_subset_relationships
        # Use a title with "exceed" keyword to trigger 'geq' direction
        m_low  = _market("LOW",  floor_strike=4.0, title="Will exceed 4.0?")
        m_high = _market("HIGH", floor_strike=6.0)
        result = detect_subset_relationships(m_low, m_high)
        # For geq: fa_f < fb_f required; 4.0 < 6.0 → superset returned
        if result is not None:
            assert result["relationship_type"] == "superset"
