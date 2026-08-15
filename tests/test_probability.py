"""
tests/test_probability.py
Tests for probability conversion / cross-asset engine.
"""

import pytest
import math

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from analysis.probability_engine import (
    boc_rate_implied_probability,
    boc_rate_distribution,
    fx_threshold_probability,
    commodity_threshold_probability,
    compute_cross_market_spread,
    kalshi_midpoint,
)


class TestBOCRateProbability:
    """BOC policy rate probability engine."""

    def test_at_money_cut_probability(self):
        """When OIS = target, probability of hold should be near 50%."""
        # Current OIS = 4.25%, target = 4.25% (hold) - at-money
        p = boc_rate_implied_probability(
            current_ois_rate=0.0425,
            meeting_date_days_ahead=14,
            target_rate=0.0425,
            direction="hold",
            rate_step_size=0.0025,
        )
        # With 25bp step centred at OIS, hold probability should be substantial
        assert 0.05 < p < 0.95   # reasonable range

    def test_probability_in_valid_range(self):
        for ois in [0.02, 0.03, 0.04, 0.05]:
            for target in [0.03, 0.04, 0.05]:
                p = boc_rate_implied_probability(ois, 14, target, "cut")
                assert 0.0 <= p <= 1.0

    def test_distribution_sums_to_one(self):
        """Full distribution across all outcomes must sum to 1.0."""
        rates = [0.0325, 0.0350, 0.0375, 0.0400, 0.0425]
        dist = boc_rate_distribution(0.0375, rates)
        total = sum(dist.values())
        assert abs(total - 1.0) < 1e-6

    def test_distribution_keys_are_strings(self):
        rates = [0.04, 0.0425, 0.045]
        dist = boc_rate_distribution(0.0425, rates)
        for k in dist.keys():
            assert isinstance(k, str)
            assert "%" in k

    def test_certainty_case(self):
        """OIS much higher than target -> very low probability of cut to that level."""
        # OIS at 5%, target = 4% (100bps below): P(cut to ≤4%) with 25bp uncertainty
        # z = (0.04 - 0.05) / 0.0025 = -40 -> essentially 0
        p = boc_rate_implied_probability(0.05, 14, 0.04, "cut", rate_step_size=0.0025)
        assert p < 0.01


class TestFXProbability:
    """FX threshold probability (log-normal digital)."""

    def test_at_money_is_near_half(self):
        """When spot = strike, probability should be near 50%."""
        p = fx_threshold_probability(0.74, 0.74, 30, 0.06, "above")
        assert 0.35 < p < 0.65

    def test_deep_itm_above(self):
        """Spot >> strike -> P(above) -> 1.0."""
        p = fx_threshold_probability(0.90, 0.70, 30, 0.06, "above")
        assert p > 0.9

    def test_deep_otm_above(self):
        """Spot << strike -> P(above) -> 0."""
        p = fx_threshold_probability(0.60, 0.80, 30, 0.06, "above")
        assert p < 0.1

    def test_above_plus_below_sum_to_one(self):
        """P(above) + P(below) ≈ 1.0 for continuous distribution."""
        p_above = fx_threshold_probability(0.74, 0.74, 30, 0.06, "above")
        p_below = fx_threshold_probability(0.74, 0.74, 30, 0.06, "below")
        assert abs(p_above + p_below - 1.0) < 0.01   # not exactly 1 due to discreteness


class TestCrossMarketSpread:
    """Cross-asset spread calculations."""

    def test_zero_spread(self):
        result = compute_cross_market_spread(0.60, 0.60)
        assert result["spread"] == pytest.approx(0.0, abs=1e-10)
        assert not result["significant_5pct"]

    def test_large_spread_flagged(self):
        result = compute_cross_market_spread(0.70, 0.55)
        assert result["spread"] == pytest.approx(0.15, abs=1e-6)
        assert result["significant_5pct"]
        assert result["significant_10pct"]

    def test_log_odds_computed(self):
        result = compute_cross_market_spread(0.60, 0.50)
        assert result["log_odds_diff"] is not None
        assert isinstance(result["log_odds_diff"], float)

    def test_kalshi_midpoint(self):
        mid = kalshi_midpoint(0.55, 0.65)
        assert mid == pytest.approx(0.60, abs=1e-10)

    def test_kalshi_midpoint_with_none(self):
        assert kalshi_midpoint(None, 0.60) is None
        assert kalshi_midpoint(0.60, None) is None
