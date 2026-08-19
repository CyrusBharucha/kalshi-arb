"""
tests/test_synthesis_live_db_helpers.py
=========================================
Unit tests for data/synthesis_live.py DB helper functions:
  - load_relationships_from_db(): parses DB rows into list of dicts
  - persist_opportunity(): executes insert SQL with correct params

All DB calls mocked — no live PostgreSQL required.
"""
from __future__ import annotations

import sys
import json
from unittest.mock import patch, MagicMock, call
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(
            DB_URL="postgresql://localhost/test",
            KALSHI_READ_RPS=10.0,
            KALSHI_WRITE_RPS=1.0,
            KALSHI_KEY_ID="",
            KALSHI_PRIVKEY_PATH="",
        ),
        "feeds.kalshi_client": MagicMock(),
        "feeds.websocket_client": MagicMock(),
        "engine.relationship_detector": MagicMock(),
    }):
        yield


# ---------------------------------------------------------------------------
# load_relationships_from_db
# ---------------------------------------------------------------------------

class TestLoadRelationshipsFromDb:
    def _mock_session(self, rows):
        session = MagicMock()
        session.execute.return_value.fetchall.return_value = rows
        return session

    def test_empty_rows_returns_empty_list(self):
        from feeds.synthesis_live import load_relationships_from_db
        session = self._mock_session([])
        result = load_relationships_from_db(session)
        assert result == []

    def test_single_row_returns_one_dict(self):
        from feeds.synthesis_live import load_relationships_from_db
        rows = [("MKT-A", "MKT-B", "mutually_exclusive", "P(A)+P(B)<=1", 0.9)]
        session = self._mock_session(rows)
        result = load_relationships_from_db(session)
        assert len(result) == 1

    def test_result_has_required_keys(self):
        from feeds.synthesis_live import load_relationships_from_db
        rows = [("MKT-A", "MKT-B", "mutually_exclusive", "P(A)+P(B)<=1", 0.9)]
        session = self._mock_session(rows)
        result = load_relationships_from_db(session)
        rel = result[0]
        for key in ("market_id_1", "market_id_2", "relationship_type",
                    "implied_inequality", "confidence"):
            assert key in rel

    def test_market_ids_correct(self):
        from feeds.synthesis_live import load_relationships_from_db
        rows = [("ALPHA", "BETA", "threshold_order", "P(A)>=P(B)", 0.95)]
        session = self._mock_session(rows)
        result = load_relationships_from_db(session)
        assert result[0]["market_id_1"] == "ALPHA"
        assert result[0]["market_id_2"] == "BETA"

    def test_confidence_is_float(self):
        from feeds.synthesis_live import load_relationships_from_db
        rows = [("A", "B", "superset", "P(A)>=P(B)", 0.9)]
        session = self._mock_session(rows)
        result = load_relationships_from_db(session)
        assert isinstance(result[0]["confidence"], float)

    def test_multiple_rows_all_returned(self):
        from feeds.synthesis_live import load_relationships_from_db
        rows = [
            ("M1", "M2", "mutually_exclusive", "P(M1)+P(M2)<=1", 0.9),
            ("M3", "M4", "threshold_order",    "P(M3)>=P(M4)",   0.95),
            ("M5", "M6", "superset",           "P(M5)>=P(M6)",   1.0),
        ]
        session = self._mock_session(rows)
        result = load_relationships_from_db(session)
        assert len(result) == 3

    def test_calls_execute(self):
        from feeds.synthesis_live import load_relationships_from_db
        session = self._mock_session([])
        load_relationships_from_db(session)
        session.execute.assert_called_once()


# ---------------------------------------------------------------------------
# persist_opportunity
# ---------------------------------------------------------------------------

class TestPersistOpportunity:
    def _mock_session(self):
        return MagicMock()

    def _opp(self, strategy="sell_yes_both_me", gross=0.15, fees=0.07, net=0.08):
        return {
            "strategy":         strategy,
            "relationship_type": "mutually_exclusive",
            "classification":   "A",
            "market_id_1":      "MKT-A",
            "market_id_2":      "MKT-B",
            "detected_at":      "2026-06-23T10:00:00+00:00",
            "prices": {
                "MKT-A": {"yes_bid": 0.60, "yes_ask": 0.65},
                "MKT-B": {"yes_bid": 0.60, "yes_ask": 0.65},
            },
            "gross_edge":  gross,
            "fees":        fees,
            "net_edge":    net,
        }

    def test_calls_execute(self):
        from feeds.synthesis_live import persist_opportunity
        session = self._mock_session()
        persist_opportunity(session, self._opp())
        session.execute.assert_called_once()

    def test_never_raises(self):
        from feeds.synthesis_live import persist_opportunity
        session = self._mock_session()
        try:
            persist_opportunity(session, self._opp())
        except Exception:
            pytest.fail("persist_opportunity raised unexpectedly")

    def test_params_include_strategy(self):
        from feeds.synthesis_live import persist_opportunity
        session = self._mock_session()
        persist_opportunity(session, self._opp(strategy="sell_yes_both_me"))
        # Check execute was called with params containing the strategy
        call_args = session.execute.call_args
        params = call_args[0][1]  # second positional arg to execute
        assert params["stype"] == "sell_yes_both_me"

    def test_params_include_edges(self):
        from feeds.synthesis_live import persist_opportunity
        session = self._mock_session()
        persist_opportunity(session, self._opp(gross=0.20, fees=0.08, net=0.12))
        call_args = session.execute.call_args
        params = call_args[0][1]
        assert params["gross"] == pytest.approx(0.20)
        assert params["fees"] == pytest.approx(0.08)
        assert params["net"] == pytest.approx(0.12)

    def test_generates_unique_opportunity_id(self):
        from feeds.synthesis_live import persist_opportunity
        session1, session2 = self._mock_session(), self._mock_session()
        persist_opportunity(session1, self._opp())
        persist_opportunity(session2, self._opp())
        id1 = session1.execute.call_args[0][1]["oid"]
        id2 = session2.execute.call_args[0][1]["oid"]
        assert id1 != id2  # UUIDs should be different
