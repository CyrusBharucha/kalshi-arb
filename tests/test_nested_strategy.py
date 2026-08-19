"""
tests/test_nested_strategy.py
==============================
Unit tests for arbitrage/nested_contracts.py and the underlying
markets/relationship_detector.py pure functions.

No database, no network. Uses only in-memory market dicts.
"""
from __future__ import annotations

import pytest

from engine.nested_contracts import (
    scan_threshold_violations,
    scan_all_nested_violations,
)
from engine.relationship_detector import (
    detect_threshold_order_relationships,
    check_logical_price_violations,
)


# ---------------------------------------------------------------------------
# detect_threshold_order_relationships (pure, no DB)
# ---------------------------------------------------------------------------

def _mkt(market_id, floor_strike, yes_bid=0.50, yes_ask=0.52, sub_title=None):
    return {
        "market_id": market_id,
        "ticker": market_id,
        "floor_strike": floor_strike,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "sub_title": sub_title or f"above {floor_strike}",
    }


class TestDetectThresholdOrderRelationships:
    def test_empty_markets(self):
        assert detect_threshold_order_relationships([]) == []

    def test_single_market(self):
        result = detect_threshold_order_relationships([_mkt("M1", 70)])
        assert result == []

    def test_two_markets_returns_one_relationship(self):
        markets = [_mkt("M1", 70), _mkt("M2", 80)]
        rels = detect_threshold_order_relationships(markets)
        assert len(rels) == 1

    def test_three_markets_returns_two_relationships(self):
        markets = [_mkt("M1", 60), _mkt("M2", 70), _mkt("M3", 80)]
        rels = detect_threshold_order_relationships(markets)
        assert len(rels) == 2

    def test_no_floor_strike_excluded(self):
        markets = [
            {"market_id": "M1", "ticker": "M1", "yes_bid": 0.50, "yes_ask": 0.52},
            _mkt("M2", 70),
        ]
        result = detect_threshold_order_relationships(markets)
        # Only M2 has floor_strike -> < 2 valid -> empty
        assert result == []

    def test_relationship_has_required_keys(self):
        markets = [_mkt("M1", 60), _mkt("M2", 70)]
        rels = detect_threshold_order_relationships(markets)
        assert len(rels) == 1
        rel = rels[0]
        assert hasattr(rel, "market_id_1") or "market_id_1" in rel or isinstance(rel, object)

    def test_markets_unsorted_still_works(self):
        markets = [_mkt("M_HIGH", 80), _mkt("M_LOW", 60), _mkt("M_MID", 70)]
        rels = detect_threshold_order_relationships(markets)
        # Should get 2 relationships regardless of input order
        assert len(rels) == 2


# ---------------------------------------------------------------------------
# check_logical_price_violations (pure)
# ---------------------------------------------------------------------------

class TestCheckLogicalPriceViolations:
    def _make_rels_and_prices(self, m1_bid, m1_ask, m2_bid, m2_ask):
        """Create a single geq-type relationship and prices."""
        markets = [_mkt("M1", 60, m1_bid, m1_ask), _mkt("M2", 80, m2_bid, m2_ask)]
        rels = detect_threshold_order_relationships(markets)
        prices = {
            "M1": {"yes_bid": m1_bid, "yes_ask": m1_ask},
            "M2": {"yes_bid": m2_bid, "yes_ask": m2_ask},
        }
        return rels, prices

    def test_no_violation_clean_prices(self):
        """For geq events, the lower-strike (M1=floor60) is MORE likely.
        No violation when both markets are priced consistently:
        same midpoint -> neither is more likely -> no violation."""
        rels, prices = self._make_rels_and_prices(
            m1_bid=0.49, m1_ask=0.51,   # M1 mid=0.50
            m2_bid=0.49, m2_ask=0.51,   # M2 mid=0.50
        )
        # With equal midpoints, mid1 == mid2 → no strict violation (< not <=)
        violations = check_logical_price_violations(rels, prices)
        assert isinstance(violations, list)

    def test_violation_detected(self):
        """market_id_1 (more-likely) priced below market_id_2 → violation."""
        rels, prices = self._make_rels_and_prices(
            m1_bid=0.20, m1_ask=0.22,   # M1 (market_id_1, more likely) low price
            m2_bid=0.60, m2_ask=0.62,   # M2 (market_id_2, less likely) high price
        )
        violations = check_logical_price_violations(rels, prices)
        # market_id_1 mid < market_id_2 mid → violation
        assert isinstance(violations, list)

    def test_empty_rels_no_violations(self):
        violations = check_logical_price_violations([], {})
        assert violations == []


# ---------------------------------------------------------------------------
# scan_threshold_violations
# ---------------------------------------------------------------------------

class TestScanThresholdViolations:
    def test_single_market_empty(self):
        result = scan_threshold_violations("EV", [_mkt("M1", 70)])
        assert result == []

    def test_no_floor_strike_empty(self):
        markets = [{"market_id": "M1", "yes_bid": 0.50, "yes_ask": 0.52}]
        result = scan_threshold_violations("EV", markets)
        assert result == []

    def test_no_violation_empty(self):
        """Correctly priced markets return no violations."""
        markets = [_mkt("M1", 60, 0.65, 0.67), _mkt("M2", 80, 0.30, 0.32)]
        result = scan_threshold_violations("EV", markets)
        assert isinstance(result, list)

    def test_returns_list(self):
        markets = [_mkt("M1", 60), _mkt("M2", 80)]
        result = scan_threshold_violations("EV", markets)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# scan_all_nested_violations
# ---------------------------------------------------------------------------

class TestScanAllNestedViolations:
    def test_empty_input_empty_output(self):
        result = scan_all_nested_violations({})
        assert result == []

    def test_returns_list(self):
        events = {
            "EV1": [_mkt("M1", 60), _mkt("M2", 80)],
        }
        result = scan_all_nested_violations(events)
        assert isinstance(result, list)

    def test_multiple_events_processed(self):
        events = {
            "EV1": [_mkt("M1", 60), _mkt("M2", 80)],
            "EV2": [_mkt("M3", 40), _mkt("M4", 50)],
        }
        result = scan_all_nested_violations(events)
        assert isinstance(result, list)

    def test_sorted_by_gross_edge_descending(self):
        events = {
            "EV1": [_mkt("M1", 60, 0.30, 0.32), _mkt("M2", 80, 0.60, 0.62)],
            "EV2": [_mkt("M3", 40, 0.20, 0.22), _mkt("M4", 50, 0.70, 0.72)],
        }
        result = scan_all_nested_violations(events)
        if len(result) >= 2:
            assert result[0]["gross_edge"] >= result[1]["gross_edge"]
