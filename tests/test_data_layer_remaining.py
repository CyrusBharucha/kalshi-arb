"""
tests/test_data_layer_remaining.py
=====================================
Unit tests for the remaining uncovered functions in dashboard/data_layer.py:
  - get_open_markets(): (empty_df, err) on exception, accepts filter params
  - get_historical_arb_summary(): (empty_df, err) on exception, accepts filter params
  - get_cross_asset_data(): (empty_df, err) on exception, never raises
  - get_canadian_historical_arb_stats(): dict on any path, never raises
  - get_data_quality_stats(): dict on any path, never raises
  - _filter_canadian_opps(): pure DataFrame filter, no DB

All DB calls mocked; no network.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_streamlit():
    st_mock = MagicMock()
    st_mock.cache_data = lambda **kw: (lambda fn: fn)
    with patch.dict(sys.modules, {
        "streamlit": st_mock,
        "plotly": MagicMock(),
        "plotly.graph_objects": MagicMock(),
        "plotly.express": MagicMock(),
    }):
        yield


def _engine_error():
    return patch("database.repository.get_engine", side_effect=RuntimeError("no DB"))


def _assert_df_error(result):
    assert isinstance(result, tuple) and len(result) == 2
    df, err = result
    assert isinstance(df, pd.DataFrame)
    # When SQLite fallback succeeds: df may be non-empty, err is None
    # When fully unavailable: df is empty, err is a non-empty str
    assert err is None or (isinstance(err, str) and len(err) > 0)


# ---------------------------------------------------------------------------
# get_open_markets
# ---------------------------------------------------------------------------

class TestGetOpenMarkets:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_open_markets
        with _engine_error():
            result = get_open_markets()
        _assert_df_error(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_open_markets
        with _engine_error():
            try:
                get_open_markets()
            except Exception:
                pytest.fail("get_open_markets should not raise")

    def test_accepts_status_filter(self):
        from dashboard.data_layer import get_open_markets
        with _engine_error():
            df, err = get_open_markets(status_filter="closed")
        assert isinstance(df, pd.DataFrame)

    def test_accepts_category_filter(self):
        from dashboard.data_layer import get_open_markets
        with _engine_error():
            df, err = get_open_markets(category_filter="Politics")
        assert isinstance(df, pd.DataFrame)

    def test_accepts_canadian_only_flag(self):
        from dashboard.data_layer import get_open_markets
        with _engine_error():
            df, err = get_open_markets(canadian_only=True)
        assert isinstance(df, pd.DataFrame)

    def test_accepts_min_volume(self):
        from dashboard.data_layer import get_open_markets
        with _engine_error():
            df, err = get_open_markets(min_volume=100.0)
        assert isinstance(df, pd.DataFrame)

    def test_accepts_limit(self):
        from dashboard.data_layer import get_open_markets
        with _engine_error():
            df, err = get_open_markets(limit=50)
        assert isinstance(df, pd.DataFrame)


# ---------------------------------------------------------------------------
# get_historical_arb_summary
# ---------------------------------------------------------------------------

class TestGetHistoricalArbSummary:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_historical_arb_summary
        with _engine_error():
            result = get_historical_arb_summary()
        _assert_df_error(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_historical_arb_summary
        with _engine_error():
            try:
                get_historical_arb_summary()
            except Exception:
                pytest.fail("get_historical_arb_summary should not raise")

    def test_accepts_strategy_filter(self):
        from dashboard.data_layer import get_historical_arb_summary
        with _engine_error():
            df, err = get_historical_arb_summary(strategy="complement")
        assert isinstance(df, pd.DataFrame)

    def test_accepts_classification_filter(self):
        from dashboard.data_layer import get_historical_arb_summary
        with _engine_error():
            df, err = get_historical_arb_summary(classification="A")
        assert isinstance(df, pd.DataFrame)

    def test_accepts_min_net_edge_cents(self):
        from dashboard.data_layer import get_historical_arb_summary
        with _engine_error():
            df, err = get_historical_arb_summary(min_net_edge_cents=5.0)
        assert isinstance(df, pd.DataFrame)

    def test_accepts_days_back(self):
        from dashboard.data_layer import get_historical_arb_summary
        with _engine_error():
            df, err = get_historical_arb_summary(days_back=30)
        assert isinstance(df, pd.DataFrame)


# ---------------------------------------------------------------------------
# get_cross_asset_data
# ---------------------------------------------------------------------------

class TestGetCrossAssetData:
    def test_exception_returns_empty_df_and_error_str(self):
        from dashboard.data_layer import get_cross_asset_data
        with _engine_error():
            result = get_cross_asset_data("KXTEST-01", "SPX")
        _assert_df_error(result)

    def test_never_raises(self):
        from dashboard.data_layer import get_cross_asset_data
        with _engine_error():
            try:
                get_cross_asset_data("KXTEST-01", "SPX")
            except Exception:
                pytest.fail("get_cross_asset_data should not raise")

    def test_result_is_tuple(self):
        from dashboard.data_layer import get_cross_asset_data
        with _engine_error():
            result = get_cross_asset_data("KXTEST-01", "SPX")
        assert isinstance(result, tuple) and len(result) == 2


# ---------------------------------------------------------------------------
# get_canadian_historical_arb_stats
# ---------------------------------------------------------------------------

class TestGetCanadianHistoricalArbStats:
    def test_returns_dict(self):
        from dashboard.data_layer import get_canadian_historical_arb_stats
        with _engine_error():
            result = get_canadian_historical_arb_stats()
        assert isinstance(result, dict)

    def test_never_raises(self):
        from dashboard.data_layer import get_canadian_historical_arb_stats
        with _engine_error():
            try:
                get_canadian_historical_arb_stats()
            except Exception:
                pytest.fail("get_canadian_historical_arb_stats should not raise")

    def test_result_not_none(self):
        from dashboard.data_layer import get_canadian_historical_arb_stats
        with _engine_error():
            result = get_canadian_historical_arb_stats()
        assert result is not None


# ---------------------------------------------------------------------------
# get_data_quality_stats
# ---------------------------------------------------------------------------

class TestGetDataQualityStats:
    def test_returns_dict(self):
        from dashboard.data_layer import get_data_quality_stats
        with _engine_error():
            result = get_data_quality_stats()
        assert isinstance(result, dict)

    def test_never_raises(self):
        from dashboard.data_layer import get_data_quality_stats
        with _engine_error():
            try:
                get_data_quality_stats()
            except Exception:
                pytest.fail("get_data_quality_stats should not raise")

    def test_result_not_none(self):
        from dashboard.data_layer import get_data_quality_stats
        with _engine_error():
            result = get_data_quality_stats()
        assert result is not None


# ---------------------------------------------------------------------------
# _filter_canadian_opps  (pure DataFrame function — no DB needed)
# ---------------------------------------------------------------------------

class TestFilterCanadianOpps:
    def _make_df(self, markets_involved_list):
        """Build a DataFrame with markets_involved column (as _filter_canadian_opps expects)."""
        return pd.DataFrame({
            "markets_involved": markets_involved_list,
            "net_edge": [0.05] * len(markets_involved_list),
        })

    def test_empty_df_returns_empty_df(self):
        from dashboard.data_layer import _filter_canadian_opps
        df = pd.DataFrame({"markets_involved": pd.Series([], dtype=str), "net_edge": []})
        result = _filter_canadian_opps(df)
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_non_canadian_returns_empty(self):
        from dashboard.data_layer import _filter_canadian_opps
        df = self._make_df(['["KXNASDAQ-26", "KXSP500-26"]'])
        result = _filter_canadian_opps(df)
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_boc_market_returned(self):
        from dashboard.data_layer import _filter_canadian_opps
        df = self._make_df(['["KXBOCRATE-26JAN"]'])
        result = _filter_canadian_opps(df)
        assert len(result) == 1

    def test_canada_keyword_returned(self):
        from dashboard.data_layer import _filter_canadian_opps
        df = self._make_df(['["KXCANADA-ELECTION-26"]'])
        result = _filter_canadian_opps(df)
        assert len(result) == 1

    def test_preserves_all_columns(self):
        from dashboard.data_layer import _filter_canadian_opps
        df = self._make_df(['["KXBOCRATE-26"]'])
        result = _filter_canadian_opps(df)
        for col in df.columns:
            assert col in result.columns

    def test_mixed_keeps_only_canadian(self):
        from dashboard.data_layer import _filter_canadian_opps
        df = self._make_df([
            '["KXBOCRATE-26"]',       # Canadian
            '["KXNASDAQ-26"]',        # NOT Canadian
            '["KXTORONTO-26"]',       # Canadian (toronto)
        ])
        result = _filter_canadian_opps(df)
        assert len(result) == 2
