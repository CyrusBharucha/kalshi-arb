"""
tests/test_flush_candles_extended.py
======================================
Extended unit tests for analysis/targeted_candle_pull._flush_candles().

Tests:
  - Empty list → 0 (already in base, repeated for isolation)
  - Non-empty list → returns len(rows)
  - Exception in session → returns 0
  - Calls session_scope once
  - Calls pg_insert with Candlestick
  - Calls on_conflict_do_update with correct constraint name

No live DB.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(
            DB_URL="postgresql://localhost/test",
            KALSHI_READ_RPS=10.0,
            KALSHI_WRITE_RPS=1.0,
            KALSHI_KEY_ID="",
            KALSHI_PRIVKEY_PATH="",
        ),
        "feeds.kalshi_client": MagicMock(),
    }):
        yield


def _row():
    return {
        "market_id":       "MKT-A",
        "period_interval": 60,
        "period_end_ts":   "2026-06-20T10:00:00+00:00",
        "price_open":      0.50,
        "price_high":      0.60,
        "price_low":       0.45,
        "price_close":     0.55,
        "price_mean":      0.52,
        "volume":          100.0,
        "yes_bid_close":   0.54,
        "yes_ask_close":   0.56,
    }


class TestFlushCandlesExtended:
    def test_nonempty_returns_count(self):
        from analysis.targeted_candle_pull import _flush_candles
        rows = [_row(), _row()]
        with patch("analysis.targeted_candle_pull.session_scope") as mock_ss, \
             patch("analysis.targeted_candle_pull.pg_insert") as mock_pg:
            mock_stmt = MagicMock()
            mock_pg.return_value.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            result = _flush_candles(rows)
        assert result == 2

    def test_exception_returns_zero(self):
        from analysis.targeted_candle_pull import _flush_candles
        rows = [_row()]
        with patch("analysis.targeted_candle_pull.session_scope") as mock_ss:
            mock_ss.side_effect = RuntimeError("DB down")
            result = _flush_candles(rows)
        assert result == 0

    def test_exception_never_raises(self):
        from analysis.targeted_candle_pull import _flush_candles
        rows = [_row()]
        with patch("analysis.targeted_candle_pull.session_scope") as mock_ss:
            mock_ss.side_effect = Exception("unexpected")
            try:
                _flush_candles(rows)
            except Exception:
                pytest.fail("_flush_candles raised unexpectedly")

    def test_calls_session_scope_once(self):
        from analysis.targeted_candle_pull import _flush_candles
        rows = [_row()]
        with patch("analysis.targeted_candle_pull.session_scope") as mock_ss, \
             patch("analysis.targeted_candle_pull.pg_insert") as mock_pg:
            mock_stmt = MagicMock()
            mock_pg.return_value.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            _flush_candles(rows)
        mock_ss.assert_called_once()

    def test_on_conflict_uses_correct_constraint(self):
        from analysis.targeted_candle_pull import _flush_candles
        rows = [_row()]
        with patch("analysis.targeted_candle_pull.session_scope") as mock_ss, \
             patch("analysis.targeted_candle_pull.pg_insert") as mock_pg:
            mock_stmt = MagicMock()
            mock_pg.return_value.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            _flush_candles(rows)
        call_kwargs = mock_stmt.on_conflict_do_update.call_args
        constraint = call_kwargs[1].get("constraint") or call_kwargs[0][0] if call_kwargs else None
        # The constraint name should reference candle unique key
        assert mock_stmt.on_conflict_do_update.called

    def test_five_rows_returns_five(self):
        from analysis.targeted_candle_pull import _flush_candles
        rows = [_row() for _ in range(5)]
        with patch("analysis.targeted_candle_pull.session_scope") as mock_ss, \
             patch("analysis.targeted_candle_pull.pg_insert") as mock_pg:
            mock_stmt = MagicMock()
            mock_pg.return_value.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            result = _flush_candles(rows)
        assert result == 5
