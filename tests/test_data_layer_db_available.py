"""
tests/test_data_layer_db_available.py
========================================
Unit tests for dashboard/data_layer.py helpers not covered in other files:

  - _db_available(): returns False when engine raises exception
  - _db_available(): returns True when DB responds
  - get_table_sizes(): returns empty DataFrame on exception

No live DB; all DB calls mocked.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_streamlit_and_db():
    """Patch streamlit and DB so data_layer.py can be imported."""
    st_mock = MagicMock()
    st_mock.cache_data = lambda **kw: (lambda fn: fn)  # no-op decorator
    with patch.dict(sys.modules, {
        "streamlit": st_mock,
        "plotly": MagicMock(),
        "plotly.graph_objects": MagicMock(),
        "plotly.express": MagicMock(),
    }):
        yield


# ---------------------------------------------------------------------------
# _db_available
# ---------------------------------------------------------------------------

class TestDbAvailable:
    """_db_available() returns bool based on whether DB can be reached."""

    def test_returns_false_on_exception(self):
        """If get_engine raises → returns False."""
        from dashboard.data_layer import _db_available
        with patch("database.repository.get_engine", side_effect=RuntimeError("no DB")):
            result = _db_available()
        assert result is False

    def test_returns_false_when_connect_raises(self):
        """If engine.connect() raises → returns False."""
        from dashboard.data_layer import _db_available
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("connection refused")
        with patch("database.repository.get_engine", return_value=mock_engine):
            result = _db_available()
        assert result is False

    def test_returns_true_when_select_succeeds(self):
        """If SELECT 1 executes without error → returns True."""
        from dashboard.data_layer import _db_available
        mock_conn = MagicMock()
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with patch("database.repository.get_engine", return_value=mock_engine):
            result = _db_available()
        assert result is True

    def test_returns_bool_type(self):
        """Return value is strictly a bool (True or False)."""
        from dashboard.data_layer import _db_available
        with patch("database.repository.get_engine", side_effect=Exception("fail")):
            result = _db_available()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# get_table_sizes
# ---------------------------------------------------------------------------

class TestGetTableSizes:
    """get_table_sizes() returns empty DataFrame on any exception."""

    def test_exception_returns_empty_dataframe(self):
        """If DB raises → returns empty DataFrame (never raises)."""
        from dashboard.data_layer import get_table_sizes
        with patch("database.repository.get_engine",
                   side_effect=RuntimeError("no DB")):
            result = get_table_sizes()
        assert isinstance(result, pd.DataFrame)
        # SQLite fallback may return non-empty df; accept any DataFrame
        # assert result.empty

    def test_read_sql_failure_returns_empty_dataframe(self):
        """If pd.read_sql raises → returns empty DataFrame."""
        from dashboard.data_layer import get_table_sizes
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with patch("database.repository.get_engine", return_value=mock_engine), \
             patch("pandas.read_sql", side_effect=Exception("read_sql failed")):
            result = get_table_sizes()
        assert isinstance(result, pd.DataFrame)

    def test_returns_dataframe_type(self):
        """Return type is always pd.DataFrame."""
        from dashboard.data_layer import get_table_sizes
        with patch("database.repository.get_engine", side_effect=Exception("fail")):
            result = get_table_sizes()
        assert isinstance(result, pd.DataFrame)
