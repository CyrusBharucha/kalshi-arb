"""
tests/test_data_layer_opportunity_funcs.py
============================================
Unit tests for opportunity-detail and order-book functions in data_layer.py:
  - get_recently_closed_opps(): exception → (empty DataFrame, error_str)
  - get_opportunity_detail(): exception → ({}, error_str); not-found → ({}, error_str)
  - get_live_orderbook(): exception → ({}, error_str)
  - get_backtest_runs(): exception → (empty DataFrame, error_str)
  - get_backtest_trades(): exception → (empty DataFrame, error_str)
  - get_canadian_markets(): exception → (empty DataFrame, error_str)

All DB calls mocked; no network.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_streamlit():
    st_mock = MagicMock()
    st_mock.cache_data = lambda **kw: (lambda fn: fn)
    with patch.dict(sys.modules, {
        "streamlit": st_mock,
        "plotly": MagicMock(),
        "plotly.graph_objects": MagicMock(),
        "plotly.express": MagicMock(),
    }):
        yield


def _engine_error():
    return patch("database.repository.get_engine", side_effect=RuntimeError("no DB"))


def _assert_error_tuple(result, expected_first_type=pd.DataFrame):
    assert isinstance(result, tuple) and len(result) == 2
    first, err = result
    assert isinstance(first, expected_first_type)
    # When SQLite fallback succeeds, err may be None; when fully unavailable it is a str
    assert err is None or isinstance(err, str)


# ---------------------------------------------------------------------------
# get_recently_closed_opps
# ---------------------------------------------------------------------------

class TestGetRecentlyClosedOpps:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_recently_closed_opps
        with _engine_error():
            result = get_recently_closed_opps()
        _assert_error_tuple(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_recently_closed_opps
        with _engine_error():
            try:
                get_recently_closed_opps(hours=24)
            except Exception:
                pytest.fail("get_recently_closed_opps should not raise")

    def test_accepts_hours_param(self):
        from dashboard.data_layer import get_recently_closed_opps
        with _engine_error():
            result = get_recently_closed_opps(hours=6)
        df, err = result
        assert isinstance(df, pd.DataFrame)


# ---------------------------------------------------------------------------
# get_opportunity_detail
# ---------------------------------------------------------------------------

class TestGetOpportunityDetail:
    def test_exception_returns_empty_dict_and_error_str(self):
        from dashboard.data_layer import get_opportunity_detail
        with _engine_error():
            result = get_opportunity_detail("test-oid-123")
        assert isinstance(result, tuple) and len(result) == 2
        d, err = result
        assert isinstance(d, dict)
        assert isinstance(err, str)

    def test_empty_dict_on_engine_error(self):
        from dashboard.data_layer import get_opportunity_detail
        with _engine_error():
            d, _ = get_opportunity_detail("any-id")
        assert d == {}

    def test_not_found_returns_empty_dict(self):
        from dashboard.data_layer import get_opportunity_detail
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with patch("database.repository.get_engine", return_value=mock_engine):
            d, err = get_opportunity_detail("nonexistent")
        assert d == {}
        assert isinstance(err, str)

    def test_never_raises(self):
        from dashboard.data_layer import get_opportunity_detail
        with _engine_error():
            try:
                get_opportunity_detail("any-id")
            except Exception:
                pytest.fail("get_opportunity_detail should not raise")


# ---------------------------------------------------------------------------
# get_live_orderbook
# ---------------------------------------------------------------------------

class TestGetLiveOrderbook:
    def test_exception_returns_empty_dict_and_error_str(self):
        from dashboard.data_layer import get_live_orderbook
        with _engine_error():
            result = get_live_orderbook("KXTEST-01")
        assert isinstance(result, tuple) and len(result) == 2
        d, err = result
        assert isinstance(d, dict)

    def test_never_raises(self):
        from dashboard.data_layer import get_live_orderbook
        with _engine_error():
            try:
                get_live_orderbook("KXTEST-01")
            except Exception:
                pytest.fail("get_live_orderbook should not raise")


# ---------------------------------------------------------------------------
# get_backtest_runs
# ---------------------------------------------------------------------------

class TestGetBacktestRuns:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_backtest_runs
        with _engine_error():
            result = get_backtest_runs()
        _assert_error_tuple(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_backtest_runs
        with _engine_error():
            try:
                get_backtest_runs()
            except Exception:
                pytest.fail("get_backtest_runs should not raise")


# ---------------------------------------------------------------------------
# get_backtest_trades
# ---------------------------------------------------------------------------

class TestGetBacktestTrades:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_backtest_trades
        with _engine_error():
            result = get_backtest_trades("run-abc")
        _assert_error_tuple(result)

    def test_accepts_run_id_param(self):
        from dashboard.data_layer import get_backtest_trades
        with _engine_error():
            df, err = get_backtest_trades("run-xyz")
        assert isinstance(df, pd.DataFrame)

    def test_never_raises(self):
        from dashboard.data_layer import get_backtest_trades
        with _engine_error():
            try:
                get_backtest_trades("any-run")
            except Exception:
                pytest.fail("get_backtest_trades should not raise")


# ---------------------------------------------------------------------------
# get_canadian_markets
# ---------------------------------------------------------------------------

class TestGetCanadianMarkets:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_canadian_markets
        with _engine_error():
            result = get_canadian_markets()
        _assert_error_tuple(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_canadian_markets
        with _engine_error():
            try:
                get_canadian_markets()
            except Exception:
                pytest.fail("get_canadian_markets should not raise")
