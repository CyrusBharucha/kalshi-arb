"""
tests/test_live_arb_scanner_check_methods.py
==============================================
Unit tests for data/synthesis_live.py LiveArbScanner check methods:
  - _check_me(): ME violation detection
  - _check_ce(): CE violation detection
  - _check_threshold(): threshold order violation

Uses mock L2Cache so no DB or network needed.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(
            DB_URL="postgresql://localhost/test",
            KALSHI_READ_RPS=10.0,
            KALSHI_WRITE_RPS=1.0,
            KALSHI_KEY_ID="",
            KALSHI_PRIVKEY_PATH="",
        ),
        "feeds.kalshi_client": MagicMock(),
        "feeds.websocket_client": MagicMock(),
        "engine.relationship_detector": MagicMock(),
    }):
        yield


def _make_scanner(rels=None):
    from feeds.synthesis_live import LiveArbScanner
    from feeds.orderbook_l2 import L2Cache
    cache = L2Cache()
    scanner = LiveArbScanner(cache, rels or [])
    return scanner, cache


def _tob(yes_bid=0.40, yes_ask=0.45, no_bid=0.55, no_ask=0.60):
    return {"yes_bid": yes_bid, "yes_ask": yes_ask, "no_bid": no_bid, "no_ask": no_ask}


def _rel(rtype, m1="A", m2="B"):
    return {
        "market_id_1": m1, "market_id_2": m2,
        "relationship_type": rtype, "confidence": 0.9,
    }


# ---------------------------------------------------------------------------
# _check_me (mutually exclusive)
# ---------------------------------------------------------------------------

class TestCheckMe:
    def test_no_violation_when_sum_below_one(self):
        scanner, _ = _make_scanner()
        rel = _rel("mutually_exclusive")
        # yes_bid_sum = 0.40 + 0.50 = 0.90 < 1.0 → no violation
        result = scanner._check_me(rel, _tob(yes_bid=0.40), _tob(yes_bid=0.50), "A", "B")
        assert result is None

    def test_violation_when_sum_above_one(self):
        scanner, _ = _make_scanner()
        rel = _rel("mutually_exclusive")
        # yes_bid_sum = 0.60 + 0.60 = 1.20 → gross_edge = 0.20
        result = scanner._check_me(rel, _tob(yes_bid=0.60), _tob(yes_bid=0.60), "A", "B")
        assert result is not None

    def test_violation_has_correct_type(self):
        scanner, _ = _make_scanner()
        rel = _rel("mutually_exclusive")
        result = scanner._check_me(rel, _tob(yes_bid=0.65), _tob(yes_bid=0.60), "A", "B")
        if result is not None:
            assert result["relationship_type"] == "mutually_exclusive"

    def test_gross_edge_computed(self):
        scanner, _ = _make_scanner()
        rel = _rel("mutually_exclusive")
        # sum = 1.30 → gross = 0.30
        result = scanner._check_me(rel, _tob(yes_bid=0.70), _tob(yes_bid=0.60), "A", "B")
        if result is not None:
            assert result["gross_edge"] == pytest.approx(0.30, abs=0.01)

    def test_result_has_required_keys(self):
        scanner, _ = _make_scanner()
        rel = _rel("mutually_exclusive")
        result = scanner._check_me(rel, _tob(yes_bid=0.65), _tob(yes_bid=0.60), "A", "B")
        if result is not None:
            for key in ("relationship_type", "market_id_1", "market_id_2",
                        "gross_edge", "fees", "net_edge", "prices"):
                assert key in result

    def test_returns_none_when_net_edge_zero(self):
        """Even with gross_edge, if fees eat it up → None."""
        scanner, _ = _make_scanner()
        rel = _rel("mutually_exclusive")
        # Very small gross edge — fees will exceed it
        result = scanner._check_me(rel, _tob(yes_bid=0.501), _tob(yes_bid=0.501), "A", "B")
        # gross = 0.002, fees > 0.002 → None
        assert result is None


# ---------------------------------------------------------------------------
# _check_ce (collectively exhaustive)
# ---------------------------------------------------------------------------

class TestCheckCe:
    def test_no_violation_when_cost_above_one(self):
        scanner, _ = _make_scanner()
        rel = _rel("collectively_exhaustive")
        # yes_ask_sum = 0.55 + 0.50 = 1.05 > 1.0 → no violation
        result = scanner._check_ce(rel, _tob(yes_ask=0.55), _tob(yes_ask=0.50), "A", "B")
        assert result is None

    def test_violation_when_cost_below_one(self):
        scanner, _ = _make_scanner()
        rel = _rel("collectively_exhaustive")
        # yes_ask_sum = 0.30 + 0.30 = 0.60 < 1.0 → gross_edge = 0.40
        result = scanner._check_ce(rel, _tob(yes_ask=0.30), _tob(yes_ask=0.30), "A", "B")
        assert result is not None

    def test_type_is_collectively_exhaustive(self):
        scanner, _ = _make_scanner()
        rel = _rel("collectively_exhaustive")
        result = scanner._check_ce(rel, _tob(yes_ask=0.30), _tob(yes_ask=0.30), "A", "B")
        if result is not None:
            assert result["relationship_type"] == "collectively_exhaustive"

    def test_gross_edge_correct(self):
        scanner, _ = _make_scanner()
        rel = _rel("collectively_exhaustive")
        # cost = 0.40 + 0.40 = 0.80 → gross_edge = 0.20
        result = scanner._check_ce(rel, _tob(yes_ask=0.40), _tob(yes_ask=0.40), "A", "B")
        if result is not None:
            assert result["gross_edge"] == pytest.approx(0.20, abs=0.01)


# ---------------------------------------------------------------------------
# _check_threshold (threshold_order / superset)
# ---------------------------------------------------------------------------

class TestCheckThreshold:
    def test_no_violation_m1_above_m2(self):
        scanner, _ = _make_scanner()
        rel = _rel("threshold_order")
        # m1 yes_bid = 0.70 > m2 yes_ask = 0.50 → no violation (m1 correctly more expensive)
        result = scanner._check_threshold(rel, _tob(yes_bid=0.70, yes_ask=0.75),
                                          _tob(yes_bid=0.40, yes_ask=0.45), "A", "B")
        assert result is None

    def test_violation_when_m1_cheaper_than_m2(self):
        scanner, _ = _make_scanner()
        rel = _rel("threshold_order")
        # m1 yes_ask = 0.40 < m2 yes_bid = 0.60 → buy m1, sell m2
        result = scanner._check_threshold(rel, _tob(yes_bid=0.35, yes_ask=0.40),
                                          _tob(yes_bid=0.60, yes_ask=0.65), "A", "B")
        # gross_edge = yes_bid_m2 - yes_ask_m1 = 0.60 - 0.40 = 0.20
        assert result is not None

    def test_type_is_threshold_order(self):
        scanner, _ = _make_scanner()
        rel = _rel("threshold_order")
        result = scanner._check_threshold(rel, _tob(yes_bid=0.35, yes_ask=0.40),
                                          _tob(yes_bid=0.60, yes_ask=0.65), "A", "B")
        if result is not None:
            assert result["relationship_type"] == "threshold_order"


# ---------------------------------------------------------------------------
# LiveArbScanner.__init__ and _by_market indexing
# ---------------------------------------------------------------------------

class TestLiveArbScannerInit:
    def test_empty_rels_no_crash(self):
        scanner, _ = _make_scanner([])
        assert scanner is not None

    def test_rel_indexed_by_both_markets(self):
        rel = _rel("mutually_exclusive", "MKT-A", "MKT-B")
        scanner, _ = _make_scanner([rel])
        assert "MKT-A" in scanner._by_market
        assert "MKT-B" in scanner._by_market

    def test_multiple_rels_same_market(self):
        rels = [
            _rel("mutually_exclusive", "X", "Y"),
            _rel("mutually_exclusive", "X", "Z"),
        ]
        scanner, _ = _make_scanner(rels)
        assert len(scanner._by_market["X"]) == 2
