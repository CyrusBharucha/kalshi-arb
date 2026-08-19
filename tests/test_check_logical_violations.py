"""
tests/test_check_logical_violations.py
========================================
Unit tests for markets/relationship_detector.check_logical_price_violations().

Tests cover all three relationship types:
  - threshold_order / superset: violation when P(m1) < P(m2)
  - mutually_exclusive: violation when bid1 + bid2 > 1.0
  - collectively_exhaustive: violation when ask1 + ask2 < 1.0

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


def _prices(yes_bid=0.40, yes_ask=0.45):
    return {"yes_bid": yes_bid, "yes_ask": yes_ask}


def _rel(rtype, m1="A", m2="B"):
    return {
        "market_id_1": m1, "market_id_2": m2,
        "relationship_type": rtype,
        "confidence": 0.9,
    }


class TestCheckLogicalPriceViolationsEmpty:
    def test_empty_rels_returns_empty(self):
        from engine.relationship_detector import check_logical_price_violations
        result = check_logical_price_violations([], {})
        assert result == []

    def test_rels_but_no_prices_returns_empty(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("threshold_order")
        result = check_logical_price_violations([rel], {})
        assert result == []


class TestThresholdOrderViolations:
    def test_no_violation_when_m1_above_m2(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("threshold_order")
        prices = {
            "A": _prices(yes_bid=0.60, yes_ask=0.65),
            "B": _prices(yes_bid=0.40, yes_ask=0.45),
        }
        result = check_logical_price_violations([rel], prices)
        assert result == []

    def test_violation_when_m1_below_m2(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("threshold_order")
        prices = {
            "A": _prices(yes_bid=0.30, yes_ask=0.35),  # m1 cheaper
            "B": _prices(yes_bid=0.50, yes_ask=0.55),  # m2 pricier
        }
        result = check_logical_price_violations([rel], prices)
        assert len(result) == 1

    def test_violation_has_type_field(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("threshold_order")
        prices = {
            "A": _prices(yes_bid=0.30, yes_ask=0.35),
            "B": _prices(yes_bid=0.60, yes_ask=0.65),
        }
        v = check_logical_price_violations([rel], prices)[0]
        assert v["type"] == "threshold_order"

    def test_violation_magnitude_correct(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("threshold_order")
        # midpoint A = 0.325, midpoint B = 0.625 → magnitude = 0.625 - 0.325 = 0.30
        prices = {
            "A": _prices(yes_bid=0.30, yes_ask=0.35),
            "B": _prices(yes_bid=0.60, yes_ask=0.65),
        }
        v = check_logical_price_violations([rel], prices)[0]
        assert v["magnitude"] == pytest.approx(0.30, abs=0.01)

    def test_superset_treated_same_as_threshold_order(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("superset")
        prices = {
            "A": _prices(yes_bid=0.30, yes_ask=0.35),
            "B": _prices(yes_bid=0.60, yes_ask=0.65),
        }
        result = check_logical_price_violations([rel], prices)
        assert len(result) == 1

    def test_equal_midpoints_no_violation(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("threshold_order")
        prices = {
            "A": _prices(yes_bid=0.50, yes_ask=0.50),
            "B": _prices(yes_bid=0.50, yes_ask=0.50),
        }
        result = check_logical_price_violations([rel], prices)
        assert result == []


class TestMutuallyExclusiveViolations:
    def test_no_violation_when_sum_below_one(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("mutually_exclusive")
        prices = {
            "A": _prices(yes_bid=0.40),
            "B": _prices(yes_bid=0.50),
        }
        result = check_logical_price_violations([rel], prices)
        assert result == []

    def test_violation_when_sum_above_one(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("mutually_exclusive")
        prices = {
            "A": _prices(yes_bid=0.60),
            "B": _prices(yes_bid=0.55),
        }
        result = check_logical_price_violations([rel], prices)
        assert len(result) == 1

    def test_violation_type_correct(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("mutually_exclusive")
        prices = {
            "A": _prices(yes_bid=0.65),
            "B": _prices(yes_bid=0.60),
        }
        v = check_logical_price_violations([rel], prices)[0]
        assert v["type"] == "mutually_exclusive"

    def test_magnitude_equals_excess(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("mutually_exclusive")
        prices = {
            "A": _prices(yes_bid=0.65),
            "B": _prices(yes_bid=0.60),
        }
        v = check_logical_price_violations([rel], prices)[0]
        assert v["magnitude"] == pytest.approx(0.25, abs=0.01)

    def test_missing_bid_skipped(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("mutually_exclusive")
        prices = {
            "A": {"yes_ask": 0.65},  # no yes_bid
            "B": _prices(yes_bid=0.60),
        }
        result = check_logical_price_violations([rel], prices)
        assert result == []


class TestCollectivelyExhaustiveViolations:
    def test_no_violation_when_cost_above_one(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("collectively_exhaustive")
        prices = {
            "A": _prices(yes_ask=0.55),
            "B": _prices(yes_ask=0.50),
        }
        result = check_logical_price_violations([rel], prices)
        assert result == []

    def test_violation_when_cost_below_one(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("collectively_exhaustive")
        prices = {
            "A": _prices(yes_ask=0.40),
            "B": _prices(yes_ask=0.45),
        }
        result = check_logical_price_violations([rel], prices)
        assert len(result) == 1

    def test_violation_type_correct(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("collectively_exhaustive")
        prices = {
            "A": _prices(yes_ask=0.40),
            "B": _prices(yes_ask=0.40),
        }
        v = check_logical_price_violations([rel], prices)[0]
        assert v["type"] == "collectively_exhaustive"

    def test_magnitude_equals_discount(self):
        from engine.relationship_detector import check_logical_price_violations
        rel = _rel("collectively_exhaustive")
        prices = {
            "A": _prices(yes_ask=0.40),
            "B": _prices(yes_ask=0.40),
        }
        v = check_logical_price_violations([rel], prices)[0]
        # cost = 0.80 < 1.0 → magnitude = 0.20
        assert v["magnitude"] == pytest.approx(0.20, abs=0.01)
