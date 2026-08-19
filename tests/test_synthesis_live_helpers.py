"""
tests/test_synthesis_live_helpers.py
======================================
Unit tests for data/synthesis_live.py pure-logic helpers:
  - _kalshi_fee(): fee formula
  - TopOfBook: midpoint_yes, executable_cost_yes/no
  - LiveOrderbookCache: apply_delta, get, snapshot, len, register_callback
"""
from __future__ import annotations

import sys
import time
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(
            DB_URL="postgresql://localhost/test",
            KALSHI_READ_RPS=10.0,
            KALSHI_WRITE_RPS=1.0,
            KALSHI_KEY_ID="",
            KALSHI_PRIVKEY_PATH="",
        ),
        "feeds.kalshi_client": MagicMock(),
        "feeds.websocket_client": MagicMock(),
        "engine.relationship_detector": MagicMock(),
    }):
        yield


# ---------------------------------------------------------------------------
# _kalshi_fee
# ---------------------------------------------------------------------------

class TestKalshiFee:
    def test_returns_float(self):
        from feeds.synthesis_live import _kalshi_fee
        assert isinstance(_kalshi_fee(0.5), float)

    def test_symmetric_around_half(self):
        """Fee at 0.3 equals fee at 0.7 because P*(1-P) is symmetric."""
        from feeds.synthesis_live import _kalshi_fee
        assert _kalshi_fee(0.3) == _kalshi_fee(0.7)

    def test_maximum_at_half(self):
        """P*(1-P) is maximized at P=0.5."""
        from feeds.synthesis_live import _kalshi_fee
        fee_half = _kalshi_fee(0.5)
        fee_other = _kalshi_fee(0.3)
        assert fee_half >= fee_other

    def test_zero_prob_zero_fee(self):
        from feeds.synthesis_live import _kalshi_fee
        assert _kalshi_fee(0.0) == pytest.approx(0.0)

    def test_one_prob_zero_fee(self):
        from feeds.synthesis_live import _kalshi_fee
        assert _kalshi_fee(1.0) == pytest.approx(0.0)

    def test_fee_capped_at_max(self):
        """Fee should never exceed MAX_FEE."""
        from feeds.synthesis_live import _kalshi_fee, MAX_FEE
        assert _kalshi_fee(0.5) <= MAX_FEE

    def test_fee_non_negative(self):
        from feeds.synthesis_live import _kalshi_fee
        for p in [0.1, 0.25, 0.5, 0.75, 0.9]:
            assert _kalshi_fee(p) >= 0.0


# ---------------------------------------------------------------------------
# TopOfBook
# ---------------------------------------------------------------------------

class TestTopOfBook:
    def _make_book(self, yes_bid=0.40, yes_ask=0.45, no_bid=0.55, no_ask=0.60):
        from feeds.synthesis_live import TopOfBook
        return TopOfBook(
            market_id="MKT-TEST",
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
        )

    def test_midpoint_yes_correct(self):
        book = self._make_book(yes_bid=0.40, yes_ask=0.44)
        assert book.midpoint_yes() == pytest.approx(0.42)

    def test_midpoint_yes_returns_float(self):
        book = self._make_book()
        assert isinstance(book.midpoint_yes(), float)

    def test_executable_cost_yes_equals_ask(self):
        book = self._make_book(yes_ask=0.45)
        assert book.executable_cost_yes() == pytest.approx(0.45)

    def test_executable_cost_no_equals_no_ask(self):
        book = self._make_book(no_ask=0.60)
        assert book.executable_cost_no() == pytest.approx(0.60)

    def test_default_sequence_zero(self):
        from feeds.synthesis_live import TopOfBook
        book = TopOfBook(market_id="X")
        assert book.sequence == 0

    def test_updated_at_is_recent(self):
        book = self._make_book()
        assert time.time() - book.updated_at < 5.0


# ---------------------------------------------------------------------------
# LiveOrderbookCache
# ---------------------------------------------------------------------------

class TestLiveOrderbookCache:
    def _make_cache(self):
        from feeds.synthesis_live import LiveOrderbookCache
        return LiveOrderbookCache()

    def _delta(self, mid="MKT-A", seq=1, yb=0.40, ya=0.45, nb=0.55, na=0.60):
        return {
            "market_id": mid, "sequence": seq,
            "yes_best_bid": yb, "yes_best_ask": ya,
            "no_best_bid": nb, "no_best_ask": na,
        }

    def test_initial_len_zero(self):
        cache = self._make_cache()
        assert len(cache) == 0

    def test_apply_delta_returns_book(self):
        from feeds.synthesis_live import TopOfBook
        cache = self._make_cache()
        result = cache.apply_delta(self._delta())
        assert isinstance(result, TopOfBook)

    def test_apply_delta_increments_len(self):
        cache = self._make_cache()
        cache.apply_delta(self._delta("MKT-A"))
        cache.apply_delta(self._delta("MKT-B"))
        assert len(cache) == 2

    def test_apply_delta_same_market_no_duplicate(self):
        cache = self._make_cache()
        cache.apply_delta(self._delta("MKT-A", seq=1))
        cache.apply_delta(self._delta("MKT-A", seq=2))
        assert len(cache) == 1

    def test_apply_delta_stale_ignored(self):
        """Delta with lower seq than current should be ignored."""
        cache = self._make_cache()
        cache.apply_delta(self._delta("MKT-A", seq=5, yb=0.50))
        result = cache.apply_delta(self._delta("MKT-A", seq=3, yb=0.10))
        assert result is None
        # The book should still reflect the higher-seq update
        assert cache.get("MKT-A").yes_bid == pytest.approx(0.50)

    def test_apply_delta_missing_market_id_returns_none(self):
        cache = self._make_cache()
        result = cache.apply_delta({"sequence": 1, "yes_best_bid": 0.40})
        assert result is None

    def test_get_returns_none_for_missing(self):
        cache = self._make_cache()
        assert cache.get("MISSING") is None

    def test_get_returns_book_after_apply(self):
        from feeds.synthesis_live import TopOfBook
        cache = self._make_cache()
        cache.apply_delta(self._delta("MKT-A", yb=0.42))
        book = cache.get("MKT-A")
        assert isinstance(book, TopOfBook)
        assert book.yes_bid == pytest.approx(0.42)

    def test_snapshot_returns_dict(self):
        cache = self._make_cache()
        cache.apply_delta(self._delta("MKT-A"))
        snap = cache.snapshot()
        assert isinstance(snap, dict)
        assert "MKT-A" in snap

    def test_snapshot_is_copy(self):
        """Modifying the snapshot should not affect the cache."""
        cache = self._make_cache()
        cache.apply_delta(self._delta("MKT-A"))
        snap = cache.snapshot()
        snap["FAKE"] = "junk"
        assert "FAKE" not in cache.snapshot()

    def test_register_callback_called_on_update(self):
        cache = self._make_cache()
        calls = []
        cache.register_callback(lambda book: calls.append(book.market_id))
        cache.apply_delta(self._delta("MKT-A"))
        assert calls == ["MKT-A"]

    def test_callback_error_does_not_raise(self):
        """Exceptions in callbacks should be swallowed."""
        cache = self._make_cache()
        cache.register_callback(lambda book: (_ for _ in ()).throw(RuntimeError("bad")))
        # Should not raise
        cache.apply_delta(self._delta("MKT-A"))
