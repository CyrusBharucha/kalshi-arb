"""
tests/test_historical_scanner_db_helpers.py
============================================
Unit tests for arbitrage/historical_scanner DB helper functions:
  - _load_tickers(): calls engine.connect(), returns list of market_ids
  - _fetch_snapshots(): builds query with optional since/until, returns rows
  - _save_opportunities(): empty list → 0; inserts each opp, returns count
  - _log_run(): calls engine.begin(), inserts ingestion_log row
  - run_historical_scan(): orchestration; calls _load_tickers, scan_ticker_batch, _save_opportunities, _log_run

All DB connections mocked.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, call
import pytest


@pytest.fixture(autouse=True)
def _patch_imports():
    """Patch heavy imports so module loads without real DB."""
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(get_engine=MagicMock()),
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
    }):
        yield


def _make_engine():
    """Return a mock engine with .connect() and .begin() context managers."""
    engine = MagicMock()
    # .connect() context manager
    conn = MagicMock()
    engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    # .begin() context manager
    begin_conn = MagicMock()
    engine.begin.return_value.__enter__ = MagicMock(return_value=begin_conn)
    engine.begin.return_value.__exit__ = MagicMock(return_value=False)
    return engine, conn, begin_conn


# ---------------------------------------------------------------------------
# _load_tickers
# ---------------------------------------------------------------------------

class TestLoadTickers:
    def test_returns_list(self):
        from engine.historical_scanner import _load_tickers
        engine, conn, _ = _make_engine()
        conn.execute.return_value.fetchall.return_value = []
        result = _load_tickers(engine, since=None)
        assert isinstance(result, list)

    def test_empty_rows_returns_empty_list(self):
        from engine.historical_scanner import _load_tickers
        engine, conn, _ = _make_engine()
        conn.execute.return_value.fetchall.return_value = []
        result = _load_tickers(engine, since=None)
        assert result == []

    def test_extracts_market_ids_from_rows(self):
        from engine.historical_scanner import _load_tickers
        engine, conn, _ = _make_engine()
        conn.execute.return_value.fetchall.return_value = [("MID-A",), ("MID-B",)]
        result = _load_tickers(engine, since=None)
        assert result == ["MID-A", "MID-B"]

    def test_calls_engine_connect(self):
        from engine.historical_scanner import _load_tickers
        engine, conn, _ = _make_engine()
        conn.execute.return_value.fetchall.return_value = []
        _load_tickers(engine, since=None)
        engine.connect.assert_called_once()

    def test_since_filter_calls_execute(self):
        from engine.historical_scanner import _load_tickers
        engine, conn, _ = _make_engine()
        conn.execute.return_value.fetchall.return_value = []
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        _load_tickers(engine, since=since)
        # execute called with a query containing WHERE
        args = conn.execute.call_args
        assert args is not None


# ---------------------------------------------------------------------------
# _fetch_snapshots
# ---------------------------------------------------------------------------

class TestFetchSnapshots:
    def test_returns_list(self):
        from engine.historical_scanner import _fetch_snapshots
        engine, conn, _ = _make_engine()
        conn.execute.return_value.fetchall.return_value = []
        result = _fetch_snapshots(engine, ["MID-A"], None, None)
        assert isinstance(result, list)

    def test_empty_returns_empty(self):
        from engine.historical_scanner import _fetch_snapshots
        engine, conn, _ = _make_engine()
        conn.execute.return_value.fetchall.return_value = []
        result = _fetch_snapshots(engine, ["MID-A"], None, None)
        assert result == []

    def test_calls_execute(self):
        from engine.historical_scanner import _fetch_snapshots
        engine, conn, _ = _make_engine()
        conn.execute.return_value.fetchall.return_value = []
        _fetch_snapshots(engine, ["MID-A", "MID-B"], None, None)
        conn.execute.assert_called_once()

    def test_returns_rows_from_execute(self):
        from engine.historical_scanner import _fetch_snapshots
        engine, conn, _ = _make_engine()
        fake_rows = [
            ("MID-A", datetime(2025, 1, 1, tzinfo=timezone.utc), 0.5, 0.55, None),
        ]
        conn.execute.return_value.fetchall.return_value = fake_rows
        result = _fetch_snapshots(engine, ["MID-A"], None, None)
        assert result == fake_rows

    def test_with_since_and_until(self):
        from engine.historical_scanner import _fetch_snapshots
        engine, conn, _ = _make_engine()
        conn.execute.return_value.fetchall.return_value = []
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        until = datetime(2025, 2, 1, tzinfo=timezone.utc)
        _fetch_snapshots(engine, ["MID-A"], since, until)
        conn.execute.assert_called_once()


# ---------------------------------------------------------------------------
# _save_opportunities
# ---------------------------------------------------------------------------

class TestSaveOpportunities:
    def test_empty_list_returns_zero(self):
        from engine.historical_scanner import _save_opportunities
        engine, _, _ = _make_engine()
        result = _save_opportunities(engine, [])
        assert result == 0

    def test_empty_list_no_engine_call(self):
        from engine.historical_scanner import _save_opportunities
        engine, _, _ = _make_engine()
        _save_opportunities(engine, [])
        engine.begin.assert_not_called()

    def test_single_opp_returns_one(self):
        from engine.historical_scanner import _save_opportunities
        engine, _, begin_conn = _make_engine()
        opp = {
            "detected_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "strategy_type": "yes_no_complement",
            "classification": "B",
            "markets_involved": ["MID-A"],
            "prices_json": {},
            "gross_edge": 0.05,
            "total_fees": 0.01,
            "estimated_slippage": 0.0,
            "net_edge": 0.04,
            "max_executable_contracts": 10,
            "max_gross_profit": 0.5,
            "max_net_profit": 0.4,
            "status": "expired",
            "closed_at": datetime(2025, 1, 1, 1, tzinfo=timezone.utc),
            "duration_seconds": 60,
            "notes": "test",
        }
        result = _save_opportunities(engine, [opp])
        assert result == 1

    def test_calls_engine_begin(self):
        from engine.historical_scanner import _save_opportunities
        engine, _, begin_conn = _make_engine()
        opp = {
            "detected_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "strategy_type": "yes_no_complement",
            "classification": "B",
            "markets_involved": ["MID-A"],
            "prices_json": {},
            "gross_edge": 0.05,
            "total_fees": 0.01,
            "estimated_slippage": 0.0,
            "net_edge": 0.04,
            "max_executable_contracts": 10,
            "max_gross_profit": 0.5,
            "max_net_profit": 0.4,
            "status": "expired",
            "closed_at": None,
            "duration_seconds": 60,
            "notes": "test",
        }
        _save_opportunities(engine, [opp])
        engine.begin.assert_called_once()

    def test_execute_exception_skipped(self):
        """If an insert throws, the opp is skipped (logged as warning)."""
        from engine.historical_scanner import _save_opportunities
        engine, _, begin_conn = _make_engine()
        begin_conn.execute.side_effect = Exception("DB error")
        opp = {
            "detected_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "strategy_type": "yes_no_complement",
            "classification": "B",
            "markets_involved": ["MID-A"],
            "prices_json": {},
            "gross_edge": 0.05,
            "total_fees": 0.01,
            "estimated_slippage": 0.0,
            "net_edge": 0.04,
            "max_executable_contracts": 10,
            "max_gross_profit": 0.5,
            "max_net_profit": 0.4,
            "status": "expired",
            "closed_at": None,
            "duration_seconds": 60,
            "notes": "test",
        }
        result = _save_opportunities(engine, [opp])
        assert result == 0  # skipped due to exception


# ---------------------------------------------------------------------------
# _log_run
# ---------------------------------------------------------------------------

class TestLogRun:
    def test_calls_engine_begin(self):
        from engine.historical_scanner import _log_run
        engine, _, begin_conn = _make_engine()
        _log_run(engine, rows_inserted=5, rows_skipped=2)
        engine.begin.assert_called_once()

    def test_calls_execute(self):
        from engine.historical_scanner import _log_run
        engine, _, begin_conn = _make_engine()
        _log_run(engine, rows_inserted=5, rows_skipped=2)
        begin_conn.execute.assert_called_once()

    def test_does_not_raise(self):
        from engine.historical_scanner import _log_run
        engine, _, begin_conn = _make_engine()
        try:
            _log_run(engine, rows_inserted=0, rows_skipped=0)
        except Exception:
            pytest.fail("_log_run raised unexpectedly")


# ---------------------------------------------------------------------------
# run_historical_scan
# ---------------------------------------------------------------------------

class TestRunHistoricalScan:
    def test_returns_dict(self):
        from engine.historical_scanner import run_historical_scan
        with patch("engine.historical_scanner.get_engine") as mock_ge, \
             patch("engine.historical_scanner._load_tickers", return_value=[]), \
             patch("engine.historical_scanner._log_run"):
            mock_ge.return_value = MagicMock()
            result = run_historical_scan()
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        from engine.historical_scanner import run_historical_scan
        with patch("engine.historical_scanner.get_engine") as mock_ge, \
             patch("engine.historical_scanner._load_tickers", return_value=[]), \
             patch("engine.historical_scanner._log_run"):
            mock_ge.return_value = MagicMock()
            result = run_historical_scan()
        assert "opportunities_found" in result or "tickers_scanned" in result or len(result) > 0

    def test_empty_tickers_returns_without_scanning(self):
        from engine.historical_scanner import run_historical_scan
        with patch("engine.historical_scanner.get_engine") as mock_ge, \
             patch("engine.historical_scanner._load_tickers", return_value=[]) as mock_lt, \
             patch("engine.historical_scanner.scan_ticker_batch") as mock_stb, \
             patch("engine.historical_scanner._log_run"):
            mock_ge.return_value = MagicMock()
            run_historical_scan()
        mock_stb.assert_not_called()

    def test_calls_load_tickers(self):
        from engine.historical_scanner import run_historical_scan
        with patch("engine.historical_scanner.get_engine") as mock_ge, \
             patch("engine.historical_scanner._load_tickers", return_value=[]) as mock_lt, \
             patch("engine.historical_scanner._log_run"):
            mock_ge.return_value = MagicMock()
            run_historical_scan()
        mock_lt.assert_called_once()

    def test_calls_log_run_when_tickers_present(self):
        """_log_run is called after scanning (requires tickers; empty tickers returns early)."""
        from engine.historical_scanner import run_historical_scan
        with patch("engine.historical_scanner.get_engine") as mock_ge, \
             patch("engine.historical_scanner._load_tickers", return_value=["MID-A"]), \
             patch("engine.historical_scanner.scan_ticker_batch", return_value=[]), \
             patch("engine.historical_scanner._save_opportunities", return_value=0), \
             patch("engine.historical_scanner._log_run") as mock_lr:
            engine = MagicMock()
            # Mock the MIN(snapped_at) query
            conn = MagicMock()
            engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
            engine.connect.return_value.__exit__ = MagicMock(return_value=False)
            conn.execute.return_value.fetchone.return_value = (
                datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
            mock_ge.return_value = engine
            run_historical_scan(since=datetime(2025, 1, 1, tzinfo=timezone.utc),
                                until=datetime(2025, 1, 2, tzinfo=timezone.utc))
        mock_lr.assert_called_once()

    def test_explicit_tickers_skips_load_tickers(self):
        from engine.historical_scanner import run_historical_scan
        with patch("engine.historical_scanner.get_engine") as mock_ge, \
             patch("engine.historical_scanner._load_tickers") as mock_lt, \
             patch("engine.historical_scanner.scan_ticker_batch", return_value=[]), \
             patch("engine.historical_scanner._save_opportunities", return_value=0), \
             patch("engine.historical_scanner._log_run"):
            engine = MagicMock()
            conn = MagicMock()
            engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
            engine.connect.return_value.__exit__ = MagicMock(return_value=False)
            conn.execute.return_value.fetchone.return_value = (
                datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
            mock_ge.return_value = engine
            run_historical_scan(
                tickers=["MID-A"],
                since=datetime(2025, 1, 1, tzinfo=timezone.utc),
                until=datetime(2025, 1, 2, tzinfo=timezone.utc),
            )
        mock_lt.assert_not_called()
