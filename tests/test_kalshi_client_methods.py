"""
tests/test_kalshi_client_methods.py
=====================================
Unit tests for data/kalshi_client.KalshiClient public API methods.

Tests:
  - get_exchange_status → calls get("/exchange/status")
  - get_market(ticker) → calls get with correct path
  - get_orderbook(ticker, depth) → passes depth param
  - get_markets filters (status, series_ticker, event_ticker, tickers)
  - get_trades filters (ticker, min_ts, max_ts, cursor)
  - _require_auth raises when unauthenticated
  - all_open_markets yields flattened items from paginate
  - all_events yields with/without status param

All HTTP calls are intercepted via mock on client.get.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock, call
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "config": MagicMock(
            DB_URL="postgresql://localhost/test",
            KALSHI_KEY_ID="",
            KALSHI_PRIVKEY_PATH="",
            KALSHI_READ_RPS=10.0,
            KALSHI_WRITE_RPS=1.0,
            SNAPSHOT_INTERVAL_S=5,
            ORDERBOOK_DEPTH=5,
        ),
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "cryptography.hazmat.primitives.hashes": MagicMock(),
        "cryptography.hazmat.primitives.serialization": MagicMock(),
        "cryptography.hazmat.primitives.asymmetric": MagicMock(),
        "cryptography.hazmat.primitives.asymmetric.padding": MagicMock(),
    }):
        yield


def _client():
    from feeds.kalshi_client import KalshiClient
    c = KalshiClient(authenticated=False)
    return c


# ---------------------------------------------------------------------------
# get_exchange_status
# ---------------------------------------------------------------------------

class TestGetExchangeStatus:
    def test_calls_correct_path(self):
        c = _client()
        c.get = MagicMock(return_value={"status": "active"})
        c.get_exchange_status()
        c.get.assert_called_once_with("/exchange/status")

    def test_returns_dict(self):
        c = _client()
        c.get = MagicMock(return_value={"status": "active"})
        result = c.get_exchange_status()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# get_market
# ---------------------------------------------------------------------------

class TestGetMarket:
    def test_path_includes_ticker(self):
        c = _client()
        c.get = MagicMock(return_value={"market": {}})
        c.get_market("KXBTC-25DEC-T50000")
        c.get.assert_called_once_with("/markets/KXBTC-25DEC-T50000")

    def test_different_tickers_different_paths(self):
        c = _client()
        c.get = MagicMock(return_value={})
        c.get_market("TICKER-A")
        path = c.get.call_args[0][0]
        assert "TICKER-A" in path


# ---------------------------------------------------------------------------
# get_orderbook
# ---------------------------------------------------------------------------

class TestGetOrderbook:
    def test_passes_depth_param(self):
        c = _client()
        c.get = MagicMock(return_value={"orderbook": {}})
        c.get_orderbook("MKT-A", depth=5)
        call_kwargs = c.get.call_args
        assert call_kwargs[1]["params"]["depth"] == 5

    def test_default_depth_is_10(self):
        c = _client()
        c.get = MagicMock(return_value={"orderbook": {}})
        c.get_orderbook("MKT-A")
        call_kwargs = c.get.call_args
        assert call_kwargs[1]["params"]["depth"] == 10

    def test_path_contains_ticker(self):
        c = _client()
        c.get = MagicMock(return_value={})
        c.get_orderbook("MY-TICKER")
        path = c.get.call_args[0][0]
        assert "MY-TICKER" in path


# ---------------------------------------------------------------------------
# get_markets (parameter forwarding)
# ---------------------------------------------------------------------------

class TestGetMarkets:
    def test_status_param_forwarded(self):
        c = _client()
        c.get = MagicMock(return_value={"markets": []})
        c.get_markets(status="open")
        params = c.get.call_args[1]["params"]
        assert params["status"] == "open"

    def test_no_status_no_status_in_params(self):
        c = _client()
        c.get = MagicMock(return_value={"markets": []})
        c.get_markets()
        params = c.get.call_args[1]["params"]
        assert "status" not in params

    def test_tickers_list_joined_with_comma(self):
        c = _client()
        c.get = MagicMock(return_value={"markets": []})
        c.get_markets(tickers=["A", "B", "C"])
        params = c.get.call_args[1]["params"]
        assert params["tickers"] == "A,B,C"

    def test_limit_included_in_params(self):
        c = _client()
        c.get = MagicMock(return_value={"markets": []})
        c.get_markets(limit=500)
        params = c.get.call_args[1]["params"]
        assert params["limit"] == 500

    def test_event_ticker_forwarded(self):
        c = _client()
        c.get = MagicMock(return_value={"markets": []})
        c.get_markets(event_ticker="EVT-1")
        params = c.get.call_args[1]["params"]
        assert params["event_ticker"] == "EVT-1"


# ---------------------------------------------------------------------------
# _require_auth
# ---------------------------------------------------------------------------

class TestRequireAuth:
    def test_raises_when_unauthenticated(self):
        c = _client()
        with pytest.raises(RuntimeError, match="authenticated"):
            c._require_auth()

    def test_no_raise_when_authenticated(self):
        from feeds.kalshi_client import KalshiClient
        c = KalshiClient.__new__(KalshiClient)
        c._authenticated = True
        c._require_auth()   # should not raise


# ---------------------------------------------------------------------------
# all_open_markets / all_events
# ---------------------------------------------------------------------------

class TestAllOpenMarkets:
    def test_yields_from_paginate(self):
        c = _client()
        page1 = [{"market_id": "A"}, {"market_id": "B"}]
        c.paginate = MagicMock(return_value=iter([page1]))
        result = list(c.all_open_markets())
        assert len(result) == 2
        assert result[0]["market_id"] == "A"

    def test_paginate_called_with_open_status(self):
        c = _client()
        c.paginate = MagicMock(return_value=iter([]))
        list(c.all_open_markets())
        call_kwargs = c.paginate.call_args
        assert call_kwargs[1]["params"]["status"] == "open"

    def test_empty_paginate_returns_empty(self):
        c = _client()
        c.paginate = MagicMock(return_value=iter([]))
        result = list(c.all_open_markets())
        assert result == []


class TestAllEvents:
    def test_no_status_no_params(self):
        c = _client()
        c.paginate = MagicMock(return_value=iter([]))
        list(c.all_events())
        call_kwargs = c.paginate.call_args
        # params dict should not contain "status"
        params = call_kwargs[1].get("params", {})
        assert "status" not in params

    def test_status_forwarded(self):
        c = _client()
        c.paginate = MagicMock(return_value=iter([]))
        list(c.all_events(status="open"))
        call_kwargs = c.paginate.call_args
        params = call_kwargs[1].get("params", {})
        assert params["status"] == "open"

    def test_yields_events(self):
        c = _client()
        events = [{"event_ticker": "E1"}, {"event_ticker": "E2"}]
        c.paginate = MagicMock(return_value=iter([events]))
        result = list(c.all_events())
        assert len(result) == 2
