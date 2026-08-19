"""
tests/test_l2_reconstruction.py
================================
Unit tests for data/orderbook_l2.py — SideBook, L2Book, L2Cache, VWAP.

No database, no network, no API keys.
"""
from __future__ import annotations

import threading
import pytest

from feeds.orderbook_l2 import (
    L2Book, L2Cache, PriceLevel, SideBook, _safe_float, _vwap,
)


# ---------------------------------------------------------------------------
# _safe_float helper
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_int(self):
        assert _safe_float(50) == 50.0

    def test_str_float(self):
        assert abs(_safe_float("0.45") - 0.45) < 1e-9

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_invalid_returns_none(self):
        assert _safe_float("abc") is None

    def test_float_passthrough(self):
        assert abs(_safe_float(0.72) - 0.72) < 1e-9


# ---------------------------------------------------------------------------
# SideBook
# ---------------------------------------------------------------------------

class TestSideBook:
    def _sb(self, best_bid=0.50, best_ask=0.52):
        return SideBook(best_bid=best_bid, best_ask=best_ask)

    def test_apply_level_bid(self):
        sb = SideBook()
        sb.apply_level(0.48, 100, best_bid=0.50, best_ask=0.52)
        # 0.48 <= 0.50 → bid
        assert 0.48 in sb.bids

    def test_apply_level_ask(self):
        sb = SideBook()
        sb.apply_level(0.53, 200, best_bid=0.50, best_ask=0.52)
        # 0.53 >= 0.52 → ask
        assert 0.53 in sb.asks

    def test_apply_level_zero_clears(self):
        sb = SideBook()
        sb.apply_level(0.48, 100, best_bid=0.50, best_ask=0.52)
        sb.apply_level(0.48, 0.0, best_bid=0.50, best_ask=0.52)
        assert 0.48 not in sb.bids

    def test_best_bid_level_highest(self):
        sb = SideBook()
        sb.apply_level(0.48, 100, 0.50, 0.52)
        sb.apply_level(0.45, 200, 0.50, 0.52)
        best = sb.best_bid_level()
        assert best.price == 0.48

    def test_best_ask_level_lowest(self):
        sb = SideBook()
        sb.apply_level(0.53, 100, 0.50, 0.52)
        sb.apply_level(0.55, 200, 0.50, 0.52)
        best = sb.best_ask_level()
        assert best.price == 0.53

    def test_empty_best_bid_none(self):
        assert SideBook().best_bid_level() is None

    def test_empty_best_ask_none(self):
        assert SideBook().best_ask_level() is None

    def test_sorted_bids_descending(self):
        sb = SideBook()
        for p in [0.40, 0.48, 0.45]:
            sb.apply_level(p, 100, 0.50, 0.52)
        prices = [lvl.price for lvl in sb.sorted_bids()]
        assert prices == sorted(prices, reverse=True)

    def test_sorted_asks_ascending(self):
        sb = SideBook()
        for p in [0.55, 0.53, 0.60]:
            sb.apply_level(p, 100, 0.50, 0.52)
        prices = [lvl.price for lvl in sb.sorted_asks()]
        assert prices == sorted(prices)

    def test_total_qty(self):
        sb = SideBook()
        sb.apply_level(0.48, 100, 0.50, 0.52)
        sb.apply_level(0.45, 200, 0.50, 0.52)
        assert sb.total_bid_qty() == 300.0

    def test_levels_count(self):
        sb = SideBook()
        sb.apply_level(0.48, 100, 0.50, 0.52)
        sb.apply_level(0.53, 50, 0.50, 0.52)
        assert sb.levels() == 2


# ---------------------------------------------------------------------------
# L2Book — basic apply_delta
# ---------------------------------------------------------------------------

class TestL2BookDelta:
    def _delta(self, side, price, amount, seq=1, ybb=0.50, yba=0.52, nbb=0.48, nba=0.50):
        return {
            "market_id": "KXTEST-01",
            "side": side,
            "price": price,
            "amount": amount,
            "sequence": seq,
            "yes_best_bid": ybb, "yes_best_ask": yba,
            "no_best_bid": nbb, "no_best_ask": nba,
        }

    def test_apply_returns_true(self):
        book = L2Book("KXTEST-01")
        assert book.apply_delta(self._delta("yes", 0.48, 100)) is True

    def test_sequence_updated(self):
        book = L2Book("KXTEST-01")
        book.apply_delta(self._delta("yes", 0.48, 100, seq=5))
        assert book.sequence == 5

    def test_delta_count_incremented(self):
        book = L2Book("KXTEST-01")
        book.apply_delta(self._delta("yes", 0.48, 100))
        book.apply_delta(self._delta("no",  0.45, 200, seq=2))
        assert book.delta_count == 2

    def test_stale_rejected(self):
        book = L2Book("KXTEST-01")
        book.apply_delta(self._delta("yes", 0.48, 100, seq=10))
        applied = book.apply_delta(self._delta("yes", 0.48, 50, seq=5))
        assert applied is False

    def test_yes_side_populated(self):
        book = L2Book("KXTEST-01")
        book.apply_delta(self._delta("yes", 0.48, 100))
        assert 0.48 in book.yes.bids or 0.48 in book.yes.asks

    def test_no_side_populated(self):
        book = L2Book("KXTEST-01")
        book.apply_delta(self._delta("no", 0.45, 200))
        assert 0.45 in book.no.bids or 0.45 in book.no.asks

    def test_zero_amount_removes_level(self):
        book = L2Book("KXTEST-01")
        book.apply_delta(self._delta("yes", 0.48, 100, seq=1))
        book.apply_delta(self._delta("yes", 0.48, 0.0, seq=2))
        assert 0.48 not in book.yes.bids

    def test_unknown_side_does_not_raise(self):
        book = L2Book("KXTEST-01")
        d = self._delta("yes", 0.48, 100)
        d["side"] = "unknown"
        assert book.apply_delta(d) is True  # should not crash


# ---------------------------------------------------------------------------
# L2Book — sequence gap detection
# ---------------------------------------------------------------------------

class TestL2BookGapDetection:
    def _delta(self, seq, price=0.48, amount=100, side="yes"):
        return {
            "market_id": "KXTEST-01",
            "side": side, "price": price, "amount": amount,
            "sequence": seq,
            "yes_best_bid": 0.50, "yes_best_ask": 0.52,
            "no_best_bid": 0.48,  "no_best_ask": 0.50,
        }

    def test_no_gap_on_init(self):
        book = L2Book("X")
        assert book.gap_detected is False
        assert book.needs_snapshot is False

    def test_no_gap_consecutive(self):
        book = L2Book("X")
        book.apply_delta(self._delta(1))
        book.apply_delta(self._delta(2))
        assert book.gap_detected is False

    def test_gap_detected(self):
        book = L2Book("X")
        book.apply_delta(self._delta(1))
        book.apply_delta(self._delta(5))  # gap of 3
        assert book.gap_detected is True
        assert book.needs_snapshot is True

    def test_gap_count_incremented(self):
        book = L2Book("X")
        book.apply_delta(self._delta(1))
        book.apply_delta(self._delta(5))
        book.apply_delta(self._delta(6))
        book.apply_delta(self._delta(10))  # second gap
        assert book.gap_count == 2

    def test_clear_gap(self):
        book = L2Book("X")
        book.apply_delta(self._delta(1))
        book.apply_delta(self._delta(5))
        assert book.gap_detected is True
        book.clear_gap()
        assert book.gap_detected is False
        assert book.needs_snapshot is False

    def test_first_delta_no_gap(self):
        """First delta (seq=0 → first) should never trigger gap."""
        book = L2Book("X")
        book.apply_delta(self._delta(5))  # first message, seq=5
        assert book.gap_detected is False


# ---------------------------------------------------------------------------
# L2Book — VWAP views
# ---------------------------------------------------------------------------

class TestL2BookVWAP:
    def _book_with_yes_asks(self, levels):
        """Build book from (price, qty) YES ask levels."""
        book = L2Book("X")
        for i, (p, q) in enumerate(levels):
            book.apply_delta({
                "market_id": "X", "side": "yes", "price": p, "amount": q,
                "sequence": i + 1,
                "yes_best_bid": 0.40, "yes_best_ask": min(l[0] for l in levels),
                "no_best_bid": 0.30, "no_best_ask": 0.60,
            })
        return book

    def test_vwap_single_level(self):
        book = self._book_with_yes_asks([(0.55, 100)])
        result = book.vwap_yes_ask(50)
        assert result is not None
        vwap, filled = result
        assert abs(vwap - 0.55) < 1e-6
        assert filled == 50

    def test_vwap_insufficient_liquidity_partial_fill(self):
        """_vwap returns partial fill when insufficient depth — actual_filled < target."""
        book = self._book_with_yes_asks([(0.55, 10)])
        result = book.vwap_yes_ask(100)
        assert result is not None
        vwap, filled = result
        assert filled == 10.0    # only 10 available
        assert abs(vwap - 0.55) < 1e-6

    def test_vwap_empty_book_returns_none(self):
        book = L2Book("X")
        result = book.vwap_yes_ask(50)
        assert result is None

    def test_vwap_multi_level(self):
        book = self._book_with_yes_asks([(0.55, 50), (0.57, 50)])
        result = book.vwap_yes_ask(100)
        assert result is not None
        vwap, filled = result
        assert abs(vwap - (0.55 * 50 + 0.57 * 50) / 100) < 1e-6
        assert filled == 100

    def test_vwap_partial_fill(self):
        book = self._book_with_yes_asks([(0.55, 200)])
        result = book.vwap_yes_ask(100)
        assert result is not None
        vwap, filled = result
        assert abs(vwap - 0.55) < 1e-6
        assert filled == 100


# ---------------------------------------------------------------------------
# L2Book — depth and summary
# ---------------------------------------------------------------------------

class TestL2BookDepth:
    def _book(self):
        book = L2Book("KXTEST-01")
        for seq, p, side in [(1, 0.48, "yes"), (2, 0.53, "yes"), (3, 0.45, "no")]:
            book.apply_delta({
                "market_id": "KXTEST-01", "side": side, "price": p, "amount": 100,
                "sequence": seq,
                "yes_best_bid": 0.50, "yes_best_ask": 0.52,
                "no_best_bid": 0.47, "no_best_ask": 0.50,
            })
        return book

    def test_depth_keys_present(self):
        d = self._book().depth()
        for key in ("yes_bids", "yes_asks", "no_bids", "no_asks", "total"):
            assert key in d

    def test_summary_contains_market_id(self):
        s = self._book().summary()
        assert "KXTEST-01" in s

    def test_summary_contains_seq(self):
        s = self._book().summary()
        assert "seq=" in s


# ---------------------------------------------------------------------------
# L2Cache — thread-safety and stats
# ---------------------------------------------------------------------------

class TestL2Cache:
    def _make_delta(self, market_id, seq=1, price=0.48, amount=100, side="yes"):
        return {
            "market_id": market_id, "side": side, "price": price, "amount": amount,
            "sequence": seq,
            "yes_best_bid": 0.50, "yes_best_ask": 0.52,
            "no_best_bid": 0.48, "no_best_ask": 0.50,
        }

    def test_apply_returns_book(self):
        cache = L2Cache()
        book = cache.apply_delta(self._make_delta("M1"))
        assert book is not None
        assert book.market_id == "M1"

    def test_new_market_created(self):
        cache = L2Cache()
        cache.apply_delta(self._make_delta("M1"))
        assert cache.get("M1") is not None

    def test_missing_market_returns_none(self):
        cache = L2Cache()
        assert cache.get("NONEXISTENT") is None

    def test_stats_updated(self):
        cache = L2Cache()
        cache.apply_delta(self._make_delta("M1"))
        cache.apply_delta(self._make_delta("M2"))
        stats = cache.stats()
        assert stats["total_deltas"] >= 2
        assert stats["markets_seen"] == 2

    def test_stale_discarded_counted(self):
        cache = L2Cache()
        cache.apply_delta(self._make_delta("M1", seq=10))
        cache.apply_delta(self._make_delta("M1", seq=5))  # stale
        stats = cache.stats()
        assert stats["stale_discarded"] >= 1

    def test_len(self):
        cache = L2Cache()
        cache.apply_delta(self._make_delta("M1"))
        cache.apply_delta(self._make_delta("M2"))
        assert len(cache) == 2

    def test_snapshot_is_copy(self):
        cache = L2Cache()
        cache.apply_delta(self._make_delta("M1"))
        snap = cache.snapshot()
        snap["M99"] = "injected"
        assert "M99" not in cache.snapshot()

    def test_no_market_id_returns_none(self):
        cache = L2Cache()
        result = cache.apply_delta({"side": "yes", "price": 0.5, "amount": 100})
        assert result is None

    def test_callback_invoked(self):
        cache = L2Cache()
        seen = []
        cache.register_callback(lambda book: seen.append(book.market_id))
        cache.apply_delta(self._make_delta("M1"))
        assert "M1" in seen

    def test_callback_exception_does_not_propagate(self):
        cache = L2Cache()
        cache.register_callback(lambda b: (_ for _ in ()).throw(RuntimeError("bad cb")))
        # Should not raise:
        cache.apply_delta(self._make_delta("M1"))

    def test_thread_safety(self):
        """Multiple threads applying deltas should not corrupt the cache."""
        cache = L2Cache()
        errors = []

        def worker(mkt, n_deltas):
            for i in range(n_deltas):
                try:
                    cache.apply_delta(self._make_delta(mkt, seq=i + 1))
                except Exception as exc:
                    errors.append(str(exc))

        threads = [threading.Thread(target=worker, args=(f"M{i}", 50)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(cache) == 10

    def test_top_of_book_returns_dict(self):
        cache = L2Cache()
        cache.apply_delta(self._make_delta("M1"))
        tob = cache.top_of_book("M1")
        assert tob is not None
        for key in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
            assert key in tob

    def test_top_of_book_missing_returns_none(self):
        cache = L2Cache()
        assert cache.top_of_book("NONEXISTENT") is None
