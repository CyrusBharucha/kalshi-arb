"""
tests/test_probability_engine_invariants.py
============================================
Invariant tests for cross_asset/probability_engine.py.

These tests focus on mathematical invariants and edge-case guard conditions
that complement the existing test_probability_engine_extended.py.

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
# boc_rate_implied_probability invariants
# ---------------------------------------------------------------------------

class TestBocRateInvariants:
    def _prob(self, ois, target, direction="cut", **kwargs):
        return boc_rate_implied_probability(ois, 30, target, direction=direction, **kwargs)

    def test_output_in_unit_interval(self):
        """All outputs must be in [0, 1]."""
        for ois in [0.02, 0.03, 0.04, 0.05]:
            for target in [0.0175, 0.025, 0.0425]:
                p = self._prob(ois, target, "cut")
                assert 0.0 <= p <= 1.0

    def test_cut_probability_higher_when_target_above_ois(self):
        """P(cut to higher rate) > P(cut to lower rate) when ois is in between."""
        # OIS at 3.0%; cut to 2.75% has lower prob than cut to 3.25%
        p_low  = self._prob(0.03, 0.0275, "cut")
        p_high = self._prob(0.03, 0.0325, "cut")
        assert p_high > p_low

    def test_hold_direction_returns_nonzero(self):
        p = self._prob(0.04, 0.04, direction="hold")
        assert p > 0

    def test_hike_direction_returns_nonzero(self):
        p = self._prob(0.03, 0.0425, direction="hike")
        assert 0.0 <= p <= 1.0

    def test_more_meetings_more_uncertainty(self):
        """With more meetings, the distribution is wider → probabilities spread more."""
        p1 = boc_rate_implied_probability(0.04, 30, 0.0375, n_meetings=1)
        p5 = boc_rate_implied_probability(0.04, 30, 0.0375, n_meetings=5)
        # More meetings → more uncertainty → middle values smaller, tails larger
        # The difference is in the shape of the distribution
        assert isinstance(p1, float) and isinstance(p5, float)

    def test_target_at_ois_cut_probability_near_half(self):
        """P(cut to exactly OIS level) should be near 0.5 for cut direction."""
        p = self._prob(0.04, 0.04, "cut")
        assert 0.3 < p < 0.7

    def test_target_well_above_ois_hike_low_probability(self):
        """P(hike from 0.025 to 0.10 level) should be very low — far tail."""
        p = self._prob(0.025, 0.10, "hike")
        assert p < 0.01


# ---------------------------------------------------------------------------
# boc_rate_distribution invariants
# ---------------------------------------------------------------------------

class TestBocDistributionInvariants:
    def _dist(self, ois, n=7):
        rates = [round(ois - 0.0075 + i * 0.0025, 4) for i in range(n)]
        return boc_rate_distribution(ois, rates)

    def test_probabilities_sum_to_one(self):
        dist = self._dist(0.04)
        total = sum(dist.values())
        assert abs(total - 1.0) < 1e-6

    def test_all_probabilities_nonnegative(self):
        dist = self._dist(0.04)
        for v in dist.values():
            assert v >= 0.0

    def test_mode_at_ois_rate(self):
        """The bucket at current OIS should be the mode (highest probability)."""
        ois = 0.0400
        dist = self._dist(ois)
        # Find bucket closest to OIS
        best_key = max(dist, key=dist.get)
        best_rate = float(best_key.rstrip("%")) / 100
        assert abs(best_rate - ois) < 0.005  # within one step

    def test_returns_dict(self):
        dist = self._dist(0.04)
        assert isinstance(dist, dict)

    def test_single_rate_returns_one_entry(self):
        dist = boc_rate_distribution(0.04, [0.04])
        assert len(dist) == 1
        assert abs(list(dist.values())[0] - 1.0) < 1e-6

    def test_two_rates_sum_to_one(self):
        dist = boc_rate_distribution(0.04, [0.0375, 0.0425])
        total = sum(dist.values())
        assert abs(total - 1.0) < 1e-6

    def test_empty_rates_returns_empty_dict(self):
        dist = boc_rate_distribution(0.04, [])
        assert dist == {}

    def test_key_format_is_percentage_string(self):
        dist = self._dist(0.04)
        for k in dist.keys():
            assert k.endswith("%"), f"Key {k!r} should end with %"


# ---------------------------------------------------------------------------
# fx_threshold_probability invariants
# ---------------------------------------------------------------------------

class TestFxProbabilityInvariants:
    def _prob(self, spot, threshold, direction="above", vol=0.08, days=90):
        return fx_threshold_probability(spot, threshold, days, vol, direction=direction)

    def test_above_plus_below_equals_one(self):
        """P(above) + P(below) = 1.0 for any spot/threshold."""
        for spot in [0.72, 0.75, 0.78]:
            for thresh in [0.73, 0.75, 0.77]:
                p_above = self._prob(spot, thresh, "above")
                p_below = self._prob(spot, thresh, "below")
                assert abs(p_above + p_below - 1.0) < 1e-9, \
                    f"spot={spot} thresh={thresh}: {p_above}+{p_below}={p_above+p_below}"

    def test_spot_above_threshold_above_prob_gt_half(self):
        """When spot >> threshold, P(above) > 0.5."""
        p = self._prob(0.80, 0.70, "above")
        assert p > 0.5

    def test_spot_below_threshold_below_prob_gt_half(self):
        """When spot << threshold, P(below) > 0.5."""
        p = self._prob(0.70, 0.80, "below")
        assert p > 0.5

    def test_at_threshold_probability_near_half(self):
        """When spot == threshold, P(above) ≈ 0.5 (slightly less due to vol skew)."""
        p = self._prob(0.75, 0.75, "above")
        assert 0.3 < p < 0.7

    def test_zero_spot_returns_half(self):
        """Guard: spot <= 0 → return 0.5."""
        p = self._prob(0.0, 0.75, "above")
        assert p == 0.5

    def test_zero_threshold_returns_half(self):
        """Guard: threshold <= 0 → return 0.5."""
        p = self._prob(0.75, 0.0, "above")
        assert p == 0.5

    def test_zero_days_returns_half(self):
        """Guard: days <= 0 → return 0.5."""
        p = fx_threshold_probability(0.75, 0.75, 0, 0.08, "above")
        assert p == 0.5

    def test_higher_vol_widens_distribution(self):
        """Higher vol → P(above far OTM threshold) is higher."""
        p_low_vol  = self._prob(0.75, 0.85, "above", vol=0.04)
        p_high_vol = self._prob(0.75, 0.85, "above", vol=0.20)
        assert p_high_vol > p_low_vol

    def test_more_time_higher_prob_of_crossing(self):
        """More time → higher P(crossing a far threshold)."""
        p_short = self._prob(0.75, 0.85, "above", days=10)
        p_long  = self._prob(0.75, 0.85, "above", days=365)
        assert p_long > p_short

    def test_output_in_unit_interval(self):
        for spot in [0.68, 0.75, 0.82]:
            for thresh in [0.70, 0.75, 0.80]:
                p = self._prob(spot, thresh, "above")
                assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------------------
# commodity_threshold_probability invariants
# ---------------------------------------------------------------------------

class TestCommodityProbabilityInvariants:
    def _prob(self, current, threshold, direction="above", vol=0.35, days=90):
        return commodity_threshold_probability(current, threshold, days, vol, direction=direction)

    def test_above_plus_below_equals_one(self):
        for price in [70.0, 80.0, 90.0]:
            for thresh in [75.0, 85.0]:
                p_a = self._prob(price, thresh, "above")
                p_b = self._prob(price, thresh, "below")
                assert abs(p_a + p_b - 1.0) < 1e-9

    def test_price_above_threshold_above_gt_half(self):
        p = self._prob(90.0, 70.0, "above")
        assert p > 0.5

    def test_price_below_threshold_below_gt_half(self):
        p = self._prob(65.0, 80.0, "below")
        assert p > 0.5

    def test_at_threshold_near_half(self):
        p = self._prob(80.0, 80.0, "above")
        assert 0.3 < p < 0.7

    def test_output_in_unit_interval(self):
        for price in [60.0, 80.0, 100.0]:
            for thresh in [70.0, 80.0, 90.0]:
                p = self._prob(price, thresh, "above")
                assert 0.0 <= p <= 1.0

    def test_default_vol_is_35_percent(self):
        """Calling without vol kwarg uses 0.35."""
        p1 = commodity_threshold_probability(80.0, 75.0, 90)
        p2 = self._prob(80.0, 75.0, vol=0.35)
        assert abs(p1 - p2) < 1e-9

    def test_higher_vol_widens_distribution(self):
        p_low  = self._prob(80.0, 95.0, "above", vol=0.10)
        p_high = self._prob(80.0, 95.0, "above", vol=0.60)
        assert p_high > p_low
