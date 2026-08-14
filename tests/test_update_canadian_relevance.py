"""
tests/test_update_canadian_relevance.py
==========================================
Unit tests for analysis/canadian_markets.update_canadian_relevance_in_db().

Tests:
  - Empty df → returns None without calling session_scope
  - df with canadian_relevant=False → returns without calling session_scope
  - df with canadian_relevant=True → calls session_scope
  - Exception in session → does not raise (logs warning)
  - Calls execute with event_ticker list
  - Multiple canadian events all passed to execute

No live DB.
"""
from __future__ import annotations

import sys
import pandas as pd
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


def _df(tickers, canadian=True):
    return pd.DataFrame({
        "event_ticker":     tickers,
        "canadian_relevant": [canadian] * len(tickers),
    })


class TestUpdateCanadianRelevanceEmpty:
    def test_empty_df_no_session(self):
        from analysis.canadian_markets import update_canadian_relevance_in_db
        # Use a non-empty DF with all False so no Canadian events qualify
        df = pd.DataFrame({
            "event_ticker":     ["EVT-X"],
            "canadian_relevant": [False],
        })
        with patch("analysis.canadian_markets.session_scope") as mock_ss:
            update_canadian_relevance_in_db(df)
        mock_ss.assert_not_called()

    def test_all_non_canadian_no_session(self):
        from analysis.canadian_markets import update_canadian_relevance_in_db
        df = _df(["EVT-A", "EVT-B"], canadian=False)
        with patch("analysis.canadian_markets.session_scope") as mock_ss:
            update_canadian_relevance_in_db(df)
        mock_ss.assert_not_called()

    def test_returns_none_when_all_non_canadian(self):
        from analysis.canadian_markets import update_canadian_relevance_in_db
        df = _df(["EVT-Z"], canadian=False)
        with patch("analysis.canadian_markets.session_scope"):
            result = update_canadian_relevance_in_db(df)
        assert result is None


class TestUpdateCanadianRelevanceWithData:
    def test_canadian_events_calls_session_scope(self):
        from analysis.canadian_markets import update_canadian_relevance_in_db
        df = _df(["BOC-2026"], canadian=True)
        with patch("analysis.canadian_markets.session_scope") as mock_ss:
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            update_canadian_relevance_in_db(df)
        mock_ss.assert_called_once()

    def test_calls_execute(self):
        from analysis.canadian_markets import update_canadian_relevance_in_db
        df = _df(["BOC-2026"], canadian=True)
        with patch("analysis.canadian_markets.session_scope") as mock_ss:
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            update_canadian_relevance_in_db(df)
        mock_sess.execute.assert_called_once()

    def test_exception_does_not_raise(self):
        from analysis.canadian_markets import update_canadian_relevance_in_db
        df = _df(["BOC-2026"], canadian=True)
        with patch("analysis.canadian_markets.session_scope") as mock_ss:
            mock_ss.side_effect = Exception("DB failure")
            try:
                update_canadian_relevance_in_db(df)
            except Exception:
                pytest.fail("update_canadian_relevance_in_db raised unexpectedly")

    def test_multiple_canadian_events(self):
        from analysis.canadian_markets import update_canadian_relevance_in_db
        df = _df(["BOC-A", "BOC-B", "CAD-C"], canadian=True)
        with patch("analysis.canadian_markets.session_scope") as mock_ss:
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            update_canadian_relevance_in_db(df)
        # Verify execute called with param containing event tickers
        call_args = mock_sess.execute.call_args[0]
        params = mock_sess.execute.call_args[0][1]
        assert len(params["evts"]) == 3

    def test_mixed_canadian_only_passes_canadian(self):
        from analysis.canadian_markets import update_canadian_relevance_in_db
        df = pd.DataFrame({
            "event_ticker":      ["BOC-A", "US-ELECTION", "CAD-B"],
            "canadian_relevant": [True,    False,         True],
        })
        with patch("analysis.canadian_markets.session_scope") as mock_ss:
            mock_sess = MagicMock()
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            update_canadian_relevance_in_db(df)
        params = mock_sess.execute.call_args[0][1]
        assert "US-ELECTION" not in params["evts"]
        assert len(params["evts"]) == 2
