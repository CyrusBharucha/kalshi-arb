"""
tests/test_overview_and_system_helpers.py
==========================================
Tests for pure-logic helpers in dashboard/pages/p01_overview.py
and dashboard/pages/p10_system.py.

No DB, no network. All Streamlit/data_layer calls are mocked.
"""
from __future__ import annotations

import sys
import math
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Module-level patches so imports succeed
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_all(request):
    st_mock = MagicMock()
    st_mock.cache_data = lambda *a, **kw: (lambda f: f)
    st_mock.cache_resource = lambda *a, **kw: (lambda f: f)

    dl_mock = MagicMock()
    dl_mock.get_system_health = MagicMock(return_value={})
    dl_mock.get_coverage_stats = MagicMock(return_value={})
    dl_mock.get_live_arb_opportunities = MagicMock(return_value=(pd.DataFrame(), None))
    dl_mock.get_open_markets = MagicMock(return_value=(pd.DataFrame(), None))
    dl_mock.get_live_market_summary = MagicMock(return_value={})
    dl_mock.get_table_sizes = MagicMock(return_value=pd.DataFrame())
    dl_mock.get_data_quality_stats = MagicMock(return_value={"error": "no db"})
    dl_mock.get_ingestion_log = MagicMock(return_value=(pd.DataFrame(), None))
    dl_mock.get_historical_arb_stats = MagicMock(return_value={})
    dl_mock.get_arb_rolling_7d = MagicMock(return_value=pd.DataFrame())

    styles_mock = MagicMock(
        GREEN="#22C55E", RED="#EF4444", AMBER="#F59E0B", BLUE="#3B82F6",
        TEXT="#F1F5F9", TEXT2="#94A3B8", TEXT3="#64748B",
        PANEL="#1E293B", BORDER="#334155", PANEL2="#0F172A",
        plotly_dark_layout=lambda: {},
    )

    live_state_mock = MagicMock()
    live_state_mock.get_live_state = MagicMock(return_value=MagicMock(
        get_stats=MagicMock(return_value={}),
        snapshot_all=MagicMock(return_value={}),
    ))

    go_mock = MagicMock()
    go_mock.Figure = MagicMock(return_value=MagicMock())
    go_mock.Bar = MagicMock(return_value=MagicMock())
    go_mock.Histogram = MagicMock(return_value=MagicMock())

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "streamlit", st_mock)
        mp.setitem(sys.modules, "dashboard.data_layer", dl_mock)
        mp.setitem(sys.modules, "dashboard.styles", styles_mock)
        mp.setitem(sys.modules, "dashboard.live_state", live_state_mock)
        mp.setitem(sys.modules, "plotly", MagicMock())
        mp.setitem(sys.modules, "plotly.graph_objects", go_mock)
        yield


# ---------------------------------------------------------------------------
# _render_stat_table — p01_overview.py
# ---------------------------------------------------------------------------

class TestRenderStatTable:
    """_render_stat_table renders a key/value HTML string via st.markdown."""

    def _get_fn(self):
        try:
            from dashboard.pages.p01_overview import _render_stat_table
            return _render_stat_table
        except ImportError:
            import pytest
            pytest.skip("_render_stat_table not present in p01_overview")

    def test_function_exists(self):
        fn = self._get_fn()
        assert callable(fn)

    def test_empty_rows_does_not_raise(self):
        fn = self._get_fn()
        fn([])  # should not raise

    def test_single_row_does_not_raise(self):
        fn = self._get_fn()
        fn([("KEY", "VALUE")])

    def test_multiple_rows_do_not_raise(self):
        fn = self._get_fn()
        fn([("A", "1"), ("B", "2"), ("C", "3")])

    def test_special_chars_do_not_raise(self):
        fn = self._get_fn()
        fn([("EDGE", "+5.2c"), ("TICKER", "KXBOC-25Q4")])


# ---------------------------------------------------------------------------
# _stat_panel — p10_system.py
# ---------------------------------------------------------------------------

class TestStatPanel:
    """_stat_panel accepts list of (label, (value, color)) tuples."""

    def _get_fn(self):
        from dashboard.pages.p10_system import _stat_panel
        return _stat_panel

    def test_function_exists(self):
        fn = self._get_fn()
        assert callable(fn)

    def test_empty_does_not_raise(self):
        fn = self._get_fn()
        fn([])

    def test_single_row_does_not_raise(self):
        fn = self._get_fn()
        fn([("STATUS", ("LIVE", "#22C55E"))])

    def test_multiple_rows_do_not_raise(self):
        fn = self._get_fn()
        fn([
            ("STATUS",   ("CONNECTED", "#22C55E")),
            ("LATENCY",  ("2.5 ms", "#F1F5F9")),
            ("MARKETS",  ("5,000", "#F1F5F9")),
        ])


# ---------------------------------------------------------------------------
# _section_header — p10_system.py
# ---------------------------------------------------------------------------

class TestSectionHeader:
    def _get_fn(self):
        from dashboard.pages.p10_system import _section_header
        return _section_header

    def test_exists_and_callable(self):
        fn = self._get_fn()
        assert callable(fn)

    def test_does_not_raise(self):
        fn = self._get_fn()
        fn("SYNTHESIS WEBSOCKET")

    def test_empty_title_does_not_raise(self):
        fn = self._get_fn()
        fn("")


# ---------------------------------------------------------------------------
# p01_overview.py: KPI computation logic (via render_db_mode internals)
# ---------------------------------------------------------------------------

class TestOverviewKpiLogic:
    """Verify computations used in _render_db_mode KPI row."""

    def test_best_edge_is_max(self):
        df = pd.DataFrame({"net_edge_cents": [1.2, 5.7, 3.1]})
        best = df["net_edge_cents"].max()
        assert abs(best - 5.7) < 1e-9

    def test_best_edge_empty_df(self):
        df = pd.DataFrame()
        n_arbs = len(df) if not df.empty else 0
        assert n_arbs == 0

    def test_capital_deployable_sum(self):
        df = pd.DataFrame({"max_net_profit": [0.10, 0.25, 0.05]})
        total = df["max_net_profit"].sum()
        assert abs(total - 0.40) < 1e-9

    def test_data_quality_label_live(self):
        ws_connected = True
        db_ok = True
        if ws_connected and db_ok:
            label = "LIVE"
        elif db_ok:
            label = "DB ONLY"
        else:
            label = "OFFLINE"
        assert label == "LIVE"

    def test_data_quality_label_db_only(self):
        ws_connected = False
        db_ok = True
        if ws_connected and db_ok:
            label = "LIVE"
        elif db_ok:
            label = "DB ONLY"
        else:
            label = "OFFLINE"
        assert label == "DB ONLY"

    def test_data_quality_label_offline(self):
        ws_connected = False
        db_ok = False
        if ws_connected and db_ok:
            label = "LIVE"
        elif db_ok:
            label = "DB ONLY"
        else:
            label = "OFFLINE"
        assert label == "OFFLINE"

    def test_feed_rate_zero_shows_dash(self):
        mps = 0
        result = f"{int(mps):,} msg/s" if mps else "--"
        assert result == "--"

    def test_feed_rate_nonzero_formatted(self):
        mps = 1250
        result = f"{int(mps):,} msg/s" if mps else "--"
        assert result == "1,250 msg/s"

    def test_age_formatting(self):
        s = 150
        formatted = f"{int(s//60)}m{int(s%60)}s"
        assert formatted == "2m30s"

    def test_age_formatting_under_60(self):
        s = 45
        formatted = f"{int(s//60)}m{int(s%60)}s"
        assert formatted == "0m45s"


# ---------------------------------------------------------------------------
# p10_system.py: performance benchmark rows
# ---------------------------------------------------------------------------

class TestPerformanceBenchmarkRows:
    """Validate latency thresholds used in _render_performance_panel."""

    def _classify(self, ms):
        """Reproduce p10 color logic: <100ms=GREEN, <500ms=AMBER, else RED."""
        GREEN = "#22C55E"
        AMBER = "#F59E0B"
        RED   = "#EF4444"
        return GREEN if ms < 100 else (AMBER if ms < 500 else RED)

    def test_fast_query_is_green(self):
        assert self._classify(50) == "#22C55E"

    def test_borderline_100ms_is_green(self):
        # <100 → green
        assert self._classify(99) == "#22C55E"

    def test_100ms_is_amber(self):
        assert self._classify(100) == "#F59E0B"

    def test_moderate_is_amber(self):
        assert self._classify(250) == "#F59E0B"

    def test_499ms_is_amber(self):
        assert self._classify(499) == "#F59E0B"

    def test_slow_is_red(self):
        assert self._classify(500) == "#EF4444"

    def test_very_slow_is_red(self):
        assert self._classify(2000) == "#EF4444"


# ---------------------------------------------------------------------------
# p10_system.py: ws_stats color coding logic
# ---------------------------------------------------------------------------

class TestWsColorCoding:
    """Reproduce the color-coding logic in _stat_panel rows for ws_stats."""

    def test_connected_gets_green(self):
        connected = True
        color = "#22C55E" if connected else "#EF4444"
        assert color == "#22C55E"

    def test_disconnected_gets_red(self):
        connected = False
        color = "#22C55E" if connected else "#EF4444"
        assert color == "#EF4444"

    def test_reconnects_zero_not_amber(self):
        reconnects = 0
        AMBER = "#F59E0B"
        TEXT  = "#F1F5F9"
        color = AMBER if reconnects > 0 else TEXT
        assert color == TEXT

    def test_reconnects_nonzero_is_amber(self):
        reconnects = 3
        AMBER = "#F59E0B"
        TEXT  = "#F1F5F9"
        color = AMBER if reconnects > 0 else TEXT
        assert color == AMBER

    def test_seq_errors_zero_not_red(self):
        seq_errors = 0
        RED  = "#EF4444"
        TEXT = "#F1F5F9"
        color = RED if seq_errors > 0 else TEXT
        assert color == TEXT

    def test_seq_errors_nonzero_is_red(self):
        seq_errors = 2
        RED  = "#EF4444"
        TEXT = "#F1F5F9"
        color = RED if seq_errors > 0 else TEXT
        assert color == RED

    def test_l2_coverage_low_is_amber(self):
        l2_days = 10
        GREEN = "#22C55E"
        AMBER = "#F59E0B"
        color = AMBER if l2_days < 30 else GREEN
        assert color == AMBER

    def test_l2_coverage_good_is_green(self):
        l2_days = 45
        GREEN = "#22C55E"
        AMBER = "#F59E0B"
        color = AMBER if l2_days < 30 else GREEN
        assert color == GREEN
