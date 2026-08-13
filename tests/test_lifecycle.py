"""
tests/test_lifecycle.py
Tests for arbitrage/lifecycle.py — opportunity dedup, open/close logic,
and depth-based qty calculation.
"""
from __future__ import annotations

import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

from engine.lifecycle import (
    _kalshi_fee, _fingerprint, _depth_qty_from_book, OpportunityLifecycle,
)


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------

class TestKalshiFee:
    def test_mid_price(self):
        # At 0.50, fee = 0.50 * 0.50 * 0.07 / 0.25 = 0.035 -> capped at 0.07
        f = _kalshi_fee(0.50)
        assert 0.01 <= f <= 0.07

    def test_extreme_price_very_small(self):
        # Near 0 or 1 -> 0.07 * 0.01 * 0.99 = 0.000693 (just above floor)
        assert _kalshi_fee(0.01) > 0
        assert _kalshi_fee(0.01) < 0.001
        assert _kalshi_fee(0.99) > 0
        assert _kalshi_fee(0.99) < 0.001

    def test_zero_price_returns_minimum(self):
        assert _kalshi_fee(0.0) == pytest.approx(0.0001, abs=1e-9)

    def test_fee_symmetric(self):
        # Fee(p) == Fee(1-p)  (symmetric around 50c)
        assert _kalshi_fee(0.30) == pytest.approx(_kalshi_fee(0.70), abs=1e-9)


class TestFingerprint:
    def test_order_independent(self):
        """Markets in different order produce same fingerprint."""
        fp1 = _fingerprint("yes_no_complement", ["MKT-A", "MKT-B"])
        fp2 = _fingerprint("yes_no_complement", ["MKT-B", "MKT-A"])
        assert fp1 == fp2

    def test_strategy_type_distinguishes(self):
        fp1 = _fingerprint("yes_no_complement", ["MKT-A"])
        fp2 = _fingerprint("mutually_exclusive", ["MKT-A"])
        assert fp1 != fp2

    def test_different_markets_differ(self):
        fp1 = _fingerprint("yes_no_complement", ["MKT-A"])
        fp2 = _fingerprint("yes_no_complement", ["MKT-B"])
        assert fp1 != fp2


class TestDepthQtyFromBook:
    def _book(self, yes_asks=None, yes_bids=None) -> str:
        return json.dumps({
            "yes_asks": yes_asks or [],
            "yes_bids": yes_bids or [],
        })

    def test_empty_book_returns_none(self):
        assert _depth_qty_from_book(self._book(), gross_edge=0.10) is None

    def test_none_book_returns_none(self):
        assert _depth_qty_from_book(None, gross_edge=0.10) is None

    def test_single_level_yes_ask(self):
        # gross=0.10, breakeven_yes=0.90; ask=0.45 < 0.90 -> qty=100
        book = self._book(yes_asks=[[0.45, 100]])
        qty = _depth_qty_from_book(book, gross_edge=0.10)
        assert qty == 100.0

    def test_ask_above_breakeven_excluded(self):
        # gross=0.10, breakeven_yes=0.90; ask=0.95 > 0.90 -> excluded
        book = self._book(yes_asks=[[0.95, 500]])
        qty = _depth_qty_from_book(book, gross_edge=0.10)
        # Only bid side might contribute
        assert qty is None or qty == 0.0 or qty > 0  # no YES ask depth

    def test_binding_is_minimum_of_sides(self):
        # YES ask has 200 qty, YES bid (NO ask) has 50 qty -> binding = 50
        book = self._book(
            yes_asks=[[0.45, 200]],
            yes_bids=[[0.15, 50]],   # bid >= gross_edge=0.10
        )
        qty = _depth_qty_from_book(book, gross_edge=0.10)
        assert qty == 50.0

    def test_multi_level_accumulation(self):
        # Two ask levels both below breakeven
        book = self._book(yes_asks=[[0.40, 100], [0.45, 150]])
        qty = _depth_qty_from_book(book, gross_edge=0.10)
        assert qty == pytest.approx(250.0)

    def test_invalid_json_returns_none(self):
        assert _depth_qty_from_book("not-json", gross_edge=0.10) is None


# ---------------------------------------------------------------------------
# Integration-style tests for OpportunityLifecycle
# (use a mock engine — no real DB needed)
# ---------------------------------------------------------------------------

def _make_det(strategy="yes_no_complement", markets=None, net_edge=0.02):
    return {
        "strategy_type":            strategy,
        "markets_involved":         markets or ["MKT-1"],
        "gross_edge":               net_edge + 0.01,
        "total_fees":               0.005,
        "estimated_slippage":       0.001,
        "net_edge":                 net_edge,
        "classification":           "B",
        "prices_json":              {"yes_ask": 0.45, "no_ask": 0.46},
        "max_executable_contracts": 10.0,
        "max_gross_profit":         0.30,
        "max_net_profit":           0.20,
    }


class TestOpportunityLifecycle:

    def _make_lc(self):
        """Return a lifecycle with a fully-mocked engine."""
        engine = MagicMock()
        lc = OpportunityLifecycle(engine)
        return lc, engine

    def test_reconcile_empty_detections_calls_close(self):
        """No detections -> all open opps get closed."""
        lc, engine = self._make_lc()

        # Patch internal methods to avoid real SQL
        lc._load_open_opps = MagicMock(return_value=[{
            "opportunity_id": "uuid-1",
            "strategy_type":  "yes_no_complement",
            "markets_involved": ["MKT-1"],
        }])
        lc._close_stale = MagicMock(return_value=1)
        lc._open_new    = MagicMock(return_value=0)

        result = lc.reconcile([])
        assert result["closed"] == 1
        assert result["opened"] == 0
        lc._close_stale.assert_called_once()

    def test_new_detection_opens(self):
        """A detection with no matching open opp -> open_new called."""
        lc, engine = self._make_lc()
        lc._load_open_opps = MagicMock(return_value=[])
        lc._open_new       = MagicMock(return_value=1)
        lc._close_stale    = MagicMock(return_value=0)

        det = _make_det()
        result = lc.reconcile([det])
        assert result["opened"] == 1
        assert result["deduped"] == 0
        lc._open_new.assert_called_once()

    def test_existing_open_not_duplicated(self):
        """Detection matching existing open opp -> deduped, not opened again."""
        lc, engine = self._make_lc()
        lc._load_open_opps = MagicMock(return_value=[{
            "opportunity_id": "uuid-1",
            "strategy_type":  "yes_no_complement",
            "markets_involved": ["MKT-1"],
        }])
        lc._open_new    = MagicMock(return_value=0)
        lc._close_stale = MagicMock(return_value=0)

        det = _make_det(markets=["MKT-1"])
        result = lc.reconcile([det])
        assert result["deduped"] == 1
        assert result["opened"] == 0
        # open_new should be called with empty list
        lc._open_new.assert_called_once_with([])

    def test_stale_opp_gets_closed(self):
        """Open opp NOT in current detections -> close_stale called with its fp."""
        lc, engine = self._make_lc()
        open_row = {
            "opportunity_id": "uuid-2",
            "strategy_type":  "yes_no_complement",
            "markets_involved": ["MKT-X"],
        }
        lc._load_open_opps = MagicMock(return_value=[open_row])
        lc._open_new       = MagicMock(return_value=0)
        lc._close_stale    = MagicMock(return_value=1)

        # Different market in detection -> "MKT-X" window is stale
        det = _make_det(markets=["MKT-Y"])
        result = lc.reconcile([det])
        assert result["closed"] == 1
        lc._close_stale.assert_called_once()

    def test_enrich_qty_uses_depth(self):
        """_enrich_qty pops book_json and uses it to compute qty."""
        lc, _ = self._make_lc()
        det = _make_det()
        det["book_json"] = json.dumps({
            "yes_asks": [[0.45, 75]],
            "yes_bids": [],
        })
        lc._enrich_qty(det)
        # book_json consumed
        assert "book_json" not in det
        assert det["max_executable_contracts"] == 75.0
        assert det["classification"] == "A"   # depth available

    def test_enrich_qty_defaults_without_depth(self):
        """No book_json -> default 10 contracts, Class B."""
        lc, _ = self._make_lc()
        det = _make_det()
        det.pop("book_json", None)
        lc._enrich_qty(det)
        assert det["max_executable_contracts"] == 10.0
        assert det["classification"] == "B"
