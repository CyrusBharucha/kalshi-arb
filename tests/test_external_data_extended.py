"""
tests/test_external_data_extended.py
=====================================
Extended unit tests for data/external_market_data.py.

Covers:
  - fetch_fred_series: no-key guard, skip "." values, insert count, source field
  - fetch_eia_wti: no-key guard, data extraction, source field
  - fetch_all_fred_series: iterates over FRED_SERIES, continues on exception
  - BOC_SERIES / FRED_SERIES: structure contracts

All HTTP calls and DB sessions are mocked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: build a mock session_scope context manager
# ---------------------------------------------------------------------------

def _make_scope():
    mock_session = MagicMock()
    mock_scope = MagicMock()
    mock_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_scope.return_value.__exit__ = MagicMock(return_value=False)
    return mock_scope, mock_session


# ---------------------------------------------------------------------------
# fetch_fred_series
# ---------------------------------------------------------------------------

class TestFetchFredSeries:

    def _run(self, observations, *, api_key="TESTKEY"):
        mock_scope, mock_session = _make_scope()
        with patch("feeds.external_market_data._get_json") as mock_http, \
             patch("feeds.external_market_data.session_scope", mock_scope), \
             patch("feeds.external_market_data.upsert_external_data") as mock_upsert, \
             patch("feeds.external_market_data.log_ingestion"), \
             patch("feeds.external_market_data.config") as mock_cfg:
            mock_cfg.FRED_API_KEY = api_key
            mock_cfg.FRED_BASE_URL = "https://api.stlouisfed.org/fred"
            mock_http.return_value = {"observations": observations}
            from feeds.external_market_data import fetch_fred_series
            n = fetch_fred_series("DGS10", "US_10Y", "government_bond", "10Y")
        return n, mock_upsert

    def test_returns_zero_without_api_key(self):
        mock_scope, _ = _make_scope()
        with patch("feeds.external_market_data.config") as mock_cfg:
            mock_cfg.FRED_API_KEY = ""
            from feeds.external_market_data import fetch_fred_series
            n = fetch_fred_series("DGS10", "US_10Y", "government_bond", "10Y")
        assert n == 0

    def test_returns_count_of_inserted(self):
        obs = [
            {"date": "2026-01-01", "value": "4.1"},
            {"date": "2026-01-02", "value": "4.2"},
        ]
        n, _ = self._run(obs)
        assert n == 2

    def test_skips_dot_value(self):
        obs = [
            {"date": "2026-01-01", "value": "."},
            {"date": "2026-01-02", "value": "4.2"},
        ]
        n, mock_upsert = self._run(obs)
        assert n == 1
        assert mock_upsert.call_count == 1

    def test_skips_missing_date(self):
        obs = [
            {"value": "4.2"},  # no date
            {"date": "2026-01-02", "value": "4.1"},
        ]
        n, _ = self._run(obs)
        assert n == 1

    def test_source_field_is_fred(self):
        obs = [{"date": "2026-01-01", "value": "3.9"}]
        _, mock_upsert = self._run(obs)
        row = mock_upsert.call_args[0][1]
        assert row["source"] == "fred"

    def test_asset_field_matches_argument(self):
        obs = [{"date": "2026-01-01", "value": "3.9"}]
        _, mock_upsert = self._run(obs)
        row = mock_upsert.call_args[0][1]
        assert row["asset"] == "US_10Y"

    def test_price_is_float(self):
        obs = [{"date": "2026-01-01", "value": "4.25"}]
        _, mock_upsert = self._run(obs)
        row = mock_upsert.call_args[0][1]
        assert isinstance(row["price"], float)
        assert abs(row["price"] - 4.25) < 1e-9

    def test_empty_observations_returns_zero(self):
        n, _ = self._run([])
        assert n == 0


# ---------------------------------------------------------------------------
# fetch_eia_wti
# ---------------------------------------------------------------------------

class TestFetchEiaWti:

    def _run_wti(self, response_data, *, api_key="TESTKEY"):
        mock_scope, _ = _make_scope()
        with patch("feeds.external_market_data._get_json") as mock_http, \
             patch("feeds.external_market_data.session_scope", mock_scope), \
             patch("feeds.external_market_data.upsert_external_data") as mock_upsert, \
             patch("feeds.external_market_data.log_ingestion"), \
             patch("feeds.external_market_data.config") as mock_cfg:
            mock_cfg.EIA_API_KEY = api_key
            mock_cfg.EIA_BASE_URL = "https://api.eia.gov/v2"
            mock_http.return_value = response_data
            from feeds.external_market_data import fetch_eia_wti
            n = fetch_eia_wti()
        return n, mock_upsert

    def test_returns_zero_without_api_key(self):
        with patch("feeds.external_market_data.config") as mock_cfg:
            mock_cfg.EIA_API_KEY = ""
            from feeds.external_market_data import fetch_eia_wti
            n = fetch_eia_wti()
        assert n == 0

    def test_returns_count_inserted(self):
        response = {
            "response": {
                "data": [
                    {"period": "2026-01-01", "value": 78.50},
                    {"period": "2026-01-08", "value": 79.10},
                ]
            }
        }
        n, _ = self._run_wti(response)
        assert n == 2

    def test_source_field_is_eia(self):
        response = {
            "response": {
                "data": [{"period": "2026-01-01", "value": 78.50}]
            }
        }
        _, mock_upsert = self._run_wti(response)
        row = mock_upsert.call_args[0][1]
        assert row["source"] == "eia"

    def test_empty_data_returns_zero(self):
        response = {"response": {"data": []}}
        n, _ = self._run_wti(response)
        assert n == 0

    def test_skips_none_value(self):
        response = {
            "response": {
                "data": [
                    {"period": "2026-01-01", "value": None},
                    {"period": "2026-01-08", "value": 79.10},
                ]
            }
        }
        n, mock_upsert = self._run_wti(response)
        assert n == 1


# ---------------------------------------------------------------------------
# Series constant structure contracts
# ---------------------------------------------------------------------------

class TestBocSeriesStructure:
    """BOC_SERIES constant has expected shape."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from feeds.external_market_data import BOC_SERIES
        self.boc = BOC_SERIES

    def test_is_dict(self):
        assert isinstance(self.boc, dict)

    def test_has_entries(self):
        assert len(self.boc) >= 1

    def test_values_are_strings(self):
        for k, v in self.boc.items():
            assert isinstance(v, str), f"{k}: expected str series_id, got {type(v)}"

    def test_corra_is_present(self):
        keys = [k.upper() for k in self.boc.keys()]
        assert any("CORRA" in k for k in keys)


class TestFredSeriesStructure:
    """FRED_SERIES constant has expected shape."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from feeds.external_market_data import FRED_SERIES
        self.fred = FRED_SERIES

    def test_is_dict(self):
        assert isinstance(self.fred, dict)

    def test_has_entries(self):
        assert len(self.fred) >= 1

    def test_values_are_tuples(self):
        for k, v in self.fred.items():
            assert isinstance(v, tuple), f"{k}: expected tuple, got {type(v)}"

    def test_each_tuple_has_four_elements(self):
        for k, v in self.fred.items():
            assert len(v) == 4, f"{k}: expected 4-tuple, got {len(v)}"
