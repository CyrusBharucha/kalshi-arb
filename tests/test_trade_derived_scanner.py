"""
tests/test_trade_derived_scanner.py
===================================
Unit tests for arbitrage/trade_derived_scanner.py (Class C detection).

The scanner turns raw trade-tape crossings into opportunity rows *only when the
edge survives fees and slippage*, and labels every one of them Class C so they
are never confused with book-confirmed (Class A/B) findings.

No database is required -- the SQL boundary is mocked.
"""

from __future__ import annotations

import datetime as dt
import math
from unittest.mock import MagicMock, patch

import pytest

from engine.trade_derived_scanner import (
    kalshi_fee,
    build_opportunity,
    run_trade_derived_scan,
    MIN_NET_EDGE,
)


def _row(price_a=0.60, price_b=0.60, qty_a=100, qty_b=80, **kw):
    base = {
        "market_a": "MKT-A",
        "market_b": "MKT-B",
        "confidence": 0.95,
        "observed_at": dt.datetime(2026, 6, 23, 16, 35, tzinfo=dt.timezone.utc),
        "price_a": price_a,
        "price_b": price_b,
        "qty_a": qty_a,
        "qty_b": qty_b,
        "trades_a": 3,
        "trades_b": 2,
        "last_ts_a": dt.datetime(2026, 6, 23, 16, 35, 10, tzinfo=dt.timezone.utc),
        "last_ts_b": dt.datetime(2026, 6, 23, 16, 35, 40, tzinfo=dt.timezone.utc),
    }
    base.update(kw)
    # The scan SQL returns gross_edge alongside the two prices; mirror that so
    # the fixture matches the real row shape.
    base.setdefault("gross_edge", base["price_a"] + base["price_b"] - 1.0)
    return base


class TestKalshiFee:
    def test_fee_is_zero_at_the_extremes(self):
        assert kalshi_fee(0.0) == 0.0
        assert kalshi_fee(1.0) == 0.0

    def test_fee_peaks_at_the_midpoint(self):
        assert kalshi_fee(0.5) >= kalshi_fee(0.2)
        assert kalshi_fee(0.5) >= kalshi_fee(0.8)

    def test_fee_is_rounded_up_to_the_cent(self):
        fee = kalshi_fee(0.5)
        assert math.isclose(fee * 100, round(fee * 100))

    def test_out_of_range_input_is_clamped(self):
        assert kalshi_fee(-5) == 0.0
        assert kalshi_fee(99) == 0.0


class TestBuildOpportunity:
    def test_returns_none_when_prices_do_not_cross(self):
        assert build_opportunity(_row(price_a=0.40, price_b=0.50)) is None

    def test_returns_none_when_fees_consume_the_edge(self):
        # Sum is 1.01 -- a one-cent gross edge cannot survive two-sided fees.
        assert build_opportunity(_row(price_a=0.50, price_b=0.51)) is None

    def test_builds_a_row_for_a_large_crossing(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97))
        assert opp is not None
        assert opp["gross_edge"] == pytest.approx(0.89)

    def test_classification_is_always_c(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97))
        assert opp["classification"] == "C"

    def test_status_is_historical(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97))
        assert opp["status"] == "historical"

    def test_net_edge_is_gross_less_fees_and_slippage(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97))
        expected = opp["gross_edge"] - opp["total_fees"] - opp["estimated_slippage"]
        assert opp["net_edge"] == pytest.approx(expected, abs=1e-6)

    def test_net_edge_is_strictly_less_than_gross(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97))
        assert opp["net_edge"] < opp["gross_edge"]

    def test_surviving_edge_clears_the_threshold(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97))
        assert opp["net_edge"] > MIN_NET_EDGE

    def test_both_markets_are_recorded(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97))
        assert opp["markets_involved"] == ["MKT-A", "MKT-B"]

    def test_quantity_is_the_smaller_leg(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97, qty_a=100, qty_b=80))
        assert opp["max_executable_contracts"] == 80

    def test_print_skew_is_recorded_for_honesty(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97))
        assert opp["prices_json"]["print_skew_seconds"] == pytest.approx(30.0)

    def test_notes_disclose_the_class_c_limitation(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97))
        notes = opp["notes"].lower()
        assert "class c" in notes
        assert "not evidence" in notes

    def test_naive_timestamp_is_made_utc_aware(self):
        opp = build_opportunity(
            _row(price_a=0.92, price_b=0.97,
                 observed_at=dt.datetime(2026, 6, 23, 16, 35))
        )
        assert opp["detected_at"].tzinfo is not None

    def test_missing_quantities_fall_back_to_default(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97, qty_a=0, qty_b=0))
        assert opp["max_executable_contracts"] >= 1.0

    def test_profit_scales_with_quantity(self):
        opp = build_opportunity(_row(price_a=0.92, price_b=0.97, qty_a=10, qty_b=10))
        assert opp["max_net_profit"] == pytest.approx(opp["net_edge"] * 10, abs=1e-4)


class TestRunTradeDerivedScan:
    def _run(self, raw_rows, persist=False):
        with patch("engine.trade_derived_scanner.find_trade_derived_violations",
                   return_value=raw_rows):
            return run_trade_derived_scan(engine=MagicMock(), persist=persist)

    def test_reports_zero_when_the_tape_is_clean(self):
        stats = self._run([])
        assert stats["violations_found"] == 0
        assert stats["survived_fees"] == 0

    def test_counts_raw_violations_separately_from_survivors(self):
        rows = [
            _row(price_a=0.92, price_b=0.97),   # survives
            _row(price_a=0.50, price_b=0.51),   # eaten by fees
        ]
        stats = self._run(rows)
        assert stats["violations_found"] == 2
        assert stats["survived_fees"] == 1

    def test_reports_the_classification(self):
        assert self._run([_row(price_a=0.92, price_b=0.97)])["classification"] == "C"

    def test_carries_the_caveat_forward(self):
        stats = self._run([_row(price_a=0.92, price_b=0.97)])
        assert "upper bound" in stats["caveat"]

    def test_max_gross_edge_reflects_the_widest_crossing(self):
        rows = [_row(price_a=0.92, price_b=0.97), _row(price_a=0.55, price_b=0.55)]
        assert self._run(rows)["max_gross_edge"] == pytest.approx(0.89)

    def test_nothing_is_written_when_persist_is_false(self):
        engine = MagicMock()
        with patch("engine.trade_derived_scanner.find_trade_derived_violations",
                   return_value=[_row(price_a=0.92, price_b=0.97)]):
            stats = run_trade_derived_scan(engine=engine, persist=False)
        assert stats["opportunities_saved"] == 0

    def test_persist_purges_before_writing_so_rescans_do_not_duplicate(self):
        engine = MagicMock()
        with patch("engine.trade_derived_scanner.find_trade_derived_violations",
                   return_value=[_row(price_a=0.92, price_b=0.97)]), \
             patch("engine.trade_derived_scanner.purge_existing_class_c",
                   return_value=5) as purge, \
             patch("engine.trade_derived_scanner.save_opportunities",
                   return_value=1) as save:
            stats = run_trade_derived_scan(engine=engine, persist=True, replace=True)
        purge.assert_called_once()
        save.assert_called_once()
        assert stats["prior_class_c_purged"] == 5
        assert stats["opportunities_saved"] == 1

    def test_replace_false_skips_the_purge(self):
        engine = MagicMock()
        with patch("engine.trade_derived_scanner.find_trade_derived_violations",
                   return_value=[]), \
             patch("engine.trade_derived_scanner.purge_existing_class_c") as purge, \
             patch("engine.trade_derived_scanner.save_opportunities",
                   return_value=0):
            run_trade_derived_scan(engine=engine, persist=True, replace=False)
        purge.assert_not_called()
