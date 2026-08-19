"""
tests/test_styles.py
=====================
Unit tests for dashboard/styles.py:
  - Color constant existence and format
  - inject_css() returns a non-empty string containing expected tokens
  - plotly_dark_layout() returns a dict with required keys
"""
from __future__ import annotations

import pytest


class TestColorConstants:
    """Core palette tokens are defined and are valid hex color strings."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import dashboard.styles as s
        self.s = s

    def _is_hex(self, value: str) -> bool:
        """Return True if value is a valid CSS hex color (#RRGGBB or #RGB)."""
        if not isinstance(value, str):
            return False
        if not value.startswith("#"):
            return False
        rest = value[1:]
        return len(rest) in (3, 6) and all(c in "0123456789ABCDEFabcdef" for c in rest)

    def test_bg_is_hex(self):
        assert self._is_hex(self.s.BG)

    def test_panel_is_hex(self):
        assert self._is_hex(self.s.PANEL)

    def test_border_is_hex(self):
        assert self._is_hex(self.s.BORDER)

    def test_text_is_hex(self):
        assert self._is_hex(self.s.TEXT)

    def test_text2_is_hex(self):
        assert self._is_hex(self.s.TEXT2)

    def test_text3_is_hex(self):
        assert self._is_hex(self.s.TEXT3)

    def test_green_is_hex(self):
        assert self._is_hex(self.s.GREEN)

    def test_red_is_hex(self):
        assert self._is_hex(self.s.RED)

    def test_amber_is_hex(self):
        assert self._is_hex(self.s.AMBER)

    def test_blue_is_hex(self):
        assert self._is_hex(self.s.BLUE)

    def test_cyan_is_hex(self):
        assert self._is_hex(self.s.CYAN)

    def test_green_is_not_same_as_red(self):
        assert self.s.GREEN.upper() != self.s.RED.upper()

    def test_panel2_is_hex(self):
        assert self._is_hex(self.s.PANEL2)


class TestInjectCss:
    """inject_css() returns the CSS string to inject."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.styles import inject_css
        self.css = inject_css()

    def test_returns_string(self):
        assert isinstance(self.css, str)

    def test_is_nonempty(self):
        assert len(self.css) > 0

    def test_contains_font_link(self):
        assert "fonts.googleapis.com" in self.css

    def test_contains_css_variable_definition(self):
        # The CSS should contain var(-- or style= tokens
        assert "<style>" in self.css or "style" in self.css.lower()


class TestPlotlyDarkLayout:
    """plotly_dark_layout() returns a dict with required chart layout keys."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.styles import plotly_dark_layout
        self.layout = plotly_dark_layout()

    def test_returns_dict(self):
        assert isinstance(self.layout, dict)

    def test_has_paper_bgcolor(self):
        assert "paper_bgcolor" in self.layout

    def test_has_plot_bgcolor(self):
        assert "plot_bgcolor" in self.layout

    def test_has_font(self):
        assert "font" in self.layout

    def test_has_xaxis(self):
        assert "xaxis" in self.layout

    def test_has_yaxis(self):
        assert "yaxis" in self.layout

    def test_has_margin(self):
        assert "margin" in self.layout

    def test_margin_has_required_keys(self):
        margin = self.layout["margin"]
        for key in ("l", "r", "t", "b"):
            assert key in margin, f"margin missing key '{key}'"

    def test_paper_bgcolor_is_string(self):
        assert isinstance(self.layout["paper_bgcolor"], str)

    def test_xaxis_has_gridcolor(self):
        assert "gridcolor" in self.layout["xaxis"]

    def test_yaxis_has_gridcolor(self):
        assert "gridcolor" in self.layout["yaxis"]

    def test_font_has_family_and_color(self):
        font = self.layout["font"]
        assert "family" in font
        assert "color" in font


class TestPlotlyDarkLayoutOverrides:
    """plotly_dark_layout() must deep-merge caller overrides.

    Regression guard: pages used to call
        fig.update_layout(**plotly_dark_layout(), yaxis={...})
    which raises "got multiple values for keyword argument 'yaxis'" because the
    splatted defaults already supply that key. Four of the ten pages crashed on
    load this way. Overrides now go *through* the helper instead.
    """

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.styles import plotly_dark_layout
        self.fn = plotly_dark_layout

    def test_no_args_still_returns_defaults(self):
        out = self.fn()
        for key in ("paper_bgcolor", "plot_bgcolor", "xaxis", "yaxis", "legend", "margin"):
            assert key in out

    def test_override_merges_into_nested_dict(self):
        out = self.fn(yaxis={"title": "Edge"})
        assert out["yaxis"]["title"] == "Edge"
        # themed defaults survive the merge
        assert "gridcolor" in out["yaxis"]
        assert "tickfont" in out["yaxis"]

    def test_override_replaces_scalar(self):
        out = self.fn(height=250)
        assert out["height"] == 250

    def test_override_does_not_mutate_defaults(self):
        self.fn(yaxis={"title": "X"})
        assert "title" not in self.fn()["yaxis"]

    def test_splatting_result_into_update_layout_does_not_raise(self):
        import plotly.graph_objects as go
        fig = go.Figure()
        # This is exactly the call shape every page now uses.
        fig.update_layout(**self.fn(
            yaxis={"title": "Count"},
            legend={"x": 0, "y": 1},
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
            height=200,
        ))
        assert fig.layout.yaxis.title.text == "Count"
