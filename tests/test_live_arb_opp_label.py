"""
tests/test_live_arb_opp_label.py
===================================
Unit tests for dashboard/pages/p02_live_arb.py _opp_label() function
and the _fmt_markets() JSON parsing helper paths not covered by
test_live_arb_helpers.py.

_opp_label(df, oid): returns oid fallback when row not found, or
  formatted "STRATEGY | +X.XXc" string when row exists.

_fmt_markets(val): already partly covered; test JSON array input path.

No DB, no network.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch, MagicMock
import pytest
import pandas as pd


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    """Patch streamlit and dashboard DB imports before page import."""
    st_mock = MagicMock()
    st_mock.cache_data = lambda **kw: (lambda f: f)

    mods = {
        "streamlit": st_mock,
        "plotly": MagicMock(),
        "plotly.graph_objects": MagicMock(),
        "dashboard.data_layer": MagicMock(),
        "dashboard.styles": MagicMock(
            GREEN="#00FF00", RED="#FF0000", AMBER="#FFAA00",
            BLUE="#0000FF", CYAN="#00FFFF", TEXT="#FFF",
            TEXT2="#CCC", TEXT3="#999", PANEL="#111", BORDER="#333",
            PANEL2="#222", plotly_dark_layout=lambda: {},
        ),
        "dashboard.ws_bridge": MagicMock(),
    }
    with patch.dict(sys.modules, mods):
        import importlib
        if "dashboard.pages.p02_live_arb" in sys.modules:
            del sys.modules["dashboard.pages.p02_live_arb"]
        importlib.import_module("dashboard.pages.p02_live_arb")
        yield


class TestOppLabel:
    """_opp_label(df, oid): fallback and formatted output."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.pages.p02_live_arb import _opp_label
        self.fn = _opp_label

    def _df(self, rows):
        return pd.DataFrame(rows)

    def test_unknown_oid_returns_oid(self):
        df = self._df([{
            "opportunity_id": "OID-1",
            "strategy_type": "complement",
            "net_edge_cents": 3.5,
        }])
        assert self.fn(df, "OID-MISSING") == "OID-MISSING"

    def test_known_oid_formats_label(self):
        df = self._df([{
            "opportunity_id": "OID-1",
            "strategy_type": "complement",
            "net_edge_cents": 3.5,
        }])
        result = self.fn(df, "OID-1")
        assert "COMPLEMENT" in result
        assert "3.50" in result

    def test_label_includes_edge(self):
        df = self._df([{
            "opportunity_id": "X",
            "strategy_type": "me",
            "net_edge_cents": 12.0,
        }])
        result = self.fn(df, "X")
        assert "12.00" in result

    def test_empty_df_returns_oid(self):
        # Empty df must have the opportunity_id column to avoid KeyError
        df = pd.DataFrame(columns=["opportunity_id", "strategy_type", "net_edge_cents"])
        assert self.fn(df, "ANY") == "ANY"

    def test_missing_strategy_column_returns_edge_only(self):
        df = self._df([{
            "opportunity_id": "OID-2",
            "net_edge_cents": 5.0,
            # no strategy_type column
        }])
        result = self.fn(df, "OID-2")
        # Should not raise; strategy defaults to ""
        assert "5.00" in result

    def test_missing_edge_column_defaults_zero(self):
        df = self._df([{
            "opportunity_id": "OID-3",
            "strategy_type": "threshold",
            # no net_edge_cents column
        }])
        result = self.fn(df, "OID-3")
        assert "THRESHOLD" in result
        assert "0.00" in result

    def test_label_starts_with_strategy_upper(self):
        df = self._df([{
            "opportunity_id": "Z",
            "strategy_type": "mutually_exclusive",
            "net_edge_cents": 2.0,
        }])
        result = self.fn(df, "Z")
        assert result.startswith("MUTUALLY_EXCLUSIVE")


class TestFmtMarketsExtended:
    """_fmt_markets(): additional paths not covered by test_live_arb_helpers."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.pages.p02_live_arb import _fmt_markets
        self.fn = _fmt_markets

    def test_json_array_string_joins_with_pipe(self):
        import json
        val = json.dumps(["MKT-A", "MKT-B"])
        result = self.fn(val)
        assert "MKT-A" in result
        assert "MKT-B" in result

    def test_python_list_joins_markets(self):
        result = self.fn(["MKT-X", "MKT-Y"])
        assert "MKT-X" in result
        assert "MKT-Y" in result

    def test_none_returns_dash(self):
        assert self.fn(None) == "--"

    def test_empty_string_returns_dash(self):
        assert self.fn("") == "--"

    def test_long_market_id_truncated(self):
        # Individual markets are capped at 30 chars
        long_id = "X" * 40
        result = self.fn([long_id])
        assert len(result) <= 31  # 30 chars + some separator allowance

    def test_invalid_json_string_returned_as_is(self):
        result = self.fn("not-json")
        assert result == "not-json"
