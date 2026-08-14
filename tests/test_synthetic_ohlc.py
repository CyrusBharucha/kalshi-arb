"""
tests/test_synthetic_ohlc.py
=============================
Unit tests for analysis/synthetic_ohlc.py:
  - PERIODS constant: is a list of positive ints, has both hourly (60) and daily (1440)
  - _flush(): empty list returns 0 immediately, no session call
  - run_all_periods(): aggregates rows_written from all periods, returns dict

No real DB or trade data — session_scope and build_synthetic_ohlc are mocked.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


class TestPeriodsConstant:
    """PERIODS list of period_minutes values."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.synthetic_ohlc import PERIODS
        self.periods = PERIODS

    def test_is_list(self):
        assert isinstance(self.periods, list)

    def test_nonempty(self):
        assert len(self.periods) >= 1

    def test_all_positive_ints(self):
        for p in self.periods:
            assert isinstance(p, int)
            assert p > 0

    def test_contains_60_minutes(self):
        assert 60 in self.periods

    def test_contains_1440_minutes(self):
        assert 1440 in self.periods

    def test_no_duplicates(self):
        assert len(self.periods) == len(set(self.periods))


class TestFlushEmpty:
    """_flush([]) returns 0 without calling session."""

    def test_empty_returns_zero(self):
        from analysis.synthetic_ohlc import _flush
        mock_ss = MagicMock()
        with patch("analysis.synthetic_ohlc.session_scope", return_value=mock_ss):
            result = _flush([])
        assert result == 0

    def test_empty_does_not_open_session(self):
        from analysis.synthetic_ohlc import _flush
        mock_ss = MagicMock()
        with patch("analysis.synthetic_ohlc.session_scope", return_value=mock_ss):
            _flush([])
        mock_ss.__enter__.assert_not_called()


class TestRunAllPeriods:
    """run_all_periods() aggregates rows_written from all period builds."""

    def test_returns_dict(self):
        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc",
                   return_value={"rows_written": 5, "period_minutes": 60}):
            from analysis.synthetic_ohlc import run_all_periods
            result = run_all_periods()
        assert isinstance(result, dict)

    def test_has_total_rows_key(self):
        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc",
                   return_value={"rows_written": 0}):
            from analysis.synthetic_ohlc import run_all_periods
            result = run_all_periods()
        assert "total_rows" in result

    def test_has_periods_key(self):
        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc",
                   return_value={"rows_written": 0}):
            from analysis.synthetic_ohlc import run_all_periods
            result = run_all_periods()
        assert "periods" in result

    def test_periods_key_matches_constant(self):
        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc",
                   return_value={"rows_written": 0}):
            from analysis.synthetic_ohlc import run_all_periods, PERIODS
            result = run_all_periods()
        assert result["periods"] == PERIODS

    def test_total_rows_sums_all_periods(self):
        call_count = {"n": 0}

        def fake_build(period):
            call_count["n"] += 1
            return {"rows_written": 100}

        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc", side_effect=fake_build):
            from analysis.synthetic_ohlc import run_all_periods, PERIODS
            result = run_all_periods()
        assert result["total_rows"] == 100 * len(PERIODS)

    def test_build_called_for_each_period(self):
        calls = []

        def fake_build(period):
            calls.append(period)
            return {"rows_written": 0}

        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc", side_effect=fake_build):
            from analysis.synthetic_ohlc import run_all_periods, PERIODS
            run_all_periods()
        assert sorted(calls) == sorted(PERIODS)

    def test_total_rows_zero_when_all_zero(self):
        with patch("analysis.synthetic_ohlc.build_synthetic_ohlc",
                   return_value={"rows_written": 0}):
            from analysis.synthetic_ohlc import run_all_periods
            result = run_all_periods()
        assert result["total_rows"] == 0
