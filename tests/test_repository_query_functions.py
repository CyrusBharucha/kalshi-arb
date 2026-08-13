"""
tests/test_repository_query_functions.py
==========================================
Unit tests for database/repository.py query functions that use pd.read_sql.

Tests:
  - get_all_open_markets: calls pd.read_sql, returns DataFrame
  - get_markets_by_event: passes event_ticker in params
  - get_candlesticks: builds correct WHERE clause with/without timestamps
  - get_live_arbitrage_opportunities: calls pd.read_sql
  - get_candlesticks with only start_ts
  - get_candlesticks with only end_ts
  - get_candlesticks with both

All DB calls mocked via patch("database.repository.pd.read_sql").
"""
from __future__ import annotations

import sys
import pandas as pd
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
    }):
        yield


def _mock_session():
    s = MagicMock()
    s.bind = MagicMock()
    return s


class TestGetAllOpenMarkets:
    def test_calls_read_sql(self):
        from database.repository import get_all_open_markets
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_all_open_markets(session)
        mock_rs.assert_called_once()

    def test_returns_dataframe(self):
        from database.repository import get_all_open_markets
        session = _mock_session()
        expected = pd.DataFrame({"market_id": ["A"]})
        with patch("database.repository.pd.read_sql", return_value=expected):
            result = get_all_open_markets(session)
        assert isinstance(result, pd.DataFrame)

    def test_passes_session_get_bind(self):
        """repository.py uses session.get_bind() (SA2 compat, not session.bind)."""
        from database.repository import get_all_open_markets
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_all_open_markets(session)
        call_args = mock_rs.call_args
        assert call_args[0][1] is session.get_bind()


class TestGetMarketsByEvent:
    def test_calls_read_sql(self):
        from database.repository import get_markets_by_event
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_markets_by_event(session, "BOC-2026")
        mock_rs.assert_called_once()

    def test_returns_dataframe(self):
        from database.repository import get_markets_by_event
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()):
            result = get_markets_by_event(session, "EVT-1")
        assert isinstance(result, pd.DataFrame)

    def test_event_ticker_in_params(self):
        from database.repository import get_markets_by_event
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_markets_by_event(session, "BOC-2026")
        params = mock_rs.call_args[1].get("params") or mock_rs.call_args[0][2]
        assert params.get("event_ticker") == "BOC-2026"


class TestGetCandlesticks:
    def test_calls_read_sql(self):
        from database.repository import get_candlesticks
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_candlesticks(session, "MKT-A", 60)
        mock_rs.assert_called_once()

    def test_returns_dataframe(self):
        from database.repository import get_candlesticks
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()):
            result = get_candlesticks(session, "MKT-A", 60)
        assert isinstance(result, pd.DataFrame)

    def test_market_id_in_params(self):
        from database.repository import get_candlesticks
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_candlesticks(session, "MKT-XYZ", 60)
        # params are the 3rd positional or keyword arg
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else None)
        assert params["market_id"] == "MKT-XYZ"

    def test_period_interval_in_params(self):
        from database.repository import get_candlesticks
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_candlesticks(session, "MKT-A", 1440)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else None)
        assert params["period"] == 1440

    def test_start_ts_added_to_params(self):
        from database.repository import get_candlesticks
        session = _mock_session()
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_candlesticks(session, "MKT-A", 60, start_ts=ts)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else None)
        assert "start_ts" in params

    def test_end_ts_added_to_params(self):
        from database.repository import get_candlesticks
        session = _mock_session()
        ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_candlesticks(session, "MKT-A", 60, end_ts=ts)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else None)
        assert "end_ts" in params

    def test_no_timestamps_no_start_end_params(self):
        from database.repository import get_candlesticks
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_candlesticks(session, "MKT-A", 60)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else None)
        assert "start_ts" not in params
        assert "end_ts" not in params


class TestGetLiveArbitrageOpportunities:
    def test_calls_read_sql(self):
        from database.repository import get_live_arbitrage_opportunities
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_live_arbitrage_opportunities(session)
        mock_rs.assert_called_once()

    def test_returns_dataframe(self):
        from database.repository import get_live_arbitrage_opportunities
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()):
            result = get_live_arbitrage_opportunities(session)
        assert isinstance(result, pd.DataFrame)
