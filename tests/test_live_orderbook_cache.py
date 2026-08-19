"""
tests/test_live_orderbook_cache.py
=====================================
Unit tests for data/synthesis_live.py LiveOrderbookCache:
  - apply_delta: creates TopOfBook entry from delta dict
  - get: returns None for unknown market, TopOfBook for known
  - snapshot: returns copy of all books
  - sequence guard: stale delta (lower seq) is ignored
  - callback registration and invocation
  - __len__

No DB, no network.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest
import sys


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


class TestLiveOrderbookCache:
    """LiveOrderbookCache: in-memory orderbook cache."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from feeds.synthesis_live import LiveOrderbookCache
        self.cls = LiveOrderbookCache

    def _delta(self, mid="KXTEST-YES", seq=1, yes_bid=0.40, yes_ask=0.45):
        return {
            "market_id":     mid,
            "sequence":      seq,
            "yes_best_bid":  yes_bid,
            "yes_best_ask":  yes_ask,
            "no_best_bid":   1.0 - yes_ask,
            "no_best_ask":   1.0 - yes_bid,
        }

    def test_initially_empty(self):
        cache = self.cls()
        assert len(cache) == 0

    def test_apply_delta_adds_book(self):
        cache = self.cls()
        cache.apply_delta(self._delta())
        assert len(cache) == 1

    def test_get_unknown_returns_none(self):
        cache = self.cls()
        assert cache.get("KXUNKNOWN") is None

    def test_get_known_returns_book(self):
        cache = self.cls()
        cache.apply_delta(self._delta("KXBOC-YES", seq=1))
        book = cache.get("KXBOC-YES")
        assert book is not None
        assert book.market_id == "KXBOC-YES"

    def test_yes_bid_stored_correctly(self):
        cache = self.cls()
        cache.apply_delta(self._delta(yes_bid=0.55))
        book = cache.get("KXTEST-YES")
        assert abs(book.yes_bid - 0.55) < 1e-9

    def test_yes_ask_stored_correctly(self):
        cache = self.cls()
        cache.apply_delta(self._delta(yes_ask=0.60))
        book = cache.get("KXTEST-YES")
        assert abs(book.yes_ask - 0.60) < 1e-9

    def test_missing_market_id_returns_none(self):
        cache = self.cls()
        result = cache.apply_delta({})
        assert result is None

    def test_stale_sequence_ignored(self):
        cache = self.cls()
        # Apply seq=10 first
        cache.apply_delta(self._delta(seq=10, yes_bid=0.50))
        # Apply seq=5 (stale) — should be ignored
        result = cache.apply_delta(self._delta(seq=5, yes_bid=0.99))
        assert result is None
        # Bid should still be 0.50 from seq=10
        book = cache.get("KXTEST-YES")
        assert abs(book.yes_bid - 0.50) < 1e-9

    def test_newer_sequence_updates(self):
        cache = self.cls()
        cache.apply_delta(self._delta(seq=5, yes_bid=0.50))
        cache.apply_delta(self._delta(seq=10, yes_bid=0.65))
        book = cache.get("KXTEST-YES")
        assert abs(book.yes_bid - 0.65) < 1e-9

    def test_snapshot_returns_all_books(self):
        cache = self.cls()
        cache.apply_delta(self._delta("MKT-A", seq=1))
        cache.apply_delta(self._delta("MKT-B", seq=1))
        snap = cache.snapshot()
        assert "MKT-A" in snap
        assert "MKT-B" in snap

    def test_snapshot_is_copy(self):
        cache = self.cls()
        cache.apply_delta(self._delta("MKT-X", seq=1))
        snap = cache.snapshot()
        snap["NEW_KEY"] = object()
        assert "NEW_KEY" not in cache.snapshot()

    def test_len_grows_with_new_markets(self):
        cache = self.cls()
        cache.apply_delta(self._delta("MKT-1", seq=1))
        cache.apply_delta(self._delta("MKT-2", seq=1))
        assert len(cache) == 2

    def test_len_same_market_update_doesnt_grow(self):
        cache = self.cls()
        cache.apply_delta(self._delta("MKT-X", seq=1))
        cache.apply_delta(self._delta("MKT-X", seq=2))
        assert len(cache) == 1

    def test_callback_called_on_apply_delta(self):
        cache = self.cls()
        calls = []
        cache.register_callback(lambda book: calls.append(book.market_id))
        cache.apply_delta(self._delta("KXCB-YES", seq=1))
        assert calls == ["KXCB-YES"]

    def test_callback_not_called_on_stale(self):
        cache = self.cls()
        calls = []
        cache.register_callback(lambda book: calls.append(book))
        cache.apply_delta(self._delta(seq=10))
        cache.apply_delta(self._delta(seq=5))  # stale — callback should NOT fire
        assert len(calls) == 1  # only first delta

    def test_callback_exception_does_not_propagate(self):
        cache = self.cls()

        def bad_callback(book):
            raise RuntimeError("callback error")

        cache.register_callback(bad_callback)
        # Should not raise
        cache.apply_delta(self._delta(seq=1))
