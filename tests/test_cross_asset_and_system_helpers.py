"""
tests/test_cross_asset_and_system_helpers.py
=============================================
Unit tests for:
  - dashboard/pages/p07_cross_asset.py: _ASSETS constant
  - dashboard/pages/p10_system.py: _section_header, _stat_panel render helpers

All tests are pure Python — streamlit and other UI dependencies are mocked.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module-level patch for streamlit and dashboard deps
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_modules():
    """Patch Streamlit + dashboard deps before importing the pages."""
    st_mock = MagicMock()
    # st.columns returns a list of MagicMocks (iterable for 'with col1: ...')
    st_mock.columns.return_value = [MagicMock(), MagicMock(), MagicMock()]

    styles_mock = MagicMock()
    styles_mock.GREEN  = "#00FF88"
    styles_mock.RED    = "#FF4C4C"
    styles_mock.AMBER  = "#FFA500"
    styles_mock.BLUE   = "#4C9FFF"
    styles_mock.CYAN   = "#00E5FF"
    styles_mock.TEXT   = "#E0E0E0"
    styles_mock.TEXT2  = "#B0B0B0"
    styles_mock.TEXT3  = "#707070"
    styles_mock.PANEL  = "#1A1D26"
    styles_mock.BORDER = "#2E3140"
    styles_mock.PANEL2 = "#22263A"
    styles_mock.plotly_dark_layout.return_value = {}

    data_layer_mock = MagicMock()
    data_layer_mock.get_cross_asset_data.return_value = (MagicMock(), None)
    data_layer_mock.get_system_health.return_value     = (MagicMock(), None)
    data_layer_mock.get_coverage_stats.return_value    = (MagicMock(), None)
    data_layer_mock.get_table_sizes.return_value       = (MagicMock(), None)
    data_layer_mock.get_data_quality_stats.return_value = (MagicMock(), None)
    data_layer_mock.get_ingestion_log.return_value     = (MagicMock(), None)

    live_state_mock = MagicMock()
    live_state_mock.get_live_state.return_value = MagicMock()

    mods = {
        "streamlit": st_mock,
        "dashboard.styles": styles_mock,
        "dashboard.data_layer": data_layer_mock,
        "dashboard.live_state": live_state_mock,
        "plotly": MagicMock(),
        "plotly.graph_objects": MagicMock(),
    }
    with patch.dict(sys.modules, mods):
        yield st_mock, styles_mock


# ---------------------------------------------------------------------------
# p07 _ASSETS
# ---------------------------------------------------------------------------

class TestCrossAssetAssetsConstant:
    """_ASSETS: list of (code, label) 2-tuples."""

    @pytest.fixture(autouse=True)
    def _import(self, _patch_modules):
        # Need fresh import after modules are patched
        if "dashboard.pages.p07_cross_asset" in sys.modules:
            del sys.modules["dashboard.pages.p07_cross_asset"]
        from dashboard.pages.p07_cross_asset import _ASSETS
        self.assets = _ASSETS

    def test_is_list(self):
        assert isinstance(self.assets, list)

    def test_nonempty(self):
        assert len(self.assets) > 0

    def test_has_8_entries(self):
        assert len(self.assets) == 8

    def test_each_entry_is_2_tuple(self):
        for entry in self.assets:
            assert len(entry) == 2

    def test_codes_are_strings(self):
        for code, label in self.assets:
            assert isinstance(code, str)
            assert len(code) > 0

    def test_labels_are_strings(self):
        for code, label in self.assets:
            assert isinstance(label, str)
            assert len(label) > 0

    def test_contains_corra(self):
        codes = [a[0] for a in self.assets]
        assert "CORRA" in codes

    def test_contains_cadusd(self):
        codes = [a[0] for a in self.assets]
        assert "CADUSD" in codes

    def test_contains_boc_rate(self):
        codes = [a[0] for a in self.assets]
        assert "BOC_RATE" in codes

    def test_codes_are_unique(self):
        codes = [a[0] for a in self.assets]
        assert len(codes) == len(set(codes))


# ---------------------------------------------------------------------------
# p10 _section_header and _stat_panel
# ---------------------------------------------------------------------------

class TestSystemPageHelpers:
    """_section_header and _stat_panel call st.markdown without raising."""

    def _get_helpers(self):
        if "dashboard.pages.p10_system" in sys.modules:
            del sys.modules["dashboard.pages.p10_system"]
        from dashboard.pages.p10_system import _section_header, _stat_panel
        return _section_header, _stat_panel

    def test_section_header_does_not_raise(self, _patch_modules):
        sh, _ = self._get_helpers()
        sh("TEST SECTION")  # should not raise

    def test_section_header_calls_st_markdown(self, _patch_modules):
        st_mock, _ = _patch_modules
        sh, _ = self._get_helpers()
        sh("MY SECTION")
        assert st_mock.markdown.called

    def test_stat_panel_does_not_raise(self, _patch_modules):
        _, sp = self._get_helpers()
        rows = [
            ("MESSAGES", ("1,234", "#00FF88")),
            ("ERRORS",   ("0", "#FF4C4C")),
        ]
        sp(rows)  # should not raise

    def test_stat_panel_calls_st_markdown(self, _patch_modules):
        st_mock, _ = _patch_modules
        _, sp = self._get_helpers()
        sp([("KEY", ("VAL", "#FFFFFF"))])
        assert st_mock.markdown.called

    def test_stat_panel_handles_empty_rows(self, _patch_modules):
        _, sp = self._get_helpers()
        sp([])  # empty list — should not raise

    def test_section_header_empty_string(self, _patch_modules):
        sh, _ = self._get_helpers()
        sh("")  # empty title — should not raise
