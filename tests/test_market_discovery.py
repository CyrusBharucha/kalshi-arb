"""
tests/test_market_discovery.py
================================
Unit tests for dashboard/market_discovery.py:
  - KALSHI_MARKETS_URL: is a string starting with https://
  - _JUNK_PREFIXES: is a tuple of strings
  - _CLOSE_WINDOWS_DAYS: is a tuple of positive ints in ascending order
  - _volume(): float extraction helper
  - fetch_liquid_tickers(): with mocked _fetch_page

No network calls — _fetch_page is always mocked.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


class TestConstants:
    """Module-level constants structure."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.market_discovery import (
            KALSHI_MARKETS_URL, _JUNK_PREFIXES, _CLOSE_WINDOWS_DAYS
        )
        self.url = KALSHI_MARKETS_URL
        self.junk = _JUNK_PREFIXES
        self.windows = _CLOSE_WINDOWS_DAYS

    def test_url_is_string(self):
        assert isinstance(self.url, str)

    def test_url_starts_with_https(self):
        assert self.url.startswith("https://")

    def test_url_contains_kalshi(self):
        assert "kalshi" in self.url.lower()

    def test_junk_prefixes_is_tuple(self):
        assert isinstance(self.junk, tuple)

    def test_junk_prefixes_nonempty(self):
        assert len(self.junk) >= 1

    def test_junk_prefixes_all_strings(self):
        for p in self.junk:
            assert isinstance(p, str)

    def test_close_windows_is_tuple(self):
        assert isinstance(self.windows, tuple)

    def test_close_windows_all_positive(self):
        for d in self.windows:
            assert isinstance(d, int)
            assert d > 0

    def test_close_windows_ascending(self):
        for i in range(len(self.windows) - 1):
            assert self.windows[i] < self.windows[i + 1]

    def test_close_windows_has_at_least_two_entries(self):
        assert len(self.windows) >= 2


class TestVolumeHelper:
    """_volume() extracts volume_fp as float."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.market_discovery import _volume
        self.fn = _volume

    def test_returns_float(self):
        assert isinstance(self.fn({"volume_fp": "100.5"}), float)

    def test_numeric_string_parsed(self):
        assert self.fn({"volume_fp": "42.5"}) == 42.5

    def test_integer_value_parsed(self):
        assert self.fn({"volume_fp": 10}) == 10.0

    def test_missing_key_returns_zero(self):
        assert self.fn({}) == 0.0

    def test_none_value_returns_zero(self):
        assert self.fn({"volume_fp": None}) == 0.0

    def test_empty_string_returns_zero(self):
        assert self.fn({"volume_fp": ""}) == 0.0

    def test_zero_returns_zero(self):
        assert self.fn({"volume_fp": 0}) == 0.0

    def test_large_volume_parsed(self):
        assert self.fn({"volume_fp": "1234567.89"}) == pytest.approx(1234567.89)


class TestFetchLiquidTickers:
    """fetch_liquid_tickers() with _fetch_kalshi_page mocked."""

    _PATCH = "dashboard.market_discovery._fetch_kalshi_page"
    _HIGH_VOL = "100000"  # above MIN_VOLUME_FP=50_000 so markets survive the filter

    def _make_page(self, tickers, cursor=None):
        markets = [{"ticker": t, "volume_fp": self._HIGH_VOL}
                   for t in tickers]
        result = {"markets": markets}
        if cursor:
            result["cursor"] = cursor
        return result

    def test_returns_list(self):
        with patch(self._PATCH, return_value=self._make_page(["KXTEST-YES"])):
            from dashboard.market_discovery import fetch_liquid_tickers
            result = fetch_liquid_tickers()
        assert isinstance(result, list)

    def test_tickers_are_strings(self):
        with patch(self._PATCH, return_value=self._make_page(["KXTEST-YES", "KXTEST2-YES"])):
            from dashboard.market_discovery import fetch_liquid_tickers
            result = fetch_liquid_tickers()
        for t in result:
            assert isinstance(t, str)

    def test_filters_junk_prefixes(self):
        with patch(self._PATCH, return_value=self._make_page(
                ["KXMVECROSSCATEGORY-JUNK", "KXREAL-YES"])):
            from dashboard.market_discovery import fetch_liquid_tickers
            result = fetch_liquid_tickers()
        assert "KXMVECROSSCATEGORY-JUNK" not in result
        assert "KXREAL-YES" in result

    def test_empty_market_list_returns_empty(self):
        with patch(self._PATCH, return_value={"markets": []}):
            from dashboard.market_discovery import fetch_liquid_tickers
            result = fetch_liquid_tickers()
        assert result == []

    def test_network_error_returns_empty(self):
        with patch(self._PATCH, side_effect=Exception("network error")):
            from dashboard.market_discovery import fetch_liquid_tickers
            result = fetch_liquid_tickers()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_limit_respected(self):
        page = self._make_page([f"KXTEST{i}-YES" for i in range(5)])
        with patch(self._PATCH, return_value=page):
            from dashboard.market_discovery import fetch_liquid_tickers
            result = fetch_liquid_tickers(limit=3)
        assert len(result) <= 3

    def test_sorted_by_volume_desc(self):
        page = {
            "markets": [
                {"ticker": "LOW-YES",  "volume_fp": "60000"},
                {"ticker": "HIGH-YES", "volume_fp": "999999"},
            ]
        }
        with patch(self._PATCH, return_value=page):
            from dashboard.market_discovery import fetch_liquid_tickers
            result = fetch_liquid_tickers()
        assert result.index("HIGH-YES") < result.index("LOW-YES")

    def test_deduplicates_across_windows(self):
        """Same ticker in multiple close-time windows should appear once."""
        page = self._make_page(["KXSHARED-YES"])
        with patch(self._PATCH, return_value=page):
            from dashboard.market_discovery import fetch_liquid_tickers
            result = fetch_liquid_tickers()
        assert result.count("KXSHARED-YES") == 1
