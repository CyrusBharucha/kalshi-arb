"""
tests/test_lifecycle_helpers.py
================================
Unit tests for pure-function helpers in arbitrage/lifecycle.py:
  - _kalshi_fee(price)
  - _fingerprint(strategy_type, markets)
  - _depth_qty_from_book(book_json_str, gross_edge)

No database, no network.
"""
from __future__ import annotations

import json
import pytest

from engine.lifecycle import _kalshi_fee, _fingerprint, _depth_qty_from_book


# ---------------------------------------------------------------------------
# _kalshi_fee
# ---------------------------------------------------------------------------

class TestKalshiFee:
    def test_known_value_50(self):
        """0.07 * 0.5 * 0.5 = 0.0175"""
        assert abs(_kalshi_fee(0.50) - 0.0175) < 1e-4

    def test_known_value_20(self):
        assert abs(_kalshi_fee(0.20) - 0.0112) < 1e-3

    def test_nonneg_for_all_valid(self):
        for p in [0.01, 0.10, 0.25, 0.50, 0.75, 0.90, 0.99]:
            assert _kalshi_fee(p) >= 0

    def test_cap_enforced(self):
        from engine.lifecycle import _FEE_CAP
        for p in [0.10, 0.30, 0.50, 0.70, 0.90]:
            assert _kalshi_fee(p) <= _FEE_CAP + 1e-9

    def test_invalid_zero_returns_min(self):
        from engine.lifecycle import _FEE_MIN
        assert _kalshi_fee(0.0) == _FEE_MIN

    def test_invalid_one_returns_min(self):
        from engine.lifecycle import _FEE_MIN
        assert _kalshi_fee(1.0) == _FEE_MIN

    def test_negative_returns_min(self):
        from engine.lifecycle import _FEE_MIN
        assert _kalshi_fee(-0.5) == _FEE_MIN

    def test_none_returns_min(self):
        from engine.lifecycle import _FEE_MIN
        assert _kalshi_fee(None) == _FEE_MIN

    def test_symmetric_around_50(self):
        assert abs(_kalshi_fee(0.30) - _kalshi_fee(0.70)) < 1e-6


# ---------------------------------------------------------------------------
# _fingerprint
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_returns_frozenset(self):
        fp = _fingerprint("complement", ["M1", "M2"])
        assert isinstance(fp, frozenset)

    def test_includes_strategy_type(self):
        fp = _fingerprint("complement", ["M1", "M2"])
        assert "complement" in fp

    def test_includes_all_markets(self):
        fp = _fingerprint("complement", ["M1", "M2"])
        assert "M1" in fp and "M2" in fp

    def test_order_independent(self):
        fp1 = _fingerprint("me", ["M2", "M1", "M3"])
        fp2 = _fingerprint("me", ["M1", "M3", "M2"])
        assert fp1 == fp2

    def test_different_strategy_different_fingerprint(self):
        fp1 = _fingerprint("complement", ["M1", "M2"])
        fp2 = _fingerprint("nested",     ["M1", "M2"])
        assert fp1 != fp2

    def test_different_markets_different_fingerprint(self):
        fp1 = _fingerprint("complement", ["M1", "M2"])
        fp2 = _fingerprint("complement", ["M1", "M3"])
        assert fp1 != fp2

    def test_single_market(self):
        fp = _fingerprint("complement", ["M1"])
        assert "M1" in fp

    def test_hashable(self):
        fp = _fingerprint("me", ["M1", "M2"])
        # Should be usable as dict key
        d = {fp: 42}
        assert d[fp] == 42


# ---------------------------------------------------------------------------
# _depth_qty_from_book
# ---------------------------------------------------------------------------

class TestDepthQtyFromBook:
    def _book_json(self, yes_asks=None, yes_bids=None):
        return json.dumps({
            "yes_asks": yes_asks or [],
            "yes_bids": yes_bids or [],
        })

    def test_none_book_returns_none(self):
        assert _depth_qty_from_book(None, 0.05) is None

    def test_empty_string_returns_none(self):
        assert _depth_qty_from_book("", 0.05) is None

    def test_invalid_json_returns_none(self):
        assert _depth_qty_from_book("not-json", 0.05) is None

    def test_empty_book_returns_none(self):
        result = _depth_qty_from_book(self._book_json(), gross_edge=0.05)
        assert result is None

    def test_profitable_yes_ask_counted(self):
        """YES ask at 0.90 with edge=0.05: breakeven_yes=0.95, 0.90<=0.95 -> counted."""
        book = self._book_json(yes_asks=[[0.90, 100]])
        result = _depth_qty_from_book(book, gross_edge=0.05)
        assert result is not None
        assert result > 0

    def test_ask_above_breakeven_excluded(self):
        """YES ask at 0.97 with edge=0.05: breakeven=0.95, excluded."""
        book = self._book_json(yes_asks=[[0.97, 100]])
        result = _depth_qty_from_book(book, gross_edge=0.05)
        # Only this level, and it's above breakeven -> None
        assert result is None

    def test_profitable_bid_counted(self):
        """YES bid at 0.10 with edge=0.05: breakeven_bid=0.05, 0.10>=0.05 -> counted."""
        book = self._book_json(yes_bids=[[0.10, 200]])
        result = _depth_qty_from_book(book, gross_edge=0.05)
        assert result is not None
        assert result > 0

    def test_min_of_two_sides(self):
        """Binding constraint = min of qty_yes and qty_no (no side)."""
        book = self._book_json(
            yes_asks=[[0.90, 100]],
            yes_bids=[[0.10, 50]],
        )
        result = _depth_qty_from_book(book, gross_edge=0.05)
        # yes side: 100, no side (via yes_bids): 50 -> min = 50
        assert result is not None
        assert abs(result - 50) < 1e-6

    def test_dict_book_accepted(self):
        """book_json_str can also be a dict (already parsed)."""
        book = {"yes_asks": [[0.90, 100]], "yes_bids": []}
        result = _depth_qty_from_book(book, gross_edge=0.05)
        assert result is not None

    def test_multiple_ask_levels_summed(self):
        """Multiple YES ask levels at profitable prices should be summed."""
        book = self._book_json(yes_asks=[[0.85, 60], [0.90, 40]])
        result = _depth_qty_from_book(book, gross_edge=0.05)
        assert result is not None
        # breakeven = 0.95; both 0.85 and 0.90 qualify -> qty_yes = 100
        assert abs(result - 100) < 1e-6
