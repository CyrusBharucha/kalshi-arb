"""
tests/test_parse_candle.py
============================
Unit tests for analysis/targeted_candle_pull.py _parse_candle().

_parse_candle() converts a raw Kalshi API candlestick dict to a DB row dict.
Tests cover:
  - Returns None when no timestamp key present
  - Parses epoch-seconds timestamps
  - Parses ISO string timestamps (with "Z" suffix)
  - Parses end_period_ts, period_end_ts, end_ts (all three key names)
  - Numeric fields extracted: price_open, price_high, price_low, price_close
  - None-safe float extraction: missing keys → None
  - price_close fallback: uses "yes_price" when "close" absent
  - price_mean fallback: uses "vwap" when "mean" absent
  - market_id / period_interval set correctly in output

No DB, no network.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    """Patch external imports so targeted_candle_pull can be imported."""
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "feeds.kalshi_client": MagicMock(),
        "config": MagicMock(),
    }):
        yield


def _parse(ticker, candle_dict, period_interval=60):
    from analysis.targeted_candle_pull import _parse_candle
    return _parse_candle(ticker, candle_dict, period_interval)


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

class TestParseCandleTimestamp:
    def test_returns_none_when_no_ts_key(self):
        result = _parse("MKT-X", {"open": 0.45, "close": 0.50})
        assert result is None

    def test_parses_epoch_seconds(self):
        ts_epoch = 1700000000  # Nov 2023
        result = _parse("MKT-X", {"end_period_ts": ts_epoch})
        assert result is not None
        expected = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
        assert result["period_end_ts"] == expected

    def test_parses_epoch_float(self):
        ts_epoch = 1700000000.5
        result = _parse("MKT-X", {"end_period_ts": ts_epoch})
        assert result is not None
        expected = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
        assert result["period_end_ts"] == expected

    def test_parses_iso_string_with_z(self):
        result = _parse("MKT-X", {"end_period_ts": "2026-06-23T14:00:00Z"})
        assert result is not None
        assert result["period_end_ts"].tzinfo is not None
        assert result["period_end_ts"].year == 2026

    def test_parses_iso_string_with_offset(self):
        result = _parse("MKT-X", {"end_period_ts": "2026-06-23T14:00:00+00:00"})
        assert result is not None
        assert result["period_end_ts"].year == 2026

    def test_uses_period_end_ts_key(self):
        ts = 1700000000
        result = _parse("MKT-X", {"period_end_ts": ts})
        assert result is not None

    def test_uses_end_ts_key(self):
        ts = 1700000000
        result = _parse("MKT-X", {"end_ts": ts})
        assert result is not None

    def test_prefers_end_period_ts_over_others(self):
        """end_period_ts takes priority (first key checked)."""
        ts1 = 1700000000
        ts2 = 1800000000
        result = _parse("MKT-X", {"end_period_ts": ts1, "period_end_ts": ts2})
        expected = datetime.fromtimestamp(ts1, tz=timezone.utc)
        assert result["period_end_ts"] == expected


# ---------------------------------------------------------------------------
# market_id / period_interval
# ---------------------------------------------------------------------------

class TestParseCandleIdentifiers:
    def test_market_id_set(self):
        result = _parse("KXTEST-01", {"end_period_ts": 1700000000})
        assert result["market_id"] == "KXTEST-01"

    def test_period_interval_set(self):
        result = _parse("X", {"end_period_ts": 1700000000}, period_interval=1440)
        assert result["period_interval"] == 1440

    def test_period_interval_60_default(self):
        result = _parse("X", {"end_period_ts": 1700000000})
        assert result["period_interval"] == 60


# ---------------------------------------------------------------------------
# Numeric fields
# ---------------------------------------------------------------------------

class TestParseCandleNumericFields:
    def _full_candle(self):
        return {
            "end_period_ts": 1700000000,
            "open": 0.40, "high": 0.55, "low": 0.38, "close": 0.50,
            "volume": 250.0, "open_interest": 1000.0,
            "yes_bid_open": 0.39, "yes_bid_high": 0.54,
            "yes_bid_low": 0.37, "yes_bid_close": 0.49,
            "yes_ask_open": 0.41, "yes_ask_high": 0.56,
            "yes_ask_low": 0.39, "yes_ask_close": 0.51,
            "mean": 0.45,
        }

    def test_price_open(self):
        result = _parse("X", self._full_candle())
        assert result["price_open"] == pytest.approx(0.40)

    def test_price_high(self):
        result = _parse("X", self._full_candle())
        assert result["price_high"] == pytest.approx(0.55)

    def test_price_low(self):
        result = _parse("X", self._full_candle())
        assert result["price_low"] == pytest.approx(0.38)

    def test_price_close(self):
        result = _parse("X", self._full_candle())
        assert result["price_close"] == pytest.approx(0.50)

    def test_volume(self):
        result = _parse("X", self._full_candle())
        assert result["volume"] == pytest.approx(250.0)

    def test_yes_bid_close(self):
        result = _parse("X", self._full_candle())
        assert result["yes_bid_close"] == pytest.approx(0.49)

    def test_yes_ask_close(self):
        result = _parse("X", self._full_candle())
        assert result["yes_ask_close"] == pytest.approx(0.51)

    def test_price_mean_from_mean_key(self):
        result = _parse("X", self._full_candle())
        assert result["price_mean"] == pytest.approx(0.45)


# ---------------------------------------------------------------------------
# None-safe extraction / fallbacks
# ---------------------------------------------------------------------------

class TestParseCandleFallbacks:
    def test_missing_open_is_none(self):
        result = _parse("X", {"end_period_ts": 1700000000})
        assert result["price_open"] is None

    def test_missing_volume_is_none(self):
        result = _parse("X", {"end_period_ts": 1700000000})
        assert result["volume"] is None

    def test_price_close_fallback_to_yes_price(self):
        """When 'close' absent, uses 'yes_price'."""
        result = _parse("X", {"end_period_ts": 1700000000, "yes_price": 0.60})
        assert result["price_close"] == pytest.approx(0.60)

    def test_price_close_none_when_both_absent(self):
        result = _parse("X", {"end_period_ts": 1700000000})
        assert result["price_close"] is None

    def test_price_mean_fallback_to_vwap(self):
        """When 'mean' absent, uses 'vwap'."""
        result = _parse("X", {"end_period_ts": 1700000000, "vwap": 0.47})
        assert result["price_mean"] == pytest.approx(0.47)

    def test_price_mean_none_when_both_absent(self):
        result = _parse("X", {"end_period_ts": 1700000000})
        assert result["price_mean"] is None

    def test_string_value_converted_to_float(self):
        result = _parse("X", {"end_period_ts": 1700000000, "open": "0.42"})
        assert result["price_open"] == pytest.approx(0.42)

    def test_returns_dict_not_none_when_ts_present(self):
        result = _parse("X", {"end_period_ts": 1700000000})
        assert isinstance(result, dict)
