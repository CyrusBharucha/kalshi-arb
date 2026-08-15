"""
tests/test_market_health_score.py
===================================
Tests for the _compute_market_health function in dashboard/pages/p03_markets.py.

No DB, no network. Pure logic.
"""
from __future__ import annotations

import sys
import pytest
from unittest.mock import MagicMock

# Patch streamlit and data_layer before importing the module
@pytest.fixture(autouse=True, scope="module")
def _patch_deps(request):
    st_mock = MagicMock()
    st_mock.cache_data = lambda *a, **kw: (lambda f: f)
    st_mock.cache_resource = lambda *a, **kw: (lambda f: f)
    dl_mock = MagicMock()
    dl_mock.get_open_markets = MagicMock(return_value=(MagicMock(), None))
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "streamlit", st_mock)
        mp.setitem(sys.modules, "dashboard.data_layer", dl_mock)
        mp.setitem(sys.modules, "dashboard.styles", MagicMock(
            GREEN="#22C55E", RED="#EF4444", AMBER="#F59E0B", BLUE="#3B82F6",
            TEXT="#F1F5F9", TEXT2="#94A3B8", TEXT3="#64748B",
            PANEL="#1E293B", BORDER="#334155", PANEL2="#0F172A",
        ))
        yield


def _health(yes_bid=0.44, yes_ask=0.46, spread=0.02, volume=5000,
            open_interest=1000, rel_count=2, has_arb=False):
    from dashboard.pages.p03_markets import _compute_market_health
    return _compute_market_health(
        yes_bid=yes_bid, yes_ask=yes_ask, spread=spread,
        volume=volume, open_interest=open_interest,
        rel_count=rel_count, has_arb=has_arb,
    )


class TestComputeMarketHealth:
    def test_returns_tuple(self):
        result = _health()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_score_in_range_0_to_100(self):
        score, _ = _health()
        assert 0 <= score <= 100

    def test_components_is_list(self):
        _, components = _health()
        assert isinstance(components, list)

    def test_perfect_market_high_score(self):
        score, _ = _health(
            yes_bid=0.49, yes_ask=0.51,  # tight
            spread=0.02, volume=50000, rel_count=5, has_arb=False,
        )
        assert score >= 65

    def test_no_bid_ask_penalized(self):
        score_good, _ = _health(yes_bid=0.44, yes_ask=0.46)
        score_bad, _  = _health(yes_bid=None, yes_ask=None)
        assert score_bad < score_good

    def test_tight_spread_scores_higher_than_wide(self):
        score_tight, _ = _health(spread=0.01)
        score_wide,  _ = _health(spread=0.15)
        assert score_tight > score_wide

    def test_high_volume_scores_higher(self):
        score_hi, _ = _health(volume=50000)
        score_lo, _ = _health(volume=10)
        assert score_hi > score_lo

    def test_has_arb_adds_bonus(self):
        score_arb,  _ = _health(has_arb=True)
        score_no,   _ = _health(has_arb=False)
        assert score_arb > score_no

    def test_has_relationships_adds_score(self):
        score_rels, _ = _health(rel_count=3)
        score_none, _ = _health(rel_count=0)
        assert score_rels > score_none

    def test_score_capped_at_100(self):
        score, _ = _health(
            yes_bid=0.49, yes_ask=0.51, spread=0.01,
            volume=100000, rel_count=10, has_arb=True,
        )
        assert score <= 100

    def test_no_quote_label_in_components_when_missing(self):
        _, components = _health(yes_bid=None, yes_ask=None)
        assert any("NO QUOTE" in c for c in components)

    def test_bid_ask_ok_label_in_components(self):
        _, components = _health(yes_bid=0.44, yes_ask=0.46)
        assert any("BID/ASK" in c for c in components)

    def test_arb_label_in_components_when_has_arb(self):
        _, components = _health(has_arb=True)
        assert any("ARB" in c for c in components)

    def test_vol_label_in_components_when_volume(self):
        _, components = _health(volume=5000)
        assert any("VOL" in c for c in components)

    def test_rels_label_in_components_when_rels(self):
        _, components = _health(rel_count=3)
        assert any("RELS" in c for c in components)

    def test_zero_volume_gives_no_vol_score(self):
        score_zero, _ = _health(volume=0)
        score_some, _ = _health(volume=1000)
        assert score_some > score_zero

    def test_none_spread_skips_spread_component(self):
        score_none, _ = _health(spread=None)
        score_good, _ = _health(spread=0.02)
        # Without spread data we get no spread score
        assert score_none <= score_good
