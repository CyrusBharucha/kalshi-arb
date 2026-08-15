"""
tests/test_historical_arb_summary_query.py
============================================
Unit tests for database/repository.get_historical_arbitrage_summary().

Tests:
  - Calls pd.read_sql, returns DataFrame
  - min_net_edge always in params
  - strategy_type added to params when provided
  - classification added when provided
  - start_date added when provided
  - end_date added when provided
  - No optional params → no optional keys in params
  - All optional params → all in params

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


class TestGetHistoricalArbSummaryBasic:
    def test_calls_read_sql(self):
        from database.repository import get_historical_arbitrage_summary
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_historical_arbitrage_summary(session)
        mock_rs.assert_called_once()

    def test_returns_dataframe(self):
        from database.repository import get_historical_arbitrage_summary
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()):
            result = get_historical_arbitrage_summary(session)
        assert isinstance(result, pd.DataFrame)

    def test_min_net_edge_in_params(self):
        from database.repository import get_historical_arbitrage_summary
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_historical_arbitrage_summary(session, min_net_edge=0.05)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert params["min_net_edge"] == 0.05

    def test_default_min_net_edge_zero(self):
        from database.repository import get_historical_arbitrage_summary
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_historical_arbitrage_summary(session)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert params["min_net_edge"] == 0.0


class TestGetHistoricalArbSummaryFilters:
    def test_strategy_type_in_params_when_provided(self):
        from database.repository import get_historical_arbitrage_summary
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_historical_arbitrage_summary(session, strategy_type="sell_yes_both_me")
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert params["strategy_type"] == "sell_yes_both_me"

    def test_no_strategy_type_not_in_params(self):
        from database.repository import get_historical_arbitrage_summary
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_historical_arbitrage_summary(session)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert "strategy_type" not in params

    def test_classification_in_params_when_provided(self):
        from database.repository import get_historical_arbitrage_summary
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_historical_arbitrage_summary(session, classification="A")
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert params["classification"] == "A"

    def test_start_date_in_params_when_provided(self):
        from database.repository import get_historical_arbitrage_summary
        session = _mock_session()
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_historical_arbitrage_summary(session, start_date=ts)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert "start_date" in params

    def test_end_date_in_params_when_provided(self):
        from database.repository import get_historical_arbitrage_summary
        session = _mock_session()
        ts = datetime(2026, 6, 30, tzinfo=timezone.utc)
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_historical_arbitrage_summary(session, end_date=ts)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert "end_date" in params

    def test_no_dates_not_in_params(self):
        from database.repository import get_historical_arbitrage_summary
        session = _mock_session()
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_historical_arbitrage_summary(session)
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert "start_date" not in params
        assert "end_date" not in params

    def test_all_filters_all_in_params(self):
        from database.repository import get_historical_arbitrage_summary
        session = _mock_session()
        ts1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ts2 = datetime(2026, 6, 30, tzinfo=timezone.utc)
        with patch("database.repository.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            get_historical_arbitrage_summary(
                session,
                strategy_type="sell_yes_both_me",
                classification="B",
                min_net_edge=0.03,
                start_date=ts1,
                end_date=ts2,
            )
        call = mock_rs.call_args
        params = call[1].get("params") or (call[0][2] if len(call[0]) > 2 else {})
        assert "strategy_type" in params
        assert "classification" in params
        assert "start_date" in params
        assert "end_date" in params
        assert params["min_net_edge"] == 0.03
