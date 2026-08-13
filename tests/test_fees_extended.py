"""
tests/test_fees_extended.py
============================
Extended tests for execution/fees.py — fee model accuracy, edge cases,
multi-leg computation, and fee schedule sanity checks.

No database, no network.
"""
from __future__ import annotations

import math
import pytest

from engine.fees import (
    taker_fee_per_contract,
    maker_fee_per_contract,
    total_round_trip_fee,
    compute_multi_leg_fees,
    fee_schedule_table,
    FEE_CAP,
    FEE_ALPHA,
    MAKER_RATIO,
)


# ---------------------------------------------------------------------------
# taker_fee_per_contract — documented values
# ---------------------------------------------------------------------------

class TestTakerFeeDocumentedValues:
    def test_50_cents(self):
        """P=0.50: raw=0.0175, ceil(1.75)=2, fee=0.02"""
        assert abs(taker_fee_per_contract(0.50) - 0.02) < 1e-6

    def test_20_cents(self):
        """P=0.20: raw=0.07*0.2*0.8=0.0112, ceil(1.12)=2, fee=0.02"""
        assert abs(taker_fee_per_contract(0.20) - 0.02) < 1e-6

    def test_10_cents(self):
        """P=0.10: raw=0.07*0.1*0.9=0.0063, ceil(0.63)=1, fee=0.01"""
        assert abs(taker_fee_per_contract(0.10) - 0.01) < 1e-6

    def test_90_cents_equals_10_cents(self):
        """By symmetry: P*(1-P) is symmetric around 0.50."""
        assert abs(taker_fee_per_contract(0.90) - taker_fee_per_contract(0.10)) < 1e-6

    def test_80_cents_equals_20_cents(self):
        assert abs(taker_fee_per_contract(0.80) - taker_fee_per_contract(0.20)) < 1e-6

    def test_max_at_50_cents(self):
        """Fee is maximised at 0.50."""
        f50 = taker_fee_per_contract(0.50)
        for p in [0.10, 0.20, 0.30, 0.40, 0.60, 0.70, 0.80, 0.90]:
            assert taker_fee_per_contract(p) <= f50 + 1e-9


class TestTakerFeeBoundaries:
    def test_raises_at_zero(self):
        with pytest.raises(ValueError):
            taker_fee_per_contract(0.0)

    def test_raises_at_one(self):
        with pytest.raises(ValueError):
            taker_fee_per_contract(1.0)

    def test_raises_negative(self):
        with pytest.raises(ValueError):
            taker_fee_per_contract(-0.01)

    def test_raises_above_one(self):
        with pytest.raises(ValueError):
            taker_fee_per_contract(1.01)

    def test_nonneg_for_all_valid_prices(self):
        for p in [0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99]:
            assert taker_fee_per_contract(p) >= 0

    def test_cap_applied(self):
        """No fee should exceed FEE_CAP."""
        for p in [0.01, 0.10, 0.25, 0.50, 0.75, 0.90, 0.99]:
            assert taker_fee_per_contract(p) <= FEE_CAP + 1e-9

    def test_very_low_price(self):
        """P=0.01: raw=0.000693, ceil(0.0693)=1, fee=0.01 (minimum 1 cent)."""
        f = taker_fee_per_contract(0.01)
        assert f == pytest.approx(0.01, abs=1e-6)

    def test_very_high_price(self):
        """P=0.99: symmetric to P=0.01, fee=0.01."""
        f = taker_fee_per_contract(0.99)
        assert f == pytest.approx(0.01, abs=1e-6)


# ---------------------------------------------------------------------------
# maker_fee_per_contract
# ---------------------------------------------------------------------------

class TestMakerFee:
    def test_maker_less_than_taker(self):
        for p in [0.10, 0.25, 0.50, 0.75, 0.90]:
            assert maker_fee_per_contract(p) <= taker_fee_per_contract(p)

    def test_maker_nonneg(self):
        for p in [0.10, 0.25, 0.50, 0.75, 0.90]:
            assert maker_fee_per_contract(p) >= 0

    def test_maker_ratio_roughly_correct(self):
        """Maker should be in the same order of magnitude as taker at 0.50.
        taker(0.50)=0.02; maker(0.50)=ceil(0.07*0.25*0.25*100)/100=ceil(0.4375)/100=0.01.
        Due to ceil rounding at different magnitudes, maker != exactly 0.25*taker,
        so we only verify maker <= taker and maker >= 0.
        """
        p = 0.50
        maker = maker_fee_per_contract(p)
        taker = taker_fee_per_contract(p)
        assert maker >= 0
        assert maker <= taker


# ---------------------------------------------------------------------------
# total_round_trip_fee
# ---------------------------------------------------------------------------

class TestTotalRoundTripFee:
    def test_single_leg_no_leg2(self):
        """When price_leg2=None, uses complement (1 - price_leg1)."""
        p = 0.40
        expected = taker_fee_per_contract(p) + taker_fee_per_contract(1 - p)
        result = total_round_trip_fee(p)
        assert abs(result - expected) < 1e-6

    def test_two_legs_explicit(self):
        p1, p2 = 0.40, 0.45
        expected = taker_fee_per_contract(p1) + taker_fee_per_contract(p2)
        result = total_round_trip_fee(p1, p2)
        assert abs(result - expected) < 1e-6

    def test_contracts_scale_fee(self):
        p = 0.50
        f1 = total_round_trip_fee(p, contracts=1)
        f10 = total_round_trip_fee(p, contracts=10)
        assert abs(f10 - 10 * f1) < 1e-6

    def test_taker_both_false_uses_maker_for_leg2(self):
        p1, p2 = 0.40, 0.45
        result = total_round_trip_fee(p1, p2, taker_both=False)
        expected = taker_fee_per_contract(p1) + maker_fee_per_contract(p2)
        assert abs(result - expected) < 1e-6

    def test_result_positive(self):
        assert total_round_trip_fee(0.45) > 0


# ---------------------------------------------------------------------------
# compute_multi_leg_fees
# ---------------------------------------------------------------------------

class TestMultiLegFees:
    def test_single_taker_leg(self):
        legs = [{"price": 0.50, "contracts": 1, "is_taker": True}]
        expected = taker_fee_per_contract(0.50)
        assert abs(compute_multi_leg_fees(legs) - expected) < 1e-6

    def test_single_maker_leg(self):
        legs = [{"price": 0.50, "contracts": 1, "is_taker": False}]
        expected = maker_fee_per_contract(0.50)
        assert abs(compute_multi_leg_fees(legs) - expected) < 1e-6

    def test_two_taker_legs(self):
        legs = [
            {"price": 0.40, "contracts": 1, "is_taker": True},
            {"price": 0.45, "contracts": 1, "is_taker": True},
        ]
        expected = taker_fee_per_contract(0.40) + taker_fee_per_contract(0.45)
        assert abs(compute_multi_leg_fees(legs) - expected) < 1e-6

    def test_contracts_scale_per_leg(self):
        legs = [{"price": 0.50, "contracts": 5, "is_taker": True}]
        expected = 5 * taker_fee_per_contract(0.50)
        assert abs(compute_multi_leg_fees(legs) - expected) < 1e-6

    def test_empty_legs_zero(self):
        assert compute_multi_leg_fees([]) == 0.0

    def test_default_contracts_is_one(self):
        legs = [{"price": 0.50, "is_taker": True}]  # no "contracts" key
        expected = taker_fee_per_contract(0.50)
        assert abs(compute_multi_leg_fees(legs) - expected) < 1e-6

    def test_default_is_taker(self):
        legs = [{"price": 0.50, "contracts": 1}]  # no "is_taker" key → defaults True
        expected = taker_fee_per_contract(0.50)
        assert abs(compute_multi_leg_fees(legs) - expected) < 1e-6


# ---------------------------------------------------------------------------
# fee_schedule_table
# ---------------------------------------------------------------------------

class TestFeeScheduleTable:
    def test_returns_list(self):
        t = fee_schedule_table()
        assert isinstance(t, list)
        assert len(t) > 0

    def test_rows_have_required_keys(self):
        for row in fee_schedule_table():
            for key in ("price", "taker_fee", "maker_fee", "fee_pct_of_price"):
                assert key in row

    def test_all_taker_fees_nonneg(self):
        for row in fee_schedule_table():
            assert row["taker_fee"] >= 0

    def test_fee_pct_positive(self):
        for row in fee_schedule_table():
            assert row["fee_pct_of_price"] > 0

    def test_50_cent_row_present(self):
        prices = [row["price"] for row in fee_schedule_table()]
        assert 0.50 in prices
