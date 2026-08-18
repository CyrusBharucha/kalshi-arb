"""
tests/test_scan_cross_event.py
================================
Unit tests for scripts/scan_cross_event.py ticker parsing and arb math.
No network calls — all functions under test are pure.
"""
from __future__ import annotations

import sys
import os
from datetime import date

import pytest

# Make scripts/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from scan_cross_event import (
    _parse_ticker_parts,
    _fee,
    _get_yes_ask,
    _get_no_ask,
    _is_point_in_time,
    _leg_volume,
)


class TestParseTickerParts:
    def test_yymm_pattern(self):
        base, expiry, threshold = _parse_ticker_parts("KXFED-26SEP-T5.25")
        assert base      == "KXFED"
        assert expiry    == date(2026, 9, 1)
        assert threshold == "T5.25"

    def test_yymmdd_pattern(self):
        base, expiry, threshold = _parse_ticker_parts("INXU-26SEP30-B5600")
        assert base      == "INXU"
        assert expiry    == date(2026, 9, 30)
        assert threshold == "B5600"

    def test_long_dated_yymmdd(self):
        base, expiry, threshold = _parse_ticker_parts("KXUSCPIYEAR-35FEB01-T1.0")
        assert base      == "KXUSCPIYEAR"
        assert expiry    == date(2035, 2, 1)
        assert threshold == "T1.0"

    def test_multi_part_threshold(self):
        base, expiry, threshold = _parse_ticker_parts("KXBOC-26SEP-T2.25")
        assert base      == "KXBOC"
        assert expiry    == date(2026, 9, 1)
        assert threshold == "T2.25"

    def test_no_date_returns_none(self):
        base, expiry, threshold = _parse_ticker_parts("KXNFLT100TOP-26T25")
        assert expiry    is None
        assert threshold == ""

    def test_too_few_parts(self):
        base, expiry, threshold = _parse_ticker_parts("SINGLETICKER")
        assert expiry is None

    def test_december(self):
        _, expiry, _ = _parse_ticker_parts("KXFEDFUNDSYEAR-31DEC01-T5.00")
        assert expiry == date(2031, 12, 1)

    def test_jan_2030(self):
        _, expiry, _ = _parse_ticker_parts("KXFEDFUNDSYEAR-30JAN01-T1.50")
        assert expiry == date(2030, 1, 1)


class TestFee:
    def test_low_price(self):
        # 7% * 0.1 * 0.9 = 0.63c → rounds to 0c (0.0063 → rounds to 0)
        # Actually: 0.07 * 0.1 * 0.9 * 100 = 0.63, /10000 = 0.000063 → 0.0001 rounded
        fee = _fee(0.10)
        assert 0 <= fee <= 0.035

    def test_max_fee(self):
        # At p=0.5, fee = 0.07 * 0.5 * 0.5 * 100 / 10000 = 0.0175 → well under 0.035
        fee = _fee(0.5)
        assert fee <= 0.035

    def test_fee_capped(self):
        # Very extreme: fee can never exceed 0.035
        assert _fee(0.0) <= 0.035
        assert _fee(1.0) <= 0.035
        assert _fee(0.5) <= 0.035


class TestGetAsk:
    def test_yes_ask_dollars(self):
        m = {"yes_ask_dollars": "0.65"}
        assert _get_yes_ask(m) == pytest.approx(0.65)

    def test_yes_ask_cents_legacy(self):
        # Values > 1.0 are treated as cents and divided by 100
        m = {"yes_ask": "65"}
        assert _get_yes_ask(m) == pytest.approx(0.65)

    def test_no_ask_dollars(self):
        m = {"no_ask_dollars": "0.12"}
        assert _get_no_ask(m) == pytest.approx(0.12)

    def test_missing_returns_none(self):
        assert _get_yes_ask({}) is None
        assert _get_no_ask({})  is None

    def test_fractional_dollar_passthrough(self):
        m = {"yes_ask_dollars": "0.073"}
        assert _get_yes_ask(m) == pytest.approx(0.073)


class TestIsPointInTime:
    def test_in_december(self):
        m = {"title": "Will the CPI in December 2034 be above 1%?"}
        assert _is_point_in_time(m) is True

    def test_on_december(self):
        m = {"title": "Will the Fed rate on December 31, 2034 be above 1.5%?",
             "rules_primary": ""}
        assert _is_point_in_time(m) is True

    def test_cumulative_not_flagged(self):
        # A "has X happened before Y?" market won't contain point-in-time keywords
        m = {"title": "Will Bitcoin reach $100,000 before November 2026?",
             "rules_primary": ""}
        assert _is_point_in_time(m) is False

    def test_at_time(self):
        m = {"title": "Something", "rules_primary": "rate in effect at 11:59 PM ET"}
        assert _is_point_in_time(m) is True


class TestLegVolume:
    def test_sums_fp_and_oi(self):
        m = {"volume_fp": "425.91", "open_interest_fp": "136.31"}
        assert _leg_volume(m) == pytest.approx(562.22)

    def test_missing_fields(self):
        assert _leg_volume({}) == 0.0

    def test_none_fields(self):
        m = {"volume_fp": None, "open_interest_fp": None}
        assert _leg_volume(m) == 0.0
