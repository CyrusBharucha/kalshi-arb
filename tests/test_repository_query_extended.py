"""
tests/test_repository_query_extended.py
=========================================
Unit tests for more database/repository.py query functions.

Tests:
  - get_event_market_matrix: calls read_sql with event_ticker
  - get_cross_asset_spread_history: params include market_id/asset
  - get_cross_asset_spread_history: start_ts/end_ts forwarded when provided
  - get_backtest_performance: calls read_sql with run_id
  - get_complement_candidates: calls read_sql
  - get_markets_needing_candlesticks: calls session.execute; returns list of str
  - get_external_data_series: params include asset/instrument
  - get_external_data_series: start_ts/end_ts conditional

All DB calls mocked — no live PostgreSQL.
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


def _mock_session(rows=None):
    s = MagicMock()
    s.bind = MagicMock()
    if rows is not None:
        s.execute.return_value.__iter__ = MagicMock(return_value=iter(rows))
    return s


class TestGetEventMarketMatrix:
    def test_calls_read_sql(self):
        from database.repository import get_event_market_matrix
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_event_market_matrix(session, "BOC-2026")
        mock_rs.assert_called_once()

    def test_returns_dataframe(self):
        from database.repository import get_event_market_matrix
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()):
            result = get_event_market_matrix(session, "EVT-1")
        assert isinstance(result, pd.DataFrame)

    def test_event_ticker_in_params(self):
        from database.repository import get_event_market_matrix
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_event_market_matrix(session, "BOC-2026")
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert params.get("event_ticker") == "BOC-2026"


class TestGetCrossAssetSpreadHistory:
    def test_calls_read_sql(self):
        from database.repository import get_cross_asset_spread_history
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_cross_asset_spread_history(session, "MKT-A", "BTC")
        mock_rs.assert_called_once()

    def test_returns_dataframe(self):
        from database.repository import get_cross_asset_spread_history
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()):
            result = get_cross_asset_spread_history(session, "MKT-A", "BTC")
        assert isinstance(result, pd.DataFrame)

    def test_params_include_market_id_and_asset(self):
        from database.repository import get_cross_asset_spread_history
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_cross_asset_spread_history(session, "MKT-X", "GOLD")
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert params["market_id"] == "MKT-X"
        assert params["asset"] == "GOLD"

    def test_start_ts_forwarded(self):
        from database.repository import get_cross_asset_spread_history
        session = _mock_session()
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_cross_asset_spread_history(session, "MKT-A", "BTC", start_ts=ts)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert "start_ts" in params

    def test_end_ts_forwarded(self):
        from database.repository import get_cross_asset_spread_history
        session = _mock_session()
        ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_cross_asset_spread_history(session, "MKT-A", "BTC", end_ts=ts)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert "end_ts" in params

    def test_no_timestamps_no_extra_params(self):
        from database.repository import get_cross_asset_spread_history
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_cross_asset_spread_history(session, "MKT-A", "BTC")
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert "start_ts" not in params
        assert "end_ts" not in params


class TestGetBacktestPerformance:
    def test_calls_read_sql(self):
        from database.repository import get_backtest_performance
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_backtest_performance(session, "run-123")
        mock_rs.assert_called_once()

    def test_returns_dataframe(self):
        from database.repository import get_backtest_performance
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()):
            result = get_backtest_performance(session, "run-abc")
        assert isinstance(result, pd.DataFrame)

    def test_run_id_in_params(self):
        from database.repository import get_backtest_performance
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_backtest_performance(session, "run-999")
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert params.get("run_id") == "run-999"


class TestGetComplementCandidates:
    def test_calls_read_sql(self):
        from database.repository import get_complement_candidates
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_complement_candidates(session)
        mock_rs.assert_called_once()

    def test_returns_dataframe(self):
        from database.repository import get_complement_candidates
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()):
            result = get_complement_candidates(session)
        assert isinstance(result, pd.DataFrame)


class TestGetMarketsNeedingCandlesticks:
    def test_returns_list(self):
        from database.repository import get_markets_needing_candlesticks
        session = _mock_session()
        session.execute.return_value = iter([("MKT-A",), ("MKT-B",)])
        result = get_markets_needing_candlesticks(session, 60)
        assert isinstance(result, list)

    def test_returns_market_ids(self):
        from database.repository import get_markets_needing_candlesticks
        session = _mock_session()
        session.execute.return_value = iter([("MKT-A",), ("MKT-B",)])
        result = get_markets_needing_candlesticks(session, 60)
        assert "MKT-A" in result
        assert "MKT-B" in result

    def test_empty_result_returns_empty_list(self):
        from database.repository import get_markets_needing_candlesticks
        session = _mock_session()
        session.execute.return_value = iter([])
        result = get_markets_needing_candlesticks(session, 60)
        assert result == []

    def test_calls_session_execute(self):
        from database.repository import get_markets_needing_candlesticks
        session = _mock_session()
        session.execute.return_value = iter([])
        get_markets_needing_candlesticks(session, 60, lookback_days=14)
        session.execute.assert_called_once()


class TestGetExternalDataSeries:
    def test_calls_read_sql(self):
        from database.repository import get_external_data_series
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_external_data_series(session, "rates", "CAD-OIS")
        mock_rs.assert_called_once()

    def test_asset_and_instrument_in_params(self):
        from database.repository import get_external_data_series
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_external_data_series(session, "GOLD", "SPOT")
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert params["asset"] == "GOLD"
        assert params["instrument"] == "SPOT"

    def test_start_ts_forwarded(self):
        from database.repository import get_external_data_series
        session = _mock_session()
        ts = datetime(2026, 3, 1, tzinfo=timezone.utc)
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_external_data_series(session, "BTC", "PERP", start_ts=ts)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert "start_ts" in params
