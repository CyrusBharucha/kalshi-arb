"""
tests/test_canadian_loaders.py
================================
Unit tests for analysis/canadian_markets:
  - load_canadian_markets(): calls session_scope, returns DataFrame with classification columns
  - run_canadian_analysis(): orchestrates load + produce_summary + update_db, returns summary dict

All DB calls mocked.
"""
from __future__ import annotations

import sys
import pandas as pd
from unittest.mock import patch, MagicMock, call
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


def _session_ctx(return_df=None):
    if return_df is None:
        return_df = pd.DataFrame()
    mock_ss = MagicMock()
    mock_sess = MagicMock()
    mock_sess.bind = MagicMock()
    mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
    mock_ss.return_value.__exit__ = MagicMock(return_value=False)
    return mock_ss, mock_sess


def _base_markets_df():
    return pd.DataFrame({
        "market_id": ["MID-1", "MID-2"],
        "ticker":    ["BOC-RATE", "US-ELEC"],
        "title":     ["Bank of Canada rate decision", "US election"],
        "category":  ["economics", "politics"],
        "status":    ["active", "active"],
        "event_ticker": ["BOC-2026", "US-2026"],
        "floor_strike": [None, None],
        "cap_strike":   [None, None],
        "geographic_region": ["Canada", "United States"],
        "trade_count": [100, 200],
    })


# ---------------------------------------------------------------------------
# load_canadian_markets
# ---------------------------------------------------------------------------

def _read_sql_side_effect(call_count_holder):
    """Return markets df on first call, relationships df on second call."""
    def side_effect(query, bind, *args, **kwargs):
        call_count_holder[0] += 1
        if call_count_holder[0] == 1:
            return _base_markets_df()
        else:
            # Second call: contract_relationships cross-reference returns {"mid": [...]}
            return pd.DataFrame({"mid": []})
    return side_effect


class TestLoadCanadianMarkets:
    def _run(self, markets_df=None):
        from analysis.canadian_markets import load_canadian_markets
        mock_ss, _ = _session_ctx()
        counter = [0]
        def side_effect(query, bind, *args, **kwargs):
            counter[0] += 1
            if counter[0] == 1:
                return markets_df if markets_df is not None else _base_markets_df()
            return pd.DataFrame({"mid": []})
        with patch("analysis.canadian_markets.session_scope", mock_ss), \
             patch("analysis.canadian_markets.pd.read_sql", side_effect=side_effect):
            return load_canadian_markets(), mock_ss

    def test_returns_dataframe(self):
        result, _ = self._run()
        assert isinstance(result, pd.DataFrame)

    def test_calls_session_scope_twice(self):
        _, mock_ss = self._run()
        assert mock_ss.call_count == 2

    def test_adds_canadian_tags_column(self):
        result, _ = self._run()
        assert "canadian_tags" in result.columns

    def test_adds_canadian_relevant_column(self):
        result, _ = self._run()
        assert "canadian_relevant" in result.columns

    def test_boc_ticker_flagged_as_canadian(self):
        result, _ = self._run()
        boc_row = result[result["ticker"] == "BOC-RATE"]
        assert len(boc_row) == 1
        assert bool(boc_row.iloc[0]["canadian_relevant"]) is True

    def test_in_relationship_graph_column_added(self):
        result, _ = self._run()
        assert "in_relationship_graph" in result.columns

    def test_empty_df_returns_empty(self):
        empty_df = pd.DataFrame({
            "market_id": [], "ticker": [], "title": [], "category": [],
            "status": [], "event_ticker": [], "floor_strike": [],
            "cap_strike": [], "geographic_region": [], "trade_count": [],
        })
        result, _ = self._run(markets_df=empty_df)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# run_canadian_analysis
# ---------------------------------------------------------------------------

class TestRunCanadianAnalysis:
    def _fake_summary(self):
        return {
            "total_active_markets": 2,
            "canadian_relevant": 1,
            "cross_asset_candidates": 0,
            "by_category": {"boc": 1},
            "top_markets_by_trades": [],
        }

    def test_returns_dict(self):
        from analysis.canadian_markets import run_canadian_analysis
        with patch("analysis.canadian_markets.load_canadian_markets",
                   return_value=_base_markets_df()), \
             patch("analysis.canadian_markets.produce_canadian_summary",
                   return_value=self._fake_summary()), \
             patch("analysis.canadian_markets.update_canadian_relevance_in_db"):
            result = run_canadian_analysis()
        assert isinstance(result, dict)

    def test_calls_load_canadian_markets(self):
        from analysis.canadian_markets import run_canadian_analysis
        with patch("analysis.canadian_markets.load_canadian_markets",
                   return_value=_base_markets_df()) as mock_load, \
             patch("analysis.canadian_markets.produce_canadian_summary",
                   return_value=self._fake_summary()), \
             patch("analysis.canadian_markets.update_canadian_relevance_in_db"):
            run_canadian_analysis()
        mock_load.assert_called_once()

    def test_calls_produce_summary(self):
        from analysis.canadian_markets import run_canadian_analysis
        with patch("analysis.canadian_markets.load_canadian_markets",
                   return_value=_base_markets_df()), \
             patch("analysis.canadian_markets.produce_canadian_summary",
                   return_value=self._fake_summary()) as mock_ps, \
             patch("analysis.canadian_markets.update_canadian_relevance_in_db"):
            run_canadian_analysis()
        mock_ps.assert_called_once()

    def test_calls_update_relevance(self):
        from analysis.canadian_markets import run_canadian_analysis
        with patch("analysis.canadian_markets.load_canadian_markets",
                   return_value=_base_markets_df()), \
             patch("analysis.canadian_markets.produce_canadian_summary",
                   return_value=self._fake_summary()), \
             patch("analysis.canadian_markets.update_canadian_relevance_in_db") as mock_upd:
            run_canadian_analysis()
        mock_upd.assert_called_once()

    def test_returns_summary_from_produce(self):
        from analysis.canadian_markets import run_canadian_analysis
        fake = self._fake_summary()
        with patch("analysis.canadian_markets.load_canadian_markets",
                   return_value=_base_markets_df()), \
             patch("analysis.canadian_markets.produce_canadian_summary",
                   return_value=fake), \
             patch("analysis.canadian_markets.update_canadian_relevance_in_db"):
            result = run_canadian_analysis()
        assert result["canadian_relevant"] == 1
        assert result["total_active_markets"] == 2
