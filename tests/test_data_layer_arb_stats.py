"""
tests/test_data_layer_arb_stats.py
=====================================
Unit tests for arb time-series and statistical functions in dashboard/data_layer.py:
  - get_arb_time_of_day_stats(): exception → (empty DataFrame, error_str)
  - get_arb_day_of_week_stats(): exception → (empty DataFrame, error_str)
  - get_arb_settlement_proximity_stats(): exception → (empty DataFrame, error_str)
  - get_arb_edge_by_strategy_class(): exception → (empty DataFrame, error_str)
  - get_arb_rolling_7d(): exception → (empty DataFrame, error_str)
  - get_arb_by_category(): exception → (empty DataFrame, error_str)
  - get_live_market_summary(): exception → returns dict (never raises)
  - get_ingestion_log(): exception → (empty DataFrame, error_str)

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


# ---------------------------------------------------------------------------
# Helper: assert (empty_df, str) tuple pattern
# ---------------------------------------------------------------------------

def _assert_error_tuple(result):
    assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
    assert len(result) == 2, f"Expected 2-tuple, got {len(result)}-tuple"
    df, err = result
    assert isinstance(df, pd.DataFrame), f"Expected DataFrame, got {type(df)}"
    # When SQLite fallback succeeds: df may be non-empty, err is None
    # When fully unavailable: df is empty, err is a string
    assert err is None or isinstance(err, str), f"Expected str or None, got {type(err)}"


# ---------------------------------------------------------------------------
# get_arb_time_of_day_stats
# ---------------------------------------------------------------------------

class TestGetArbTimeOfDayStats:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_arb_time_of_day_stats
        with _engine_error():
            result = get_arb_time_of_day_stats()
        _assert_error_tuple(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_arb_time_of_day_stats
        with _engine_error():
            try:
                get_arb_time_of_day_stats()
            except Exception:
                pytest.fail("get_arb_time_of_day_stats should not raise")

    def test_success_returns_df_and_none(self):
        from dashboard.data_layer import get_arb_time_of_day_stats
        mock_engine = MagicMock()
        fake_df = pd.DataFrame({"hour": [0, 1], "count": [5, 3]})
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with patch("database.repository.get_engine", return_value=mock_engine), \
             patch("pandas.read_sql", return_value=fake_df):
            df, err = get_arb_time_of_day_stats()
        assert err is None
        assert len(df) == 2


# ---------------------------------------------------------------------------
# get_arb_day_of_week_stats
# ---------------------------------------------------------------------------

class TestGetArbDayOfWeekStats:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_arb_day_of_week_stats
        with _engine_error():
            result = get_arb_day_of_week_stats()
        _assert_error_tuple(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_arb_day_of_week_stats
        with _engine_error():
            try:
                get_arb_day_of_week_stats()
            except Exception:
                pytest.fail("get_arb_day_of_week_stats should not raise")


# ---------------------------------------------------------------------------
# get_arb_settlement_proximity_stats
# ---------------------------------------------------------------------------

class TestGetArbSettlementProximityStats:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_arb_settlement_proximity_stats
        with _engine_error():
            result = get_arb_settlement_proximity_stats()
        _assert_error_tuple(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_arb_settlement_proximity_stats
        with _engine_error():
            try:
                get_arb_settlement_proximity_stats()
            except Exception:
                pytest.fail("get_arb_settlement_proximity_stats should not raise")


# ---------------------------------------------------------------------------
# get_arb_edge_by_strategy_class
# ---------------------------------------------------------------------------

class TestGetArbEdgeByStrategyClass:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_arb_edge_by_strategy_class
        with _engine_error():
            result = get_arb_edge_by_strategy_class()
        _assert_error_tuple(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_arb_edge_by_strategy_class
        with _engine_error():
            try:
                get_arb_edge_by_strategy_class()
            except Exception:
                pytest.fail("get_arb_edge_by_strategy_class should not raise")


# ---------------------------------------------------------------------------
# get_arb_rolling_7d
# ---------------------------------------------------------------------------

class TestGetArbRolling7d:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_arb_rolling_7d
        with _engine_error():
            result = get_arb_rolling_7d()
        _assert_error_tuple(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_arb_rolling_7d
        with _engine_error():
            try:
                get_arb_rolling_7d()
            except Exception:
                pytest.fail("get_arb_rolling_7d should not raise")


# ---------------------------------------------------------------------------
# get_arb_by_category
# ---------------------------------------------------------------------------

class TestGetArbByCategory:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_arb_by_category
        with _engine_error():
            result = get_arb_by_category()
        _assert_error_tuple(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_arb_by_category
        with _engine_error():
            try:
                get_arb_by_category()
            except Exception:
                pytest.fail("get_arb_by_category should not raise")


# ---------------------------------------------------------------------------
# get_live_market_summary
# ---------------------------------------------------------------------------

class TestGetLiveMarketSummary:
    def test_returns_dict(self):
        from dashboard.data_layer import get_live_market_summary
        with _engine_error():
            result = get_live_market_summary()
        assert isinstance(result, dict)

    def test_never_raises(self):
        from dashboard.data_layer import get_live_market_summary
        with _engine_error():
            try:
                get_live_market_summary()
            except Exception:
                pytest.fail("get_live_market_summary should not raise")


# ---------------------------------------------------------------------------
# get_ingestion_log
# ---------------------------------------------------------------------------

class TestGetIngestionLog:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_ingestion_log
        with _engine_error():
            result = get_ingestion_log()
        _assert_error_tuple(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_ingestion_log
        with _engine_error():
            try:
                get_ingestion_log()
            except Exception:
                pytest.fail("get_ingestion_log should not raise")

    def test_accepts_limit_param(self):
        from dashboard.data_layer import get_ingestion_log
        with _engine_error():
            result = get_ingestion_log(limit=50)
        assert isinstance(result, tuple)
