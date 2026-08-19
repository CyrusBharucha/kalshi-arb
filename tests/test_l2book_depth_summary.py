"""
tests/test_l2book_depth_summary.py
=====================================
Unit tests for L2Book.depth(), L2Book.summary(), L2Cache.stats()
and L2Cache.top_of_book() from data/orderbook_l2.py.

No DB, no network.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
    }):
        yield


def _make_book(market_id="MKT-A"):
    from feeds.orderbook_l2 import L2Book
    return L2Book(market_id=market_id)


def _apply_level(book, side, price, qty, best_bid=None, best_ask=None):
    from feeds.orderbook_l2 import SideBook
    sb: SideBook = getattr(book, side)
    sb.apply_level(price, qty, best_bid=best_bid, best_ask=best_ask)


# ---------------------------------------------------------------------------
# L2Book.depth()
# ---------------------------------------------------------------------------

class TestL2BookDepth:
    def test_returns_dict(self):
        book = _make_book()
        assert isinstance(book.depth(), dict)

    def test_empty_book_all_zeros(self):
        book = _make_book()
        d = book.depth()
        assert d["yes_bids"] == 0
        assert d["yes_asks"] == 0
        assert d["no_bids"] == 0
        assert d["no_asks"] == 0
        assert d["total"] == 0

    def test_has_required_keys(self):
        book = _make_book()
        d = book.depth()
        for key in ("yes_bids", "yes_asks", "no_bids", "no_asks", "total"):
            assert key in d

    def test_yes_bids_counted(self):
        book = _make_book()
        # Add two bid levels to YES side
        book.yes.bids[0.40] = 100
        book.yes.bids[0.35] = 50
        d = book.depth()
        assert d["yes_bids"] == 2

    def test_total_is_sum_of_all_sides(self):
        book = _make_book()
        book.yes.bids[0.40] = 100
        book.yes.asks[0.45] = 50
        book.no.bids[0.55] = 80
        d = book.depth()
        assert d["total"] == 3


# ---------------------------------------------------------------------------
# L2Book.summary()
# ---------------------------------------------------------------------------

class TestL2BookSummary:
    def test_returns_string(self):
        book = _make_book()
        assert isinstance(book.summary(), str)

    def test_contains_market_id(self):
        book = _make_book("TEST-MKT")
        assert "TEST-MKT" in book.summary()

    def test_contains_seq(self):
        book = _make_book()
        book.sequence = 42
        summary = book.summary()
        assert "42" in summary

    def test_contains_yes(self):
        book = _make_book()
        summary = book.summary()
        assert "YES" in summary

    def test_contains_no(self):
        book = _make_book()
        summary = book.summary()
        assert "NO" in summary

    def test_question_marks_when_no_levels(self):
        book = _make_book()
        summary = book.summary()
        assert "?" in summary

    def test_prices_shown_when_levels_populated(self):
        book = _make_book()
        book.yes.bids[0.40] = 100
        book.yes.best_bid = 0.40
        book.yes.asks[0.45] = 50
        book.yes.best_ask = 0.45
        summary = book.summary()
        assert "0.40" in summary


# ---------------------------------------------------------------------------
# L2Cache.stats()
# ---------------------------------------------------------------------------

class TestL2CacheStats:
    def _make_cache(self):
        from feeds.orderbook_l2 import L2Cache
        return L2Cache()

    def test_returns_dict(self):
        cache = self._make_cache()
        assert isinstance(cache.stats(), dict)

    def test_initial_total_deltas_zero(self):
        cache = self._make_cache()
        s = cache.stats()
        assert s["total_deltas"] == 0

    def test_initial_stale_discarded_zero(self):
        cache = self._make_cache()
        assert cache.stats()["stale_discarded"] == 0

    def test_initial_markets_seen_zero(self):
        cache = self._make_cache()
        assert cache.stats()["markets_seen"] == 0

    def test_stats_increments_after_delta(self):
        cache = self._make_cache()
        cache.apply_delta({
            "market_id": "MKT-A",
            "sequence": 1,
            "yes": [{"price": 40, "quantity": 100, "yes_best_bid": 40, "yes_best_ask": 45}],
            "no": [],
        })
        s = cache.stats()
        assert s["total_deltas"] >= 1

    def test_stats_is_copy(self):
        """Mutating the returned dict should not affect the cache."""
        cache = self._make_cache()
        s = cache.stats()
        s["total_deltas"] = 9999
        assert cache.stats()["total_deltas"] != 9999


# ---------------------------------------------------------------------------
# L2Cache.top_of_book()
# ---------------------------------------------------------------------------

class TestL2CacheTopOfBook:
    def _make_cache(self):
        from feeds.orderbook_l2 import L2Cache
        return L2Cache()

    def test_returns_none_for_missing_market(self):
        cache = self._make_cache()
        assert cache.top_of_book("MISSING") is None

    def test_returns_dict_after_delta(self):
        cache = self._make_cache()
        cache.apply_delta({
            "market_id": "MKT-A",
            "sequence": 1,
            "yes": [{"price": 40, "quantity": 100, "yes_best_bid": 40, "yes_best_ask": 45}],
            "no": [],
        })
        result = cache.top_of_book("MKT-A")
        assert isinstance(result, dict)

    def test_top_of_book_has_required_keys(self):
        cache = self._make_cache()
        cache.apply_delta({
            "market_id": "MKT-B",
            "sequence": 1,
            "yes": [{"price": 40, "quantity": 100, "yes_best_bid": 40, "yes_best_ask": 45}],
            "no": [],
        })
        result = cache.top_of_book("MKT-B")
        if result is not None:
            for key in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
                assert key in result
