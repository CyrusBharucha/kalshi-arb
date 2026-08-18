"""
tests/test_scanner_helpers.py
==============================
Tests for pure helper functions in arbitrage/scanner.py:
  - _safe_float
  - ArbitrageScanner._group_by_event

No database, no network, no Kalshi API.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Patch DB and Kalshi imports before importing scanner
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_deps():
    repo_mock = MagicMock()
    repo_mock.get_engine.side_effect = RuntimeError("No DB")
    repo_mock.session_scope = MagicMock()
    repo_mock.insert_opportunity = MagicMock()
    repo_mock.get_all_open_markets = MagicMock(return_value=[])
    repo_mock.get_event_market_matrix = MagicMock(return_value={})
    repo_mock.insert_snapshot = MagicMock()

    kalshi_mock = MagicMock()
    lifecycle_mock = MagicMock()
    lifecycle_mock.OpportunityLifecycle = MagicMock()

    with patch.dict(__import__("sys").modules, {
        "database.repository": repo_mock,
        "database": MagicMock(),
        "feeds.kalshi_client": kalshi_mock,
        "engine.lifecycle": lifecycle_mock,
    }):
        yield


# ---------------------------------------------------------------------------
# _safe_float
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def _sf(self, value):
        from engine.scanner import _safe_float
        return _safe_float(value)

    def test_none_returns_none(self):
        assert self._sf(None) is None

    def test_int_converts(self):
        assert self._sf(42) == 42.0

    def test_float_passthrough(self):
        assert abs(self._sf(0.55) - 0.55) < 1e-9

    def test_string_float_converts(self):
        assert abs(self._sf("0.45") - 0.45) < 1e-9

    def test_string_int_converts(self):
        assert self._sf("10") == 10.0

    def test_empty_string_returns_none(self):
        assert self._sf("") is None

    def test_non_numeric_string_returns_none(self):
        assert self._sf("abc") is None

    def test_zero_converts(self):
        assert self._sf(0) == 0.0

    def test_negative_converts(self):
        assert self._sf(-1.5) == -1.5

    def test_list_returns_none(self):
        assert self._sf([1, 2]) is None

    def test_dict_returns_none(self):
        assert self._sf({"v": 1}) is None

    def test_true_converts_to_one(self):
        assert self._sf(True) == 1.0

    def test_false_converts_to_zero(self):
        assert self._sf(False) == 0.0


# ---------------------------------------------------------------------------
# _group_by_event
# ---------------------------------------------------------------------------

def _mkt(event_ticker, market_id="M1"):
    return {"market_id": market_id, "event_ticker": event_ticker,
            "ticker": market_id, "yes_bid": 0.44, "yes_ask": 0.46}


class TestGroupByEvent:
    @pytest.fixture
    def scanner(self):
        from engine.scanner import ArbitrageScanner
        scanner = ArbitrageScanner.__new__(ArbitrageScanner)
        scanner.client = MagicMock()
        scanner._lifecycle = MagicMock()
        return scanner

    def test_single_market_single_event(self, scanner):
        markets = [_mkt("EV-001", "M1")]
        result = scanner._group_by_event(markets)
        assert "EV-001" in result
        assert len(result["EV-001"]) == 1

    def test_two_markets_same_event(self, scanner):
        markets = [_mkt("EV-001", "M1"), _mkt("EV-001", "M2")]
        result = scanner._group_by_event(markets)
        assert len(result["EV-001"]) == 2

    def test_two_markets_different_events(self, scanner):
        markets = [_mkt("EV-001", "M1"), _mkt("EV-002", "M2")]
        result = scanner._group_by_event(markets)
        assert len(result) == 2
        assert "EV-001" in result
        assert "EV-002" in result

    def test_empty_input_returns_empty_dict(self, scanner):
        result = scanner._group_by_event([])
        assert result == {}

    def test_missing_event_ticker_uses_unknown(self, scanner):
        markets = [{"market_id": "M1", "ticker": "M1"}]
        result = scanner._group_by_event(markets)
        assert "unknown" in result

    def test_preserves_market_data(self, scanner):
        mkt = _mkt("EV-001", "M1")
        result = scanner._group_by_event([mkt])
        assert result["EV-001"][0] == mkt

    def test_many_markets_grouped_correctly(self, scanner):
        markets = [_mkt(f"EV-{i}", f"M{i}-{j}") for i in range(5) for j in range(3)]
        result = scanner._group_by_event(markets)
        assert len(result) == 5
        for group in result.values():
            assert len(group) == 3

    def test_returns_dict(self, scanner):
        result = scanner._group_by_event([_mkt("EV-X", "M1")])
        assert isinstance(result, dict)
