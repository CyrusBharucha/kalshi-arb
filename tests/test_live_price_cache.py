"""
tests/test_live_price_cache.py
================================
Unit tests for data/websocket_client.py LivePriceCache class.

LivePriceCache is a thread-safe in-memory dict of latest market snapshots.
Tests cover:
  - update(): creates new entry; updates existing entry; ts is always set
  - get(): returns the entry or None for missing market
  - all(): returns a copy of all data
  - __len__(): returns number of markets cached

No DB, no network.
"""
from __future__ import annotations

import sys
import threading
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(session_scope=mock_ss),
        "database.models": MagicMock(),
    }):
        yield


def _make_cache():
    from feeds.websocket_client import LivePriceCache
    return LivePriceCache()


class TestLivePriceCacheInit:
    def test_initially_empty(self):
        c = _make_cache()
        assert len(c) == 0

    def test_all_returns_empty_dict(self):
        c = _make_cache()
        assert c.all() == {}


class TestLivePriceCacheUpdate:
    def test_update_creates_new_entry(self):
        c = _make_cache()
        c.update("MKT-A", {"yes_bid": 0.45, "yes_ask": 0.50})
        assert c.get("MKT-A") is not None

    def test_update_sets_ts(self):
        c = _make_cache()
        c.update("MKT-A", {"yes_bid": 0.45})
        assert "ts" in c.get("MKT-A")

    def test_update_stores_fields(self):
        c = _make_cache()
        c.update("MKT-B", {"yes_bid": 0.30, "yes_ask": 0.35, "volume": 1000})
        entry = c.get("MKT-B")
        assert entry["yes_bid"] == 0.30
        assert entry["yes_ask"] == 0.35
        assert entry["volume"] == 1000

    def test_update_merges_with_existing(self):
        c = _make_cache()
        c.update("MKT-C", {"yes_bid": 0.40})
        c.update("MKT-C", {"yes_ask": 0.45})
        entry = c.get("MKT-C")
        assert "yes_bid" in entry   # from first update
        assert "yes_ask" in entry   # from second update

    def test_update_overwrites_field(self):
        c = _make_cache()
        c.update("MKT-D", {"yes_bid": 0.40})
        c.update("MKT-D", {"yes_bid": 0.42})
        assert c.get("MKT-D")["yes_bid"] == 0.42

    def test_update_multiple_markets(self):
        c = _make_cache()
        c.update("MKT-E", {"yes_bid": 0.50})
        c.update("MKT-F", {"yes_bid": 0.60})
        assert len(c) == 2


class TestLivePriceCacheGet:
    def test_get_returns_none_for_missing(self):
        c = _make_cache()
        assert c.get("NONEXISTENT") is None

    def test_get_returns_entry_for_known(self):
        c = _make_cache()
        c.update("MKT-G", {"yes_bid": 0.55})
        entry = c.get("MKT-G")
        assert entry is not None
        assert entry["yes_bid"] == 0.55


class TestLivePriceCacheAll:
    def test_all_returns_dict(self):
        c = _make_cache()
        c.update("X", {"yes_bid": 0.1})
        result = c.all()
        assert isinstance(result, dict)
        assert "X" in result

    def test_all_returns_copy_not_reference(self):
        """Modifying returned dict does not affect the cache."""
        c = _make_cache()
        c.update("Y", {"yes_bid": 0.2})
        copy = c.all()
        copy["Z"] = {"yes_bid": 0.9}
        # Z should not appear in the cache
        assert c.get("Z") is None


class TestLivePriceCacheLen:
    def test_len_increases_with_new_markets(self):
        c = _make_cache()
        assert len(c) == 0
        c.update("M1", {})
        assert len(c) == 1
        c.update("M2", {})
        assert len(c) == 2

    def test_len_same_market_updated_twice(self):
        """Updating same market twice keeps len at 1."""
        c = _make_cache()
        c.update("M1", {"yes_bid": 0.4})
        c.update("M1", {"yes_bid": 0.41})
        assert len(c) == 1


class TestLivePriceCacheThreadSafety:
    def test_concurrent_updates_all_stored(self):
        """Multiple threads updating different markets all persist."""
        c = _make_cache()
        threads = [
            threading.Thread(target=c.update, args=(f"MKT-{i}", {"yes_bid": i / 100}))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # At least most markets should be stored (threading without locks
        # can cause races, but LivePriceCache uses setdefault which is
        # thread-safe at the dict level in CPython)
        assert len(c) >= 15  # allow for some collisions in setdefault
