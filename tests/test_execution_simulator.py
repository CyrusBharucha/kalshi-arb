"""
tests/test_execution_simulator.py
===================================
Unit tests for execution/simulator.py — ExecutionSimulator, Leg, ExecutionResult.

No database, no network, no API keys. Pure arithmetic logic.
"""
from __future__ import annotations

import math
import pytest

from engine.simulator import ExecutionSimulator, Leg, ExecutionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIM = ExecutionSimulator()


def _complement(yes_ask: float, no_ask: float, **kw) -> ExecutionResult:
    return SIM.simulate_complement_arb("KXTEST-01", yes_ask, no_ask, **kw)


# ---------------------------------------------------------------------------
# Leg dataclass
# ---------------------------------------------------------------------------

class TestLeg:
    def test_fee_nonzero_for_taker(self):
        leg = Leg("MKT", "yes", "buy", 0.50, 100, is_taker=True)
        assert leg.fee > 0

    def test_fee_zero_for_maker(self):
        leg = Leg("MKT", "yes", "buy", 0.50, 100, is_taker=False)
        assert leg.fee == 0.0

    def test_cost_positive_for_buy(self):
        leg = Leg("MKT", "yes", "buy", 0.50, 100)
        # buying costs cash: price + fee > 0
        assert leg.cost_per_contract > 0

    def test_cost_negative_for_sell(self):
        leg = Leg("MKT", "yes", "sell", 0.50, 100)
        # selling brings in cash: -(price - fee) < 0
        assert leg.cost_per_contract < 0

    def test_cost_buy_equals_price_plus_fee(self):
        leg = Leg("MKT", "yes", "buy", 0.45, 50)
        assert abs(leg.cost_per_contract - (0.45 + leg.fee)) < 1e-9

    def test_cost_sell_equals_minus_price_minus_fee(self):
        leg = Leg("MKT", "yes", "sell", 0.60, 50)
        expected = -(0.60 - leg.fee)
        assert abs(leg.cost_per_contract - expected) < 1e-9


# ---------------------------------------------------------------------------
# ExecutionResult helpers
# ---------------------------------------------------------------------------

class TestExecutionResult:
    def _make(self, net_edge: float, max_net_profit: float) -> ExecutionResult:
        return ExecutionResult(
            strategy_type="yes_no_complement",
            classification="A",
            markets_involved=["M1"],
            legs=[],
            gross_edge=0.10,
            total_fees=0.01,
            bid_ask_cost=0.0,
            estimated_slippage=0.005,
            net_edge=net_edge,
            max_executable=10,
            max_gross_profit=1.0,
            max_net_profit=max_net_profit,
        )

    def test_is_profitable_true(self):
        r = self._make(net_edge=0.05, max_net_profit=0.50)
        assert r.is_profitable() is True

    def test_is_profitable_false_negative_net(self):
        r = self._make(net_edge=-0.01, max_net_profit=-0.10)
        assert r.is_profitable() is False

    def test_is_profitable_false_zero_net(self):
        r = self._make(net_edge=0.0, max_net_profit=0.0)
        assert r.is_profitable() is False

    def test_to_db_dict_has_required_keys(self):
        r = self._make(0.05, 0.50)
        d = r.to_db_dict()
        for key in ("strategy_type", "classification", "markets_involved",
                    "gross_edge", "total_fees", "net_edge",
                    "max_executable_contracts", "max_net_profit"):
            assert key in d, f"Missing key: {key}"

    def test_to_db_dict_rounds_edge(self):
        r = self._make(0.0499999999, 0.499999)
        d = r.to_db_dict()
        # Should be rounded to 6 decimal places
        assert len(str(d["net_edge"]).split(".")[-1]) <= 6


# ---------------------------------------------------------------------------
# simulate_complement_arb — no edge cases
# ---------------------------------------------------------------------------

class TestComplementArbNoEdge:
    def test_no_edge_at_exactly_1(self):
        r = _complement(0.50, 0.50)
        assert r.classification == "D"
        assert r.net_edge <= 0

    def test_no_edge_above_1(self):
        r = _complement(0.60, 0.45)
        assert r.classification == "D"

    def test_raises_on_zero_price(self):
        with pytest.raises((ValueError, Exception)):
            _complement(0.0, 0.50)

    def test_raises_on_negative_price(self):
        with pytest.raises((ValueError, Exception)):
            _complement(-0.01, 0.50)


# ---------------------------------------------------------------------------
# simulate_complement_arb — positive edge
# ---------------------------------------------------------------------------

class TestComplementArbEdge:
    def test_gross_edge_correct(self):
        r = _complement(0.45, 0.48)
        expected_gross = 1.0 - 0.45 - 0.48
        assert abs(r.gross_edge - expected_gross) < 1e-9

    def test_net_edge_less_than_gross(self):
        r = _complement(0.45, 0.48)
        assert r.net_edge < r.gross_edge

    def test_classification_b_without_depth(self):
        r = _complement(0.45, 0.48)
        # No depth → B (if profitable)
        assert r.classification in ("B", "D")
        if r.net_edge > 0:
            assert r.classification == "B"

    def test_classification_a_with_depth(self):
        r = _complement(0.40, 0.45, yes_depth=200, no_depth=300)
        if r.net_edge > 0:
            assert r.classification == "A"

    def test_max_executable_no_depth_conservative(self):
        r = _complement(0.45, 0.48)
        # Without depth, uses conservative default (10)
        assert r.max_executable == 10.0

    def test_max_executable_with_depth_min_of_legs(self):
        r = _complement(0.40, 0.45, yes_depth=100, no_depth=50)
        assert r.max_executable == 50.0

    def test_max_gross_profit_equals_gross_times_qty(self):
        r = _complement(0.40, 0.45, yes_depth=100, no_depth=80)
        expected = r.gross_edge * r.max_executable
        assert abs(r.max_gross_profit - expected) < 1e-6

    def test_two_legs_returned(self):
        r = _complement(0.42, 0.44)
        assert len(r.legs) == 2

    def test_legs_are_both_buys(self):
        r = _complement(0.42, 0.44)
        for leg in r.legs:
            assert leg.action == "buy"

    def test_prices_snapshot_has_market(self):
        r = _complement(0.42, 0.44)
        assert "KXTEST-01" in r.prices_snapshot

    def test_strategy_type(self):
        r = _complement(0.42, 0.44)
        assert r.strategy_type == "yes_no_complement"


# ---------------------------------------------------------------------------
# simulate_mutually_exclusive_arb
# ---------------------------------------------------------------------------

class TestMutuallyExclusiveArb:
    def _markets(self, prices):
        return [{"market_id": f"M{i}", "yes_ask": p} for i, p in enumerate(prices)]

    def test_no_edge_sums_to_1(self):
        r = SIM.simulate_mutually_exclusive_arb(self._markets([0.34, 0.34, 0.34]))
        # 0.34*3=1.02 → no edge
        assert r.net_edge <= 0

    def test_gross_edge_correct(self):
        prices = [0.20, 0.25, 0.30]
        r = SIM.simulate_mutually_exclusive_arb(self._markets(prices))
        expected_gross = 1.0 - sum(prices)
        assert abs(r.gross_edge - expected_gross) < 1e-9

    def test_strategy_type_is_me(self):
        r = SIM.simulate_mutually_exclusive_arb(self._markets([0.20, 0.25, 0.30]))
        assert "mutually_exclusive" in r.strategy_type

    def test_raises_empty_input(self):
        with pytest.raises((ValueError, Exception)):
            SIM.simulate_mutually_exclusive_arb([])

    def test_fees_positive(self):
        r = SIM.simulate_mutually_exclusive_arb(self._markets([0.20, 0.25]))
        assert r.total_fees > 0

    def test_net_less_than_gross(self):
        r = SIM.simulate_mutually_exclusive_arb(self._markets([0.20, 0.25, 0.30]))
        if r.gross_edge > 0:
            assert r.net_edge < r.gross_edge

    def test_all_markets_in_snapshot(self):
        markets = self._markets([0.20, 0.25, 0.30])
        r = SIM.simulate_mutually_exclusive_arb(markets)
        for m in markets:
            assert m["market_id"] in r.markets_involved


# ---------------------------------------------------------------------------
# Slippage model
# ---------------------------------------------------------------------------

class TestSlippageModel:
    def test_lower_slippage_with_depth(self):
        r_no_depth  = _complement(0.40, 0.42)
        r_with_depth = _complement(0.40, 0.42, yes_depth=100, no_depth=100)
        # slippage should be lower when depth is known
        assert r_with_depth.estimated_slippage <= r_no_depth.estimated_slippage

    def test_slippage_scales_with_price(self):
        r_low  = _complement(0.10, 0.12)
        r_high = _complement(0.45, 0.48)
        # Higher priced legs → higher absolute slippage
        assert r_high.estimated_slippage > r_low.estimated_slippage

    def test_slippage_is_nonnegative(self):
        r = _complement(0.45, 0.48)
        assert r.estimated_slippage >= 0
