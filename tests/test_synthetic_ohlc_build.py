"""
tests/test_synthetic_ohlc_build.py
=====================================
Unit tests for analysis/synthetic_ohlc:
  - build_synthetic_ohlc(): empty trades → {rows_written: 0}
  - build_synthetic_ohlc(): calls session_scope, returns dict with expected keys
  - run_all_periods(): aggregates rows_written across PERIODS, returns correct keys

All DB calls mocked — no live PostgreSQL.
"""
from __future__ import annotations

import sys
import pandas as pd
from unittest.mock import patch, MagicMock, call
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
    }):
        yield


def _session_ctx(return_df=None):
    if return_df is None:
        return_df = pd.DataFrame()
    mock_ss = MagicMock()
    mock_sess = MagicMock()
    mock_sess.bind = MagicMock()
    mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
    mock_ss.return_value.__exit__ = MagicMock(return_value=False)
    return mock_ss, mock_sess


# ---------------------------------------------------------------------------
# build_synthetic_ohlc — empty trades path
# ---------------------------------------------------------------------------

class TestBuildSyntheticOhlcEmpty:
    def test_empty_trades_returns_zero_rows_written(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        mock_ss, _ = _session_ctx(pd.DataFrame())
        with patch("analysis.synthetic_ohlc.session_scope", mock_ss), \
             patch("analysis.synthetic_ohlc.pd.read_sql", return_value=pd.DataFrame()):
            result = build_synthetic_ohlc()
        assert result["rows_written"] == 0

    def test_empty_returns_dict(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        mock_ss, _ = _session_ctx()
        with patch("analysis.synthetic_ohlc.session_scope", mock_ss), \
             patch("analysis.synthetic_ohlc.pd.read_sql", return_value=pd.DataFrame()):
            result = build_synthetic_ohlc()
        assert isinstance(result, dict)

    def test_empty_calls_session_scope_once(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        mock_ss, _ = _session_ctx()
        with patch("analysis.synthetic_ohlc.session_scope", mock_ss), \
             patch("analysis.synthetic_ohlc.pd.read_sql", return_value=pd.DataFrame()):
            build_synthetic_ohlc()
        # At least one call to session_scope (for the read)
        assert mock_ss.call_count >= 1


# ---------------------------------------------------------------------------
# build_synthetic_ohlc — non-empty trades path
# ---------------------------------------------------------------------------

class TestBuildSyntheticOhlcWithTrades:
    def _make_trades_df(self):
        return pd.DataFrame({
            "market_id": ["MKT-A", "MKT-A", "MKT-A"],
            "trade_ts": pd.to_datetime([
                "2026-01-01 09:00:00",
                "2026-01-01 09:30:00",
                "2026-01-01 10:00:00",
            ], utc=True),
            "price": [0.50, 0.55, 0.60],
            "quantity": [10, 20, 15],
        })

    def test_returns_dict_with_keys(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        mock_ss, _ = _session_ctx()
        trades = self._make_trades_df()
        with patch("analysis.synthetic_ohlc.session_scope", mock_ss), \
             patch("analysis.synthetic_ohlc.pd.read_sql", return_value=trades), \
             patch("analysis.synthetic_ohlc._flush", return_value=2):
            result = build_synthetic_ohlc(period_minutes=60)
        assert "rows_written" in result
        assert "period_minutes" in result
        assert "markets" in result

    def test_period_minutes_in_result(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        mock_ss, _ = _session_ctx()
        trades = self._make_trades_df()
        with patch("analysis.synthetic_ohlc.session_scope", mock_ss), \
             patch("analysis.synthetic_ohlc.pd.read_sql", return_value=trades), \
             patch("analysis.synthetic_ohlc._flush", return_value=1):
            result = build_synthetic_ohlc(period_minutes=5)
        assert result["period_minutes"] == 5

    def test_markets_count_correct(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        mock_ss, _ = _session_ctx()
        trades = self._make_trades_df()
        with patch("analysis.synthetic_ohlc.session_scope", mock_ss), \
             patch("analysis.synthetic_ohlc.pd.read_sql", return_value=trades), \
             patch("analysis.synthetic_ohlc._flush", return_value=1):
            result = build_synthetic_ohlc(period_minutes=60)
        assert result["markets"] == 1  # only MKT-A

    def test_rows_written_from_flush(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        mock_ss, _ = _session_ctx()
        trades = self._make_trades_df()
        with patch("analysis.synthetic_ohlc.session_scope", mock_ss), \
             patch("analysis.synthetic_ohlc.pd.read_sql", return_value=trades), \
             patch("analysis.synthetic_ohlc._flush", return_value=42):
            result = build_synthetic_ohlc(period_minutes=60)
        assert result["rows_written"] == 42


# ---------------------------------------------------------------------------
# run_all_periods
# ---------------------------------------------------------------------------

class TestRunAllPeriods:
    def test_returns_dict(self):
        from analysis.synthetic_ohlc import run_all_periods
        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc", return_value={"rows_written": 10}):
            result = run_all_periods()
        assert isinstance(result, dict)

    def test_has_total_rows_key(self):
        from analysis.synthetic_ohlc import run_all_periods
        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc", return_value={"rows_written": 5}):
            result = run_all_periods()
        assert "total_rows" in result

    def test_has_periods_key(self):
        from analysis.synthetic_ohlc import run_all_periods
        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc", return_value={"rows_written": 0}):
            result = run_all_periods()
        assert "periods" in result

    def test_total_rows_sums_all_periods(self):
        from analysis.synthetic_ohlc import run_all_periods, PERIODS
        call_count = [0]

        def fake_build(period_minutes=60):
            call_count[0] += 1
            return {"rows_written": 100}

        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc", side_effect=fake_build):
            result = run_all_periods()
        # total_rows = 100 * len(PERIODS)
        assert result["total_rows"] == 100 * len(PERIODS)

    def test_periods_matches_constant(self):
        from analysis.synthetic_ohlc import run_all_periods, PERIODS
        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc", return_value={"rows_written": 0}):
            result = run_all_periods()
        assert result["periods"] == PERIODS

    def test_build_called_once_per_period(self):
        from analysis.synthetic_ohlc import run_all_periods, PERIODS
        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc", return_value={"rows_written": 0}) as mock_build:
            run_all_periods()
        assert mock_build.call_count == len(PERIODS)
