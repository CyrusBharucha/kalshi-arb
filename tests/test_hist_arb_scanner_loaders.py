"""
tests/test_hist_arb_scanner_loaders.py
========================================
Unit tests for analysis/historical_arb_scanner loader functions:
  - load_relationships_with_candles()
  - load_price_series()

All DB calls are mocked — no live PostgreSQL required.
"""
from __future__ import annotations

import sys
import pandas as pd
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
        "feeds.kalshi_client": MagicMock(),
    }):
        yield


def _mock_session(return_df=None):
    if return_df is None:
        return_df = pd.DataFrame()
    mock_ss = MagicMock()
    mock_sess = MagicMock()
    mock_sess.bind = MagicMock()
    mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
    mock_ss.return_value.__exit__ = MagicMock(return_value=False)
    return mock_ss, mock_sess, return_df


# ---------------------------------------------------------------------------
# load_relationships_with_candles
# ---------------------------------------------------------------------------

class TestLoadRelationshipsWithCandles:
    def test_returns_dataframe(self):
        from analysis.historical_arb_scanner import load_relationships_with_candles
        mock_ss, _, expected = _mock_session(pd.DataFrame({
            "market_id_1": ["A"], "market_id_2": ["B"],
            "relationship_type": ["mutually_exclusive"],
        }))
        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", return_value=expected):
            result = load_relationships_with_candles()
        assert isinstance(result, pd.DataFrame)

    def test_calls_session_scope(self):
        from analysis.historical_arb_scanner import load_relationships_with_candles
        mock_ss, _, _ = _mock_session()
        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", return_value=pd.DataFrame()):
            load_relationships_with_candles()
        mock_ss.assert_called_once()

    def test_calls_read_sql(self):
        from analysis.historical_arb_scanner import load_relationships_with_candles
        mock_ss, _, _ = _mock_session()
        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", return_value=pd.DataFrame()) as mock_rs:
            load_relationships_with_candles()
        mock_rs.assert_called_once()

    def test_empty_returns_empty_df(self):
        from analysis.historical_arb_scanner import load_relationships_with_candles
        mock_ss, _, _ = _mock_session()
        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", return_value=pd.DataFrame()):
            result = load_relationships_with_candles()
        assert result.empty

    def test_returns_read_sql_result(self):
        from analysis.historical_arb_scanner import load_relationships_with_candles
        expected = pd.DataFrame({"market_id_1": ["X"], "market_id_2": ["Y"]})
        mock_ss, _, _ = _mock_session()
        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", return_value=expected):
            result = load_relationships_with_candles()
        assert len(result) == 1
        assert result["market_id_1"].iloc[0] == "X"

    def test_multiple_rows(self):
        from analysis.historical_arb_scanner import load_relationships_with_candles
        expected = pd.DataFrame({
            "market_id_1": ["A", "B", "C"],
            "market_id_2": ["D", "E", "F"],
        })
        mock_ss, _, _ = _mock_session()
        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", return_value=expected):
            result = load_relationships_with_candles()
        assert len(result) == 3


# ---------------------------------------------------------------------------
# load_price_series
# ---------------------------------------------------------------------------

class TestLoadPriceSeries:
    def test_empty_market_ids_returns_empty_df(self):
        from analysis.historical_arb_scanner import load_price_series
        result = load_price_series([])
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_empty_does_not_call_session_scope(self):
        from analysis.historical_arb_scanner import load_price_series
        with patch("analysis.historical_arb_scanner.session_scope") as mock_ss:
            load_price_series([])
        mock_ss.assert_not_called()

    def test_nonempty_calls_session_scope(self):
        from analysis.historical_arb_scanner import load_price_series
        mock_ss, _, _ = _mock_session()
        df = pd.DataFrame({
            "market_id": ["MKT-A"],
            "period_end_ts": [pd.Timestamp("2026-01-01", tz="UTC")],
            "price_close": [0.55],
            "price_open": [0.50],
            "price_high": [0.60],
            "price_low": [0.45],
            "volume": [1000.0],
            "yes_bid_close": [0.54],
            "yes_ask_close": [0.56],
        })
        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", return_value=df):
            load_price_series(["MKT-A"])
        mock_ss.assert_called_once()

    def test_nonempty_calls_read_sql(self):
        from analysis.historical_arb_scanner import load_price_series
        mock_ss, _, _ = _mock_session()
        df = pd.DataFrame({
            "market_id": ["MKT-A"],
            "period_end_ts": [pd.Timestamp("2026-01-01", tz="UTC")],
            "price_close": [0.55],
            "price_open": [0.50],
            "price_high": [0.60],
            "price_low": [0.45],
            "volume": [1000.0],
            "yes_bid_close": [0.54],
            "yes_ask_close": [0.56],
        })
        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", return_value=df) as mock_rs:
            load_price_series(["MKT-A"])
        mock_rs.assert_called_once()

    def test_returns_dataframe(self):
        from analysis.historical_arb_scanner import load_price_series
        mock_ss, _, _ = _mock_session()
        df = pd.DataFrame({
            "market_id": ["MKT-A"],
            "period_end_ts": [pd.Timestamp("2026-01-01", tz="UTC")],
            "price_close": ["0.55"],
            "price_open": [0.50],
            "price_high": [0.60],
            "price_low": [0.45],
            "volume": ["1000"],
            "yes_bid_close": [0.54],
            "yes_ask_close": [0.56],
        })
        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", return_value=df):
            result = load_price_series(["MKT-A"])
        assert isinstance(result, pd.DataFrame)

    def test_price_close_coerced_to_numeric(self):
        from analysis.historical_arb_scanner import load_price_series
        mock_ss, _, _ = _mock_session()
        df = pd.DataFrame({
            "market_id": ["MKT-A"],
            "period_end_ts": [pd.Timestamp("2026-01-01", tz="UTC")],
            "price_close": ["0.55"],
            "price_open": [0.50],
            "price_high": [0.60],
            "price_low": [0.45],
            "volume": ["1000"],
            "yes_bid_close": [0.54],
            "yes_ask_close": [0.56],
        })
        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", return_value=df):
            result = load_price_series(["MKT-A"])
        assert pd.api.types.is_float_dtype(result["price_close"])

    def test_volume_coerced_to_numeric(self):
        from analysis.historical_arb_scanner import load_price_series
        mock_ss, _, _ = _mock_session()
        df = pd.DataFrame({
            "market_id": ["MKT-A"],
            "period_end_ts": [pd.Timestamp("2026-01-01", tz="UTC")],
            "price_close": [0.55],
            "price_open": [0.50],
            "price_high": [0.60],
            "price_low": [0.45],
            "volume": ["9999.5"],
            "yes_bid_close": [0.54],
            "yes_ask_close": [0.56],
        })
        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", return_value=df):
            result = load_price_series(["MKT-A"])
        assert pd.api.types.is_numeric_dtype(result["volume"])

    def test_default_period_interval_is_60(self):
        """Default period_interval=60 is passed to the SQL query as :pi."""
        from analysis.historical_arb_scanner import load_price_series
        mock_ss, _, _ = _mock_session()
        df = pd.DataFrame({
            "market_id": ["MKT-A"],
            "period_end_ts": [pd.Timestamp("2026-01-01", tz="UTC")],
            "price_close": [0.55],
            "price_open": [0.50],
            "price_high": [0.60],
            "price_low": [0.45],
            "volume": [1000.0],
            "yes_bid_close": [0.54],
            "yes_ask_close": [0.56],
        })
        captured_params = {}

        def capture_read_sql(query, bind, params=None):
            captured_params.update(params or {})
            return df

        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", side_effect=capture_read_sql):
            load_price_series(["MKT-A"])
        assert captured_params.get("pi") == 60

    def test_custom_period_interval_forwarded(self):
        from analysis.historical_arb_scanner import load_price_series
        mock_ss, _, _ = _mock_session()
        df = pd.DataFrame({
            "market_id": ["MKT-A"],
            "period_end_ts": [pd.Timestamp("2026-01-01", tz="UTC")],
            "price_close": [0.55],
            "price_open": [0.50],
            "price_high": [0.60],
            "price_low": [0.45],
            "volume": [1000.0],
            "yes_bid_close": [0.54],
            "yes_ask_close": [0.56],
        })
        captured_params = {}

        def capture_read_sql(query, bind, params=None):
            captured_params.update(params or {})
            return df

        with patch("analysis.historical_arb_scanner.session_scope", mock_ss), \
             patch("analysis.historical_arb_scanner.pd.read_sql", side_effect=capture_read_sql):
            load_price_series(["MKT-A"], period_interval=5)
        assert captured_params.get("pi") == 5
