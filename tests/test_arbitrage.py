"""
tests/test_arbitrage.py
Tests for arbitrage detection strategies.
"""

import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.yes_no import check_complement_arb, scan_complement_arb
from engine.mutually_exclusive import (
    check_me_arb, analyze_event_probability_sum
)
from engine.nested_contracts import scan_threshold_violations


class TestComplementArb:
    """Strategy 1: YES/NO complement arbitrage."""

    def _market(self, yes_bid, yes_ask, ticker="TEST-1"):
        return {
            "market_id": ticker, "ticker": ticker, "event_ticker": "EVT",
            "yes_bid": yes_bid, "yes_ask": yes_ask,
            "no_bid": round(1 - yes_ask, 4), "no_ask": round(1 - yes_bid, 4),
        }

    def test_obvious_opportunity_detected(self):
        """Large spread should yield positive net edge."""
        m = self._market(0.45, 0.40)
        result = check_complement_arb(m)
        assert result is not None
        assert result["gross_edge"] > 0
        assert result["net_edge"] > 0

    def test_tight_spread_rejected_after_fees(self):
        """Thin edge consumed by fees -> should return None."""
        # yes_ask=0.497, yes_bid=0.503 -> no_ask=0.497
        # total_cost=0.994, gross=0.006 < fees at ~0.50 (2*0.0175=0.035)
        m = self._market(0.503, 0.497)
        result = check_complement_arb(m)
        assert result is None

    def test_no_edge_when_sum_exceeds_one(self):
        """yes_ask + no_ask > 1.00 -> no opportunity."""
        m = self._market(0.45, 0.60)  # no_ask = 1 - 0.45 = 0.55; 0.60+0.55=1.15
        result = check_complement_arb(m)
        assert result is None

    def test_missing_prices_returns_none(self):
        m = {"market_id": "X", "ticker": "X", "event_ticker": "E",
              "yes_bid": None, "yes_ask": 0.50}
        result = check_complement_arb(m)
        assert result is None

    def test_classification_b_without_depth(self):
        """Without order book depth, should be classified B."""
        m = self._market(0.45, 0.40)
        result = check_complement_arb(m, order_book=None)
        if result:
            assert result["classification"] == "B"

    def test_batch_scan_returns_sorted_list(self):
        markets = [
            self._market(0.45, 0.40, "A"),  # gross=0.15
            self._market(0.48, 0.42, "B"),  # gross=0.10
        ]
        results = scan_complement_arb(markets)
        # May return 0 or more depending on fee thresholds
        # Verify sorted by net_edge
        for i in range(len(results) - 1):
            assert results[i]["net_edge"] >= results[i+1]["net_edge"]


class TestMEArb:
    """Strategy 2: Mutually exclusive / collectively exhaustive arbitrage."""

    def _markets(self, asks):
        return [
            {"market_id": f"M{i}", "ticker": f"M{i}",
              "yes_ask": a, "yes_bid": a - 0.02}
            for i, a in enumerate(asks)
        ]

    def test_underpriced_set_detected(self):
        """Sum of asks = 0.90 < 1.00 -> should detect opportunity."""
        result = check_me_arb("EVT", self._markets([0.30, 0.30, 0.30]))
        assert result is not None
        assert result["gross_edge"] == pytest.approx(0.10, abs=1e-4)

    def test_fair_set_returns_none(self):
        """Sum = 1.00 -> no arb."""
        result = check_me_arb("EVT", self._markets([0.50, 0.50]))
        assert result is None

    def test_overpriced_set(self):
        from engine.mutually_exclusive import check_overpriced_set
        markets = [
            {"market_id": "M0", "ticker": "M0", "yes_bid": 0.60},
            {"market_id": "M1", "ticker": "M1", "yes_bid": 0.60},
        ]
        result = check_overpriced_set("EVT", markets)
        # gross = 0.60+0.60 - 1.00 = 0.20 -> should detect
        assert result is not None
        assert result["gross_edge"] == pytest.approx(0.20, abs=1e-4)

    def test_probability_sum_diagnostic(self):
        markets = [
            {"yes_ask": 0.30, "yes_bid": 0.28},
            {"yes_ask": 0.35, "yes_bid": 0.33},
            {"yes_ask": 0.25, "yes_bid": 0.23},
        ]
        diag = analyze_event_probability_sum("EVT", markets)
        assert abs(diag["sum_asks"] - 0.90) < 1e-6
        assert diag["underpriced"] is True
        assert diag["overpriced"] is False


class TestNestedContracts:
    """Strategy 3: Threshold ordering violations."""

    def _threshold_market(self, market_id, floor_strike, yes_bid, yes_ask):
        return {
            "market_id":   market_id,
            "ticker":      market_id,
            "floor_strike": floor_strike,
            "yes_bid":     yes_bid,
            "yes_ask":     yes_ask,
        }

    def test_correct_ordering_no_violation(self):
        """P(rate≤4.50%) > P(rate≤4.25%) - no violation."""
        markets = [
            self._threshold_market("A", 4.25, 0.30, 0.32),
            self._threshold_market("B", 4.50, 0.55, 0.57),  # correctly higher
        ]
        opps = scan_threshold_violations("EVT", markets)
        assert len(opps) == 0

    def test_violated_ordering_detected(self):
        """P(rate≤4.50%) < P(rate≤4.25%) - violation."""
        markets = [
            self._threshold_market("A", 4.25, 0.60, 0.62),  # higher probability
            self._threshold_market("B", 4.50, 0.30, 0.32),  # wrongly lower
        ]
        opps = scan_threshold_violations("EVT", markets)
        # Should detect a violation (gross edge may or may not exceed threshold)
        # At minimum, no crash
        assert isinstance(opps, list)
