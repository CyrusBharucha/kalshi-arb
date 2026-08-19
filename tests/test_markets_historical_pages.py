"""
tests/test_markets_historical_pages.py
========================================
Unit tests for dashboard pages p03_markets.py and p05_historical_arb.py:
  - p03: _CATEGORIES list (structure and known values)
  - p05: _DOW_LABELS list (length, order, contents)

No DB, no Streamlit rendering — pure constant inspection.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest
import sys


@pytest.fixture(autouse=True, scope="module")
def _patch_streamlit():
    """Patch streamlit and all dashboard imports before loading pages."""
    mock_st     = MagicMock()
    mock_styles = MagicMock()
    mock_dl     = MagicMock()
    mock_ls     = MagicMock()
    mock_plotly = MagicMock()
    mock_go     = MagicMock()

    mock_styles.GREEN  = "#00FF00"
    mock_styles.RED    = "#FF0000"
    mock_styles.AMBER  = "#FFA500"
    mock_styles.BLUE   = "#0000FF"
    mock_styles.CYAN   = "#00FFFF"
    mock_styles.TEXT   = "#FFFFFF"
    mock_styles.TEXT2  = "#CCCCCC"
    mock_styles.TEXT3  = "#999999"
    mock_styles.PANEL  = "#111111"
    mock_styles.BORDER = "#333333"
    mock_styles.plotly_dark_layout = MagicMock(return_value={})

    patches = {
        "streamlit":           mock_st,
        "dashboard.styles":    mock_styles,
        "dashboard.data_layer": mock_dl,
        "dashboard.live_state": mock_ls,
        "plotly":              mock_plotly,
        "plotly.graph_objects": mock_go,
    }
    with patch.dict(sys.modules, patches):
        yield


class TestMarketsPageCategories:
    """p03_markets._CATEGORIES: list of market filter options."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import importlib
        import dashboard.pages.p03_markets as mod
        importlib.reload(mod)
        self.cats = mod._CATEGORIES

    def test_is_list(self):
        assert isinstance(self.cats, list)

    def test_starts_with_all(self):
        assert self.cats[0] == "All"

    def test_has_reasonable_length(self):
        assert len(self.cats) >= 10

    def test_contains_politics(self):
        assert "Politics" in self.cats

    def test_contains_finance_markets(self):
        assert "Finance/Markets" in self.cats

    def test_contains_sports(self):
        assert "Sports" in self.cats

    def test_contains_elections(self):
        assert "Elections" in self.cats

    def test_contains_crypto(self):
        assert "Crypto" in self.cats

    def test_no_duplicates(self):
        assert len(self.cats) == len(set(self.cats))

    def test_all_strings(self):
        assert all(isinstance(c, str) for c in self.cats)

    def test_no_empty_strings(self):
        assert all(c.strip() for c in self.cats)


class TestHistoricalArbPageDowLabels:
    """p05_historical_arb._DOW_LABELS: day-of-week label list."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import importlib
        import dashboard.pages.p05_historical_arb as mod
        importlib.reload(mod)
        self.labels = mod._DOW_LABELS

    def test_is_list(self):
        assert isinstance(self.labels, list)

    def test_length_is_seven(self):
        assert len(self.labels) == 7

    def test_starts_with_sunday(self):
        assert self.labels[0] == "Sun"

    def test_ends_with_saturday(self):
        assert self.labels[-1] == "Sat"

    def test_contains_mon(self):
        assert "Mon" in self.labels

    def test_contains_fri(self):
        assert "Fri" in self.labels

    def test_no_duplicates(self):
        assert len(self.labels) == len(set(self.labels))

    def test_all_three_char(self):
        assert all(len(d) == 3 for d in self.labels)

    def test_correct_order(self):
        expected = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        assert self.labels == expected
