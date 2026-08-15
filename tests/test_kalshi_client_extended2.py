"""
tests/test_kalshi_client_extended2.py
=======================================
Unit tests for data/kalshi_client.KalshiClient — methods not covered by
test_kalshi_client_methods.py:
  - get_candlesticks()
  - get_trades()
  - get_events()
  - get_event()
  - get_series()
  - get_fee_changes()
  - get_historical_cutoff()
  - get_historical_markets()
  - get_historical_market()
  - get_historical_candlesticks()

HTTP calls replaced by patching KalshiClient.get.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "config": MagicMock(
            KALSHI_BASE_URL="https://trading-api.kalshi.com/trade-api/v2",
            KALSHI_KEY_ID="test-key-id",
        ),
    }):
        yield


def _client():
    from feeds.kalshi_client import KalshiClient
    c = KalshiClient(authenticated=False)
    c.get = MagicMock(return_value={"ok": True})
    return c


# ---------------------------------------------------------------------------
# get_candlesticks
# ---------------------------------------------------------------------------

class TestGetCandlesticks:
    def test_calls_get(self):
        c = _client()
        c.get_candlesticks(["TICK-A"], 60, 1000, 2000)
        c.get.assert_called_once()

    def test_path_contains_candlesticks(self):
        c = _client()
        c.get_candlesticks(["TICK-A"], 60, 1000, 2000)
        path = c.get.call_args[0][0]
        assert "candlesticks" in path

    def test_tickers_comma_joined_in_params(self):
        c = _client()
        c.get_candlesticks(["TICK-A", "TICK-B"], 60, 1000, 2000)
        params = c.get.call_args[1]["params"]
        assert "TICK-A,TICK-B" == params["tickers"]

    def test_period_interval_in_params(self):
        c = _client()
        c.get_candlesticks(["T"], 5, 1000, 2000)
        params = c.get.call_args[1]["params"]
        assert params["period_interval"] == 5

    def test_start_end_ts_in_params(self):
        c = _client()
        c.get_candlesticks(["T"], 60, 111, 222)
        params = c.get.call_args[1]["params"]
        assert params["start_ts"] == 111
        assert params["end_ts"] == 222


# ---------------------------------------------------------------------------
# get_trades
# ---------------------------------------------------------------------------

class TestGetTrades:
    def test_calls_get(self):
        c = _client()
        c.get_trades()
        c.get.assert_called_once()

    def test_path_contains_trades(self):
        c = _client()
        c.get_trades()
        path = c.get.call_args[0][0]
        assert "trades" in path

    def test_ticker_in_params_when_provided(self):
        c = _client()
        c.get_trades(ticker="MKT-X")
        params = c.get.call_args[1]["params"]
        assert params["ticker"] == "MKT-X"

    def test_no_ticker_not_in_params(self):
        c = _client()
        c.get_trades()
        params = c.get.call_args[1]["params"]
        assert "ticker" not in params

    def test_min_ts_max_ts_forwarded(self):
        c = _client()
        c.get_trades(min_ts=100, max_ts=200)
        params = c.get.call_args[1]["params"]
        assert params["min_ts"] == 100
        assert params["max_ts"] == 200


# ---------------------------------------------------------------------------
# get_events / get_event
# ---------------------------------------------------------------------------

class TestGetEvents:
    def test_calls_get(self):
        c = _client()
        c.get_events()
        c.get.assert_called_once()

    def test_path_is_events(self):
        c = _client()
        c.get_events()
        path = c.get.call_args[0][0]
        assert "/events" in path

    def test_status_in_params_when_provided(self):
        c = _client()
        c.get_events(status="open")
        params = c.get.call_args[1]["params"]
        assert params["status"] == "open"

    def test_no_status_not_in_params(self):
        c = _client()
        c.get_events()
        params = c.get.call_args[1]["params"]
        assert "status" not in params

    def test_with_nested_markets_adds_param(self):
        c = _client()
        c.get_events(with_nested_markets=True)
        params = c.get.call_args[1]["params"]
        assert "with_nested_markets" in params


class TestGetEvent:
    def test_calls_get(self):
        c = _client()
        c.get_event("EVT-2026")
        c.get.assert_called_once()

    def test_path_contains_event_ticker(self):
        c = _client()
        c.get_event("EVT-2026")
        path = c.get.call_args[0][0]
        assert "EVT-2026" in path


# ---------------------------------------------------------------------------
# get_series / get_fee_changes
# ---------------------------------------------------------------------------

class TestGetSeries:
    def test_calls_get(self):
        c = _client()
        c.get_series()
        c.get.assert_called_once()

    def test_path_contains_series(self):
        c = _client()
        c.get_series()
        path = c.get.call_args[0][0]
        assert "series" in path


class TestGetFeeChanges:
    def test_calls_get(self):
        c = _client()
        c.get_fee_changes()
        c.get.assert_called_once()

    def test_path_contains_fee_changes(self):
        c = _client()
        c.get_fee_changes()
        path = c.get.call_args[0][0]
        assert "fee_changes" in path


# ---------------------------------------------------------------------------
# Historical endpoints
# ---------------------------------------------------------------------------

class TestGetHistoricalCutoff:
    def test_calls_get(self):
        c = _client()
        c.get_historical_cutoff()
        c.get.assert_called_once()

    def test_path_contains_historical_cutoff(self):
        c = _client()
        c.get_historical_cutoff()
        path = c.get.call_args[0][0]
        assert "historical" in path and "cutoff" in path


class TestGetHistoricalMarkets:
    def test_calls_get(self):
        c = _client()
        c.get_historical_markets()
        c.get.assert_called_once()

    def test_path_contains_historical_markets(self):
        c = _client()
        c.get_historical_markets()
        path = c.get.call_args[0][0]
        assert "historical" in path and "markets" in path

    def test_status_in_params_when_provided(self):
        c = _client()
        c.get_historical_markets(status="finalized")
        params = c.get.call_args[1]["params"]
        assert params["status"] == "finalized"

    def test_no_status_not_in_params(self):
        c = _client()
        c.get_historical_markets()
        params = c.get.call_args[1]["params"]
        assert "status" not in params


class TestGetHistoricalMarket:
    def test_calls_get(self):
        c = _client()
        c.get_historical_market("TICK-X")
        c.get.assert_called_once()

    def test_path_contains_ticker(self):
        c = _client()
        c.get_historical_market("TICK-X")
        path = c.get.call_args[0][0]
        assert "TICK-X" in path


class TestGetHistoricalCandlesticks:
    def test_calls_get(self):
        c = _client()
        c.get_historical_candlesticks("TICK-Y", 60, 1000, 2000)
        c.get.assert_called_once()

    def test_path_contains_ticker(self):
        c = _client()
        c.get_historical_candlesticks("TICK-Y", 60, 1000, 2000)
        path = c.get.call_args[0][0]
        assert "TICK-Y" in path

    def test_period_interval_in_params(self):
        c = _client()
        c.get_historical_candlesticks("TICK-Y", 5, 1000, 2000)
        params = c.get.call_args[1]["params"]
        assert params["period_interval"] == 5

    def test_start_end_ts_in_params(self):
        c = _client()
        c.get_historical_candlesticks("TICK-Y", 60, 333, 444)
        params = c.get.call_args[1]["params"]
        assert params["start_ts"] == 333
        assert params["end_ts"] == 444
