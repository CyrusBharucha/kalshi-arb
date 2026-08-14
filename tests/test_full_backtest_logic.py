"""
tests/test_full_backtest_logic.py
==================================
Unit tests for pure-logic functions in backtest/full_backtest.py:
  - simulate_trade

No database, no network.
"""
from __future__ import annotations

import math
import pandas as pd
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Patch DB imports at module level
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    repo_mock = MagicMock()
    repo_mock.session_scope = MagicMock()
    models_mock = MagicMock()

    with patch.dict(__import__("sys").modules, {
        "database.repository": repo_mock,
        "database": MagicMock(),
        "database.models": models_mock,
        "sqlalchemy.dialects.postgresql": MagicMock(),
    }):
        yield


# ---------------------------------------------------------------------------
# Helper: build a minimal opportunity Series
# ---------------------------------------------------------------------------

def _opp(strategy="yes_no_complement", gross_edge=0.05, total_fees=0.003,
         m1="MKT-A", m2="MKT-B", p1=0.44, p2=0.50):
    return pd.Series({
        "opportunity_id": "opp-001",
        "detected_at":    datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        "strategy_type":  strategy,
        "classification": "B",
        "markets_involved": [m1, m2],
        "prices_json":    {"p1": p1, "p2": p2},
        "gross_edge":     gross_edge,
        "total_fees":     total_fees,
        "estimated_slippage": 0.001,
        "net_edge":       gross_edge - total_fees - 0.001,
    })


# ---------------------------------------------------------------------------
# simulate_trade
# ---------------------------------------------------------------------------

class TestSimulateTradeBasic:
    def _sim(self, opp, settlement_values=None, run_id="bt_test"):
        from backtest.full_backtest import simulate_trade
        return simulate_trade(opp, settlement_values or {}, run_id)

    def test_returns_list(self):
        result = self._sim(_opp())
        assert isinstance(result, list)

    def test_returns_at_least_one_trade(self):
        result = self._sim(_opp())
        assert len(result) >= 1

    def test_run_id_propagated(self):
        result = self._sim(_opp(), run_id="bt_MY_RUN")
        for t in result:
            assert t["run_id"] == "bt_MY_RUN"

    def test_market_id_from_first_market(self):
        result = self._sim(_opp(m1="MKT-ALPHA", m2="MKT-BETA"))
        assert result[0]["market_id"] == "MKT-ALPHA"

    def test_quantity_is_contracts_constant(self):
        from backtest.full_backtest import CONTRACTS
        result = self._sim(_opp())
        assert result[0]["quantity"] == float(CONTRACTS)

    def test_gross_pnl_positive_for_positive_edge(self):
        result = self._sim(_opp(gross_edge=0.10))
        assert result[0]["gross_pnl"] > 0

    def test_net_pnl_less_than_gross_pnl(self):
        result = self._sim(_opp(gross_edge=0.10, total_fees=0.005))
        trade = result[0]
        assert trade["net_pnl"] < trade["gross_pnl"]

    def test_taker_fees_nonnegative(self):
        result = self._sim(_opp())
        assert result[0]["taker_fees"] >= 0

    def test_slippage_nonnegative(self):
        result = self._sim(_opp())
        assert result[0]["slippage"] >= 0

    def test_entry_ts_is_datetime(self):
        result = self._sim(_opp())
        from datetime import datetime as dt_class
        assert isinstance(result[0]["entry_ts"], dt_class)

    def test_action_is_strategy_type(self):
        result = self._sim(_opp(strategy="yes_no_complement"))
        assert result[0]["action"] == "yes_no_complement"


class TestSimulateTradeEdgeCases:
    def _sim(self, opp, settlement_values=None, run_id="bt_test"):
        from backtest.full_backtest import simulate_trade
        return simulate_trade(opp, settlement_values or {}, run_id)

    def test_missing_markets_returns_empty(self):
        opp = _opp()
        opp["markets_involved"] = []
        result = self._sim(opp)
        assert result == []

    def test_single_market_returns_empty(self):
        opp = _opp()
        opp["markets_involved"] = ["MKT-ONLY"]
        result = self._sim(opp)
        assert result == []

    def test_none_markets_returns_empty(self):
        opp = _opp()
        opp["markets_involved"] = None
        result = self._sim(opp)
        assert result == []

    def test_zero_gross_edge_produces_zero_gross_pnl(self):
        result = self._sim(_opp(gross_edge=0.0, total_fees=0.0))
        assert result[0]["gross_pnl"] == 0.0

    def test_with_settlement_data_class_a_note(self):
        sv = {"MKT-A": 1.0}
        result = self._sim(_opp(), settlement_values=sv)
        notes = result[0]["notes"]
        assert "class=A" in notes

    def test_without_settlement_data_class_b_note(self):
        result = self._sim(_opp())
        notes = result[0]["notes"]
        assert "class=B" in notes

    def test_mutually_exclusive_strategy_short_side(self):
        result = self._sim(_opp(strategy="mutually_exclusive"))
        assert result[0]["side"] == "short"

    def test_complement_strategy_long_side(self):
        result = self._sim(_opp(strategy="yes_no_complement"))
        assert result[0]["side"] == "long"

    def test_net_pnl_rounded_to_6dp(self):
        result = self._sim(_opp(gross_edge=0.123456789))
        net = result[0]["net_pnl"]
        # Should be rounded to 6 decimal places
        assert abs(net - round(net, 6)) < 1e-10

    def test_large_gross_edge_large_pnl(self):
        from backtest.full_backtest import CONTRACTS
        result = self._sim(_opp(gross_edge=0.20, total_fees=0.001))
        expected_gross = 0.20 * CONTRACTS
        assert abs(result[0]["gross_pnl"] - expected_gross) < 1e-6

    def test_notes_contains_strategy_name(self):
        result = self._sim(_opp(strategy="threshold_order"))
        assert "threshold_order" in result[0]["notes"]

    def test_maker_fees_always_zero(self):
        """Backtest models only taker fees at entry."""
        result = self._sim(_opp())
        assert result[0]["maker_fees"] == 0.0
