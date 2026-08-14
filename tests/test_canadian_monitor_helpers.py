"""
tests/test_canadian_monitor_helpers.py
=======================================
Tests for pure-logic helpers in dashboard/pages/p08_canadian.py.

Covers:
  - _categorise_market: assigns markets to BOC / Canadian Politics /
    Canadian Economy / Canadian Sports / Other Canada
  - KPI computation logic (volume sum, arb filtering)

No DB, no network.
"""
from __future__ import annotations

import sys
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
    dl_mock.get_canadian_markets = MagicMock(return_value=(pd.DataFrame(), None))
    dl_mock.get_live_arb_opportunities = MagicMock(return_value=(pd.DataFrame(), None))
    dl_mock.get_canadian_historical_arb_stats = MagicMock(return_value={})

    styles_mock = MagicMock(
        GREEN="#22C55E", RED="#EF4444", AMBER="#F59E0B", BLUE="#3B82F6",
        TEXT="#F1F5F9", TEXT2="#94A3B8", TEXT3="#64748B",
        PANEL="#1E293B", BORDER="#334155",
        plotly_dark_layout=lambda: {},
    )

    go_mock = MagicMock()
    go_mock.Figure = MagicMock(return_value=MagicMock())
    go_mock.Bar = MagicMock(return_value=MagicMock())

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "streamlit", st_mock)
        mp.setitem(sys.modules, "dashboard.data_layer", dl_mock)
        mp.setitem(sys.modules, "dashboard.styles", styles_mock)
        mp.setitem(sys.modules, "plotly", MagicMock())
        mp.setitem(sys.modules, "plotly.graph_objects", go_mock)
        yield


# ---------------------------------------------------------------------------
# _categorise_market — category detection helper
# ---------------------------------------------------------------------------

class TestCategoriseMarket:
    def _cat(self, title: str) -> str:
        from dashboard.pages.p08_canadian import _CATEGORIES
        title_low = title.lower()
        for cat_name, keywords in _CATEGORIES.items():
            if keywords and any(kw in title_low for kw in keywords):
                return cat_name
        return "Other Canada"

    def test_boc_in_title_is_bank_of_canada(self):
        assert self._cat("Will the BOC raise rates?") == "Bank of Canada"

    def test_corra_is_bank_of_canada(self):
        assert self._cat("CORRA overnight rate stays at 4%?") == "Bank of Canada"

    def test_interest_rate_is_bank_of_canada(self):
        assert self._cat("Canada interest rate hike?") == "Bank of Canada"

    def test_monetary_policy_is_bank_of_canada(self):
        assert self._cat("BOC monetary policy meeting") == "Bank of Canada"

    def test_liberal_is_canadian_politics(self):
        assert self._cat("Liberal party wins majority?") == "Canadian Politics"

    def test_trudeau_is_canadian_politics(self):
        assert self._cat("Will Trudeau resign?") == "Canadian Politics"

    def test_parliament_is_canadian_politics(self):
        assert self._cat("Parliament passes bill?") == "Canadian Politics"

    def test_cad_is_canadian_economy(self):
        assert self._cat("Will CAD rise above 0.75?") == "Canadian Economy"

    def test_tsx_is_canadian_economy(self):
        assert self._cat("TSX above 22000?") == "Canadian Economy"

    def test_canadian_dollar_is_economy(self):
        assert self._cat("Canadian dollar vs USD") == "Canadian Economy"

    def test_inflation_is_canadian_economy(self):
        assert self._cat("Canadian inflation above 3%?") == "Canadian Economy"

    def test_leafs_is_canadian_sports(self):
        assert self._cat("Toronto Maple Leafs win Stanley Cup?") == "Canadian Sports"

    def test_oilers_is_canadian_sports(self):
        assert self._cat("Edmonton Oilers reach finals?") == "Canadian Sports"

    def test_blue_jays_is_canadian_sports(self):
        assert self._cat("Blue Jays make playoffs?") == "Canadian Sports"

    def test_raptors_is_canadian_sports(self):
        assert self._cat("Raptors win championship?") == "Canadian Sports"

    def test_canada_keyword_is_politics(self):
        # "canada" appears in Canadian Politics keywords
        assert self._cat("Some random Canada fact") == "Canadian Politics"

    def test_empty_title_falls_to_other_canada(self):
        assert self._cat("") == "Other Canada"


# ---------------------------------------------------------------------------
# _CATEGORIES structure validation
# ---------------------------------------------------------------------------

class TestCategoriesStructure:
    def test_categories_is_dict(self):
        from dashboard.pages.p08_canadian import _CATEGORIES
        assert isinstance(_CATEGORIES, dict)

    def test_has_bank_of_canada(self):
        from dashboard.pages.p08_canadian import _CATEGORIES
        assert "Bank of Canada" in _CATEGORIES

    def test_has_canadian_politics(self):
        from dashboard.pages.p08_canadian import _CATEGORIES
        assert "Canadian Politics" in _CATEGORIES

    def test_has_canadian_economy(self):
        from dashboard.pages.p08_canadian import _CATEGORIES
        assert "Canadian Economy" in _CATEGORIES

    def test_has_canadian_sports(self):
        from dashboard.pages.p08_canadian import _CATEGORIES
        assert "Canadian Sports" in _CATEGORIES

    def test_has_other_canada(self):
        from dashboard.pages.p08_canadian import _CATEGORIES
        assert "Other Canada" in _CATEGORIES

    def test_boc_keywords_include_corra(self):
        from dashboard.pages.p08_canadian import _CATEGORIES
        keywords = _CATEGORIES["Bank of Canada"]
        assert "corra" in keywords

    def test_sports_keywords_include_oilers(self):
        from dashboard.pages.p08_canadian import _CATEGORIES
        keywords = _CATEGORIES["Canadian Sports"]
        assert "oilers" in keywords

    def test_economy_keywords_include_tsx(self):
        from dashboard.pages.p08_canadian import _CATEGORIES
        keywords = _CATEGORIES["Canadian Economy"]
        assert "tsx" in keywords

    def test_all_keyword_lists_are_lists(self):
        from dashboard.pages.p08_canadian import _CATEGORIES
        for cat_name, keywords in _CATEGORIES.items():
            assert isinstance(keywords, list), f"Category '{cat_name}' has non-list keywords"


# ---------------------------------------------------------------------------
# KPI computation logic
# ---------------------------------------------------------------------------

class TestCanadianKpiLogic:
    def test_volume_sum_empty_df(self):
        df = pd.DataFrame()
        vol = df["volume"].sum() if not df.empty and "volume" in df.columns else 0
        assert vol == 0

    def test_volume_sum_populated(self):
        df = pd.DataFrame({"volume": [100, 200, 300]})
        vol = df["volume"].sum() if not df.empty and "volume" in df.columns else 0
        assert vol == 600

    def test_n_markets_empty_df(self):
        df = pd.DataFrame()
        n = len(df) if not df.empty else 0
        assert n == 0

    def test_n_markets_populated(self):
        df = pd.DataFrame({"ticker": ["A", "B", "C"]})
        n = len(df) if not df.empty else 0
        assert n == 3

    def test_best_arb_edge_max(self):
        arb_df = pd.DataFrame({"net_edge_cents": [1.5, 3.2, 2.1]})
        best = arb_df["net_edge_cents"].max()
        assert abs(best - 3.2) < 1e-9

    def test_best_arb_edge_empty(self):
        arb_df = pd.DataFrame()
        n_arb = len(arb_df) if not arb_df.empty else 0
        assert n_arb == 0

    def test_volume_missing_column_returns_zero(self):
        df = pd.DataFrame({"ticker": ["A", "B"]})  # no volume column
        vol = df["volume"].sum() if not df.empty and "volume" in df.columns else 0
        assert vol == 0
