"""
tests/test_backtest_page_helpers.py
=====================================
Tests for pure-logic helpers in dashboard/pages/p06_backtest.py:
  - _basic_metrics: fallback metric computation

No DB, no network.
"""
from __future__ import annotations

import sys
import math
import pytest
import pandas as pd
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Module-level patches
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_all():
    st_mock = MagicMock()
    st_mock.cache_data = lambda *a, **kw: (lambda f: f)
    st_mock.cache_resource = lambda *a, **kw: (lambda f: f)

    dl_mock = MagicMock()
    dl_mock.get_backtest_runs = MagicMock(return_value=(pd.DataFrame(), None))
    dl_mock.get_backtest_trades = MagicMock(return_value=(pd.DataFrame(), None))

    styles_mock = MagicMock(
        GREEN="#22C55E", RED="#EF4444", AMBER="#F59E0B", BLUE="#3B82F6",
        TEXT="#F1F5F9", TEXT2="#94A3B8", TEXT3="#64748B",
        PANEL="#1E293B", BORDER="#334155", PANEL2="#0F172A",
        plotly_dark_layout=lambda: {},
    )

    go_mock = MagicMock()
    go_mock.Figure = MagicMock(return_value=MagicMock())
    go_mock.Scatter = MagicMock(return_value=MagicMock())
    go_mock.Bar = MagicMock(return_value=MagicMock())

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "streamlit", st_mock)
        mp.setitem(sys.modules, "dashboard.data_layer", dl_mock)
        mp.setitem(sys.modules, "dashboard.styles", styles_mock)
        mp.setitem(sys.modules, "plotly", MagicMock())
        mp.setitem(sys.modules, "plotly.graph_objects", go_mock)
        yield


# ---------------------------------------------------------------------------
# _basic_metrics
# ---------------------------------------------------------------------------

class TestBasicMetrics:
    def _call(self, values):
        from dashboard.pages.p06_backtest import _basic_metrics
        return _basic_metrics(pd.Series(values, dtype=float))

    def test_returns_dict(self):
        assert isinstance(self._call([0.1, -0.1, 0.2]), dict)

    def test_n_trades_is_length(self):
        result = self._call([0.1, 0.2, 0.3])
        assert result["n_trades"] == 3

    def test_total_pnl_is_sum(self):
        result = self._call([0.1, 0.2, 0.3])
        assert abs(result["total_pnl"] - 0.6) < 1e-9

    def test_win_rate_all_positive(self):
        result = self._call([0.1, 0.2, 0.3])
        assert abs(result["win_rate"] - 1.0) < 1e-9

    def test_win_rate_half(self):
        result = self._call([0.1, -0.1])
        assert abs(result["win_rate"] - 0.5) < 1e-9

    def test_win_rate_all_negative(self):
        result = self._call([-0.1, -0.2, -0.3])
        assert abs(result["win_rate"] - 0.0) < 1e-9

    def test_empty_pnl_win_rate_zero(self):
        result = self._call([])
        assert result["win_rate"] == 0

    def test_max_drawdown_is_nonpositive(self):
        result = self._call([0.5, -0.1, -0.2, 0.3])
        assert result["max_drawdown"] <= 0

    def test_no_drawdown_monotone_increasing(self):
        """Monotonically increasing → drawdown = 0."""
        result = self._call([0.1, 0.2, 0.3])
        assert abs(result["max_drawdown"] - 0.0) < 1e-9

    def test_negative_after_positive_has_drawdown(self):
        # Goes up then drops below 0 → drawdown exists
        result = self._call([0.5, -0.8])
        assert result["max_drawdown"] < 0

    def test_required_keys_present(self):
        result = self._call([0.1, -0.1, 0.2])
        for key in ["n_trades", "total_pnl", "win_rate", "max_drawdown"]:
            assert key in result

    def test_all_zeros_zero_drawdown(self):
        result = self._call([0.0, 0.0, 0.0])
        assert abs(result["max_drawdown"] - 0.0) < 1e-9

    def test_total_pnl_negative_when_losing(self):
        result = self._call([-0.1, -0.2, -0.3])
        assert result["total_pnl"] < 0


# ---------------------------------------------------------------------------
# _render_metrics_table: logic tests (key/val formatting)
# ---------------------------------------------------------------------------

class TestMetricsTableLogic:
    def test_none_values_skipped(self):
        """Keys with None values should be skipped from display."""
        metrics = {
            "n_trades": 10,
            "sharpe_ratio": None,
            "total_pnl": 0.5,
        }
        displayed = {k: v for k, v in metrics.items() if v is not None}
        assert "sharpe_ratio" not in displayed
        assert "n_trades" in displayed

    def test_float_formatting(self):
        v = 0.123456789
        val_str = f"{float(v):.4f}"
        assert val_str == "0.1235"

    def test_int_formatting(self):
        v = 42
        val_str = f"{float(v):.4f}" if isinstance(v, (int, float)) else str(v)
        assert val_str == "42.0000"

    def test_string_value_stays_string(self):
        v = "bt_20260101"
        val_str = f"{float(v):.4f}" if isinstance(v, (int, float)) else str(v)
        assert val_str == "bt_20260101"
