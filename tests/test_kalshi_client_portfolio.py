"""
tests/test_kalshi_client_portfolio.py
========================================
Unit tests for data/kalshi_client.KalshiClient — portfolio + historical endpoints:
  - get_balance(): requires auth, calls /portfolio/balance
  - get_positions(): requires auth, calls /portfolio/positions
  - get_fills(): requires auth, optional params
  - get_historical_trades(): optional params
  - all_historical_markets(): calls paginate correctly
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


def _client(authenticated=False):
    from feeds.kalshi_client import KalshiClient
    c = KalshiClient(authenticated=authenticated)
    c.get = MagicMock(return_value={"ok": True})
    return c


def _auth_client():
    from feeds.kalshi_client import KalshiClient
    with patch("feeds.kalshi_client._load_private_key", return_value=MagicMock()), \
         patch("feeds.kalshi_client.config") as mock_cfg:
        mock_cfg.KALSHI_KEY_ID = "test-key"
        mock_cfg.KALSHI_PRIVKEY_PATH = "/fake/path.pem"
        mock_cfg.KALSHI_BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"
        c = KalshiClient(authenticated=True)
    c.get = MagicMock(return_value={"ok": True})
    return c


# ---------------------------------------------------------------------------
# get_balance
# ---------------------------------------------------------------------------

class TestGetBalance:
    def test_raises_without_auth(self):
        c = _client(authenticated=False)
        with pytest.raises(RuntimeError):
            c.get_balance()

    def test_calls_get_with_auth(self):
        c = _auth_client()
        c.get_balance()
        c.get.assert_called_once()

    def test_path_contains_balance(self):
        c = _auth_client()
        c.get_balance()
        path = c.get.call_args[0][0]
        assert "balance" in path


# ---------------------------------------------------------------------------
# get_positions
# ---------------------------------------------------------------------------

class TestGetPositions:
    def test_raises_without_auth(self):
        c = _client(authenticated=False)
        with pytest.raises(RuntimeError):
            c.get_positions()

    def test_calls_get_with_auth(self):
        c = _auth_client()
        c.get_positions()
        c.get.assert_called_once()

    def test_path_contains_positions(self):
        c = _auth_client()
        c.get_positions()
        path = c.get.call_args[0][0]
        assert "positions" in path


# ---------------------------------------------------------------------------
# get_fills
# ---------------------------------------------------------------------------

class TestGetFills:
    def test_raises_without_auth(self):
        c = _client(authenticated=False)
        with pytest.raises(RuntimeError):
            c.get_fills()

    def test_calls_get_with_auth(self):
        c = _auth_client()
        c.get_fills()
        c.get.assert_called_once()

    def test_path_contains_fills(self):
        c = _auth_client()
        c.get_fills()
        path = c.get.call_args[0][0]
        assert "fills" in path

    def test_ticker_in_params_when_provided(self):
        c = _auth_client()
        c.get_fills(ticker="MKT-A")
        params = c.get.call_args[1]["params"]
        assert params["ticker"] == "MKT-A"

    def test_no_ticker_not_in_params(self):
        c = _auth_client()
        c.get_fills()
        params = c.get.call_args[1]["params"]
        assert "ticker" not in params

    def test_min_ts_max_ts_forwarded(self):
        c = _auth_client()
        c.get_fills(min_ts=100, max_ts=200)
        params = c.get.call_args[1]["params"]
        assert params["min_ts"] == 100
        assert params["max_ts"] == 200


# ---------------------------------------------------------------------------
# get_historical_trades
# ---------------------------------------------------------------------------

class TestGetHistoricalTrades:
    def test_calls_get(self):
        c = _client()
        c.get_historical_trades()
        c.get.assert_called_once()

    def test_path_contains_historical_trades(self):
        c = _client()
        c.get_historical_trades()
        path = c.get.call_args[0][0]
        assert "historical" in path and "trades" in path

    def test_ticker_in_params_when_provided(self):
        c = _client()
        c.get_historical_trades(ticker="T-1")
        params = c.get.call_args[1]["params"]
        assert params["ticker"] == "T-1"

    def test_no_ticker_not_in_params(self):
        c = _client()
        c.get_historical_trades()
        params = c.get.call_args[1]["params"]
        assert "ticker" not in params


# ---------------------------------------------------------------------------
# all_historical_markets
# ---------------------------------------------------------------------------

class TestAllHistoricalMarkets:
    def test_yields_items_from_pages(self):
        c = _client()
        c.paginate = MagicMock(return_value=iter([[{"ticker": "T-A"}, {"ticker": "T-B"}]]))
        result = list(c.all_historical_markets())
        assert len(result) == 2
        assert result[0]["ticker"] == "T-A"

    def test_calls_paginate_with_historical_markets_path(self):
        c = _client()
        c.paginate = MagicMock(return_value=iter([]))
        list(c.all_historical_markets())
        path = c.paginate.call_args[0][0]
        assert "historical/markets" in path

    def test_empty_returns_empty(self):
        c = _client()
        c.paginate = MagicMock(return_value=iter([]))
        result = list(c.all_historical_markets())
        assert result == []
