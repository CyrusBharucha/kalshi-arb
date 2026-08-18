"""
tests/test_diagnose_ingest.py
================================
Unit tests for scripts/diagnose_ingest.py:
  - TABLES: non-empty list with known table names
  - row_count(): returns int on success, error string on exception
  - table_date_range(): returns (None, None) on empty table or exception,
    (str, str) when data present

No DB, no network.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        import importlib
        import scripts.diagnose_ingest as _m  # noqa: F401
        yield


class TestTablesConstant:
    """TABLES: list of DB table names."""

    def test_is_list(self):
        from scripts.diagnose_ingest import TABLES
        assert isinstance(TABLES, list)

    def test_nonempty(self):
        from scripts.diagnose_ingest import TABLES
        assert len(TABLES) > 0

    def test_all_string_entries(self):
        from scripts.diagnose_ingest import TABLES
        for t in TABLES:
            assert isinstance(t, str)

    def test_contains_events(self):
        from scripts.diagnose_ingest import TABLES
        assert "events" in TABLES

    def test_contains_markets(self):
        from scripts.diagnose_ingest import TABLES
        assert "markets" in TABLES

    def test_contains_arbitrage_opportunities(self):
        from scripts.diagnose_ingest import TABLES
        assert "arbitrage_opportunities" in TABLES

    def test_contains_trades(self):
        from scripts.diagnose_ingest import TABLES
        assert "trades" in TABLES

    def test_no_duplicates(self):
        from scripts.diagnose_ingest import TABLES
        assert len(TABLES) == len(set(TABLES))


class TestRowCount:
    """row_count(session, table): int on success, error string on exception."""

    def test_returns_int_on_success(self):
        from scripts.diagnose_ingest import row_count
        mock_sess = MagicMock()
        mock_sess.execute.return_value.fetchone.return_value = (42,)
        result = row_count(mock_sess, "events")
        assert result == 42

    def test_returns_zero_when_fetchone_returns_none(self):
        from scripts.diagnose_ingest import row_count
        mock_sess = MagicMock()
        mock_sess.execute.return_value.fetchone.return_value = None
        result = row_count(mock_sess, "events")
        assert result == 0

    def test_returns_error_string_on_exception(self):
        from scripts.diagnose_ingest import row_count
        mock_sess = MagicMock()
        mock_sess.execute.side_effect = RuntimeError("connection refused")
        result = row_count(mock_sess, "events")
        assert isinstance(result, str)
        assert "ERROR" in result

    def test_error_string_contains_message(self):
        from scripts.diagnose_ingest import row_count
        mock_sess = MagicMock()
        mock_sess.execute.side_effect = RuntimeError("connection refused")
        result = row_count(mock_sess, "events")
        assert "connection refused" in result


class TestTableDateRange:
    """table_date_range(session, table, ts_col): date range or (None, None)."""

    def test_none_none_when_exception(self):
        from scripts.diagnose_ingest import table_date_range
        mock_sess = MagicMock()
        mock_sess.execute.side_effect = RuntimeError("no such table")
        result = table_date_range(mock_sess, "events", "created_at")
        assert result == (None, None)

    def test_none_none_when_empty_table(self):
        from scripts.diagnose_ingest import table_date_range
        mock_sess = MagicMock()
        mock_sess.execute.return_value.fetchone.return_value = (None, None)
        result = table_date_range(mock_sess, "events", "created_at")
        assert result == (None, None)

    def test_date_strings_returned(self):
        from scripts.diagnose_ingest import table_date_range
        from datetime import datetime
        mock_sess = MagicMock()
        mock_sess.execute.return_value.fetchone.return_value = (
            datetime(2024, 1, 1),
            datetime(2024, 6, 30),
        )
        lo, hi = table_date_range(mock_sess, "events", "created_at")
        assert lo == "2024-01-01"
        assert hi == "2024-06-30"

    def test_returns_tuple_of_two(self):
        from scripts.diagnose_ingest import table_date_range
        mock_sess = MagicMock()
        mock_sess.execute.side_effect = RuntimeError("err")
        result = table_date_range(mock_sess, "x", "ts")
        assert isinstance(result, tuple)
        assert len(result) == 2
