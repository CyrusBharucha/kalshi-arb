"""
tests/test_external_data.py
============================
Unit tests for data/external_market_data.py.

Tests only pure-logic functions and mocked HTTP calls — no live DB, no real API.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import MagicMock, patch, call

import pytest
import pandas as pd


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _boc_response(series_id: str, rows: list) -> Dict[str, Any]:
    """Build a fake BOC VALET JSON response."""
    return {
        "observations": [
            {"d": date, series_id: {"v": str(val)}}
            for date, val in rows
        ]
    }


# ---------------------------------------------------------------------------
# fetch_boc_series (mocked)
# ---------------------------------------------------------------------------

class TestFetchBocSeries:
    """Test fetch_boc_series with mocked HTTP and mocked DB session."""

    @pytest.fixture(autouse=True)
    def _patch_deps(self):
        """Patch _get_json (HTTP), session_scope, upsert_external_data, log_ingestion."""
        with (
            patch("feeds.external_market_data._get_json") as mock_http,
            patch("feeds.external_market_data.session_scope") as mock_scope,
            patch("feeds.external_market_data.upsert_external_data") as mock_upsert,
            patch("feeds.external_market_data.log_ingestion") as mock_log,
        ):
            # session_scope() as ctx manager yields a fake session
            mock_session = MagicMock()
            mock_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_scope.return_value.__exit__ = MagicMock(return_value=False)

            self.mock_http   = mock_http
            self.mock_scope  = mock_scope
            self.mock_upsert = mock_upsert
            self.mock_log    = mock_log
            yield

    def _setup_response(self, series_id: str, rows: list):
        self.mock_http.return_value = _boc_response(series_id, rows)

    def test_returns_count_of_inserted(self):
        from feeds.external_market_data import fetch_boc_series
        self._setup_response("V39079", [("2026-01-01", "4.5"), ("2026-01-02", "4.5")])
        n = fetch_boc_series("V39079", "CORRA")
        assert n == 2

    def test_skips_missing_value(self):
        from feeds.external_market_data import fetch_boc_series
        # One row has None value
        self.mock_http.return_value = {
            "observations": [
                {"d": "2026-01-01", "V39079": {"v": "4.5"}},
                {"d": "2026-01-02", "V39079": {"v": None}},  # should be skipped
                {"d": "2026-01-03"},                          # no series key
            ]
        }
        n = fetch_boc_series("V39079", "CORRA")
        assert n == 1

    def test_calls_upsert_for_each_valid_row(self):
        from feeds.external_market_data import fetch_boc_series
        self._setup_response("V122514", [("2026-01-01", "4.25"), ("2026-01-02", "4.25")])
        fetch_boc_series("V122514", "BOC_RATE")
        assert self.mock_upsert.call_count == 2

    def test_converts_percent_to_decimal(self):
        """BOC yields are in percent — should be divided by 100."""
        from feeds.external_market_data import fetch_boc_series
        self._setup_response("V122531", [("2026-01-01", "3.50")])
        fetch_boc_series("V122531", "CAGB_2Y")
        call_args = self.mock_upsert.call_args
        row = call_args[0][1]  # second positional arg is the row dict
        assert abs(row["price"] - 0.035) < 1e-9

    def test_source_field_is_boc_valet(self):
        from feeds.external_market_data import fetch_boc_series
        self._setup_response("V39079", [("2026-01-01", "4.5")])
        fetch_boc_series("V39079", "CORRA")
        row = self.mock_upsert.call_args[0][1]
        assert row["source"] == "boc_valet"

    def test_empty_observations_returns_zero(self):
        from feeds.external_market_data import fetch_boc_series
        self.mock_http.return_value = {"observations": []}
        n = fetch_boc_series("V39079", "CORRA")
        assert n == 0

    def test_asset_name_in_row(self):
        from feeds.external_market_data import fetch_boc_series
        self._setup_response("V39079", [("2026-01-01", "4.5")])
        fetch_boc_series("V39079", "CORRA")
        row = self.mock_upsert.call_args[0][1]
        assert row["asset"] == "CORRA"


# ---------------------------------------------------------------------------
# fetch_all_boc_series (mocked)
# ---------------------------------------------------------------------------

class TestFetchAllBocSeries:
    def test_calls_each_configured_series(self):
        """fetch_all_boc_series loops over BOC_SERIES dict."""
        from feeds.external_market_data import BOC_SERIES
        with (
            patch("feeds.external_market_data.fetch_boc_series") as mock_fetch,
            patch("feeds.external_market_data.session_scope") as mock_scope,
            patch("feeds.external_market_data.log_ingestion"),
            patch("feeds.external_market_data.time.sleep"),
        ):
            mock_scope.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_scope.return_value.__exit__ = MagicMock(return_value=False)
            mock_fetch.return_value = 5

            from feeds.external_market_data import fetch_all_boc_series
            fetch_all_boc_series()

            assert mock_fetch.call_count == len(BOC_SERIES)

    def test_continues_on_exception(self):
        """A failing series should not abort the whole batch."""
        with (
            patch("feeds.external_market_data.fetch_boc_series", side_effect=Exception("timeout")),
            patch("feeds.external_market_data.session_scope") as mock_scope,
            patch("feeds.external_market_data.log_ingestion"),
            patch("feeds.external_market_data.time.sleep"),
        ):
            mock_scope.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_scope.return_value.__exit__ = MagicMock(return_value=False)

            from feeds.external_market_data import fetch_all_boc_series
            # Should not raise even though fetch_boc_series always throws
            fetch_all_boc_series()


# ---------------------------------------------------------------------------
# _get_json helper (retry logic)
# ---------------------------------------------------------------------------

class TestGetJson:
    def test_returns_json_on_success(self):
        from feeds.external_market_data import _get_json
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"key": "val"}
        mock_resp.raise_for_status = MagicMock()
        with patch("feeds.external_market_data._session") as mock_session:
            mock_session.get.return_value = mock_resp
            result = _get_json("https://example.com")
            assert result == {"key": "val"}

    def test_raises_after_retries_exhausted(self):
        from feeds.external_market_data import _get_json
        with patch("feeds.external_market_data._session") as mock_session:
            mock_session.get.side_effect = ConnectionError("timeout")
            with pytest.raises(ConnectionError):
                _get_json("https://example.com", retries=2)

    def test_retries_on_transient_error(self):
        """Succeeds on 2nd attempt after 1 failure."""
        from feeds.external_market_data import _get_json
        good_resp = MagicMock()
        good_resp.json.return_value = {"ok": True}
        good_resp.raise_for_status = MagicMock()
        with patch("feeds.external_market_data._session") as mock_session:
            mock_session.get.side_effect = [ConnectionError("fail"), good_resp]
            with patch("feeds.external_market_data.time.sleep"):
                result = _get_json("https://example.com", retries=2)
                assert result == {"ok": True}
