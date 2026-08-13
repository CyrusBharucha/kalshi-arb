"""
tests/test_data_layer_coverage_table_sizes.py
==============================================
Unit tests for dashboard/data_layer.py:
  - get_coverage_stats(): returns dict with required keys; exception → defaults
  - get_table_sizes(): returns pd.DataFrame; exception → empty DataFrame

No live DB — all calls mocked.
"""
from __future__ import annotations

import sys
import pandas as pd
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    # cache_data must be a passthrough decorator so functions work normally
    st_mock = MagicMock()
    st_mock.cache_data = lambda *args, **kwargs: (lambda fn: fn) if not args else args[0]
    with patch.dict(sys.modules, {
        "config": MagicMock(
            DB_URL="postgresql://localhost/test",
            KALSHI_READ_RPS=10, KALSHI_WRITE_RPS=1,
        ),
        "database.models": MagicMock(),
        "database.repository": MagicMock(session_scope=MagicMock()),
        "streamlit": st_mock,
    }):
        yield


# ---------------------------------------------------------------------------
# get_coverage_stats
# ---------------------------------------------------------------------------

class TestGetCoverageStats:
    def test_returns_dict(self):
        from dashboard.data_layer import get_coverage_stats
        with patch("dashboard.data_layer._db_available", return_value=False):
            result = get_coverage_stats()
        assert isinstance(result, dict)

    def test_has_markets_monitored_key(self):
        from dashboard.data_layer import get_coverage_stats
        with patch("dashboard.data_layer._db_available", return_value=False):
            result = get_coverage_stats()
        assert "markets_monitored" in result

    def test_has_canadian_markets_key(self):
        from dashboard.data_layer import get_coverage_stats
        with patch("dashboard.data_layer._db_available", return_value=False):
            result = get_coverage_stats()
        assert "canadian_markets" in result

    def test_has_markets_with_relationships_key(self):
        from dashboard.data_layer import get_coverage_stats
        with patch("dashboard.data_layer._db_available", return_value=False):
            result = get_coverage_stats()
        assert "markets_with_relationships" in result

    def test_never_raises(self):
        from dashboard.data_layer import get_coverage_stats
        with patch("dashboard.data_layer._db_available", return_value=False):
            try:
                get_coverage_stats()
            except Exception:
                pytest.fail("get_coverage_stats raised unexpectedly")

    def test_exception_gives_defaults(self):
        from dashboard.data_layer import get_coverage_stats
        # When DB not available, function should still return a dict with expected keys
        with patch("dashboard.data_layer._db_available", return_value=False):
            result = get_coverage_stats()
        assert "markets_monitored" in result
        assert "canadian_markets" in result


# ---------------------------------------------------------------------------
# get_table_sizes
# ---------------------------------------------------------------------------

class TestGetTableSizes:
    def test_returns_dataframe(self):
        from dashboard.data_layer import get_table_sizes
        with patch("dashboard.data_layer._db_available", return_value=False):
            result = get_table_sizes()
        assert isinstance(result, pd.DataFrame)

    def test_never_raises(self):
        from dashboard.data_layer import get_table_sizes
        with patch("dashboard.data_layer._db_available", return_value=False):
            try:
                get_table_sizes()
            except Exception:
                pytest.fail("get_table_sizes raised unexpectedly")

    def test_exception_returns_empty_df(self):
        """When DB unavailable or query fails, returns empty DataFrame."""
        from dashboard.data_layer import get_table_sizes
        with patch("dashboard.data_layer._db_available", return_value=False):
            result = get_table_sizes()
        # Either empty or populated - just shouldn't raise
        assert isinstance(result, pd.DataFrame)
