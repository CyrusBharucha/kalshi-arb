"""
tests/test_orderbook_page_helpers.py
======================================
Tests for pure-logic helpers in dashboard/pages/p04_orderbook.py:
  - _bar: generates an HTML bar span
  - Liquidity metric computations (imbalance, bid/ask totals)

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
    dl_mock.get_live_orderbook = MagicMock(return_value=({}, None))

    styles_mock = MagicMock(
        GREEN="#22C55E", RED="#EF4444", AMBER="#F59E0B", BLUE="#3B82F6",
        TEXT="#F1F5F9", TEXT2="#94A3B8", TEXT3="#64748B",
        PANEL="#1E293B", BORDER="#334155", PANEL2="#0F172A",
        plotly_dark_layout=lambda: {},
    )

    go_mock = MagicMock()
    go_mock.Figure = MagicMock(return_value=MagicMock())
    go_mock.Scatter = MagicMock(return_value=MagicMock())

    ls_mock = MagicMock()
    ls_mock.get_live_state = MagicMock(return_value=MagicMock(
        get_stats=MagicMock(return_value={}),
        snapshot=MagicMock(return_value=None),
    ))

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "streamlit", st_mock)
        mp.setitem(sys.modules, "dashboard.data_layer", dl_mock)
        mp.setitem(sys.modules, "dashboard.styles", styles_mock)
        mp.setitem(sys.modules, "dashboard.live_state", ls_mock)
        mp.setitem(sys.modules, "plotly", MagicMock())
        mp.setitem(sys.modules, "plotly.graph_objects", go_mock)
        yield


# ---------------------------------------------------------------------------
# _bar
# ---------------------------------------------------------------------------

class TestBar:
    def _call(self, size, max_size, side="bid"):
        from dashboard.pages.p04_orderbook import _bar
        return _bar(size, max_size, side)

    def test_returns_string(self):
        assert isinstance(self._call(10, 100), str)

    def test_zero_max_size_returns_zero_width(self):
        result = self._call(10, 0)
        assert "width:0px" in result

    def test_bid_side_contains_green(self):
        result = self._call(50, 100, side="bid")
        assert "22C55E" in result or "green" in result.lower() or "34,197,94" in result

    def test_ask_side_contains_red(self):
        result = self._call(50, 100, side="ask")
        assert "EF4444" in result or "239,68,68" in result

    def test_full_bar_is_40px(self):
        # 100% fill → 40px
        result = self._call(100, 100, side="bid")
        assert "width:40px" in result

    def test_half_bar_is_20px(self):
        result = self._call(50, 100, side="bid")
        assert "width:20px" in result

    def test_exceeding_max_capped_at_40px(self):
        result = self._call(1000, 100, side="bid")
        assert "width:40px" in result

    def test_bid_direction_is_right(self):
        result = self._call(10, 100, side="bid")
        assert "border-right" in result

    def test_ask_direction_is_left(self):
        result = self._call(10, 100, side="ask")
        assert "border-left" in result


# ---------------------------------------------------------------------------
# Liquidity metric computations (reproduced from _render_liquidity_metrics)
# ---------------------------------------------------------------------------

class TestLiquidityMetrics:
    def _compute(self, bids, asks, mid=0.50):
        bid_total = sum(b[1] for b in bids if len(b) > 1 and b[1])
        ask_total = sum(a[1] for a in asks if len(a) > 1 and a[1])
        imbalance = (bid_total - ask_total) / max(bid_total + ask_total, 1)
        return bid_total, ask_total, imbalance

    def test_equal_sides_zero_imbalance(self):
        bids = [[0.49, 100], [0.48, 100]]
        asks = [[0.51, 100], [0.52, 100]]
        _, _, imbalance = self._compute(bids, asks)
        assert abs(imbalance) < 1e-9

    def test_bid_heavy_positive_imbalance(self):
        bids = [[0.49, 1000]]
        asks = [[0.51, 100]]
        _, _, imbalance = self._compute(bids, asks)
        assert imbalance > 0

    def test_ask_heavy_negative_imbalance(self):
        bids = [[0.49, 100]]
        asks = [[0.51, 1000]]
        _, _, imbalance = self._compute(bids, asks)
        assert imbalance < 0

    def test_imbalance_in_minus1_to_plus1(self):
        bids = [[0.49, 500]]
        asks = [[0.51, 200]]
        _, _, imbalance = self._compute(bids, asks)
        assert -1.0 <= imbalance <= 1.0

    def test_empty_bids_negative_imbalance(self):
        _, _, imbalance = self._compute([], [[0.51, 100]])
        assert imbalance == -1.0

    def test_empty_asks_positive_imbalance(self):
        _, _, imbalance = self._compute([[0.49, 100]], [])
        assert imbalance == 1.0

    def test_both_empty_zero_imbalance(self):
        _, _, imbalance = self._compute([], [])
        assert abs(imbalance) < 1e-9

    def test_bid_total_correct(self):
        bids = [[0.49, 100], [0.48, 200]]
        bid_total, _, _ = self._compute(bids, [])
        assert bid_total == 300

    def test_ask_total_correct(self):
        asks = [[0.51, 150], [0.52, 250]]
        _, ask_total, _ = self._compute([], asks)
        assert ask_total == 400

    def test_single_element_list_skipped(self):
        # Lists of length 1 don't have qty at index 1 → skipped
        bids = [[0.49]]  # only price, no qty
        bid_total, _, _ = self._compute(bids, [])
        assert bid_total == 0


# ---------------------------------------------------------------------------
# Depth table logic (bids sorted desc, asks sorted asc)
# ---------------------------------------------------------------------------

class TestDepthTableSorting:
    def test_bids_sorted_descending(self):
        bids = [[0.48, 100], [0.50, 50], [0.45, 200]]
        sorted_bids = sorted(bids, key=lambda x: x[0], reverse=True)[:10]
        prices = [b[0] for b in sorted_bids]
        assert prices == sorted(prices, reverse=True)

    def test_asks_sorted_ascending(self):
        asks = [[0.55, 100], [0.51, 50], [0.60, 200]]
        sorted_asks = sorted(asks, key=lambda x: x[0])[:10]
        prices = [a[0] for a in sorted_asks]
        assert prices == sorted(prices)

    def test_top_10_bids_only(self):
        bids = [[0.50 - i * 0.01, 100] for i in range(15)]
        sorted_bids = sorted(bids, key=lambda x: x[0], reverse=True)[:10]
        assert len(sorted_bids) == 10

    def test_best_bid_is_first(self):
        bids = [[0.45, 100], [0.49, 50], [0.48, 200]]
        sorted_bids = sorted(bids, key=lambda x: x[0], reverse=True)[:10]
        assert sorted_bids[0][0] == 0.49

    def test_best_ask_is_first(self):
        asks = [[0.55, 100], [0.51, 50], [0.53, 200]]
        sorted_asks = sorted(asks, key=lambda x: x[0])[:10]
        assert sorted_asks[0][0] == 0.51
