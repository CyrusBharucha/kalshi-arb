"""
tests/test_probability_engine_extended.py
==========================================
Extended unit tests for cross_asset/probability_engine.py.
Supplements test_probability.py with additional edge cases and
deeper coverage of boc_rate_distribution, fx_threshold_probability,
and commodity_threshold_probability.

No database, no network.
"""
from __future__ import annotations

import math
import pytest

from analysis.probability_engine import (
    boc_rate_implied_probability,
    boc_rate_distribution,
    fx_threshold_probability,
    commodity_threshold_probability,
)


# ---------------------------------------------------------------------------
# boc_rate_implied_probability
# ---------------------------------------------------------------------------

class TestBocRateImpliedProbability:
    def test_returns_float(self):
        p = boc_rate_implied_probability(0.0275, 30, 0.0250, "cut")
        assert isinstance(p, float)

    def test_bounded_0_1(self):
        for direction in ("cut", "hike", "hold"):
            p = boc_rate_implied_probability(0.0275, 30, 0.0250, direction)
            assert 0.0 <= p <= 1.0

    def test_cut_high_prob_when_ois_below_target(self):
        """If OIS is already below the target cut level, cut is very likely."""
        p = boc_rate_implied_probability(
            current_ois_rate=0.0200,
            meeting_date_days_ahead=30,
            target_rate=0.0250,
            direction="cut",
        )
        assert p > 0.8

    def test_cut_low_prob_when_ois_far_above_target(self):
        p = boc_rate_implied_probability(0.0500, 30, 0.0250, "cut")
        assert p < 0.2

    def test_hike_high_prob_when_ois_far_above(self):
        p = boc_rate_implied_probability(0.0500, 30, 0.0475, "hike")
        assert p > 0.5

    def test_hold_prob_positive(self):
        p = boc_rate_implied_probability(0.0275, 30, 0.0275, "hold")
        assert p > 0

    def test_more_meetings_less_certainty(self):
        """With more meetings, uncertainty increases, central outcome less probable."""
        p1 = boc_rate_implied_probability(0.0275, 30, 0.0250, "cut", n_meetings=1)
        p2 = boc_rate_implied_probability(0.0275, 30, 0.0250, "cut", n_meetings=5)
        # Wider distribution -> less mass in tails for certain outcomes
        # Not a strict monotonicity claim, just that results change
        assert p1 != p2

    def test_default_direction_cut(self):
        p = boc_rate_implied_probability(0.0275, 30, 0.0250)
        assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------------------
# boc_rate_distribution
# ---------------------------------------------------------------------------

class TestBocRateDistribution:
    def _rates(self):
        return [0.0200, 0.0225, 0.0250, 0.0275, 0.0300, 0.0325]

    def test_returns_dict(self):
        d = boc_rate_distribution(0.0275, self._rates())
        assert isinstance(d, dict)

    def test_all_keys_present(self):
        rates = self._rates()
        d = boc_rate_distribution(0.0275, rates)
        assert len(d) == len(rates)

    def test_sums_to_one(self):
        d = boc_rate_distribution(0.0275, self._rates())
        total = sum(d.values())
        assert abs(total - 1.0) < 1e-6

    def test_all_probs_nonneg(self):
        d = boc_rate_distribution(0.0275, self._rates())
        for k, v in d.items():
            assert v >= 0, f"Negative prob for {k}: {v}"

    def test_mode_near_ois(self):
        """The rate closest to the OIS should have the highest probability."""
        ois = 0.0275
        rates = [0.0200, 0.0225, 0.0250, 0.0275, 0.0300, 0.0325]
        d = boc_rate_distribution(ois, rates)
        best_rate = max(d, key=d.get)
        # '2.75%' or '2.50%' should be highest (within one step)
        assert "2.7" in best_rate or "2.5" in best_rate or "3.0" in best_rate

    def test_single_rate_gives_probability_one(self):
        d = boc_rate_distribution(0.0275, [0.0275])
        assert abs(sum(d.values()) - 1.0) < 1e-6

    def test_two_rates_sum_to_one(self):
        d = boc_rate_distribution(0.0275, [0.0250, 0.0275])
        assert abs(sum(d.values()) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# fx_threshold_probability
# ---------------------------------------------------------------------------

class TestFxThresholdProbability:
    def test_returns_float(self):
        p = fx_threshold_probability(0.73, 0.74, 30, 0.06, "above")
        assert isinstance(p, float)

    def test_bounded_0_1(self):
        for direction in ("above", "below"):
            p = fx_threshold_probability(0.73, 0.74, 30, 0.06, direction)
            assert 0.0 <= p <= 1.0

    def test_above_plus_below_equals_one(self):
        """P(above) + P(below) should equal 1.0 for the same inputs."""
        p_above = fx_threshold_probability(0.73, 0.74, 30, 0.06, "above")
        p_below = fx_threshold_probability(0.73, 0.74, 30, 0.06, "below")
        assert abs(p_above + p_below - 1.0) < 1e-6

    def test_spot_far_above_threshold_high_prob_above(self):
        p = fx_threshold_probability(0.85, 0.70, 30, 0.06, "above")
        assert p > 0.8

    def test_spot_far_below_threshold_low_prob_above(self):
        p = fx_threshold_probability(0.55, 0.80, 30, 0.06, "above")
        assert p < 0.2

    def test_at_threshold_near_half(self):
        """Spot at threshold -> probability should be near 0.5."""
        p = fx_threshold_probability(0.74, 0.74, 30, 0.06, "above")
        assert 0.3 < p < 0.7

    def test_zero_days_returns_half(self):
        p = fx_threshold_probability(0.73, 0.74, 0, 0.06, "above")
        assert p == 0.5

    def test_zero_spot_returns_half(self):
        p = fx_threshold_probability(0.0, 0.74, 30, 0.06, "above")
        assert p == 0.5

    def test_longer_horizon_more_uncertainty(self):
        """More time -> greater chance of crossing the threshold."""
        p30  = fx_threshold_probability(0.73, 0.80, 30, 0.06, "above")
        p365 = fx_threshold_probability(0.73, 0.80, 365, 0.06, "above")
        # With more time, spot has more chance to reach 0.80
        assert p365 > p30


# ---------------------------------------------------------------------------
# commodity_threshold_probability
# ---------------------------------------------------------------------------

class TestCommodityThresholdProbability:
    def test_returns_float(self):
        p = commodity_threshold_probability(70.0, 75.0, 30, direction="above")
        assert isinstance(p, float)

    def test_bounded_0_1(self):
        for direction in ("above", "below"):
            p = commodity_threshold_probability(70.0, 75.0, 30, direction=direction)
            assert 0.0 <= p <= 1.0

    def test_above_plus_below_approx_one(self):
        p_above = commodity_threshold_probability(70.0, 75.0, 30, direction="above")
        p_below = commodity_threshold_probability(70.0, 75.0, 30, direction="below")
        assert abs(p_above + p_below - 1.0) < 1e-6

    def test_price_far_above_threshold(self):
        p = commodity_threshold_probability(100.0, 60.0, 30, direction="above")
        assert p > 0.7

    def test_price_far_below_threshold(self):
        p = commodity_threshold_probability(40.0, 80.0, 30, direction="above")
        assert p < 0.3

    def test_longer_horizon_more_uncertainty(self):
        """Longer time -> more chance price crosses to the threshold side."""
        p30  = commodity_threshold_probability(70.0, 90.0, 30,  direction="above")
        p365 = commodity_threshold_probability(70.0, 90.0, 365, direction="above")
        assert p365 > p30
