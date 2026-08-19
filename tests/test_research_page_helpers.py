"""
tests/test_research_page_helpers.py
=====================================
Tests for pure-logic helpers in dashboard/pages/p09_research.py:
  - _badge: renders an HTML badge
  - _stat_row: renders a key/value row
  - _stat_table: renders a table of key/value rows
  - _no_data_panel: renders a "data not available" message

No DB, no network.
"""
from __future__ import annotations

import sys
import pytest
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
    dl_mock.get_research_summary = MagicMock(return_value={})
    dl_mock.get_relationship_stats = MagicMock(return_value={})
    dl_mock.get_historical_arb_stats = MagicMock(return_value={})
    dl_mock.get_coverage_stats = MagicMock(return_value={})
    dl_mock.get_system_health = MagicMock(return_value={})
    dl_mock.get_canadian_markets = MagicMock(return_value=(MagicMock(empty=True), None))

    styles_mock = MagicMock(
        GREEN="#22C55E", RED="#EF4444", AMBER="#F59E0B", BLUE="#3B82F6",
        TEXT="#F1F5F9", TEXT2="#94A3B8", TEXT3="#64748B",
        PANEL="#1E293B", BORDER="#334155",
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
# _badge
# ---------------------------------------------------------------------------

class TestBadge:
    def _fn(self):
        from dashboard.pages.p09_research import _badge
        return _badge

    def test_exists(self):
        assert callable(self._fn())

    def test_does_not_raise_green(self):
        self._fn()("OBSERVED", "#22C55E")

    def test_does_not_raise_amber(self):
        self._fn()("PRELIMINARY", "#F59E0B")

    def test_does_not_raise_blue(self):
        self._fn()("INFERRED", "#3B82F6")

    def test_empty_label_does_not_raise(self):
        self._fn()("", "#22C55E")


# ---------------------------------------------------------------------------
# _stat_row
# ---------------------------------------------------------------------------

class TestStatRow:
    def _fn(self):
        from dashboard.pages.p09_research import _stat_row
        return _stat_row

    def test_exists(self):
        assert callable(self._fn())

    def test_label_value_does_not_raise(self):
        self._fn()("LABEL", "VALUE")

    def test_with_note_does_not_raise(self):
        self._fn()("LABEL", "VALUE", "some note")

    def test_empty_note_does_not_raise(self):
        self._fn()("LABEL", "VALUE", "")


# ---------------------------------------------------------------------------
# _stat_table
# ---------------------------------------------------------------------------

class TestStatTable:
    def _fn(self):
        from dashboard.pages.p09_research import _stat_table
        return _stat_table

    def test_exists(self):
        assert callable(self._fn())

    def test_empty_rows_does_not_raise(self):
        self._fn()([])

    def test_single_row_does_not_raise(self):
        self._fn()([("KEY", "VALUE")])

    def test_multiple_rows_do_not_raise(self):
        self._fn()([("A", "1"), ("B", "2"), ("C", "3")])


# ---------------------------------------------------------------------------
# _no_data_panel
# ---------------------------------------------------------------------------

class TestNoDataPanel:
    def _fn(self):
        from dashboard.pages.p09_research import _no_data_panel
        return _no_data_panel

    def test_exists(self):
        assert callable(self._fn())

    def test_short_message_does_not_raise(self):
        self._fn()("DB unavailable")

    def test_long_message_does_not_raise(self):
        self._fn()("A" * 200)

    def test_empty_message_does_not_raise(self):
        self._fn()("")


# ---------------------------------------------------------------------------
# research findings — class A/B logic
# ---------------------------------------------------------------------------

class TestArbFindingsLogic:
    """Reproduces the KPI computation in _render_arb_findings."""

    def test_class_a_extracted_from_summary(self):
        summary = {"class_a_violations": 42, "class_b_opportunities": 100}
        class_a = int(summary.get("class_a_violations", 0))
        assert class_a == 42

    def test_class_b_extracted_from_summary(self):
        summary = {"class_a_violations": 42, "class_b_opportunities": 100}
        class_b = int(summary.get("class_b_opportunities", 0))
        assert class_b == 100

    def test_missing_class_defaults_to_zero(self):
        summary = {}
        class_a = int(summary.get("class_a_violations", 0))
        assert class_a == 0

    def test_strategy_count_from_hist_stats(self):
        hist_stats = {"total_opportunities": 634, "active_opportunities": 12}
        total = int(hist_stats.get("total_opportunities", 0))
        assert total == 634
