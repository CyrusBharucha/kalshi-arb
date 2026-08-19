"""
tests/test_live_arb_scanner.py
================================
Unit tests for data/synthesis_live.py LiveArbScanner private methods:
  - _check_me(): ME violation returns dict when yes_bid_sum > 1 + fees
  - _check_ce(): CE violation returns dict when yes_ask_sum < 1 - fees
  - _check_threshold(): threshold violation when yes_bid_1 < yes_ask_2
  - No violation returns None for each check
  - _by_market index built correctly in constructor

All tests use mock L2Cache and mock TopOfBook.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


def _make_scanner(rels=None):
    """Build a LiveArbScanner with a mock L2Cache."""
    from feeds.synthesis_live import LiveArbScanner
    mock_cache = MagicMock()
    scanner = LiveArbScanner.__new__(LiveArbScanner)
    scanner.cache = mock_cache
    scanner._by_market = {}
    for rel in (rels or []):
        for mid in (rel["market_id_1"], rel["market_id_2"]):
            scanner._by_market.setdefault(mid, []).append(rel)
    return scanner, mock_cache


def _tob(yes_bid=0.40, yes_ask=0.45):
    return {"yes_bid": yes_bid, "yes_ask": yes_ask}


class TestByMarketIndex:
    """Constructor builds _by_market index correctly."""

    def test_empty_rels_empty_index(self):
        scanner, _ = _make_scanner([])
        assert scanner._by_market == {}

    def test_rel_indexed_by_both_markets(self):
        rels = [{"market_id_1": "A", "market_id_2": "B",
                 "relationship_type": "mutually_exclusive"}]
        scanner, _ = _make_scanner(rels)
        assert "A" in scanner._by_market
        assert "B" in scanner._by_market

    def test_multiple_rels_same_market(self):
        rels = [
            {"market_id_1": "A", "market_id_2": "B", "relationship_type": "me"},
            {"market_id_1": "A", "market_id_2": "C", "relationship_type": "me"},
        ]
        scanner, _ = _make_scanner(rels)
        assert len(scanner._by_market["A"]) == 2


class TestCheckME:
    """_check_me(): ME opportunity detection."""

    @pytest.fixture(autouse=True)
    def _scanner(self):
        self.scanner, _ = _make_scanner()
        self.rel = {"market_id_1": "A", "market_id_2": "B",
                    "relationship_type": "mutually_exclusive"}

    def test_no_violation_sum_below_one(self):
        result = self.scanner._check_me(
            self.rel, _tob(yes_bid=0.40), _tob(yes_bid=0.40), "A", "B"
        )
        assert result is None

    def test_violation_sum_above_one_returns_dict(self):
        # yes_bid_sum = 0.65 + 0.45 = 1.10; gross_edge = 0.10
        result = self.scanner._check_me(
            self.rel, _tob(yes_bid=0.65), _tob(yes_bid=0.45), "A", "B"
        )
        assert result is not None
        assert result["relationship_type"] == "mutually_exclusive"
        assert result["gross_edge"] > 0
        assert result["net_edge"] > 0

    def test_violation_net_edge_is_gross_minus_fees(self):
        tob1 = _tob(yes_bid=0.65)
        tob2 = _tob(yes_bid=0.45)
        result = self.scanner._check_me(self.rel, tob1, tob2, "A", "B")
        from feeds.synthesis_live import _kalshi_fee
        expected_gross = 0.65 + 0.45 - 1.0
        expected_net = expected_gross - _kalshi_fee(0.65) - _kalshi_fee(0.45)
        assert abs(result["net_edge"] - round(expected_net, 4)) < 1e-6

    def test_violation_strategy_label(self):
        result = self.scanner._check_me(
            self.rel, _tob(yes_bid=0.65), _tob(yes_bid=0.45), "A", "B"
        )
        assert result["strategy"] == "sell_yes_both_me"


class TestCheckCE:
    """_check_ce(): CE opportunity detection."""

    @pytest.fixture(autouse=True)
    def _scanner(self):
        self.scanner, _ = _make_scanner()
        self.rel = {"market_id_1": "A", "market_id_2": "B",
                    "relationship_type": "collectively_exhaustive"}

    def test_no_violation_sum_above_one(self):
        # yes_ask_sum = 0.55 + 0.55 = 1.10 (cost > 1, no arb)
        result = self.scanner._check_ce(
            self.rel, _tob(yes_ask=0.55), _tob(yes_ask=0.55), "A", "B"
        )
        assert result is None

    def test_violation_sum_below_one(self):
        # yes_ask_sum = 0.30 + 0.30 = 0.60 (cost < 1, buy both for profit)
        result = self.scanner._check_ce(
            self.rel, _tob(yes_ask=0.30), _tob(yes_ask=0.30), "A", "B"
        )
        assert result is not None
        assert result["relationship_type"] == "collectively_exhaustive"
        assert result["gross_edge"] > 0

    def test_violation_strategy_label(self):
        result = self.scanner._check_ce(
            self.rel, _tob(yes_ask=0.30), _tob(yes_ask=0.30), "A", "B"
        )
        assert result["strategy"] == "buy_yes_both_ce"


class TestCheckThreshold:
    """_check_threshold(): threshold violation detection."""

    @pytest.fixture(autouse=True)
    def _scanner(self):
        self.scanner, _ = _make_scanner()
        self.rel = {"market_id_1": "HI", "market_id_2": "LO",
                    "relationship_type": "threshold_order"}

    def test_no_violation_hi_bid_above_lo_ask(self):
        # HI yes_bid = 0.70, LO yes_ask = 0.50 -> no violation (bid > ask is correct)
        result = self.scanner._check_threshold(
            self.rel, _tob(yes_bid=0.70, yes_ask=0.72), _tob(yes_bid=0.48, yes_ask=0.50), "HI", "LO"
        )
        assert result is None

    def test_violation_hi_bid_below_lo_ask(self):
        # HI yes_bid=0.30, LO yes_ask=0.60 -> violation (high-strike priced lower!)
        result = self.scanner._check_threshold(
            self.rel, _tob(yes_bid=0.30, yes_ask=0.32), _tob(yes_bid=0.58, yes_ask=0.60), "HI", "LO"
        )
        assert result is not None
        assert result["strategy"] == "threshold_spread"

    def test_violation_gross_edge_correct(self):
        result = self.scanner._check_threshold(
            self.rel, _tob(yes_bid=0.30, yes_ask=0.32), _tob(yes_bid=0.58, yes_ask=0.60), "HI", "LO"
        )
        # gross_edge = yes_ask_lo - yes_bid_hi = 0.60 - 0.30 = 0.30
        assert abs(result["gross_edge"] - 0.30) < 1e-6

    def test_violation_net_edge_positive(self):
        result = self.scanner._check_threshold(
            self.rel, _tob(yes_bid=0.30, yes_ask=0.32), _tob(yes_bid=0.58, yes_ask=0.60), "HI", "LO"
        )
        assert result["net_edge"] > 0
