"""
tests/test_backtest_engine_helpers.py
========================================
Unit tests for backtest/engine.py methods not covered elsewhere:

  - BacktestEngine._get_settlement_value():
      - returns None on DB exception
      - returns None when market not found (fetchone returns None)
      - returns None when settlement_value is None in row
      - returns float when settlement value found

  - BacktestEngine.save_trades_to_db():
      - returns 0 when no trades accumulated
      - inserts all trades when trades exist
      - returns count of inserted rows

No live DB required; all DB calls are mocked.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# Module-level patch so backtest.engine can import without DB
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    repo_mock = MagicMock()
    models_mock = MagicMock()
    models_mock.BacktestTrade = MagicMock
    with patch.dict(sys.modules, {
        "database.repository": repo_mock,
        "database.models": models_mock,
        "sqlalchemy.dialects.postgresql": MagicMock(),
    }):
        yield


def _make_engine(run_id="test-run"):
    from backtest.engine import BacktestEngine
    eng = BacktestEngine.__new__(BacktestEngine)
    eng.run_id = run_id
    eng._trades = []
    return eng


# ---------------------------------------------------------------------------
# _get_settlement_value
# ---------------------------------------------------------------------------

class TestGetSettlementValue:
    """_get_settlement_value() returns float or None."""

    @pytest.fixture(autouse=True)
    def _engine(self):
        self.eng = _make_engine()

    def test_exception_returns_none(self):
        """Any DB exception → returns None (swallowed)."""
        mock_ss = MagicMock()
        mock_ss.__enter__.side_effect = RuntimeError("DB offline")
        mock_ss.__exit__ = MagicMock(return_value=False)
        with patch("backtest.engine.session_scope", return_value=mock_ss):
            result = self.eng._get_settlement_value("MKT-001")
        assert result is None

    def test_fetchone_none_returns_none(self):
        """Market not found in DB → fetchone returns None → method returns None."""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        mock_ss = MagicMock()
        mock_ss.__enter__.return_value = mock_session
        mock_ss.__exit__.return_value = False
        with patch("backtest.engine.session_scope", return_value=mock_ss):
            result = self.eng._get_settlement_value("MKT-001")
        assert result is None

    def test_settlement_value_null_in_db_returns_none(self):
        """fetchone returns row with result[0] = None → method returns None."""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = (None,)
        mock_ss = MagicMock()
        mock_ss.__enter__.return_value = mock_session
        mock_ss.__exit__.return_value = False
        with patch("backtest.engine.session_scope", return_value=mock_ss):
            result = self.eng._get_settlement_value("MKT-001")
        assert result is None

    def test_settlement_value_found_returns_float(self):
        """fetchone returns row with settlement_value = 1.0 → returns 1.0."""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = (1.0,)
        mock_ss = MagicMock()
        mock_ss.__enter__.return_value = mock_session
        mock_ss.__exit__.return_value = False
        with patch("backtest.engine.session_scope", return_value=mock_ss):
            result = self.eng._get_settlement_value("MKT-001")
        assert result == 1.0

    def test_settlement_value_zero_returns_zero(self):
        """fetchone returns (0.0,) → method returns 0.0."""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = (0.0,)
        mock_ss = MagicMock()
        mock_ss.__enter__.return_value = mock_session
        mock_ss.__exit__.return_value = False
        with patch("backtest.engine.session_scope", return_value=mock_ss):
            result = self.eng._get_settlement_value("MKT-001")
        assert result == 0.0

    def test_settlement_value_as_string_coerced(self):
        """fetchone returns ("0.75",) → float("0.75") = 0.75."""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = ("0.75",)
        mock_ss = MagicMock()
        mock_ss.__enter__.return_value = mock_session
        mock_ss.__exit__.return_value = False
        with patch("backtest.engine.session_scope", return_value=mock_ss):
            result = self.eng._get_settlement_value("MKT-001")
        assert abs(result - 0.75) < 1e-9


# ---------------------------------------------------------------------------
# save_trades_to_db
# ---------------------------------------------------------------------------

class TestSaveTradesToDb:
    """save_trades_to_db() persists trades or returns 0 when empty."""

    @pytest.fixture(autouse=True)
    def _engine(self):
        self.eng = _make_engine()

    def test_no_trades_returns_zero(self):
        """Empty _trades → returns 0 immediately without opening DB session."""
        self.eng._trades = []
        mock_ss = MagicMock()
        with patch("backtest.engine.session_scope", return_value=mock_ss):
            result = self.eng.save_trades_to_db()
        assert result == 0
        mock_ss.__enter__.assert_not_called()

    def test_empty_trades_no_session_opened(self):
        """No session should be opened when trades list is empty."""
        self.eng._trades = []
        opened = []
        mock_ss = MagicMock()
        mock_ss.__enter__.side_effect = lambda: opened.append(1)
        with patch("backtest.engine.session_scope", return_value=mock_ss):
            self.eng.save_trades_to_db()
        assert len(opened) == 0

    def test_one_trade_returns_one(self):
        """With one trade dict, returns 1."""
        self.eng._trades = [{"market_id": "MKT-001", "net_pnl": 0.05}]
        mock_session = MagicMock()
        mock_ss = MagicMock()
        mock_ss.__enter__.return_value = mock_session
        mock_ss.__exit__.return_value = False
        with patch("backtest.engine.session_scope", return_value=mock_ss), \
             patch("backtest.engine.BacktestTrade", MagicMock()):
            result = self.eng.save_trades_to_db()
        assert result == 1

    def test_multiple_trades_returns_count(self):
        """With N trades, returns N."""
        self.eng._trades = [
            {"market_id": f"MKT-{i}", "net_pnl": 0.01 * i}
            for i in range(5)
        ]
        mock_session = MagicMock()
        mock_ss = MagicMock()
        mock_ss.__enter__.return_value = mock_session
        mock_ss.__exit__.return_value = False
        with patch("backtest.engine.session_scope", return_value=mock_ss), \
             patch("backtest.engine.BacktestTrade", MagicMock()):
            result = self.eng.save_trades_to_db()
        assert result == 5

    def test_save_calls_session_add_per_trade(self):
        """session.add() is called once per trade."""
        self.eng._trades = [{"market_id": "A"}, {"market_id": "B"}]
        mock_session = MagicMock()
        mock_ss = MagicMock()
        mock_ss.__enter__.return_value = mock_session
        mock_ss.__exit__.return_value = False
        with patch("backtest.engine.session_scope", return_value=mock_ss), \
             patch("backtest.engine.BacktestTrade", MagicMock()):
            self.eng.save_trades_to_db()
        assert mock_session.add.call_count == 2
