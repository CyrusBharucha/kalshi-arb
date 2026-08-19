"""
tests/test_live_scanner_helpers.py
=====================================
Unit tests for arbitrage/live_scanner helpers:
  - _load_relationships(): empty tickers → empty dict; non-empty → calls session_scope
  - _load_candidate_tickers(): calls session_scope, returns list of strings
  - _build_opp(): returns None when prices missing; returns dict with expected keys

All DB calls mocked.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
        "feeds.kalshi_client": MagicMock(),
    }):
        yield


def _session_ctx():
    mock_ss = MagicMock()
    mock_sess = MagicMock()
    mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
    mock_ss.return_value.__exit__ = MagicMock(return_value=False)
    return mock_ss, mock_sess


def _mock_row(**kwargs):
    row = MagicMock()
    row._mapping = kwargs
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


# ---------------------------------------------------------------------------
# _load_relationships
# ---------------------------------------------------------------------------

class TestLoadRelationships:
    def test_empty_tickers_returns_empty_dict(self):
        from engine.live_scanner import _load_relationships
        result = _load_relationships([])
        assert result == {}

    def test_empty_tickers_no_session_scope(self):
        from engine.live_scanner import _load_relationships
        with patch("engine.live_scanner.session_scope") as mock_ss:
            _load_relationships([])
        mock_ss.assert_not_called()

    def test_calls_session_scope_with_nonempty_tickers(self):
        from engine.live_scanner import _load_relationships
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("engine.live_scanner.session_scope", mock_ss):
            _load_relationships(["TICK-A"])
        mock_ss.assert_called_once()

    def test_returns_dict(self):
        from engine.live_scanner import _load_relationships
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("engine.live_scanner.session_scope", mock_ss):
            result = _load_relationships(["TICK-A"])
        assert isinstance(result, dict)

    def test_empty_rows_returns_empty_dict(self):
        from engine.live_scanner import _load_relationships
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("engine.live_scanner.session_scope", mock_ss):
            result = _load_relationships(["TICK-A"])
        assert result == {}

    def test_rows_grouped_by_event_ticker(self):
        from engine.live_scanner import _load_relationships
        mock_ss, mock_sess = _session_ctx()
        rows = [
            _mock_row(market_id_1="M1", market_id_2="M2",
                      relationship_type="mutually_exclusive",
                      logical_constraint="sum<=1", implied_inequality="<=",
                      confidence=0.9, event_ticker="EVT-A"),
            _mock_row(market_id_1="M1", market_id_2="M3",
                      relationship_type="mutually_exclusive",
                      logical_constraint="sum<=1", implied_inequality="<=",
                      confidence=0.8, event_ticker="EVT-A"),
        ]
        mock_sess.execute.return_value.fetchall.return_value = rows
        with patch("engine.live_scanner.session_scope", mock_ss):
            result = _load_relationships(["M1"])
        assert "EVT-A" in result
        assert len(result["EVT-A"]) == 2

    def test_different_events_separate_keys(self):
        from engine.live_scanner import _load_relationships
        mock_ss, mock_sess = _session_ctx()
        rows = [
            _mock_row(market_id_1="M1", market_id_2="M2",
                      relationship_type="mutually_exclusive",
                      logical_constraint="sum<=1", implied_inequality="<=",
                      confidence=0.9, event_ticker="EVT-A"),
            _mock_row(market_id_1="M3", market_id_2="M4",
                      relationship_type="mutually_exclusive",
                      logical_constraint="sum<=1", implied_inequality="<=",
                      confidence=0.8, event_ticker="EVT-B"),
        ]
        mock_sess.execute.return_value.fetchall.return_value = rows
        with patch("engine.live_scanner.session_scope", mock_ss):
            result = _load_relationships(["M1", "M3"])
        assert "EVT-A" in result
        assert "EVT-B" in result


# ---------------------------------------------------------------------------
# _load_candidate_tickers
# ---------------------------------------------------------------------------

class TestLoadCandidateTickers:
    def test_returns_list(self):
        from engine.live_scanner import _load_candidate_tickers
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("engine.live_scanner.session_scope", mock_ss):
            result = _load_candidate_tickers()
        assert isinstance(result, list)

    def test_empty_returns_empty_list(self):
        from engine.live_scanner import _load_candidate_tickers
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("engine.live_scanner.session_scope", mock_ss):
            result = _load_candidate_tickers()
        assert result == []

    def test_calls_session_scope(self):
        from engine.live_scanner import _load_candidate_tickers
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("engine.live_scanner.session_scope", mock_ss):
            _load_candidate_tickers()
        mock_ss.assert_called_once()

    def test_rows_returned_as_strings(self):
        from engine.live_scanner import _load_candidate_tickers
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = [
            ("TICK-A",), ("TICK-B",), ("TICK-C",)
        ]
        with patch("engine.live_scanner.session_scope", mock_ss):
            result = _load_candidate_tickers()
        assert result == ["TICK-A", "TICK-B", "TICK-C"]

    def test_all_items_are_strings(self):
        from engine.live_scanner import _load_candidate_tickers
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = [("T-1",), ("T-2",)]
        with patch("engine.live_scanner.session_scope", mock_ss):
            result = _load_candidate_tickers()
        for item in result:
            assert isinstance(item, str)


# ---------------------------------------------------------------------------
# _build_opp
# ---------------------------------------------------------------------------

class TestBuildOpp:
    def _rel(self):
        return {
            "market_id_1": "M1",
            "market_id_2": "M2",
            "relationship_type": "mutually_exclusive",
            "logical_constraint": "sum<=1",
            "confidence": 0.9,
        }

    def _prices(self, bid1=0.6, ask1=0.65, bid2=0.5, ask2=0.55):
        return {
            "M1": {"yes_bid": bid1, "yes_ask": ask1},
            "M2": {"yes_bid": bid2, "yes_ask": ask2},
        }

    def _violation(self, mag=0.1):
        return {"type": "overpriced", "magnitude": mag}

    def test_returns_none_when_missing_prices(self):
        from engine.live_scanner import _build_opp
        result = _build_opp(self._rel(), {}, self._violation())
        assert result is None

    def test_returns_none_when_magnitude_zero(self):
        from engine.live_scanner import _build_opp
        result = _build_opp(self._rel(), self._prices(), self._violation(mag=0))
        assert result is None

    def test_returns_none_when_magnitude_negative(self):
        from engine.live_scanner import _build_opp
        result = _build_opp(self._rel(), self._prices(), self._violation(mag=-0.1))
        assert result is None

    def test_returns_dict_on_valid_input(self):
        from engine.live_scanner import _build_opp
        result = _build_opp(self._rel(), self._prices(), self._violation(0.1))
        assert isinstance(result, dict)

    def test_market_ids_in_result(self):
        from engine.live_scanner import _build_opp
        result = _build_opp(self._rel(), self._prices(), self._violation(0.1))
        assert result is not None
        assert result.get("market_id_1") == "M1" or "M1" in str(result)

    def test_returns_none_when_bid_is_none(self):
        from engine.live_scanner import _build_opp
        prices = {"M1": {"yes_bid": None, "yes_ask": 0.65},
                  "M2": {"yes_bid": 0.5,  "yes_ask": 0.55}}
        result = _build_opp(self._rel(), prices, self._violation(0.1))
        assert result is None

    def test_returns_none_when_ask_is_none(self):
        from engine.live_scanner import _build_opp
        prices = {"M1": {"yes_bid": 0.6, "yes_ask": None},
                  "M2": {"yes_bid": 0.5, "yes_ask": 0.55}}
        result = _build_opp(self._rel(), prices, self._violation(0.1))
        assert result is None
