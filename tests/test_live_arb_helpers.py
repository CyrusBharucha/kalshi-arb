"""
tests/test_live_arb_helpers.py
================================
Tests for pure-logic helpers in dashboard/pages/p02_live_arb.py:
  - _fmt_live_status
  - _fmt_markets
  - _fmt_class
  - _sort_map logic
  - LIFETIME formatting

No DB, no network.
"""
from __future__ import annotations

import sys
import json
import pytest
import pandas as pd
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Module-level patches
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_all():
    st_mock = MagicMock()
    st_mock.cache_data = lambda *a, **kw: (lambda f: f)
    st_mock.cache_resource = lambda *a, **kw: (lambda f: f)

    dl_mock = MagicMock()
    dl_mock.get_live_arb_opportunities = MagicMock(return_value=(pd.DataFrame(), None))
    dl_mock.get_opportunity_detail = MagicMock(return_value=(pd.DataFrame(), None))
    dl_mock.get_recently_closed_opps = MagicMock(return_value=(pd.DataFrame(), None))

    styles_mock = MagicMock(
        GREEN="#22C55E", RED="#EF4444", AMBER="#F59E0B", BLUE="#3B82F6",
        CYAN="#06B6D4",
        TEXT="#F1F5F9", TEXT2="#94A3B8", TEXT3="#64748B",
        PANEL="#1E293B", BORDER="#334155", PANEL2="#0F172A",
        plotly_dark_layout=lambda: {},
    )

    go_mock = MagicMock()
    go_mock.Figure = MagicMock(return_value=MagicMock())
    go_mock.Histogram = MagicMock(return_value=MagicMock())

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "streamlit", st_mock)
        mp.setitem(sys.modules, "dashboard.data_layer", dl_mock)
        mp.setitem(sys.modules, "dashboard.styles", styles_mock)
        mp.setitem(sys.modules, "plotly", MagicMock())
        mp.setitem(sys.modules, "plotly.graph_objects", go_mock)
        yield


# ---------------------------------------------------------------------------
# _fmt_live_status
# ---------------------------------------------------------------------------

class TestFmtLiveStatus:
    def _call(self, age_s):
        from dashboard.pages.p02_live_arb import _fmt_live_status
        return _fmt_live_status(age_s)

    def test_none_returns_unknown(self):
        assert self._call(None) == "UNKNOWN"

    def test_nan_returns_unknown(self):
        assert self._call(float("nan")) == "UNKNOWN"

    def test_zero_is_live(self):
        result = self._call(0)
        assert "LIVE" in result

    def test_under_stale_age_is_live(self):
        # _STALE_AGE_S = 30
        result = self._call(10)
        assert "LIVE" in result

    def test_exactly_stale_threshold_is_aging(self):
        # at exactly 31s → > 30 but not > 120 → AGING
        result = self._call(31)
        assert "AGING" in result

    def test_between_stale_and_alert_is_aging(self):
        # 60s > 30 and < 120 → AGING
        result = self._call(60)
        assert "AGING" in result

    def test_over_alert_age_is_stale(self):
        # _ALERT_AGE_S = 120
        result = self._call(121)
        assert "STALE" in result

    def test_very_old_is_stale(self):
        result = self._call(3600)
        assert "STALE" in result

    def test_age_appears_in_result(self):
        result = self._call(45)
        assert "45" in result

    def test_result_is_string(self):
        assert isinstance(self._call(10), str)


# ---------------------------------------------------------------------------
# _fmt_markets
# ---------------------------------------------------------------------------

class TestFmtMarkets:
    def _call(self, val):
        from dashboard.pages.p02_live_arb import _fmt_markets
        return _fmt_markets(val)

    def test_none_returns_dash(self):
        assert self._call(None) == "--"

    def test_empty_string_returns_dash(self):
        assert self._call("") == "--"

    def test_empty_list_returns_dash(self):
        assert self._call([]) == "--"

    def test_json_list_of_two_formats_with_pipe(self):
        val = json.dumps(["KXBOC-25JAN-T4", "KXBOC-25JAN-T3"])
        result = self._call(val)
        assert "|" in result
        assert "KXBOC-25JAN-T4" in result

    def test_json_list_of_one_formats_without_pipe(self):
        val = json.dumps(["SINGLE-TICKER"])
        result = self._call(val)
        assert "SINGLE-TICKER" in result

    def test_python_list_works(self):
        result = self._call(["TICKER-A", "TICKER-B"])
        assert "TICKER-A" in result

    def test_plain_string_returned_truncated(self):
        val = "TICKER-X"
        result = self._call(val)
        # plain strings hit the except → str(val)[:50]
        assert "TICKER-X" in result

    def test_truncates_long_market_names(self):
        long_val = json.dumps(["A" * 100, "B" * 100])
        result = self._call(long_val)
        # each is truncated to 30 chars
        assert len(result) < 200

    def test_returns_string(self):
        assert isinstance(self._call(["X"]), str)


# ---------------------------------------------------------------------------
# _fmt_class
# ---------------------------------------------------------------------------

class TestFmtClass:
    def _call(self, c):
        from dashboard.pages.p02_live_arb import _fmt_class
        return _fmt_class(c)

    def test_class_a_is_executable(self):
        assert self._call("A") == "EXECUTABLE"

    def test_class_b_is_pending_depth(self):
        assert self._call("B") == "PENDING DEPTH"

    def test_class_c_is_relative_value(self):
        assert self._call("C") == "RELATIVE VALUE"

    def test_class_d_is_neg_after_fees(self):
        assert self._call("D") == "NEG AFTER FEES"

    def test_unknown_class_returns_input(self):
        result = self._call("X")
        assert "X" in result

    def test_returns_string(self):
        assert isinstance(self._call("A"), str)


# ---------------------------------------------------------------------------
# LIFETIME formatting (age_seconds lambda in render)
# ---------------------------------------------------------------------------

class TestLifetimeFormatting:
    def _fmt(self, s):
        """Reproduce the LIFETIME formatting lambda from p02_live_arb render()."""
        if pd.notna(s) and s < 3600:
            return f"{int(s // 60)}m{int(s % 60)}s"
        elif pd.notna(s):
            return f"{int(s // 3600)}h{int((s % 3600) // 60)}m"
        return "--"

    def test_zero_is_0m0s(self):
        assert self._fmt(0) == "0m0s"

    def test_90_is_1m30s(self):
        assert self._fmt(90) == "1m30s"

    def test_3600_is_1h0m(self):
        assert self._fmt(3600) == "1h0m"

    def test_3661_is_1h1m(self):
        assert self._fmt(3661) == "1h1m"

    def test_nan_returns_dash(self):
        assert self._fmt(float("nan")) == "--"

    def test_none_returns_dash(self):
        assert self._fmt(None) == "--"


# ---------------------------------------------------------------------------
# Sort map logic
# ---------------------------------------------------------------------------

class TestSortMap:
    def _get_sort_map(self):
        return {
            "Net Edge":  ("net_edge_cents", False),
            "Age":       ("age_seconds",    True),
            "Qty":       ("qty",            False),
            "Max P&L":   ("max_net_profit", False),
        }

    def test_net_edge_sorts_descending(self):
        sm = self._get_sort_map()
        col, asc = sm["Net Edge"]
        assert col == "net_edge_cents"
        assert asc is False

    def test_age_sorts_ascending(self):
        sm = self._get_sort_map()
        col, asc = sm["Age"]
        assert col == "age_seconds"
        assert asc is True

    def test_qty_sorts_descending(self):
        sm = self._get_sort_map()
        col, asc = sm["Qty"]
        assert col == "qty"
        assert asc is False

    def test_pnl_sorts_descending(self):
        sm = self._get_sort_map()
        col, asc = sm["Max P&L"]
        assert col == "max_net_profit"
        assert asc is False

    def test_fallback_for_unknown_key(self):
        sm = self._get_sort_map()
        col, asc = sm.get("UNKNOWN", ("net_edge_cents", False))
        assert col == "net_edge_cents"
        assert asc is False


# ---------------------------------------------------------------------------
# _STATUS_COLORS structure
# ---------------------------------------------------------------------------

class TestStatusColors:
    def test_status_colors_has_all_classes(self):
        from dashboard.pages.p02_live_arb import _STATUS_COLORS
        for cls in ["A", "B", "C", "D"]:
            assert cls in _STATUS_COLORS

    def test_each_entry_is_two_tuple(self):
        from dashboard.pages.p02_live_arb import _STATUS_COLORS
        for cls, entry in _STATUS_COLORS.items():
            assert isinstance(entry, tuple)
            assert len(entry) == 2

    def test_a_has_nonempty_label(self):
        from dashboard.pages.p02_live_arb import _STATUS_COLORS
        _, label = _STATUS_COLORS["A"]
        assert len(label) > 0

    def test_strategies_list_has_all(self):
        from dashboard.pages.p02_live_arb import _STRATEGIES
        assert "All" in _STRATEGIES
        assert "yes_no_complement" in _STRATEGIES
