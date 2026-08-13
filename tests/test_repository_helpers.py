"""
tests/test_repository_helpers.py
==================================
Tests for the pure-Python logic paths in database/repository.py that do NOT
require a live PostgreSQL connection:
  - bulk_upsert_* return 0 on empty batch (short-circuit guard)
  - session_scope rolls back on exception
  - log_ingestion builds the correct kwarg signature

All DB connections are monkeypatched out.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Fixture: patch SQLAlchemy engine creation and config so the module loads
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_db_imports():
    """Prevent repository.py from actually connecting to PostgreSQL."""
    engine_mock = MagicMock()
    session_mock = MagicMock()
    session_mock.return_value = session_mock  # factory() returns itself

    config_mock = MagicMock()
    config_mock.DB_URL = "postgresql://test:test@localhost/test"

    models_mock = MagicMock()
    models_mock.Base = MagicMock()

    mods = {
        "config": config_mock,
        "database.models": models_mock,
        "sqlalchemy.dialects.postgresql": MagicMock(),
    }
    originals = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)

    with patch("sqlalchemy.create_engine", return_value=engine_mock), \
         patch("sqlalchemy.orm.sessionmaker", return_value=session_mock):
        yield

    for k, v in originals.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


# ---------------------------------------------------------------------------
# Tests: bulk_upsert_* empty-batch guard
# ---------------------------------------------------------------------------

class TestBulkUpsertEmptyGuard:
    """All bulk_upsert_* functions return 0 immediately for empty batches."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import importlib
        import database.repository as r
        # Reset module-level singletons to avoid cross-test state leakage
        r._engine = None
        r._Session = None
        self.r = r
        self.session = MagicMock()

    def test_bulk_upsert_events_empty_returns_zero(self):
        result = self.r.bulk_upsert_events(self.session, [])
        assert result == 0

    def test_bulk_upsert_events_empty_no_execute(self):
        self.r.bulk_upsert_events(self.session, [])
        self.session.execute.assert_not_called()

    def test_bulk_upsert_markets_empty_returns_zero(self):
        result = self.r.bulk_upsert_markets(self.session, [])
        assert result == 0

    def test_bulk_upsert_markets_empty_no_execute(self):
        self.r.bulk_upsert_markets(self.session, [])
        self.session.execute.assert_not_called()

    def test_bulk_upsert_candlesticks_empty_returns_zero(self):
        result = self.r.bulk_upsert_candlesticks(self.session, [])
        assert result == 0

    def test_bulk_upsert_candlesticks_empty_no_execute(self):
        self.r.bulk_upsert_candlesticks(self.session, [])
        self.session.execute.assert_not_called()

    def test_bulk_upsert_trades_empty_returns_zero(self):
        result = self.r.bulk_upsert_trades(self.session, [])
        assert result == 0

    def test_bulk_upsert_trades_empty_no_execute(self):
        self.r.bulk_upsert_trades(self.session, [])
        self.session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: session_scope rollback on exception
# ---------------------------------------------------------------------------

class TestSessionScope:
    """session_scope rolls back when an exception is raised inside the block."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import database.repository as r
        r._engine = None
        r._Session = None
        self.r = r

    def test_session_scope_commits_on_success(self):
        mock_session = MagicMock()
        mock_factory = MagicMock(return_value=mock_session)

        with patch.object(self.r, "get_session_factory", return_value=mock_factory):
            with self.r.session_scope() as session:
                pass  # no exception

        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

    def test_session_scope_rollback_on_exception(self):
        mock_session = MagicMock()
        mock_factory = MagicMock(return_value=mock_session)

        with patch.object(self.r, "get_session_factory", return_value=mock_factory):
            with pytest.raises(RuntimeError):
                with self.r.session_scope():
                    raise RuntimeError("boom")

        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()

    def test_session_scope_yields_session(self):
        mock_session = MagicMock()
        mock_factory = MagicMock(return_value=mock_session)
        received = []

        with patch.object(self.r, "get_session_factory", return_value=mock_factory):
            with self.r.session_scope() as session:
                received.append(session)

        assert received[0] is mock_session

    def test_session_closed_even_after_exception(self):
        mock_session = MagicMock()
        mock_factory = MagicMock(return_value=mock_session)

        with patch.object(self.r, "get_session_factory", return_value=mock_factory):
            try:
                with self.r.session_scope():
                    raise ValueError("test")
            except ValueError:
                pass

        mock_session.close.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: log_ingestion kwargs passed to session
# ---------------------------------------------------------------------------

class TestLogIngestion:
    """log_ingestion writes a record with the correct fields."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import database.repository as r
        r._engine = None
        r._Session = None
        self.r = r
        self.session = MagicMock()

    def test_log_ingestion_calls_add(self):
        self.r.log_ingestion(
            self.session,
            job_type="market_sync",
            rows_inserted=100,
            status="success",
        )
        self.session.add.assert_called_once()

    def test_log_ingestion_does_not_raise(self):
        # Should not raise even with minimal kwargs
        self.r.log_ingestion(
            self.session,
            job_type="test",
            rows_inserted=0,
            status="success",
        )
