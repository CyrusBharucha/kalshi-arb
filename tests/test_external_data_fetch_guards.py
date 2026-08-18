"""
tests/test_external_data_fetch_guards.py
==========================================
Unit tests for data/external_market_data.py guarded fetch functions:
  - fetch_alpha_vantage_fx(): no-key guard returns 0 without calling network
  - fetch_all_boc_series(): calls fetch_boc_series once per series
  - fetch_all_fred_series(): calls fetch_fred_series once per series

No DB, no network.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock, call
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


class TestFetchAlphaVantageFxNoKey:
    """fetch_alpha_vantage_fx(): no-key guard returns 0 without network."""

    def test_no_key_returns_zero(self):
        from feeds.external_market_data import fetch_alpha_vantage_fx
        with patch("config.ALPHA_VANTAGE_KEY", ""):
            result = fetch_alpha_vantage_fx()
        assert result == 0

    def test_no_key_does_not_call_get_json(self):
        from feeds.external_market_data import fetch_alpha_vantage_fx
        with (
            patch("config.ALPHA_VANTAGE_KEY", ""),
            patch("feeds.external_market_data._get_json") as mock_get,
        ):
            fetch_alpha_vantage_fx()
        mock_get.assert_not_called()

    def test_no_key_does_not_open_session(self):
        from feeds.external_market_data import fetch_alpha_vantage_fx
        mock_ss = MagicMock()
        with (
            patch("config.ALPHA_VANTAGE_KEY", ""),
            patch("feeds.external_market_data.session_scope", mock_ss) as scope,
        ):
            fetch_alpha_vantage_fx()
        mock_ss.assert_not_called()

    def test_returns_int(self):
        from feeds.external_market_data import fetch_alpha_vantage_fx
        with patch("config.ALPHA_VANTAGE_KEY", ""):
            result = fetch_alpha_vantage_fx()
        assert isinstance(result, int)


class TestBocSeriesConstant:
    """BOC_SERIES: non-empty dict with string keys."""

    def test_boc_series_is_dict(self):
        from feeds.external_market_data import BOC_SERIES
        assert isinstance(BOC_SERIES, dict)

    def test_boc_series_nonempty(self):
        from feeds.external_market_data import BOC_SERIES
        assert len(BOC_SERIES) > 0

    def test_boc_series_all_string_keys(self):
        from feeds.external_market_data import BOC_SERIES
        for k in BOC_SERIES:
            assert isinstance(k, str), f"Non-string key: {k!r}"

    def test_boc_series_values_are_strings(self):
        from feeds.external_market_data import BOC_SERIES
        for v in BOC_SERIES.values():
            assert isinstance(v, str), f"Non-string value: {v!r}"


class TestFredSeriesConstant:
    """FRED_SERIES: non-empty dict with string keys."""

    def test_fred_series_is_dict(self):
        from feeds.external_market_data import FRED_SERIES
        assert isinstance(FRED_SERIES, dict)

    def test_fred_series_nonempty(self):
        from feeds.external_market_data import FRED_SERIES
        assert len(FRED_SERIES) > 0

    def test_fred_series_all_string_keys(self):
        from feeds.external_market_data import FRED_SERIES
        for k in FRED_SERIES:
            assert isinstance(k, str)

    def test_fred_series_values_are_tuples(self):
        # Each value is a tuple: (series_id, category, ...)
        from feeds.external_market_data import FRED_SERIES
        for v in FRED_SERIES.values():
            assert isinstance(v, tuple), f"Expected tuple, got {type(v)}"


class TestFetchAllBocSeries:
    """fetch_all_boc_series(): calls fetch_boc_series for each key in BOC_SERIES."""

    def test_calls_fetch_for_each_series(self):
        from feeds.external_market_data import fetch_all_boc_series, BOC_SERIES
        with patch("feeds.external_market_data.fetch_boc_series", return_value=0) as mock_fetch:
            fetch_all_boc_series()
        assert mock_fetch.call_count == len(BOC_SERIES)

    def test_all_series_ids_passed(self):
        # fetch_all_boc_series calls fetch_boc_series(series_id, asset_name, ...)
        # where series_id is the VALUE in BOC_SERIES dict, not the key
        from feeds.external_market_data import fetch_all_boc_series, BOC_SERIES
        mock_scope = MagicMock()
        mock_scope.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_scope.return_value.__exit__ = MagicMock(return_value=False)
        with (
            patch("feeds.external_market_data.fetch_boc_series", return_value=0) as mock_fetch,
            patch("feeds.external_market_data.session_scope", mock_scope),
            patch("feeds.external_market_data.log_ingestion"),
            patch("time.sleep"),
        ):
            fetch_all_boc_series()
        called_series_ids = [c.args[0] for c in mock_fetch.call_args_list]
        for series_id in BOC_SERIES.values():
            assert series_id in called_series_ids


class TestFetchAllFredSeries:
    """fetch_all_fred_series(): calls fetch_fred_series for each key in FRED_SERIES."""

    def test_calls_fetch_for_each_series(self):
        from feeds.external_market_data import fetch_all_fred_series, FRED_SERIES
        with patch("feeds.external_market_data.fetch_fred_series", return_value=0) as mock_fetch:
            fetch_all_fred_series()
        assert mock_fetch.call_count == len(FRED_SERIES)


class TestFetchEiaWtiNoKey:
    """fetch_eia_wti(): no-key guard returns 0 without network."""

    def test_no_key_returns_zero(self):
        from feeds.external_market_data import fetch_eia_wti
        with patch("config.EIA_API_KEY", ""):
            result = fetch_eia_wti()
        assert result == 0

    def test_no_key_does_not_call_get_json(self):
        from feeds.external_market_data import fetch_eia_wti
        with (
            patch("config.EIA_API_KEY", ""),
            patch("feeds.external_market_data._get_json") as mock_get,
        ):
            fetch_eia_wti()
        mock_get.assert_not_called()

    def test_returns_int(self):
        from feeds.external_market_data import fetch_eia_wti
        with patch("config.EIA_API_KEY", ""):
            result = fetch_eia_wti()
        assert isinstance(result, int)
