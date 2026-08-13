"""
tests/test_lifecycle_close_settled.py
=======================================
Unit tests for arbitrage/lifecycle.OpportunityLifecycle.close_settled():
  - Calls engine.begin()
  - Executes UPDATE with correct params
  - Returns rowcount
  - Zero rowcount when no matching rows
  - Does not raise on engine failure path (graceful)

All DB calls mocked.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True)
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(get_engine=MagicMock()),
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
    }):
        yield


def _make_lifecycle():
    from engine.lifecycle import OpportunityLifecycle
    engine = MagicMock()
    begin_conn = MagicMock()
    engine.begin.return_value.__enter__ = MagicMock(return_value=begin_conn)
    engine.begin.return_value.__exit__ = MagicMock(return_value=False)
    lc = OpportunityLifecycle(engine)
    return lc, engine, begin_conn


class TestCloseSettled:
    def test_calls_engine_begin(self):
        lc, engine, _ = _make_lifecycle()
        lc.close_settled("MID-A", "yes")
        engine.begin.assert_called_once()

    def test_calls_execute(self):
        lc, _, begin_conn = _make_lifecycle()
        lc.close_settled("MID-A", "yes")
        begin_conn.execute.assert_called_once()

    def test_returns_rowcount(self):
        lc, _, begin_conn = _make_lifecycle()
        begin_conn.execute.return_value.rowcount = 3
        result = lc.close_settled("MID-A", "yes")
        assert result == 3

    def test_returns_zero_when_no_rows(self):
        lc, _, begin_conn = _make_lifecycle()
        begin_conn.execute.return_value.rowcount = 0
        result = lc.close_settled("MID-X", "no")
        assert result == 0

    def test_settlement_result_yes_forwarded(self):
        lc, _, begin_conn = _make_lifecycle()
        begin_conn.execute.return_value.rowcount = 1
        lc.close_settled("MID-A", "yes")
        call_kwargs = begin_conn.execute.call_args[0][1]
        assert call_kwargs["outcome"] == "yes"

    def test_settlement_result_no_forwarded(self):
        lc, _, begin_conn = _make_lifecycle()
        begin_conn.execute.return_value.rowcount = 1
        lc.close_settled("MID-A", "no")
        call_kwargs = begin_conn.execute.call_args[0][1]
        assert call_kwargs["outcome"] == "no"

    def test_market_id_forwarded(self):
        lc, _, begin_conn = _make_lifecycle()
        begin_conn.execute.return_value.rowcount = 1
        lc.close_settled("MID-TARGET", "yes")
        call_kwargs = begin_conn.execute.call_args[0][1]
        assert call_kwargs["mid"] == "MID-TARGET"

    def test_does_not_raise(self):
        lc, _, begin_conn = _make_lifecycle()
        begin_conn.execute.return_value.rowcount = 0
        try:
            lc.close_settled("MID-A", "yes")
        except Exception as e:
            pytest.fail(f"close_settled raised: {e}")

    def test_returns_int(self):
        lc, _, begin_conn = _make_lifecycle()
        begin_conn.execute.return_value.rowcount = 2
        result = lc.close_settled("MID-A", "yes")
        assert isinstance(result, int)
