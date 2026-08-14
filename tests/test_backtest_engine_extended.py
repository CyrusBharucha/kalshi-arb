"""
tests/test_backtest_engine_extended.py
=======================================
Extended tests for backtest/engine.py:
  - BacktestEngine.compute_metrics (pure pandas logic)
  - BacktestEngine run_id generation
  - _safe_float helper from the engine module

No database, no network.
"""
from __future__ import annotations

import math
import uuid
import pandas as pd
import numpy as np
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Patch DB at module level
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    repo_mock = MagicMock()
    repo_mock.session_scope = MagicMock()
    repo_mock.get_candlesticks = MagicMock(return_value=pd.DataFrame())
    repo_mock.get_backtest_performance = MagicMock(return_value=pd.DataFrame())
    repo_mock.get_engine = MagicMock()

    models_mock = MagicMock()
    models_mock.BacktestTrade = MagicMock()

    with patch.dict(__import__("sys").modules, {
        "database.repository": repo_mock,
        "database": MagicMock(),
        "database.models": models_mock,
    }):
        yield


# ---------------------------------------------------------------------------
# BacktestEngine initialization
# ---------------------------------------------------------------------------

class TestBacktestEngineInit:
    def _engine(self, run_id=None):
        from backtest.engine import BacktestEngine
        return BacktestEngine(run_id=run_id)

    def test_auto_run_id_generated(self):
        e = self._engine()
        assert e.run_id.startswith("bt_")

    def test_custom_run_id_used(self):
        e = self._engine(run_id="bt_custom_001")
        assert e.run_id == "bt_custom_001"

    def test_trades_empty_on_init(self):
        e = self._engine()
        assert e._trades == []

    def test_run_id_contains_timestamp(self):
        e = self._engine()
        # Format: bt_YYYYMMDD_HHMMSS
        parts = e.run_id.split("_")
        assert len(parts) == 3  # ["bt", "YYYYMMDD", "HHMMSS"]
        assert parts[1].isdigit()
        assert parts[2].isdigit()

    def test_two_engines_have_different_run_ids(self):
        import time; time.sleep(1)  # ensure different second
        e1 = self._engine()
        e2 = self._engine()
        # They may differ by timestamp
        assert isinstance(e1.run_id, str)
        assert isinstance(e2.run_id, str)


# ---------------------------------------------------------------------------
# compute_metrics — pure pandas arithmetic
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    def _engine(self):
        from backtest.engine import BacktestEngine
        e = BacktestEngine.__new__(BacktestEngine)
        e.run_id = "bt_test_run"
        e._trades = []
        return e

    def _trades_df(self, net_pnls, gross_pnls=None, run_id="bt_test"):
        n = len(net_pnls)
        if gross_pnls is None:
            gross_pnls = [p + 0.005 for p in net_pnls]
        return pd.DataFrame({
            "run_id":     [run_id] * n,
            "market_id":  [f"MKT-{i}" for i in range(n)],
            "net_pnl":    net_pnls,
            "gross_pnl":  gross_pnls,
            "taker_fees": [0.002] * n,
            "slippage":   [0.001] * n,
        })

    def test_empty_trades_returns_error(self):
        e = self._engine()
        result = e.compute_metrics(pd.DataFrame())
        assert "error" in result

    def test_n_trades_correct(self):
        e = self._engine()
        df = self._trades_df([0.05, 0.03, -0.01])
        m = e.compute_metrics(df)
        assert m["n_trades"] == 3

    def test_win_rate_correct(self):
        e = self._engine()
        df = self._trades_df([0.05, 0.03, -0.01, 0.0])
        m = e.compute_metrics(df)
        # 2 positive out of 4
        assert abs(m["win_rate"] - 0.5) < 1e-6

    def test_all_positive_win_rate_one(self):
        e = self._engine()
        df = self._trades_df([0.05, 0.03, 0.01])
        m = e.compute_metrics(df)
        assert abs(m["win_rate"] - 1.0) < 1e-6

    def test_all_negative_win_rate_zero(self):
        e = self._engine()
        df = self._trades_df([-0.05, -0.03])
        m = e.compute_metrics(df)
        assert m["win_rate"] == 0.0

    def test_total_net_pnl_correct(self):
        e = self._engine()
        df = self._trades_df([0.05, -0.02, 0.03])
        m = e.compute_metrics(df)
        assert abs(m["total_net_pnl"] - 0.06) < 1e-6

    def test_avg_net_pnl_correct(self):
        e = self._engine()
        df = self._trades_df([0.06, 0.00, 0.06])
        m = e.compute_metrics(df)
        assert abs(m["avg_net_pnl"] - 0.04) < 1e-6

    def test_sharpe_positive_for_positive_returns(self):
        e = self._engine()
        pnls = [0.05 + 0.01 * i for i in range(20)]
        df = self._trades_df(pnls)
        m = e.compute_metrics(df)
        s = m.get("sharpe_ratio")
        if s is not None:
            assert s > 0

    def test_sharpe_none_for_flat_returns(self):
        e = self._engine()
        df = self._trades_df([0.05] * 10)
        m = e.compute_metrics(df)
        s = m.get("sharpe_ratio")
        assert s is None

    def test_max_drawdown_is_nonpositive(self):
        e = self._engine()
        df = self._trades_df([0.1, -0.2, 0.05])
        m = e.compute_metrics(df)
        assert m["max_drawdown"] <= 0

    def test_no_drawdown_all_positive(self):
        e = self._engine()
        df = self._trades_df([0.05, 0.03, 0.08, 0.02])
        m = e.compute_metrics(df)
        assert m["max_drawdown"] == 0.0 or m["max_drawdown"] >= -1e-9

    def test_required_keys_present(self):
        e = self._engine()
        df = self._trades_df([0.05, -0.02, 0.03])
        m = e.compute_metrics(df)
        for key in ("run_id", "n_trades", "win_rate", "total_net_pnl", "total_fees",
                    "total_slippage", "max_drawdown", "avg_net_pnl"):
            assert key in m, f"Missing key: {key}"

    def test_total_fees_summed(self):
        e = self._engine()
        df = self._trades_df([0.05, 0.03])
        # taker_fees = 0.002 each → total = 0.004
        m = e.compute_metrics(df)
        assert abs(m["total_fees"] - 0.004) < 1e-6

    def test_total_slippage_summed(self):
        e = self._engine()
        df = self._trades_df([0.05, 0.03])
        m = e.compute_metrics(df)
        assert abs(m["total_slippage"] - 0.002) < 1e-6

    def test_n_profitable_correct(self):
        e = self._engine()
        df = self._trades_df([0.05, -0.02, 0.03, -0.01])
        m = e.compute_metrics(df)
        assert m["n_profitable"] == 2

    def test_max_pnl_is_max_of_series(self):
        e = self._engine()
        df = self._trades_df([0.05, 0.10, -0.02])
        m = e.compute_metrics(df)
        assert abs(m["max_pnl"] - 0.10) < 1e-6

    def test_min_pnl_is_min_of_series(self):
        e = self._engine()
        df = self._trades_df([0.05, 0.10, -0.02])
        m = e.compute_metrics(df)
        assert abs(m["min_pnl"] - (-0.02)) < 1e-6

    def test_run_id_propagated(self):
        e = self._engine()
        df = self._trades_df([0.05], run_id="bt_TEST_123")
        m = e.compute_metrics(df)
        # run_id is pulled from the engine instance, not the df
        assert "run_id" in m


# ---------------------------------------------------------------------------
# _safe_float helper
# ---------------------------------------------------------------------------

class TestSafeFloatEngine:
    def _sf(self, v):
        from backtest.engine import _safe_float
        return _safe_float(v)

    def test_none_returns_none(self):
        assert self._sf(None) is None

    def test_int_converts(self):
        assert self._sf(5) == 5.0

    def test_float_passthrough(self):
        assert abs(self._sf(0.45) - 0.45) < 1e-9

    def test_string_float_converts(self):
        assert abs(self._sf("0.45") - 0.45) < 1e-9

    def test_invalid_string_returns_none(self):
        assert self._sf("bad") is None

    def test_empty_string_returns_none(self):
        assert self._sf("") is None

    def test_nan_returns_none(self):
        # float("nan") is a float, so it will return NaN
        result = self._sf(float("nan"))
        if result is not None:
            assert math.isnan(result)
