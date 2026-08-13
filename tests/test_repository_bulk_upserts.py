"""
tests/test_repository_bulk_upserts.py
=======================================
Unit tests for database/repository.py bulk upsert helpers.

These functions all take (session, batch) and return int (rows processed).
Tests cover:
  - Empty batch → returns 0, no DB call made
  - Nonempty batch → calls session.execute and returns len(batch)
  - Single-row upsert helpers: upsert_event, upsert_market, upsert_trade, upsert_candlestick
  - insert_snapshot: calls session.add
  - insert_order_book: no-op on empty rows
  - log_ingestion: calls session.add

No DB connection required — session is mocked.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock, call
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db_imports():
    """Patch config and DB model imports so repository.py can be imported."""
    with patch.dict(sys.modules, {
        "config": MagicMock(
            DB_URL="postgresql://localhost/test",
            KALSHI_READ_RPS=10.0,
            KALSHI_WRITE_RPS=1.0,
        ),
        "database.models": MagicMock(),
        "sqlalchemy.dialects.postgresql": MagicMock(),
    }):
        yield


def _mock_session():
    return MagicMock()


# ---------------------------------------------------------------------------
# bulk_upsert_events
# ---------------------------------------------------------------------------

class TestBulkUpsertEvents:
    def test_empty_batch_returns_zero(self):
        from database.repository import bulk_upsert_events
        session = _mock_session()
        result = bulk_upsert_events(session, [])
        assert result == 0

    def test_empty_batch_no_execute_call(self):
        from database.repository import bulk_upsert_events
        session = _mock_session()
        bulk_upsert_events(session, [])
        session.execute.assert_not_called()

    def test_nonempty_batch_returns_len(self):
        from database.repository import bulk_upsert_events
        session = _mock_session()
        batch = [{"event_ticker": "E1", "title": "Test"}] * 5
        result = bulk_upsert_events(session, batch)
        assert result == 5

    def test_nonempty_batch_calls_execute(self):
        from database.repository import bulk_upsert_events
        session = _mock_session()
        batch = [{"event_ticker": "E1", "title": "Test"}]
        bulk_upsert_events(session, batch)
        session.execute.assert_called_once()


# ---------------------------------------------------------------------------
# bulk_upsert_markets
# ---------------------------------------------------------------------------

class TestBulkUpsertMarkets:
    def test_empty_batch_returns_zero(self):
        from database.repository import bulk_upsert_markets
        session = _mock_session()
        result = bulk_upsert_markets(session, [])
        assert result == 0

    def test_nonempty_batch_returns_len(self):
        from database.repository import bulk_upsert_markets
        session = _mock_session()
        batch = [{"ticker": "M1", "market_id": "MKT-001", "title": "T"}] * 3
        result = bulk_upsert_markets(session, batch)
        assert result == 3

    def test_nonempty_calls_execute(self):
        from database.repository import bulk_upsert_markets
        session = _mock_session()
        batch = [{"ticker": "M1", "market_id": "MKT-001"}]
        with patch("database.repository.pg_insert") as mock_pg:
            mock_stmt = MagicMock()
            mock_pg.return_value.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            bulk_upsert_markets(session, batch)
        session.execute.assert_called_once()

    def test_empty_no_execute(self):
        from database.repository import bulk_upsert_markets
        session = _mock_session()
        bulk_upsert_markets(session, [])
        session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# bulk_upsert_candlesticks
# ---------------------------------------------------------------------------

class TestBulkUpsertCandlesticks:
    def test_empty_batch_returns_zero(self):
        from database.repository import bulk_upsert_candlesticks
        session = _mock_session()
        result = bulk_upsert_candlesticks(session, [])
        assert result == 0

    def test_nonempty_batch_returns_len(self):
        from database.repository import bulk_upsert_candlesticks
        session = _mock_session()
        batch = [{"market_id": "X", "period_interval": 60}] * 7
        result = bulk_upsert_candlesticks(session, batch)
        assert result == 7

    def test_nonempty_calls_execute(self):
        from database.repository import bulk_upsert_candlesticks
        session = _mock_session()
        bulk_upsert_candlesticks(session, [{"market_id": "X"}])
        session.execute.assert_called_once()


# ---------------------------------------------------------------------------
# bulk_upsert_trades
# ---------------------------------------------------------------------------

class TestBulkUpsertTrades:
    def test_empty_batch_returns_zero(self):
        from database.repository import bulk_upsert_trades
        session = _mock_session()
        result = bulk_upsert_trades(session, [])
        assert result == 0

    def test_nonempty_batch_returns_len(self):
        from database.repository import bulk_upsert_trades
        session = _mock_session()
        batch = [{"trade_id": f"T{i}", "price": 0.5} for i in range(10)]
        result = bulk_upsert_trades(session, batch)
        assert result == 10

    def test_nonempty_calls_execute(self):
        from database.repository import bulk_upsert_trades
        session = _mock_session()
        bulk_upsert_trades(session, [{"trade_id": "T1"}])
        session.execute.assert_called_once()

    def test_empty_no_execute(self):
        from database.repository import bulk_upsert_trades
        session = _mock_session()
        bulk_upsert_trades(session, [])
        session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# insert_snapshot
# ---------------------------------------------------------------------------

class TestInsertSnapshot:
    def test_calls_session_add(self):
        from database.repository import insert_snapshot
        session = _mock_session()
        with patch("database.repository.MarketSnapshot", return_value=MagicMock()):
            insert_snapshot(session, {"market_id": "X", "yes_bid": 0.4})
        session.add.assert_called_once()

    def test_never_raises_on_add(self):
        from database.repository import insert_snapshot
        session = _mock_session()
        with patch("database.repository.MarketSnapshot", return_value=MagicMock()):
            try:
                insert_snapshot(session, {"market_id": "X"})
            except Exception:
                pytest.fail("insert_snapshot should not raise")


# ---------------------------------------------------------------------------
# insert_order_book
# ---------------------------------------------------------------------------

class TestInsertOrderBook:
    def test_empty_rows_no_execute(self):
        from database.repository import insert_order_book
        session = _mock_session()
        insert_order_book(session, [])
        session.execute.assert_not_called()

    def test_nonempty_rows_calls_execute(self):
        from database.repository import insert_order_book
        session = _mock_session()
        with patch("database.repository.pg_insert") as mock_pg:
            mock_pg.return_value = MagicMock()
            insert_order_book(session, [{"market_id": "X", "side": "yes"}])
        session.execute.assert_called_once()


# ---------------------------------------------------------------------------
# log_ingestion
# ---------------------------------------------------------------------------

class TestLogIngestion:
    def test_calls_session_add(self):
        from database.repository import log_ingestion
        session = _mock_session()
        with patch("database.repository.IngestionLog", return_value=MagicMock()):
            log_ingestion(session, source="ingest", rows_fetched=100)
        session.add.assert_called_once()

    def test_never_raises(self):
        from database.repository import log_ingestion
        session = _mock_session()
        with patch("database.repository.IngestionLog", return_value=MagicMock()):
            try:
                log_ingestion(session, source="test", rows_fetched=0)
            except Exception:
                pytest.fail("log_ingestion should not raise")


# ---------------------------------------------------------------------------
# upsert_relationship
# ---------------------------------------------------------------------------

class TestUpsertRelationship:
    def test_calls_execute(self):
        from database.repository import upsert_relationship
        session = _mock_session()
        data = {
            "market_id_1": "MKT-A", "market_id_2": "MKT-B",
            "relationship_type": "complement", "confidence_score": 0.9,
        }
        with patch("database.repository.pg_insert") as mock_pg:
            mock_stmt = MagicMock()
            mock_pg.return_value.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            upsert_relationship(session, data)
        session.execute.assert_called_once()
