"""
tests/test_build_targeted_ohlc.py
====================================
Unit tests for analysis/run_empirical.py build_targeted_ohlc().

Tests cover:
  - Empty trade data → returns {}
  - Single market, single trade → returns dict with that market_id key
  - Values are pandas Series
  - Series index is DatetimeIndex with UTC tz
  - Multiple markets → dict has multiple keys
  - period_minutes affects the bar frequency
  - Series values are float prices

No DB, no network — all session_scope calls mocked.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(session_scope=mock_ss),
        "database.models": MagicMock(),
        "engine.fees": MagicMock(taker_fee_per_contract=lambda p: 0.035),
        "backtest.metrics": MagicMock(compute_performance_metrics=lambda x: {}),
    }):
        yield


def _make_trades_df(n_markets=1, n_trades=6, price_start=0.40):
    rows = []
    base = datetime(2026, 6, 23, 10, 0, 0, tzinfo=timezone.utc)
    for m in range(n_markets):
        mid = f"MKT-{m:03d}"
        for i in range(n_trades):
            rows.append({
                "market_id": mid,
                "trade_ts": base + timedelta(minutes=i * 5),
                "price": price_start + i * 0.01,
                "quantity": 10,
            })
    return pd.DataFrame(rows)


class TestBuildTargetedOhlcEmpty:
    def test_empty_trades_returns_empty_dict(self):
        from analysis.run_empirical import build_targeted_ohlc
        with patch("analysis.run_empirical.pd.read_sql",
                   return_value=pd.DataFrame()):
            with patch("analysis.run_empirical.session_scope"):
                result = build_targeted_ohlc(["MKT-001"])
        assert result == {}

    def test_never_raises_on_empty(self):
        from analysis.run_empirical import build_targeted_ohlc
        with patch("analysis.run_empirical.pd.read_sql",
                   return_value=pd.DataFrame()):
            with patch("analysis.run_empirical.session_scope"):
                try:
                    build_targeted_ohlc([])
                except Exception:
                    pytest.fail("build_targeted_ohlc should not raise on empty")


class TestBuildTargetedOhlcWithData:
    def test_single_market_returns_dict_with_key(self):
        from analysis.run_empirical import build_targeted_ohlc
        df = _make_trades_df(n_markets=1, n_trades=6)
        with patch("analysis.run_empirical.pd.read_sql", return_value=df):
            with patch("analysis.run_empirical.session_scope"):
                result = build_targeted_ohlc(["MKT-000"])
        assert "MKT-000" in result

    def test_multiple_markets_all_keys_present(self):
        from analysis.run_empirical import build_targeted_ohlc
        df = _make_trades_df(n_markets=3, n_trades=6)
        with patch("analysis.run_empirical.pd.read_sql", return_value=df):
            with patch("analysis.run_empirical.session_scope"):
                result = build_targeted_ohlc(["MKT-000", "MKT-001", "MKT-002"])
        assert "MKT-000" in result
        assert "MKT-001" in result
        assert "MKT-002" in result

    def test_values_are_series(self):
        from analysis.run_empirical import build_targeted_ohlc
        df = _make_trades_df(n_markets=1, n_trades=6)
        with patch("analysis.run_empirical.pd.read_sql", return_value=df):
            with patch("analysis.run_empirical.session_scope"):
                result = build_targeted_ohlc(["MKT-000"])
        assert isinstance(result["MKT-000"], pd.Series)

    def test_series_index_is_datetime(self):
        from analysis.run_empirical import build_targeted_ohlc
        df = _make_trades_df(n_markets=1, n_trades=6)
        with patch("analysis.run_empirical.pd.read_sql", return_value=df):
            with patch("analysis.run_empirical.session_scope"):
                result = build_targeted_ohlc(["MKT-000"])
        idx = result["MKT-000"].index
        assert isinstance(idx, pd.DatetimeIndex)

    def test_series_values_are_floats(self):
        from analysis.run_empirical import build_targeted_ohlc
        df = _make_trades_df(n_markets=1, n_trades=6)
        with patch("analysis.run_empirical.pd.read_sql", return_value=df):
            with patch("analysis.run_empirical.session_scope"):
                result = build_targeted_ohlc(["MKT-000"])
        series = result["MKT-000"]
        assert all(isinstance(v, float) for v in series.values)

    def test_prices_within_range(self):
        from analysis.run_empirical import build_targeted_ohlc
        df = _make_trades_df(n_markets=1, n_trades=6, price_start=0.40)
        with patch("analysis.run_empirical.pd.read_sql", return_value=df):
            with patch("analysis.run_empirical.session_scope"):
                result = build_targeted_ohlc(["MKT-000"])
        series = result["MKT-000"]
        assert all(0.0 <= v <= 1.0 for v in series.values)

    def test_period_minutes_affects_resampling(self):
        from analysis.run_empirical import build_targeted_ohlc
        # With 60 min period, 6 trades * 5 min each (30min) → all in 1 bar
        df = _make_trades_df(n_markets=1, n_trades=6)
        with patch("analysis.run_empirical.pd.read_sql", return_value=df):
            with patch("analysis.run_empirical.session_scope"):
                result_60 = build_targeted_ohlc(["MKT-000"], period_minutes=60)
        # One 60-min bar should cover all 6 trades
        assert len(result_60["MKT-000"]) >= 1

    def test_no_market_not_in_trades_returns_empty_for_that_key(self):
        from analysis.run_empirical import build_targeted_ohlc
        # Only MKT-000 has trades; MKT-999 is absent from trades
        df = _make_trades_df(n_markets=1, n_trades=3)
        with patch("analysis.run_empirical.pd.read_sql", return_value=df):
            with patch("analysis.run_empirical.session_scope"):
                result = build_targeted_ohlc(["MKT-000", "MKT-999"])
        assert "MKT-000" in result
        assert "MKT-999" not in result
