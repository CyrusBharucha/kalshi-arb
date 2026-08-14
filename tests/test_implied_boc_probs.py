"""
tests/test_implied_boc_probs.py
=================================
Tests for data/external_market_data.py::compute_implied_boc_probabilities.

Key implementation detail:
  probs[f"{rate:.2f}%"] = prob
  → keys are the raw float formatted as f"{rate:.2f}%"
    (e.g. 0.04 → "0.04%", 0.05 → "0.05%")
  → rates spaced < 0.01 apart can collide; tests use rates ≥ 0.01 apart.

This is a pure math function — no DB, no network.
"""
from __future__ import annotations

import math
import pytest
from datetime import datetime, timezone, timedelta


# Use rates spaced 0.01 (100bp) apart so f"{rate:.2f}%" never collides
_RATES_3 = [0.03, 0.04, 0.05]       # 3%, 4%, 5%
_RATES_5 = [0.03, 0.04, 0.05, 0.06, 0.07]
_OIS = 0.045


def _call(meeting_date=None, possible_rates=None, current_ois_rate=_OIS):
    from feeds.external_market_data import compute_implied_boc_probabilities
    if meeting_date is None:
        meeting_date = datetime.now(timezone.utc) + timedelta(days=30)
    if possible_rates is None:
        possible_rates = _RATES_5
    return compute_implied_boc_probabilities(meeting_date, possible_rates, current_ois_rate)


class TestComputeImpliedBocProbabilities:
    def test_returns_dict(self):
        assert isinstance(_call(), dict)

    def test_keys_match_number_of_rates(self):
        # 5 rates spaced 100bp → 5 distinct "X.XX%" keys
        result = _call(possible_rates=_RATES_5)
        assert len(result) == len(_RATES_5)

    def test_keys_end_with_percent_sign(self):
        for key in _call():
            assert key.endswith("%")

    def test_all_probs_between_0_and_1(self):
        for k, v in _call().items():
            assert 0.0 <= v <= 1.0

    def test_probs_sum_to_1(self):
        total = sum(_call().values())
        assert abs(total - 1.0) < 1e-9

    def test_ois_rate_gets_some_probability(self):
        """Rate at OIS should get non-zero probability."""
        ois = 0.05   # 5%
        rates = [0.03, 0.04, 0.05, 0.06, 0.07]
        result = _call(possible_rates=rates, current_ois_rate=ois)
        # The "0.05%" key should exist and have positive probability
        assert "0.05%" in result
        assert result["0.05%"] > 0.0

    def test_single_rate_gets_full_probability(self):
        result = _call(possible_rates=[0.04])
        assert len(result) == 1
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_two_rates_sum_to_1(self):
        result = _call(possible_rates=[0.04, 0.05])
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_probabilities_not_negative(self):
        for v in _call().values():
            assert v >= 0.0

    def test_key_format_numeric(self):
        for key in _call():
            # Should parse cleanly as float
            val = float(key.rstrip("%"))
            assert val >= 0.0

    def test_ois_below_all_rates_gives_highest_prob_to_lowest(self):
        """If OIS is far below all rates, lowest rate should have highest prob."""
        ois = 0.01   # 1%, well below all rates
        rates = [0.04, 0.05, 0.06]
        result = _call(possible_rates=rates, current_ois_rate=ois)
        mode_key = max(result, key=result.get)
        mode_val = float(mode_key.rstrip("%"))
        # Lowest rate is 0.04
        assert abs(mode_val - 0.04) < 1e-9

    def test_ois_above_all_rates_gives_highest_prob_to_highest(self):
        """If OIS is far above all rates, highest rate should have highest prob."""
        ois = 0.20   # 20%, way above all rates
        rates = [0.04, 0.05, 0.06]
        result = _call(possible_rates=rates, current_ois_rate=ois)
        mode_key = max(result, key=result.get)
        mode_val = float(mode_key.rstrip("%"))
        # Highest rate is 0.06
        assert abs(mode_val - 0.06) < 1e-9

    def test_symmetry_equidistant_rates(self):
        """Two rates equidistant from OIS should get similar probs."""
        ois = 0.05   # midpoint between 0.04 and 0.06
        rates = [0.04, 0.06]
        result = _call(possible_rates=rates, current_ois_rate=ois)
        vals = list(result.values())
        assert abs(vals[0] - vals[1]) < 0.05  # within 5%

    def test_meeting_date_irrelevant(self):
        """This model ignores meeting_date — results stable across dates."""
        date1 = datetime.now(timezone.utc) + timedelta(days=7)
        date2 = datetime.now(timezone.utc) + timedelta(days=365)
        r1 = _call(meeting_date=date1)
        r2 = _call(meeting_date=date2)
        for k in r1:
            assert abs(r1[k] - r2[k]) < 1e-9

    def test_three_rates_sum_to_1(self):
        result = _call(possible_rates=_RATES_3)
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_dict_has_no_extra_keys(self):
        """Result should not have more keys than rates."""
        result = _call(possible_rates=_RATES_3)
        assert len(result) <= len(_RATES_3)
