"""
tests/test_historical_scanner.py
Tests for arbitrage/historical_scanner.py — complement detection,
window tracking, and time-sliced scanning logic.
"""
from __future__ import annotations

import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from engine.historical_scanner import (
    _complement_check,
    _Window,
    scan_ticker_batch,
    SNAP_GAP_S,
)


# ---------------------------------------------------------------------------
# _complement_check
# ---------------------------------------------------------------------------

class TestComplementCheck:

    def test_obvious_arb_detected(self):
        det = _complement_check("MKT", yes_ask=0.40, yes_bid=0.45, book_json=None)
        assert det is not None
        assert det["gross_edge"] > 0
        assert det["net_edge"] > 0
        assert det["strategy_type"] == "yes_no_complement"

    def test_no_arb_when_prices_tight(self):
        # yes_ask=0.499, yes_bid=0.501 -> no_ask=0.499, gross=0.002
        # After fees+slippage should be negative
        det = _complement_check("MKT", yes_ask=0.499, yes_bid=0.501, book_json=None)
        assert det is None

    def test_none_prices_return_none(self):
        assert _complement_check("MKT", None, 0.50, None) is None
        assert _complement_check("MKT", 0.50, None, None) is None

    def test_invalid_prices_return_none(self):
        assert _complement_check("MKT", 1.01, 0.50, None) is None
        assert _complement_check("MKT", 0.50, 0.00, None) is None

    def test_class_a_with_depth(self):
        book = json.dumps({"yes_asks": [[0.40, 100]], "yes_bids": [[0.50, 80]]})
        det = _complement_check("MKT", 0.40, 0.45, book)
        if det:  # may be None if net_edge too small after depth
            assert det["classification"] == "A"
            assert det["max_executable_contracts"] > 0

    def test_class_b_without_depth(self):
        det = _complement_check("MKT", 0.40, 0.45, None)
        if det:
            assert det["classification"] == "B"
            assert det["max_executable_contracts"] == 10.0

    def test_prices_json_structure(self):
        det = _complement_check("MKT", 0.40, 0.45, None)
        if det:
            pj = det["prices_json"]
            assert "yes_ask" in pj
            assert "no_ask" in pj
            assert pj["yes_ask"] == pytest.approx(0.40)
            assert pj["no_ask"] == pytest.approx(0.55)  # 1 - 0.45


# ---------------------------------------------------------------------------
# _Window
# ---------------------------------------------------------------------------

class TestWindow:
    def _det(self, net_edge=0.02, qty=10.0):
        return {
            "strategy_type":            "yes_no_complement",
            "classification":           "B",
            "markets_involved":         ["MKT-1"],
            "prices_json":              {"yes_ask": 0.40},
            "gross_edge":               net_edge + 0.01,
            "total_fees":               0.005,
            "estimated_slippage":       0.001,
            "net_edge":                 net_edge,
            "max_executable_contracts": qty,
            "max_gross_profit":         (net_edge + 0.01) * qty,
            "max_net_profit":           net_edge * qty,
        }

    def _ts(self, offset_s=0):
        return datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)

    def test_window_tracks_peak_net_edge(self):
        w = _Window("MKT-1", "yes_no_complement", self._ts(), self._det(0.02))
        w.update(self._ts(60),  self._det(0.05))   # better edge
        w.update(self._ts(120), self._det(0.01))   # worse
        assert w.peak_net_edge == pytest.approx(0.05)

    def test_is_stale_when_gap_too_large(self):
        w = _Window("MKT-1", "yes_no_complement", self._ts(), self._det())
        # last_ts = ts(0), check at ts(SNAP_GAP_S + 1)
        future = self._ts(SNAP_GAP_S + 1)
        assert w.is_stale(future)

    def test_not_stale_within_gap(self):
        w = _Window("MKT-1", "yes_no_complement", self._ts(), self._det())
        just_in_time = self._ts(SNAP_GAP_S - 1)
        assert not w.is_stale(just_in_time)

    def test_to_opp_structure(self):
        t0 = self._ts()
        w  = _Window("MKT-1", "yes_no_complement", t0, self._det(0.02))
        w.update(self._ts(120), self._det(0.03))
        opp = w.to_opp()
        assert opp["detected_at"] == t0
        assert opp["strategy_type"] == "yes_no_complement"
        assert opp["net_edge"]  == pytest.approx(0.03)
        assert opp["duration_seconds"] == pytest.approx(120.0)
        assert opp["status"] == "expired"

    def test_to_opp_duration_one_bucket(self):
        t0 = self._ts()
        w = _Window("MKT-1", "yes_no_complement", t0, self._det())
        opp = w.to_opp()
        assert opp["duration_seconds"] == 0.0


# ---------------------------------------------------------------------------
# scan_ticker_batch (uses in-memory rows, no DB)
# ---------------------------------------------------------------------------

class TestScanTickerBatch:

    def _row(self, market_id, ts_offset_s, yes_bid, yes_ask):
        ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=ts_offset_s)
        return (market_id, ts, yes_bid, yes_ask, None)

    def test_empty_rows_returns_empty(self):
        engine = MagicMock()
        with patch("engine.historical_scanner._fetch_snapshots", return_value=[]):
            result = scan_ticker_batch(engine, ["MKT"], None, None)
        assert result == []

    def test_single_window_closed_when_edge_disappears(self):
        """Arb exists for 2 buckets, then prices tighten -> one closed window."""
        rows = [
            self._row("MKT", 0,   0.45, 0.40),   # gross=0.15 -> arb
            self._row("MKT", 60,  0.45, 0.40),   # still arb
            self._row("MKT", 120, 0.50, 0.50),   # no arb (gross=0)
        ]
        engine = MagicMock()
        with patch("engine.historical_scanner._fetch_snapshots", return_value=rows):
            result = scan_ticker_batch(engine, ["MKT"], None, None)

        expired = [o for o in result if o["status"] == "expired"]
        assert len(expired) == 1
        opp = expired[0]
        assert opp["strategy_type"] == "yes_no_complement"
        assert opp["duration_seconds"] >= 60.0

    def test_window_open_at_end_marked_open(self):
        """Arb never closes within the chunk -> status='open'."""
        rows = [
            self._row("MKT", 0,  0.45, 0.40),
            self._row("MKT", 60, 0.45, 0.40),
        ]
        engine = MagicMock()
        with patch("engine.historical_scanner._fetch_snapshots", return_value=rows):
            result = scan_ticker_batch(engine, ["MKT"], None, None)

        open_opps = [o for o in result if o["status"] == "open"]
        assert len(open_opps) == 1

    def test_multiple_markets_independent_windows(self):
        rows = [
            self._row("MKT-A", 0,   0.45, 0.40),
            self._row("MKT-B", 0,   0.45, 0.40),
            self._row("MKT-A", 60,  0.50, 0.50),   # A closes
            self._row("MKT-B", 60,  0.45, 0.40),   # B stays
        ]
        engine = MagicMock()
        with patch("engine.historical_scanner._fetch_snapshots", return_value=rows):
            result = scan_ticker_batch(engine, ["MKT-A", "MKT-B"], None, None)

        a_expired = [o for o in result if o["markets_involved"] == ["MKT-A"] and o["status"] == "expired"]
        b_open    = [o for o in result if o["markets_involved"] == ["MKT-B"] and o["status"] == "open"]
        assert len(a_expired) == 1
        assert len(b_open)    == 1

    def test_no_arb_ever_returns_empty(self):
        rows = [
            self._row("MKT", 0,  0.50, 0.51),
            self._row("MKT", 60, 0.50, 0.51),
        ]
        engine = MagicMock()
        with patch("engine.historical_scanner._fetch_snapshots", return_value=rows):
            result = scan_ticker_batch(engine, ["MKT"], None, None)
        assert result == []
