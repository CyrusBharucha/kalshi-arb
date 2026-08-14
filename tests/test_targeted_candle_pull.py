"""
tests/test_targeted_candle_pull.py
====================================
Unit tests for analysis/targeted_candle_pull.py:
  - Constants: LOOKBACK_DAYS, PERIOD_INTERVAL, LIVE_BATCH, MAX_MARKETS
  - _parse_candle(): returns dict or None; handles epoch int, ISO string, missing fields
  - _flush_candles(): empty list returns 0 without opening DB session

No DB or API calls.
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


class TestConstants:
    """Module-level tuning constants."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.targeted_candle_pull import (
            LOOKBACK_DAYS, PERIOD_INTERVAL, LIVE_BATCH, MAX_MARKETS
        )
        self.lookback    = LOOKBACK_DAYS
        self.period      = PERIOD_INTERVAL
        self.batch       = LIVE_BATCH
        self.max_markets = MAX_MARKETS

    def test_lookback_days_positive(self):
        assert isinstance(self.lookback, int) and self.lookback > 0

    def test_period_interval_positive(self):
        assert isinstance(self.period, int) and self.period > 0

    def test_live_batch_positive(self):
        assert isinstance(self.batch, int) and self.batch > 0

    def test_max_markets_positive(self):
        assert isinstance(self.max_markets, int) and self.max_markets > 0

    def test_period_interval_is_60(self):
        assert self.period == 60


class TestParseCandle:
    """_parse_candle(): dict or None based on ts_raw presence."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.targeted_candle_pull import _parse_candle
        self.fn = _parse_candle

    def _base_candle(self, **overrides):
        c = {
            "end_period_ts": 1_750_000_000,   # epoch seconds
            "open": 0.42,
            "high": 0.45,
            "low":  0.40,
            "close": 0.43,
            "volume": 1500.0,
        }
        c.update(overrides)
        return c

    def test_returns_dict_for_valid_candle(self):
        result = self.fn("KXTEST-YES", self._base_candle(), 60)
        assert isinstance(result, dict)

    def test_returns_none_when_no_ts(self):
        result = self.fn("KXTEST-YES", {}, 60)
        assert result is None

    def test_market_id_in_result(self):
        result = self.fn("KXBOC-YES", self._base_candle(), 60)
        assert result["market_id"] == "KXBOC-YES"

    def test_period_interval_in_result(self):
        result = self.fn("KXTEST-YES", self._base_candle(), 60)
        assert result["period_interval"] == 60

    def test_epoch_int_ts_parsed(self):
        result = self.fn("KXTEST-YES", self._base_candle(end_period_ts=1_750_000_000), 60)
        assert result is not None
        from datetime import timezone
        assert result["period_end_ts"].tzinfo is not None

    def test_iso_string_ts_parsed(self):
        result = self.fn("KXTEST-YES", self._base_candle(
            end_period_ts="2026-06-23T12:00:00Z",
        ), 60)
        assert result is not None

    def test_period_end_ts_fallback_keys(self):
        """period_end_ts key (alternative name) should also work."""
        c = {"period_end_ts": 1_750_000_000}
        result = self.fn("KXTEST-YES", c, 60)
        assert result is not None

    def test_end_ts_fallback_key(self):
        c = {"end_ts": 1_750_000_000}
        result = self.fn("KXTEST-YES", c, 60)
        assert result is not None

    def test_missing_price_fields_are_none(self):
        result = self.fn("KXTEST-YES", {"end_period_ts": 1_750_000_000}, 60)
        assert result is not None
        assert result["price_open"] is None
        assert result["price_close"] is None

    def test_price_close_extracted(self):
        result = self.fn("KXTEST-YES", self._base_candle(close=0.77), 60)
        assert abs(result["price_close"] - 0.77) < 1e-9

    def test_price_open_extracted(self):
        result = self.fn("KXTEST-YES", self._base_candle(open=0.33), 60)
        assert abs(result["price_open"] - 0.33) < 1e-9

    def test_volume_extracted(self):
        result = self.fn("KXTEST-YES", self._base_candle(volume=999.0), 60)
        assert abs(result["volume"] - 999.0) < 1e-9

    def test_yes_price_used_as_fallback_for_close(self):
        """If 'close' missing, should fall back to 'yes_price'."""
        c = {"end_period_ts": 1_750_000_000, "yes_price": 0.65}
        result = self.fn("KXTEST-YES", c, 60)
        assert result["price_close"] == 0.65


class TestFlushCandlesEmpty:
    """_flush_candles([]) returns 0 without touching DB."""

    def test_empty_returns_zero(self):
        from analysis.targeted_candle_pull import _flush_candles
        mock_ss = MagicMock()
        with patch("analysis.targeted_candle_pull.session_scope", return_value=mock_ss):
            result = _flush_candles([])
        assert result == 0

    def test_empty_does_not_open_session(self):
        from analysis.targeted_candle_pull import _flush_candles
        mock_ss = MagicMock()
        with patch("analysis.targeted_candle_pull.session_scope", return_value=mock_ss):
            _flush_candles([])
        mock_ss.__enter__.assert_not_called()
