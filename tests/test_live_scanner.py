"""
tests/test_live_scanner.py
==========================
Unit tests for arbitrage/live_scanner.py.

Covers:
  - SequenceGapMonitor: gap detection, stale tracking, reset
  - LiveArbitrageScanner.stale_books: time-based stale detection
  - _build_opp: opportunity construction from violation + relationship

All tests are fully offline (no DB, no WS).
"""
from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock, patch

from engine.live_scanner import SequenceGapMonitor, STALE_BOOK_S


# ---------------------------------------------------------------------------
# SequenceGapMonitor
# ---------------------------------------------------------------------------

class TestSequenceGapMonitor:
    """Tests for the sequence gap detection helper."""

    def test_first_message_no_gap(self):
        mon = SequenceGapMonitor()
        gap = mon.update("TICK-001", 1)
        assert gap == 0

    def test_consecutive_no_gap(self):
        mon = SequenceGapMonitor()
        mon.update("TICK-001", 1)
        gap = mon.update("TICK-001", 2)
        assert gap == 0

    def test_gap_detected(self):
        mon = SequenceGapMonitor()
        mon.update("TICK-001", 1)
        gap = mon.update("TICK-001", 5)   # seq 2,3,4 missing -> gap=3
        assert gap == 3

    def test_stale_after_gap(self):
        mon = SequenceGapMonitor()
        mon.update("TICK-001", 1)
        mon.update("TICK-001", 10)
        assert mon.is_stale("TICK-001")

    def test_not_stale_without_gap(self):
        mon = SequenceGapMonitor()
        mon.update("TICK-001", 1)
        mon.update("TICK-001", 2)
        mon.update("TICK-001", 3)
        assert not mon.is_stale("TICK-001")

    def test_reset_clears_stale(self):
        mon = SequenceGapMonitor()
        mon.update("TICK-001", 1)
        mon.update("TICK-001", 99)
        assert mon.is_stale("TICK-001")
        mon.reset("TICK-001")
        assert not mon.is_stale("TICK-001")

    def test_reset_clears_last_seq(self):
        """After reset, next message is treated as first (no spurious gap)."""
        mon = SequenceGapMonitor()
        mon.update("TICK-001", 1)
        mon.update("TICK-001", 99)
        mon.reset("TICK-001")
        gap = mon.update("TICK-001", 200)  # would be a massive gap without reset
        assert gap == 0

    def test_independent_tickers(self):
        mon = SequenceGapMonitor()
        mon.update("A", 1)
        mon.update("A", 2)
        mon.update("B", 1)
        # Gap only on B
        mon.update("B", 10)
        assert not mon.is_stale("A")
        assert mon.is_stale("B")

    def test_stats_total_gaps(self):
        mon = SequenceGapMonitor()
        mon.update("A", 1)
        mon.update("A", 5)   # 1 gap
        mon.update("B", 1)
        mon.update("B", 3)   # 1 gap
        assert mon.stats()["total_gaps"] == 2

    def test_stats_stale_markets(self):
        mon = SequenceGapMonitor()
        mon.update("A", 1)
        mon.update("A", 10)
        mon.update("B", 1)
        mon.update("B", 20)
        stats = mon.stats()
        assert stats["stale_markets"] == 2

    def test_stats_markets_tracked(self):
        mon = SequenceGapMonitor()
        mon.update("X", 1)
        mon.update("Y", 1)
        mon.update("Z", 1)
        assert mon.stats()["markets_tracked"] == 3

    def test_gap_size_one(self):
        """A single skipped seq number counts as gap=1."""
        mon = SequenceGapMonitor()
        mon.update("T", 1)
        gap = mon.update("T", 3)   # seq 2 missing
        assert gap == 1

    def test_no_gap_on_repeated_seq(self):
        """Repeated seq (0 diff) should return 0 (not treated as a gap)."""
        mon = SequenceGapMonitor()
        mon.update("T", 5)
        gap = mon.update("T", 5)   # exact repeat, gap = 5 - 5 - 1 = -1 < 0 → 0
        assert gap == 0


# ---------------------------------------------------------------------------
# _build_opp helper
# ---------------------------------------------------------------------------

class TestBuildOpp:
    """Tests for the _build_opp opportunity builder."""

    def _rel(self):
        return {
            "market_id_1": "MKT-A",
            "market_id_2": "MKT-B",
            "relationship_type": "mutually_exclusive",
            "logical_constraint": "sum_le_1",
            "implied_inequality": "sum",
            "confidence": 0.95,
        }

    def _prices(self, bid1=0.40, ask1=0.45, bid2=0.58, ask2=0.62):
        return {
            "MKT-A": {"yes_bid": bid1, "yes_ask": ask1},
            "MKT-B": {"yes_bid": bid2, "yes_ask": ask2},
        }

    def _violation(self, mag=0.07):
        return {"magnitude": mag, "relationship": self._rel()}

    def test_returns_dict(self):
        from engine.live_scanner import _build_opp
        opp = _build_opp(self._rel(), self._prices(), self._violation())
        assert opp is not None
        assert isinstance(opp, dict)

    def test_net_edge_positive(self):
        from engine.live_scanner import _build_opp
        opp = _build_opp(self._rel(), self._prices(), self._violation(0.10))
        assert opp["net_edge"] > 0

    def test_returns_none_on_zero_magnitude(self):
        from engine.live_scanner import _build_opp
        opp = _build_opp(self._rel(), self._prices(), self._violation(0.0))
        assert opp is None

    def test_returns_none_missing_price(self):
        from engine.live_scanner import _build_opp
        prices = {"MKT-A": {"yes_bid": 0.40, "yes_ask": 0.45}}  # MKT-B missing
        opp = _build_opp(self._rel(), prices, self._violation(0.05))
        assert opp is None

    def test_strategy_type_preserved(self):
        from engine.live_scanner import _build_opp
        opp = _build_opp(self._rel(), self._prices(), self._violation(0.05))
        assert opp["strategy_type"] == "mutually_exclusive"

    def test_markets_involved(self):
        from engine.live_scanner import _build_opp
        opp = _build_opp(self._rel(), self._prices(), self._violation(0.05))
        assert "MKT-A" in opp["markets_involved"]
        assert "MKT-B" in opp["markets_involved"]

    def test_classification_a(self):
        from engine.live_scanner import _build_opp
        opp = _build_opp(self._rel(), self._prices(), self._violation(0.05))
        assert opp["classification"] == "A"

    def test_gross_edge_correct(self):
        from engine.live_scanner import _build_opp
        opp = _build_opp(self._rel(), self._prices(), self._violation(0.07))
        assert abs(opp["gross_edge"] - 0.07) < 1e-6

    def test_fees_deducted(self):
        from engine.live_scanner import _build_opp
        opp = _build_opp(self._rel(), self._prices(), self._violation(0.07))
        assert opp["net_edge"] < opp["gross_edge"]
        assert opp["total_fees"] >= 0

    def test_status_open(self):
        from engine.live_scanner import _build_opp
        opp = _build_opp(self._rel(), self._prices(), self._violation(0.05))
        assert opp["status"] == "open"
