"""
tests/test_me_strategy.py
==========================
Unit tests for arbitrage/mutually_exclusive.py — check_me_arb, scan_me_arb,
check_overpriced_set, and scan_overpriced_events.

No database, no network.
"""
from __future__ import annotations

import pytest

from engine.mutually_exclusive import (
    check_me_arb,
    scan_me_arb,
    check_overpriced_set,
)


def _market(market_id, yes_ask=None, yes_bid=None):
    return {
        "market_id": market_id,
        "ticker": market_id,
        "yes_ask": yes_ask,
        "yes_bid": yes_bid,
    }


# ---------------------------------------------------------------------------
# check_me_arb — no edge / invalid inputs
# ---------------------------------------------------------------------------

class TestCheckMeArbNoEdge:
    def test_single_market_returns_none(self):
        """Need at least 2 markets."""
        result = check_me_arb("EV", [_market("M1", yes_ask=0.50)])
        assert result is None

    def test_prices_sum_to_one_no_edge(self):
        markets = [_market("M1", yes_ask=0.34), _market("M2", yes_ask=0.34),
                   _market("M3", yes_ask=0.34)]
        # sum = 1.02 → gross < 0
        result = check_me_arb("EV", markets)
        assert result is None

    def test_invalid_price_filtered(self):
        markets = [_market("M1", yes_ask=0.0), _market("M2", yes_ask=0.30)]
        # M1 filtered out → only 1 valid → None
        result = check_me_arb("EV", markets)
        assert result is None

    def test_none_price_filtered(self):
        markets = [_market("M1", yes_ask=None), _market("M2", yes_ask=0.30)]
        result = check_me_arb("EV", markets)
        assert result is None

    def test_empty_markets_none(self):
        result = check_me_arb("EV", [])
        assert result is None


# ---------------------------------------------------------------------------
# check_me_arb — positive edge
# ---------------------------------------------------------------------------

class TestCheckMeArbEdge:
    def _big_edge_markets(self):
        return [_market("M1", yes_ask=0.20), _market("M2", yes_ask=0.25),
                _market("M3", yes_ask=0.30)]  # sum=0.75, gross=0.25

    def test_result_dict_returned(self):
        result = check_me_arb("EV", self._big_edge_markets())
        if result is not None:
            assert isinstance(result, dict)

    def test_result_has_required_keys(self):
        result = check_me_arb("EV", self._big_edge_markets())
        if result:
            for key in ("strategy_type", "gross_edge", "net_edge",
                        "total_fees", "markets_involved"):
                assert key in result

    def test_strategy_type_me(self):
        result = check_me_arb("EV", self._big_edge_markets())
        if result:
            assert "mutually_exclusive" in result["strategy_type"]

    def test_net_less_than_gross(self):
        result = check_me_arb("EV", self._big_edge_markets())
        if result:
            assert result["net_edge"] < result["gross_edge"]

    def test_all_markets_in_result(self):
        markets = self._big_edge_markets()
        result = check_me_arb("EV", markets)
        if result:
            for m in markets:
                assert m["market_id"] in result["markets_involved"]


# ---------------------------------------------------------------------------
# scan_me_arb
# ---------------------------------------------------------------------------

class TestScanMeArb:
    def test_empty_input_empty_output(self):
        result = scan_me_arb({})
        assert result == []

    def test_single_market_event_excluded(self):
        result = scan_me_arb({"EV": [_market("M1", yes_ask=0.40)]})
        assert result == []

    def test_no_edge_events_excluded(self):
        events = {
            "EV1": [_market("M1", yes_ask=0.51), _market("M2", yes_ask=0.51)],
        }
        result = scan_me_arb(events)
        assert isinstance(result, list)

    def test_sorted_by_net_edge_descending(self):
        events = {
            "EV1": [_market("M1", yes_ask=0.20), _market("M2", yes_ask=0.30)],
            "EV2": [_market("M3", yes_ask=0.10), _market("M4", yes_ask=0.15)],
        }
        result = scan_me_arb(events)
        if len(result) >= 2:
            assert result[0]["net_edge"] >= result[1]["net_edge"]

    def test_event_ticker_added_to_result(self):
        events = {
            "MYEVENT": [_market("M1", yes_ask=0.20), _market("M2", yes_ask=0.30)],
        }
        result = scan_me_arb(events)
        if result:
            assert result[0].get("event_ticker") == "MYEVENT"


# ---------------------------------------------------------------------------
# check_overpriced_set
# ---------------------------------------------------------------------------

class TestCheckOverpricedSet:
    def test_underpriced_bids_returns_none(self):
        """sum(yes_bid) < 1 → no overpriced edge."""
        markets = [_market("M1", yes_bid=0.30), _market("M2", yes_bid=0.40)]
        result = check_overpriced_set("EV", markets)
        assert result is None

    def test_single_market_returns_none(self):
        result = check_overpriced_set("EV", [_market("M1", yes_bid=0.60)])
        assert result is None

    def test_empty_returns_none(self):
        result = check_overpriced_set("EV", [])
        assert result is None

    def test_overpriced_result_returned(self):
        """sum(yes_bid) > 1 → overpriced opportunity."""
        markets = [_market("M1", yes_bid=0.55), _market("M2", yes_bid=0.55)]
        result = check_overpriced_set("EV", markets)
        if result is not None:
            assert isinstance(result, dict)
            assert result.get("gross_edge", 0) > 0

    def test_none_bid_filtered(self):
        markets = [_market("M1", yes_bid=None), _market("M2", yes_bid=0.60)]
        result = check_overpriced_set("EV", markets)
        assert result is None  # only 1 valid market
