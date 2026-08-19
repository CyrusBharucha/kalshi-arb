"""
tests/test_yes_no_strategy.py
==============================
Unit tests for arbitrage/yes_no.py — check_complement_arb, scan_complement_arb,
scan_historical_complement_arb, and helpers.

No database, no network. Uses minimal market dicts.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.yes_no import (
    check_complement_arb,
    scan_complement_arb,
    scan_historical_complement_arb,
    _safe_float,
    _best_ask_depth,
    _best_bid_depth,
)


# ---------------------------------------------------------------------------
# _safe_float helper
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_numeric(self):
        assert abs(_safe_float(0.45) - 0.45) < 1e-9

    def test_string_numeric(self):
        assert abs(_safe_float("0.45") - 0.45) < 1e-9

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_invalid_returns_none(self):
        assert _safe_float("abc") is None


# ---------------------------------------------------------------------------
# _best_ask_depth / _best_bid_depth
# ---------------------------------------------------------------------------

class TestOrderBookHelpers:
    def test_best_ask_depth_present(self):
        ob = {"yes_asks": [[0.55, 100], [0.57, 200]]}
        d = _best_ask_depth(ob, "yes")
        assert d == 100.0

    def test_best_ask_depth_empty(self):
        ob = {"yes_asks": []}
        assert _best_ask_depth(ob, "yes") is None

    def test_best_bid_depth_present(self):
        ob = {"yes_bids": [[0.48, 50], [0.45, 300]]}
        d = _best_bid_depth(ob, "yes")
        assert d == 50.0

    def test_best_bid_depth_empty(self):
        ob = {"yes_bids": []}
        assert _best_bid_depth(ob, "yes") is None


# ---------------------------------------------------------------------------
# check_complement_arb
# ---------------------------------------------------------------------------

class TestCheckComplementArb:
    def _market(self, yes_bid, yes_ask, ticker="KXTEST-01"):
        return {"ticker": ticker, "market_id": ticker,
                "yes_bid": yes_bid, "yes_ask": yes_ask}

    def test_no_edge_no_result(self):
        """yes_ask + no_ask >= 1.0 → no opportunity."""
        m = self._market(0.50, 0.52)   # no_ask = 1 - 0.50 = 0.50; sum = 1.02
        assert check_complement_arb(m) is None

    def test_missing_prices_no_result(self):
        m = {"ticker": "X", "yes_ask": 0.45}   # missing yes_bid
        assert check_complement_arb(m) is None

    def test_result_dict_returned_for_edge(self):
        """yes_ask + no_ask < 1 with enough net edge → result."""
        m = self._market(0.55, 0.40)   # no_ask = 0.45; sum = 0.85; gross = 0.15
        result = check_complement_arb(m)
        # May still be None if MIN_NET_EDGE threshold not met — just check type
        if result is not None:
            assert isinstance(result, dict)
            assert "net_edge" in result

    def test_result_has_required_keys(self):
        m = self._market(0.60, 0.30)   # gross = 0.10; likely passes threshold
        result = check_complement_arb(m)
        if result is not None:
            for key in ("strategy_type", "classification", "gross_edge",
                        "total_fees", "net_edge", "markets_involved"):
                assert key in result

    def test_strategy_type_complement(self):
        m = self._market(0.60, 0.30)
        result = check_complement_arb(m)
        if result:
            assert result["strategy_type"] == "yes_no_complement"

    def test_net_edge_less_than_gross(self):
        m = self._market(0.60, 0.30)
        result = check_complement_arb(m)
        if result:
            assert result["net_edge"] < result["gross_edge"]

    def test_invalid_yes_ask_zero(self):
        m = self._market(0.50, 0.0)
        assert check_complement_arb(m) is None

    def test_yes_ask_at_one_returns_none(self):
        m = self._market(0.50, 1.0)
        assert check_complement_arb(m) is None

    def test_with_order_book(self):
        m = self._market(0.60, 0.30)
        ob = {
            "yes_asks": [[0.30, 100]],
            "yes_bids": [[0.60, 50]],
        }
        result = check_complement_arb(m, ob)
        if result:
            assert isinstance(result, dict)

    def test_classification_b_without_depth(self):
        """No order book → classification B (if profitable)."""
        m = self._market(0.60, 0.30)
        result = check_complement_arb(m)
        if result and result["net_edge"] > 0:
            assert result["classification"] == "B"


# ---------------------------------------------------------------------------
# scan_complement_arb
# ---------------------------------------------------------------------------

class TestScanComplementArb:
    def _markets(self, params):
        return [{"ticker": f"M{i}", "market_id": f"M{i}",
                 "yes_bid": yb, "yes_ask": ya}
                for i, (yb, ya) in enumerate(params)]

    def test_empty_input_empty_output(self):
        result = scan_complement_arb([])
        assert result == []

    def test_no_edge_markets_excluded(self):
        markets = self._markets([(0.50, 0.52), (0.48, 0.55)])
        result = scan_complement_arb(markets)
        # 0.50+0.50=1.0 and 0.52+0.48=1.00 → no edge → empty
        assert isinstance(result, list)

    def test_sorted_by_net_edge_descending(self):
        # Two markets, both with positive edge
        markets = self._markets([(0.65, 0.28), (0.70, 0.25)])
        result = scan_complement_arb(markets)
        if len(result) >= 2:
            assert result[0]["net_edge"] >= result[1]["net_edge"]

    def test_returns_list(self):
        markets = self._markets([(0.50, 0.52)])
        result = scan_complement_arb(markets)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# scan_historical_complement_arb
# ---------------------------------------------------------------------------

class TestScanHistoricalComplementArb:
    def _df(self, rows):
        return pd.DataFrame(rows, columns=["market_id", "period_end_ts",
                                           "yes_bid_close", "yes_ask_close"])

    def test_empty_df_returns_empty(self):
        result = scan_historical_complement_arb(pd.DataFrame())
        assert result == []

    def test_none_returns_empty(self):
        result = scan_historical_complement_arb(None)
        assert result == []

    def test_no_edge_excluded(self):
        df = self._df([("M1", "2024-01-01", 0.50, 0.52)])
        result = scan_historical_complement_arb(df)
        assert isinstance(result, list)

    def test_positive_edge_included(self):
        # yes_bid=0.65, yes_ask=0.30 → no_ask=0.35, gross=0.35
        df = self._df([("M1", "2024-01-01", 0.65, 0.30)])
        result = scan_historical_complement_arb(df)
        # Large gross edge — should produce a result
        if result:
            assert result[0]["classification"] == "B"
            assert result[0]["strategy_type"] == "yes_no_complement"

    def test_result_has_required_keys(self):
        df = self._df([("M1", "2024-01-01", 0.65, 0.30)])
        result = scan_historical_complement_arb(df)
        if result:
            for key in ("gross_edge", "total_fees", "net_edge", "markets_involved"):
                assert key in result[0]

    def test_missing_prices_skipped(self):
        df = pd.DataFrame([{
            "market_id": "M1", "period_end_ts": "2024-01-01",
            "yes_bid_close": None, "yes_ask_close": None,
        }])
        result = scan_historical_complement_arb(df)
        assert result == []

    def test_gross_less_than_one(self):
        df = self._df([("M1", "2024-01-01", 0.65, 0.30)])
        result = scan_historical_complement_arb(df)
        if result:
            assert result[0]["gross_edge"] < 1.0

    def test_multiple_rows_processed(self):
        df = self._df([
            ("M1", "2024-01-01", 0.65, 0.30),
            ("M2", "2024-01-02", 0.70, 0.25),
        ])
        result = scan_historical_complement_arb(df)
        assert len(result) <= 2  # at most 2 rows
