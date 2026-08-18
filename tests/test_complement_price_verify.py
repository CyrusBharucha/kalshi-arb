"""
tests/test_complement_price_verify.py
======================================
Unit tests for verify_complement_prices() in dashboard/ws_predict.py.

Ensures that stale order-book prices are caught before surfacing a
complement arb signal in the dashboard.
"""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest

from dashboard.ws_predict import verify_complement_prices


def _mock_api_response(yes_ask: float, no_ask: float, dollars: bool = True):
    """Build a mock _kalshi_get() return value."""
    if dollars:
        return {"market": {"yes_ask_dollars": yes_ask, "no_ask_dollars": no_ask}}
    else:
        return {"market": {"yes_ask": int(yes_ask * 100), "no_ask": int(no_ask * 100)}}


TICKER = "KXTEST-MARKET"


def _fresh_cache():
    """Clear the complement price cache before each test."""
    import dashboard.ws_predict as mod
    with mod._comp_lock:
        mod._comp_cache.clear()


class TestVerifyComplementPricesValid:
    def test_matching_prices_pass(self):
        _fresh_cache()
        with patch("dashboard.ws_predict._kalshi_get", return_value=_mock_api_response(0.45, 0.52)):
            ok, reason = verify_complement_prices(TICKER, 0.45, 0.52)
        assert ok is True
        assert reason in ("ok", "cached_ok")

    def test_small_deviation_within_threshold_passes(self):
        _fresh_cache()
        # 8c deviation — under 10c threshold
        with patch("dashboard.ws_predict._kalshi_get", return_value=_mock_api_response(0.45, 0.52)):
            ok, reason = verify_complement_prices(TICKER, 0.45, 0.44, max_deviation=0.10)
        assert ok is True

    def test_cents_field_fallback(self):
        """Use yes_ask / no_ask (cents) when dollars fields absent."""
        _fresh_cache()
        resp = {"market": {"yes_ask": 45, "no_ask": 52}}
        with patch("dashboard.ws_predict._kalshi_get", return_value=resp):
            ok, reason = verify_complement_prices(TICKER, 0.45, 0.52)
        assert ok is True


class TestVerifyComplementPricesRejected:
    def test_stale_yes_ask_blocked(self):
        """YES ask in order book is 20c above API — stale resting order."""
        _fresh_cache()
        # Order book says YES=0.25, API says YES=0.45 → 20c deviation
        with patch("dashboard.ws_predict._kalshi_get", return_value=_mock_api_response(0.45, 0.52)):
            ok, reason = verify_complement_prices(TICKER, 0.25, 0.52)
        assert ok is False
        assert "yes_ask_deviation" in reason

    def test_stale_no_ask_blocked(self):
        """NO ask in order book is 30c below API — stale resting order."""
        _fresh_cache()
        # Order book says NO=0.20, API says NO=0.52 → 32c deviation
        with patch("dashboard.ws_predict._kalshi_get", return_value=_mock_api_response(0.45, 0.52)):
            ok, reason = verify_complement_prices(TICKER, 0.45, 0.20)
        assert ok is False
        assert "no_ask_deviation" in reason

    def test_alaska_primary_scenario(self):
        """Replicate Alaska governor primary false positive: NO ask stale at 0.03."""
        _fresh_cache()
        # API says market is live at YES=0.75, NO=0.30 — not arb at all
        # Order book stale: YES=0.75, NO=0.03 (resting order never cancelled)
        with patch("dashboard.ws_predict._kalshi_get", return_value=_mock_api_response(0.75, 0.30)):
            ok, reason = verify_complement_prices(TICKER, 0.75, 0.03)
        assert ok is False

    def test_no_api_price_passthrough(self):
        """Missing price in API response → pass through (cannot verify, so don't block)."""
        _fresh_cache()
        with patch("dashboard.ws_predict._kalshi_get", return_value={"market": {}}):
            ok, reason = verify_complement_prices(TICKER, 0.45, 0.52)
        assert ok is True
        assert reason == "no_api_price_passthrough"

    def test_api_error_passthrough(self):
        """API exception → pass through (only block on confirmed stale price, not on errors)."""
        _fresh_cache()
        with patch("dashboard.ws_predict._kalshi_get", side_effect=RuntimeError("timeout")):
            ok, reason = verify_complement_prices(TICKER, 0.45, 0.52)
        assert ok is True
        assert reason == "api_error_passthrough"

    def test_exact_threshold_boundary(self):
        """Deviation exactly at threshold is rejected (> not >=)."""
        _fresh_cache()
        # API=0.45, WS=0.35 → exactly 0.10 deviation
        with patch("dashboard.ws_predict._kalshi_get", return_value=_mock_api_response(0.45, 0.52)):
            ok, reason = verify_complement_prices(TICKER, 0.35, 0.52, max_deviation=0.10)
        assert ok is False

    def test_just_under_threshold_passes(self):
        """Deviation just under 10c passes."""
        _fresh_cache()
        # API=0.45, WS=0.36 → 9c deviation
        with patch("dashboard.ws_predict._kalshi_get", return_value=_mock_api_response(0.45, 0.52)):
            ok, reason = verify_complement_prices(TICKER, 0.36, 0.52, max_deviation=0.10)
        assert ok is True


class TestVerifyComplementPricesCaching:
    def test_cached_result_not_re_fetched(self):
        """Second call within TTL should not call API again."""
        _fresh_cache()
        with patch("dashboard.ws_predict._kalshi_get", return_value=_mock_api_response(0.45, 0.52)) as mock_get:
            verify_complement_prices(TICKER, 0.45, 0.52)
            verify_complement_prices(TICKER, 0.45, 0.52)
        assert mock_get.call_count == 1

    def test_cached_passthrough_returned(self):
        """Cached passthrough (api_error) is returned without re-fetching."""
        _fresh_cache()
        with patch("dashboard.ws_predict._kalshi_get", side_effect=RuntimeError("timeout")) as mock_get:
            ok1, r1 = verify_complement_prices(TICKER, 0.45, 0.52)
            ok2, r2 = verify_complement_prices(TICKER, 0.45, 0.52)
        assert mock_get.call_count == 1
        assert ok1 is True   # api errors now pass through
        assert ok2 is True

    def test_different_tickers_cached_separately(self):
        """Cache key is per-ticker — different tickers each hit API."""
        _fresh_cache()
        ticker_b = "KXTEST-OTHER"
        with patch("dashboard.ws_predict._kalshi_get", return_value=_mock_api_response(0.45, 0.52)) as mock_get:
            verify_complement_prices(TICKER, 0.45, 0.52)
            verify_complement_prices(ticker_b, 0.45, 0.52)
        assert mock_get.call_count == 2
