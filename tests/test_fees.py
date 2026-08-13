"""
tests/test_fees.py
Tests for the fee calculation module.
These are the most critical unit tests - fee errors directly corrupt P&L calculations.
"""

import math
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.fees import (
    taker_fee_per_contract,
    maker_fee_per_contract,
    total_round_trip_fee,
    compute_multi_leg_fees,
    FEE_CAP,
)


class TestTakerFee:
    """Taker fee: ceil(0.07 * P * (1 - P) * 100) / 100, capped at $0.035."""

    def test_50_cent_max_fee(self):
        """At P=0.50, fee = ceil(0.07*0.50*0.50*100)/100 = ceil(1.75)/100 = 2/100 = 0.02"""
        fee = taker_fee_per_contract(0.50)
        assert fee == pytest.approx(0.02, abs=1e-10)

    def test_20_cent(self):
        """At P=0.20, fee = ceil(0.07*0.20*0.80*100)/100 = ceil(1.12)/100 = 2/100 = 0.02"""
        fee = taker_fee_per_contract(0.20)
        assert fee == pytest.approx(0.02, abs=1e-10)

    def test_10_cent(self):
        """At P=0.10, fee = ceil(0.07*0.10*0.90*100)/100 = ceil(0.63)/100 = 1/100 = 0.01"""
        fee = taker_fee_per_contract(0.10)
        assert fee == pytest.approx(0.01, abs=1e-10)

    def test_90_cent_symmetric_with_10_cent(self):
        """Fee formula is symmetric: P and 1-P give same fee."""
        fee_10 = taker_fee_per_contract(0.10)
        fee_90 = taker_fee_per_contract(0.90)
        assert fee_10 == pytest.approx(fee_90, abs=1e-10)

    def test_cap_at_max(self):
        """Fee must never exceed FEE_CAP ($0.035)."""
        for p in [0.01, 0.10, 0.50, 0.90, 0.99]:
            assert taker_fee_per_contract(p) <= FEE_CAP + 1e-9

    def test_monotone_toward_50_cents(self):
        """Fee increases as price moves toward 50c."""
        assert taker_fee_per_contract(0.10) < taker_fee_per_contract(0.30)
        assert taker_fee_per_contract(0.30) <= taker_fee_per_contract(0.50)

    def test_invalid_price_raises(self):
        with pytest.raises(ValueError):
            taker_fee_per_contract(0.0)
        with pytest.raises(ValueError):
            taker_fee_per_contract(1.0)
        with pytest.raises(ValueError):
            taker_fee_per_contract(-0.1)

    def test_maker_less_than_taker(self):
        """Maker fee must always be ≤ taker fee."""
        for p in [0.10, 0.20, 0.30, 0.40, 0.50]:
            assert maker_fee_per_contract(p) <= taker_fee_per_contract(p)


class TestRoundTripFee:
    """Round-trip fee for a complement trade."""

    def test_complement_at_50_50(self):
        """Buying YES at 0.50 and NO at 0.50: both fees = 0.02 each = 0.04 total."""
        fee = total_round_trip_fee(0.50, 0.50, contracts=1.0)
        assert fee == pytest.approx(0.04, abs=1e-10)

    def test_scales_with_contracts(self):
        fee_1 = total_round_trip_fee(0.30, 0.70, contracts=1.0)
        fee_10 = total_round_trip_fee(0.30, 0.70, contracts=10.0)
        assert fee_10 == pytest.approx(fee_1 * 10, rel=1e-6)

    def test_multi_leg(self):
        legs = [
            {"price": 0.33, "contracts": 5, "is_taker": True},
            {"price": 0.33, "contracts": 5, "is_taker": True},
            {"price": 0.34, "contracts": 5, "is_taker": True},
        ]
        total = compute_multi_leg_fees(legs)
        manual = (
            taker_fee_per_contract(0.33) * 5 +
            taker_fee_per_contract(0.33) * 5 +
            taker_fee_per_contract(0.34) * 5
        )
        assert total == pytest.approx(manual, rel=1e-6)


class TestNetEdgeAfterFees:
    """Verify that fee deduction correctly determines executability."""

    def test_obvious_arb_remains_profitable(self):
        """0.40 + 0.40 = 0.80 cost; gross edge = 0.20; should be clearly net positive."""
        from engine.simulator import ExecutionSimulator
        sim = ExecutionSimulator()
        result = sim.simulate_complement_arb("TEST", yes_ask=0.40, no_ask=0.40)
        assert result.gross_edge == pytest.approx(0.20, abs=1e-4)
        assert result.net_edge > 0.0
        assert result.is_profitable()

    def test_thin_arb_killed_by_fees(self):
        """0.495 + 0.495 = 0.990 cost; gross = 0.010; fees ≈ 0.035 -> net negative."""
        from engine.simulator import ExecutionSimulator
        sim = ExecutionSimulator()
        result = sim.simulate_complement_arb("TEST", yes_ask=0.495, no_ask=0.495)
        assert result.gross_edge == pytest.approx(0.010, abs=1e-4)
        # Fees (2 legs at 50c) = 0.0175 * 2 = 0.035 > gross edge
        assert result.net_edge < 0.0
        assert not result.is_profitable()
        assert result.classification == "D"
