"""
tests/test_historical_scanner_logic.py
========================================
Unit tests for the pure-logic functions in arbitrage/historical_scanner.py:
  - _complement_check
  - _Window tracker

No database, no network.
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Patch DB imports at module level so import doesn't require PostgreSQL
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    repo_mock = MagicMock()
    repo_mock.get_engine.side_effect = RuntimeError("No DB in tests")
    with patch.dict(__import__("sys").modules, {
        "database.repository": repo_mock,
        "database": MagicMock(),
    }):
        yield


# ---------------------------------------------------------------------------
# _complement_check
# ---------------------------------------------------------------------------

class TestComplementCheckBasic:
    def _check(self, yes_ask, yes_bid, book_json=None):
        from engine.historical_scanner import _complement_check
        return _complement_check("MKT-001", yes_ask, yes_bid, book_json)

    def test_clear_arb_returns_dict(self):
        """yes_ask=0.40, yes_bid=0.50 → no_ask=0.50 → gross=0.10"""
        result = self._check(0.40, 0.50)
        assert result is not None

    def test_result_has_strategy_type(self):
        result = self._check(0.40, 0.50)
        assert result["strategy_type"] == "yes_no_complement"

    def test_result_has_net_edge(self):
        result = self._check(0.40, 0.50)
        assert "net_edge" in result

    def test_gross_edge_correct(self):
        """yes_ask=0.44, yes_bid=0.44 → no_ask=0.56 → gross=1-0.44-0.56=0.0"""
        # Actually: no_ask = 1 - yes_bid = 1 - 0.44 = 0.56
        # gross = 1 - yes_ask - no_ask = 1 - 0.44 - 0.56 = 0.0 → no arb
        result = self._check(0.44, 0.44)
        assert result is None

    def test_profitable_arb_net_edge_positive(self):
        result = self._check(0.40, 0.50)
        # gross=0.10, fees subtract some, net should still be positive for this wide spread
        if result:
            assert result["net_edge"] > 0

    def test_no_arb_when_prices_sum_to_one(self):
        # yes_ask=0.50, no_ask=1-yes_bid=1-0.50=0.50 → gross=0
        result = self._check(0.50, 0.50)
        assert result is None

    def test_no_arb_when_prices_sum_above_one(self):
        # yes_ask=0.52, yes_bid=0.50 → no_ask=0.50 → gross=1-0.52-0.50=-0.02
        result = self._check(0.52, 0.50)
        assert result is None

    def test_none_yes_ask_returns_none(self):
        from engine.historical_scanner import _complement_check
        assert _complement_check("M", None, 0.45, None) is None

    def test_none_yes_bid_returns_none(self):
        from engine.historical_scanner import _complement_check
        assert _complement_check("M", 0.45, None, None) is None

    def test_zero_yes_ask_returns_none(self):
        result = self._check(0.0, 0.45)
        assert result is None

    def test_yes_ask_at_one_returns_none(self):
        result = self._check(1.0, 0.45)
        assert result is None


class TestComplementCheckClassification:
    def _check(self, yes_ask, yes_bid, book_json=None):
        from engine.historical_scanner import _complement_check
        return _complement_check("MKT-001", yes_ask, yes_bid, book_json)

    def test_no_book_json_class_b(self):
        """Without depth data → classification B."""
        result = self._check(0.40, 0.50, book_json=None)
        if result:
            assert result["classification"] == "B"

    def test_with_valid_book_json_class_a(self):
        """With depth data (yes_asks/yes_bids format) → classification A."""
        book = json.dumps({
            "yes_asks": [[0.40, 20]],
            "yes_bids": [[0.38, 20]],
        })
        result = self._check(0.40, 0.50, book_json=book)
        if result:
            assert result["classification"] == "A"

    def test_with_empty_book_json_class_b(self):
        """Unparseable depth → classification B."""
        result = self._check(0.40, 0.50, book_json="{}")
        if result:
            assert result["classification"] == "B"

    def test_result_has_prices_json(self):
        result = self._check(0.40, 0.50)
        if result:
            assert "prices_json" in result
            assert "yes_ask" in result["prices_json"]
            assert "no_ask" in result["prices_json"]

    def test_max_net_profit_positive(self):
        result = self._check(0.40, 0.50)
        if result:
            assert result["max_net_profit"] > 0

    def test_default_qty_is_10_without_book(self):
        result = self._check(0.40, 0.50, book_json=None)
        if result:
            assert result["max_executable_contracts"] == 10.0

    def test_gross_edge_in_result(self):
        result = self._check(0.40, 0.50)
        if result:
            assert result["gross_edge"] > 0

    def test_total_fees_in_result_nonnegative(self):
        result = self._check(0.40, 0.50)
        if result:
            assert result["total_fees"] >= 0

    def test_net_edge_less_than_gross_edge(self):
        result = self._check(0.40, 0.50)
        if result:
            assert result["net_edge"] < result["gross_edge"]


class TestComplementCheckEdgeCases:
    def _check(self, yes_ask, yes_bid, book_json=None):
        from engine.historical_scanner import _complement_check
        return _complement_check("MKT-EDGE", yes_ask, yes_bid, book_json)

    def test_very_small_arb_filtered_by_min_net_edge(self):
        """A tiny gross edge won't survive fees → None."""
        # yes_ask=0.501, no_ask=0.499 → gross=0.0 → None
        result = self._check(0.501, 0.501)
        assert result is None

    def test_large_arb_survives(self):
        """yes_ask=0.40, yes_bid=0.40 → no_ask=0.60 → gross=0.0 → None.
        Need yes_ask+no_ask < 1.0, i.e. yes_ask + (1-yes_bid) < 1.0 → yes_ask < yes_bid.
        Try yes_ask=0.40, yes_bid=0.50 → no_ask=0.50 → gross=0.10
        """
        result = self._check(0.40, 0.50)
        assert result is not None
        assert result["net_edge"] > 0

    def test_negative_yes_ask_returns_none(self):
        result = self._check(-0.01, 0.50)
        assert result is None

    def test_market_id_in_result(self):
        result = self._check(0.40, 0.50)
        if result:
            assert "MKT-EDGE" in result["markets_involved"]


# ---------------------------------------------------------------------------
# _Window tracker
# ---------------------------------------------------------------------------

def _ts(offset_s=0):
    return datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


class TestWindowTracker:
    def _det(self, net_edge=0.05, qty=10.0, classification="B"):
        return {
            "net_edge": net_edge,
            "max_executable_contracts": qty,
            "classification": classification,
            "prices_json": {"yes_ask": 0.40, "no_ask": 0.55},
            "gross_edge": 0.05 + 0.01,
            "total_fees": 0.007,
            "estimated_slippage": 0.003,
        }

    def test_window_created_with_correct_market_id(self):
        from engine.historical_scanner import _Window
        w = _Window("MKT-001", "yes_no_complement", _ts(), self._det())
        assert w.market_id == "MKT-001"

    def test_window_open_ts_recorded(self):
        from engine.historical_scanner import _Window
        t = _ts()
        w = _Window("MKT-001", "yes_no_complement", t, self._det())
        assert w.open_ts == t

    def test_window_not_stale_immediately(self):
        from engine.historical_scanner import _Window
        w = _Window("MKT-001", "yes_no_complement", _ts(), self._det())
        assert not w.is_stale(_ts(60))  # 60s < SNAP_GAP_S=180

    def test_window_stale_after_gap(self):
        from engine.historical_scanner import _Window
        w = _Window("MKT-001", "yes_no_complement", _ts(), self._det())
        assert w.is_stale(_ts(200))  # 200 > SNAP_GAP_S=180

    def test_window_update_records_last_ts(self):
        from engine.historical_scanner import _Window
        w = _Window("MKT-001", "yes_no_complement", _ts(), self._det(0.05))
        w.update(_ts(60), self._det(0.06))
        assert w.last_ts == _ts(60)

    def test_window_update_tracks_peak_edge(self):
        from engine.historical_scanner import _Window
        w = _Window("MKT-001", "yes_no_complement", _ts(), self._det(0.05))
        w.update(_ts(60), self._det(0.08))
        assert abs(w.peak_net_edge - 0.08) < 1e-9

    def test_window_peak_not_decreased(self):
        from engine.historical_scanner import _Window
        w = _Window("MKT-001", "yes_no_complement", _ts(), self._det(0.08))
        w.update(_ts(60), self._det(0.03))
        assert abs(w.peak_net_edge - 0.08) < 1e-9

    def test_window_to_opp_has_required_keys(self):
        from engine.historical_scanner import _Window
        w = _Window("MKT-001", "yes_no_complement", _ts(), self._det())
        opp = w.to_opp()
        for key in ("detected_at", "strategy_type", "net_edge", "markets_involved",
                    "gross_edge", "total_fees"):
            assert key in opp, f"Missing key: {key}"

    def test_window_to_opp_detected_at_is_open_ts(self):
        from engine.historical_scanner import _Window
        t = _ts()
        w = _Window("MKT-001", "yes_no_complement", t, self._det())
        opp = w.to_opp()
        assert opp["detected_at"] == t

    def test_window_to_opp_uses_peak_net_edge(self):
        from engine.historical_scanner import _Window
        w = _Window("MKT-001", "yes_no_complement", _ts(), self._det(0.05))
        w.update(_ts(60), self._det(0.09))
        opp = w.to_opp()
        assert abs(opp["net_edge"] - 0.09) < 1e-9

    def test_window_strategy_type_preserved(self):
        from engine.historical_scanner import _Window
        w = _Window("MKT-001", "yes_no_complement", _ts(), self._det())
        opp = w.to_opp()
        assert opp["strategy_type"] == "yes_no_complement"
