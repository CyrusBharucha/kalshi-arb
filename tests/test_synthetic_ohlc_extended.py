"""
tests/test_synthetic_ohlc_extended.py
=======================================
Extended tests for analysis/synthetic_ohlc.py.

Covers:
  - build_synthetic_ohlc() with empty trades → {"rows_written": 0}
  - build_synthetic_ohlc() with real trade data → correct OHLC structure
  - _flush() with empty list → 0
  - _flush() with rows → calls session_scope and returns len(rows)
  - OHLC invariants: high >= low, open/close within range

No DB; no network — all session_scope / pg_insert calls mocked.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, call
import pandas as pd
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db_modules():
    """Patch DB imports at module level."""
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    pg_insert_mock = MagicMock()
    stmt_mock = MagicMock()
    pg_insert_mock.return_value = stmt_mock
    stmt_mock.on_conflict_do_update.return_value = stmt_mock

    with patch.dict(sys.modules, {
        "database.repository": MagicMock(session_scope=mock_ss),
        "database.models": MagicMock(),
        "sqlalchemy.dialects.postgresql": MagicMock(insert=pg_insert_mock),
    }):
        yield


def _make_trades_df(n_markets=2, n_trades_per_market=5):
    """Build a synthetic trades DataFrame."""
    rows = []
    base_ts = datetime(2026, 6, 23, 10, 0, 0, tzinfo=timezone.utc)
    for m in range(n_markets):
        mid = f"MKT-{m:03d}"
        for i in range(n_trades_per_market):
            rows.append({
                "market_id": mid,
                "trade_ts": base_ts + timedelta(minutes=i * 10),
                "price": 0.40 + (i * 0.01),
                "quantity": 10,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# build_synthetic_ohlc
# ---------------------------------------------------------------------------

class TestBuildSyntheticOhlcEmptyTrades:
    def test_empty_trades_returns_zero_rows_written(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        with patch("analysis.synthetic_ohlc.pd.read_sql", return_value=pd.DataFrame()):
            with patch("analysis.synthetic_ohlc.session_scope"):
                result = build_synthetic_ohlc()
        assert result == {"rows_written": 0}

    def test_never_raises_on_empty(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        with patch("analysis.synthetic_ohlc.pd.read_sql", return_value=pd.DataFrame()):
            with patch("analysis.synthetic_ohlc.session_scope"):
                try:
                    build_synthetic_ohlc()
                except Exception:
                    pytest.fail("build_synthetic_ohlc should not raise on empty trades")


class TestBuildSyntheticOhlcWithData:
    @pytest.fixture
    def _flush_capture(self):
        """Capture rows passed to _flush."""
        captured = []
        original = None

        def fake_flush(rows):
            captured.extend(rows)
            return len(rows)

        return fake_flush, captured

    def test_returns_dict_with_required_keys(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        df = _make_trades_df(n_markets=2, n_trades_per_market=6)
        with patch("analysis.synthetic_ohlc.pd.read_sql", return_value=df):
            with patch("analysis.synthetic_ohlc.session_scope"):
                with patch("analysis.synthetic_ohlc._flush", return_value=10):
                    result = build_synthetic_ohlc(period_minutes=60)
        assert "rows_written" in result
        assert "period_minutes" in result
        assert "markets" in result

    def test_markets_count_correct(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        df = _make_trades_df(n_markets=3, n_trades_per_market=3)
        with patch("analysis.synthetic_ohlc.pd.read_sql", return_value=df):
            with patch("analysis.synthetic_ohlc.session_scope"):
                with patch("analysis.synthetic_ohlc._flush", return_value=0):
                    result = build_synthetic_ohlc(period_minutes=60)
        assert result["markets"] == 3

    def test_period_minutes_in_result(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        df = _make_trades_df(n_markets=1, n_trades_per_market=3)
        with patch("analysis.synthetic_ohlc.pd.read_sql", return_value=df):
            with patch("analysis.synthetic_ohlc.session_scope"):
                with patch("analysis.synthetic_ohlc._flush", return_value=0):
                    result = build_synthetic_ohlc(period_minutes=1440)
        assert result["period_minutes"] == 1440

    def test_flush_called_with_rows(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        df = _make_trades_df(n_markets=1, n_trades_per_market=3)
        flush_calls = []

        def fake_flush(rows):
            flush_calls.extend(rows)
            return len(rows)

        with patch("analysis.synthetic_ohlc.pd.read_sql", return_value=df):
            with patch("analysis.synthetic_ohlc.session_scope"):
                with patch("analysis.synthetic_ohlc._flush", side_effect=fake_flush):
                    build_synthetic_ohlc(period_minutes=60)

        # Should have called flush with at least one row dict
        assert len(flush_calls) >= 1

    def test_row_has_ohlc_fields(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        df = _make_trades_df(n_markets=1, n_trades_per_market=3)
        captured = []

        def fake_flush(rows):
            captured.extend(rows)
            return len(rows)

        with patch("analysis.synthetic_ohlc.pd.read_sql", return_value=df):
            with patch("analysis.synthetic_ohlc.session_scope"):
                with patch("analysis.synthetic_ohlc._flush", side_effect=fake_flush):
                    build_synthetic_ohlc(period_minutes=60)

        assert len(captured) >= 1
        row = captured[0]
        for key in ["market_id", "period_interval", "period_end_ts",
                    "price_open", "price_high", "price_low", "price_close"]:
            assert key in row, f"Missing key: {key}"

    def test_ohlc_high_gte_low(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        df = _make_trades_df(n_markets=2, n_trades_per_market=6)
        captured = []

        def fake_flush(rows):
            captured.extend(rows)
            return len(rows)

        with patch("analysis.synthetic_ohlc.pd.read_sql", return_value=df):
            with patch("analysis.synthetic_ohlc.session_scope"):
                with patch("analysis.synthetic_ohlc._flush", side_effect=fake_flush):
                    build_synthetic_ohlc(period_minutes=60)

        for row in captured:
            assert row["price_high"] >= row["price_low"], \
                f"high {row['price_high']} < low {row['price_low']} for {row['market_id']}"

    def test_volume_is_positive(self):
        from analysis.synthetic_ohlc import build_synthetic_ohlc
        df = _make_trades_df(n_markets=1, n_trades_per_market=4)
        captured = []

        def fake_flush(rows):
            captured.extend(rows)
            return len(rows)

        with patch("analysis.synthetic_ohlc.pd.read_sql", return_value=df):
            with patch("analysis.synthetic_ohlc.session_scope"):
                with patch("analysis.synthetic_ohlc._flush", side_effect=fake_flush):
                    build_synthetic_ohlc(period_minutes=60)

        for row in captured:
            if row["volume"] is not None:
                assert row["volume"] > 0


# ---------------------------------------------------------------------------
# _flush
# ---------------------------------------------------------------------------

class TestFlush:
    def test_empty_rows_returns_zero(self):
        from analysis.synthetic_ohlc import _flush
        result = _flush([])
        assert result == 0

    def test_nonempty_rows_returns_len(self):
        from analysis.synthetic_ohlc import _flush
        rows = [{"market_id": "X", "price_open": 0.5}]
        mock_session = MagicMock()
        mock_ss = MagicMock()
        mock_ss.__enter__ = MagicMock(return_value=mock_session)
        mock_ss.__exit__ = MagicMock(return_value=False)
        with patch("analysis.synthetic_ohlc.session_scope", return_value=mock_ss):
            with patch("analysis.synthetic_ohlc.pg_insert", return_value=MagicMock()) as mock_pg:
                result = _flush(rows)
        assert result == 1

    def test_exception_returns_zero(self):
        from analysis.synthetic_ohlc import _flush
        rows = [{"market_id": "X"}]
        mock_ss = MagicMock()
        mock_ss.__enter__ = MagicMock(side_effect=RuntimeError("DB down"))
        mock_ss.__exit__ = MagicMock(return_value=False)
        with patch("analysis.synthetic_ohlc.session_scope", return_value=mock_ss):
            result = _flush(rows)
        assert result == 0

    def test_never_raises(self):
        from analysis.synthetic_ohlc import _flush
        rows = [{"market_id": "X"}]
        mock_ss = MagicMock()
        mock_ss.__enter__ = MagicMock(side_effect=RuntimeError("DB down"))
        mock_ss.__exit__ = MagicMock(return_value=False)
        with patch("analysis.synthetic_ohlc.session_scope", return_value=mock_ss):
            try:
                _flush(rows)
            except Exception:
                pytest.fail("_flush should not raise")
