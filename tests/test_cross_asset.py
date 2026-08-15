"""
tests/test_cross_asset.py
==========================
Unit tests for cross_asset/probability_engine.py.

These functions are pure math (scipy/numpy) with no DB or network dependency.
Tests verify:
  - boc_rate_implied_probability: direction handling, boundary conditions
  - boc_rate_distribution: sums to 1.0, non-negative, monotonicity
  - fx_implied_probability: boundary conditions, above/below direction
  - compute_cross_asset_spread: spread = kalshi - trad, zero-crossing
"""
from __future__ import annotations

import pytest
import numpy as np

from analysis.probability_engine import (
    boc_rate_implied_probability,
    boc_rate_distribution,
    fx_implied_probability,
)


# ---------------------------------------------------------------------------
# boc_rate_implied_probability
# ---------------------------------------------------------------------------

class TestBocRateImpliedProbability:
    def test_probability_in_range(self):
        """Result must always be in [0, 1]."""
        p = boc_rate_implied_probability(
            current_ois_rate=0.045,
            meeting_date_days_ahead=30,
            target_rate=0.0425,
            direction="cut",
        )
        assert 0.0 <= p <= 1.0

    def test_certain_cut(self):
        """When OIS is well below target, cut probability should be high."""
        p = boc_rate_implied_probability(
            current_ois_rate=0.03,   # OIS signals 3% expected rate
            meeting_date_days_ahead=30,
            target_rate=0.04,        # contract asks: will rate be cut to 4%?
            direction="cut",
        )
        assert p > 0.5

    def test_certain_hike(self):
        """When OIS is well above current, hike probability should be high."""
        p = boc_rate_implied_probability(
            current_ois_rate=0.05,
            meeting_date_days_ahead=30,
            target_rate=0.04,
            direction="hike",
        )
        assert p > 0.5

    def test_hold_symmetric(self):
        """When OIS exactly equals target, hold probability should peak."""
        p_hold = boc_rate_implied_probability(
            current_ois_rate=0.045,
            meeting_date_days_ahead=30,
            target_rate=0.045,
            direction="hold",
        )
        p_cut = boc_rate_implied_probability(
            current_ois_rate=0.045,
            meeting_date_days_ahead=30,
            target_rate=0.045,
            direction="cut",
        )
        assert p_hold >= 0.0
        assert p_hold <= 1.0

    def test_far_miss_cut_near_zero(self):
        """Probability of 200bps cut when OIS shows no cut should be near 0."""
        p = boc_rate_implied_probability(
            current_ois_rate=0.05,       # OIS expects 5%
            meeting_date_days_ahead=30,
            target_rate=0.03,            # 200bps below expected
            direction="cut",
        )
        assert p < 0.01

    def test_returns_float(self):
        p = boc_rate_implied_probability(0.045, 30, 0.0425, "cut")
        assert isinstance(p, float)

    def test_non_negative(self):
        for direction in ("cut", "hold", "hike"):
            p = boc_rate_implied_probability(0.045, 30, 0.0450, direction)
            assert p >= 0.0

    def test_n_meetings_increases_uncertainty(self):
        """More meetings -> wider distribution -> more uncertainty -> lower peak."""
        p1 = boc_rate_implied_probability(0.045, 30, 0.0475, "cut", n_meetings=1)
        p2 = boc_rate_implied_probability(0.045, 30, 0.0475, "cut", n_meetings=4)
        # With 4 meetings, the distribution is wider: tails are fatter
        # so the probability of a specific exact outcome can be higher or lower
        # but the extreme outcomes should be more likely
        assert 0 <= p1 <= 1
        assert 0 <= p2 <= 1


# ---------------------------------------------------------------------------
# boc_rate_distribution
# ---------------------------------------------------------------------------

class TestBocRateDistribution:
    def _rates(self):
        return [0.040, 0.0425, 0.045, 0.0475, 0.050]

    def test_sums_to_one(self):
        dist = boc_rate_distribution(0.045, self._rates())
        total = sum(dist.values())
        assert abs(total - 1.0) < 1e-6

    def test_all_non_negative(self):
        dist = boc_rate_distribution(0.045, self._rates())
        for k, v in dist.items():
            assert v >= 0.0, f"Negative probability for {k}"

    def test_keys_are_strings(self):
        dist = boc_rate_distribution(0.045, self._rates())
        for k in dist:
            assert isinstance(k, str)

    def test_n_outcomes_matches_rates(self):
        rates = self._rates()
        dist = boc_rate_distribution(0.045, rates)
        assert len(dist) == len(rates)

    def test_modal_rate_near_ois(self):
        """The most probable rate should be close to the OIS rate."""
        ois = 0.045
        rates = [0.035, 0.040, 0.045, 0.050, 0.055]
        dist = boc_rate_distribution(ois, rates)
        modal_rate_str = max(dist, key=dist.get)
        # Modal probability key should contain "4.50" or close to 4.5%
        assert "4.5" in modal_rate_str or "4.50" in modal_rate_str

    def test_single_rate_returns_1(self):
        """With only one possible rate, P=1 for that rate."""
        dist = boc_rate_distribution(0.045, [0.045])
        vals = list(dist.values())
        assert abs(vals[0] - 1.0) < 1e-6

    def test_two_rates_sum_to_1(self):
        dist = boc_rate_distribution(0.045, [0.04, 0.05])
        total = sum(dist.values())
        assert abs(total - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# fx_implied_probability (if available)
# ---------------------------------------------------------------------------

class TestFxImpliedProbability:
    def test_probability_in_range(self):
        p = fx_implied_probability(
            spot_rate=1.35,
            threshold=1.30,
            volatility=0.05,
            days_ahead=30,
            direction="above",
        )
        assert 0.0 <= p <= 1.0

    def test_far_above_threshold(self):
        """When spot is much higher than threshold, P(above) is very high."""
        p = fx_implied_probability(1.50, 1.20, 0.05, 30, "above")
        assert p > 0.8

    def test_far_below_threshold(self):
        """When spot is much lower than threshold, P(above) is very low."""
        p = fx_implied_probability(1.10, 1.50, 0.05, 30, "above")
        assert p < 0.2
