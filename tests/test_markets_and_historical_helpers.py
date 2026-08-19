"""
tests/test_markets_and_historical_helpers.py
=============================================
Unit tests for helper functions in:
  - dashboard/pages/p05_historical_arb.py  (_DOW_LABELS, _section_header, _unavailable)

All tests run without PostgreSQL, Streamlit, or Plotly.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Module-level fixture: patch streamlit, data_layer, styles, and plotly
# so p05_historical_arb can be imported without side-effects.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    st_mock = MagicMock()
    st_mock.cache_data = lambda *a, **kw: (lambda f: f)

    styles_mock = MagicMock()
    styles_mock.plotly_dark_layout.return_value = {}
    for attr in ("GREEN", "RED", "AMBER", "BLUE", "CYAN",
                 "TEXT", "TEXT2", "TEXT3", "PANEL", "BORDER"):
        setattr(styles_mock, attr, "#000000")

    data_layer_mock = MagicMock()
    data_layer_mock.get_historical_arb_summary.return_value = (pd.DataFrame(), None)
    data_layer_mock.get_historical_arb_stats.return_value = {}
    data_layer_mock.get_coverage_stats.return_value = {}
    data_layer_mock.get_arb_rolling_7d.return_value = (pd.DataFrame(), None)
    data_layer_mock.get_arb_time_of_day_stats.return_value = (pd.DataFrame(), None)
    data_layer_mock.get_arb_day_of_week_stats.return_value = (pd.DataFrame(), None)
    data_layer_mock.get_arb_settlement_proximity_stats.return_value = (pd.DataFrame(), None)
    data_layer_mock.get_arb_by_category.return_value = (pd.DataFrame(), None)

    plotly_mock = MagicMock()
    go_mock = MagicMock()
    plotly_mock.graph_objects = go_mock

    mods = {
        "streamlit": st_mock,
        "dashboard.styles": styles_mock,
        "dashboard.data_layer": data_layer_mock,
        "plotly": plotly_mock,
        "plotly.graph_objects": go_mock,
        "database.repository": MagicMock(),
    }
    originals = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    yield
    for k, v in originals.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


# ---------------------------------------------------------------------------
# Tests for _DOW_LABELS constant (p05_historical_arb.py)
# ---------------------------------------------------------------------------

class TestDowLabels:
    """_DOW_LABELS constant in p05_historical_arb."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import dashboard.pages.p05_historical_arb as m
        self.m = m

    def test_dow_labels_is_list(self):
        assert isinstance(self.m._DOW_LABELS, list)

    def test_dow_labels_length_7(self):
        assert len(self.m._DOW_LABELS) == 7

    def test_dow_labels_starts_with_sun(self):
        assert self.m._DOW_LABELS[0] == "Sun"

    def test_dow_labels_ends_with_sat(self):
        assert self.m._DOW_LABELS[-1] == "Sat"

    def test_dow_labels_contains_mon(self):
        assert "Mon" in self.m._DOW_LABELS

    def test_dow_labels_contains_fri(self):
        assert "Fri" in self.m._DOW_LABELS

    def test_dow_labels_all_strings(self):
        assert all(isinstance(d, str) for d in self.m._DOW_LABELS)

    def test_dow_labels_second_is_mon(self):
        assert self.m._DOW_LABELS[1] == "Mon"

    def test_dow_labels_no_duplicates(self):
        assert len(set(self.m._DOW_LABELS)) == 7


# ---------------------------------------------------------------------------
# Tests for _section_header and _unavailable (p05_historical_arb.py)
# ---------------------------------------------------------------------------

class TestHistoricalArbHelpers:
    """_section_header and _unavailable do not raise."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import dashboard.pages.p05_historical_arb as m
        self.m = m

    def test_section_header_exists(self):
        assert callable(self.m._section_header)

    def test_section_header_does_not_raise(self):
        self.m._section_header("TEST SECTION")

    def test_section_header_empty_title(self):
        self.m._section_header("")

    def test_section_header_special_chars(self):
        self.m._section_header("OPPS / DAY <> & 2024")

    def test_unavailable_exists(self):
        assert callable(self.m._unavailable)

    def test_unavailable_does_not_raise(self):
        self.m._unavailable("connection refused")

    def test_unavailable_long_error_truncated(self):
        # Should handle errors longer than 200 chars without crashing
        self.m._unavailable("x" * 500)

    def test_unavailable_empty_string(self):
        self.m._unavailable("")

    def test_unavailable_multiline_error(self):
        self.m._unavailable("line1\nline2\nline3")
