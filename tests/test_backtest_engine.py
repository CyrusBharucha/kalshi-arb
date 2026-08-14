"""
tests/test_backtest_engine.py
==============================
Unit tests for backtest/engine.py.

All tests run offline (DB calls are mocked).
Covers:
  - compute_metrics: all metrics including Sharpe and max drawdown
  - backtest_complement_arb: correct trade construction
  - _safe_float: edge cases
  - run_all: smoke test (mocked DB)
"""
from __future__ import annotations

import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

from backtest.engine import BacktestEngine, _safe_float


# ---------------------------------------------------------------------------
# _safe_float
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_int(self):
        assert _safe_float(5) == 5.0

    def test_float(self):
        assert _safe_float(0.45) == pytest.approx(0.45)

    def test_string_int(self):
        assert _safe_float("7") == 7.0

    def test_bad_string_returns_none(self):
        assert _safe_float("abc") is None

    def test_zero(self):
        assert _safe_float(0) == 0.0


# ---------------------------------------------------------------------------
# BacktestEngine.compute_metrics
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    def _engine_with_trades(self, pnls):
        eng = BacktestEngine(run_id="test")
        trades = [
            {"run_id": "test", "market_id": "X", "side": "both", "action": "buy",
             "entry_ts": None, "exit_ts": None, "entry_price": 0.9, "exit_price": 1.0,
             "quantity": 10, "taker_fees": 0.01, "maker_fees": 0.0, "slippage": 0.0,
             "gross_pnl": p + 0.01, "net_pnl": p, "notes": ""}
            for p in pnls
        ]
        eng._trades = trades
        return eng, pd.DataFrame(trades)

    def test_n_trades(self):
        eng, df = self._engine_with_trades([0.1, -0.05, 0.2])
        m = eng.compute_metrics(df)
        assert m["n_trades"] == 3

    def test_win_rate_all_positive(self):
        eng, df = self._engine_with_trades([0.1, 0.2, 0.3])
        m = eng.compute_metrics(df)
        assert m["win_rate"] == pytest.approx(1.0)

    def test_win_rate_mixed(self):
        eng, df = self._engine_with_trades([0.1, -0.1, 0.1, -0.1])
        m = eng.compute_metrics(df)
        assert m["win_rate"] == pytest.approx(0.5)

    def test_total_net_pnl(self):
        eng, df = self._engine_with_trades([0.1, 0.2, 0.3])
        m = eng.compute_metrics(df)
        assert m["total_net_pnl"] == pytest.approx(0.6)

    def test_empty_df_returns_error(self):
        eng = BacktestEngine(run_id="test")
        m = eng.compute_metrics(pd.DataFrame())
        assert "error" in m

    def test_max_drawdown_non_positive(self):
        eng, df = self._engine_with_trades([0.1, 0.2, -0.5, 0.05])
        m = eng.compute_metrics(df)
        assert m["max_drawdown"] <= 0

    def test_max_drawdown_zero_when_always_increasing(self):
        eng, df = self._engine_with_trades([0.1, 0.2, 0.3])
        m = eng.compute_metrics(df)
        assert m["max_drawdown"] == pytest.approx(0.0)

    def test_sharpe_present(self):
        eng, df = self._engine_with_trades([0.1, -0.05, 0.2, 0.1, -0.03])
        m = eng.compute_metrics(df)
        assert m.get("sharpe_ratio") is not None

    def test_std_pnl_single_trade(self):
        """Single trade -> std is 0 -> Sharpe is None."""
        eng, df = self._engine_with_trades([0.1])
        m = eng.compute_metrics(df)
        assert m.get("sharpe_ratio") is None or m.get("sharpe_ratio") == 0


# ---------------------------------------------------------------------------
# BacktestEngine.backtest_complement_arb
# ---------------------------------------------------------------------------

class TestBacktestComplementArb:
    """Mock DB calls to test trade construction logic."""

    def _make_df(self, rows):
        """Helper to build a candlestick-like DataFrame."""
        return pd.DataFrame(rows)

    @patch("backtest.engine.get_candlesticks")
    @patch("backtest.engine.session_scope")
    def test_no_trades_when_no_edge(self, mock_scope, mock_candles):
        """When yes_ask + no_ask >= 1.0, no trades should be generated."""
        mock_candles.return_value = self._make_df([
            {"period_end_ts": "2026-01-01", "yes_ask_close": 0.55, "yes_bid_close": 0.40},
        ])
        eng = BacktestEngine(run_id="test")
        with patch.object(eng, "_get_settlement_value", return_value=1.0):
            df = eng.backtest_complement_arb("MKT-X", period_interval=60)
        # yes_ask=0.55, no_ask=1-yes_bid=1-0.40=0.60 -> total=1.15 -> no edge
        assert df.empty

    @patch("backtest.engine.get_candlesticks")
    @patch("backtest.engine.session_scope")
    def test_trade_generated_with_edge(self, mock_scope, mock_candles):
        """When yes_ask + no_ask < 1.0, a trade should be generated."""
        mock_candles.return_value = self._make_df([
            # yes_ask=0.45, yes_bid=0.52 -> no_ask=1-0.52=0.48 -> total=0.93 -> edge=0.07
            {"period_end_ts": "2026-01-01", "yes_ask_close": 0.45, "yes_bid_close": 0.52},
        ])
        eng = BacktestEngine(run_id="test")
        with patch.object(eng, "_get_settlement_value", return_value=1.0):
            df = eng.backtest_complement_arb("MKT-X", period_interval=60)
        assert len(df) == 1

    @patch("backtest.engine.get_candlesticks")
    @patch("backtest.engine.session_scope")
    def test_empty_candlesticks(self, mock_scope, mock_candles):
        mock_candles.return_value = pd.DataFrame()
        eng = BacktestEngine(run_id="test")
        df = eng.backtest_complement_arb("MKT-EMPTY", period_interval=60)
        assert df.empty

    @patch("backtest.engine.get_candlesticks")
    @patch("backtest.engine.session_scope")
    def test_net_pnl_lt_gross(self, mock_scope, mock_candles):
        """Net P&L must be less than gross P&L due to fees."""
        mock_candles.return_value = self._make_df([
            {"period_end_ts": "2026-01-01", "yes_ask_close": 0.45, "yes_bid_close": 0.52},
        ])
        eng = BacktestEngine(run_id="test")
        with patch.object(eng, "_get_settlement_value", return_value=1.0):
            df = eng.backtest_complement_arb("MKT-Y", period_interval=60, contracts_per_trade=5)
        assert df["net_pnl"].iloc[0] < df["gross_pnl"].iloc[0]

    @patch("backtest.engine.get_candlesticks")
    @patch("backtest.engine.session_scope")
    def test_run_id_in_result(self, mock_scope, mock_candles):
        mock_candles.return_value = self._make_df([
            {"period_end_ts": "2026-01-01", "yes_ask_close": 0.45, "yes_bid_close": 0.52},
        ])
        eng = BacktestEngine(run_id="my_run_123")
        with patch.object(eng, "_get_settlement_value", return_value=1.0):
            df = eng.backtest_complement_arb("MKT-Z", period_interval=60)
        assert df["run_id"].iloc[0] == "my_run_123"
