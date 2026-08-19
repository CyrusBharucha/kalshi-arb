"""
tests/test_l2_orderbook.py
==========================
Unit tests for the L2 order book reconstruction engine.

Tests cover:
- SideBook: bid/ask level classification, clearing, sorting
- L2Book: delta application, sequence guard, VWAP calculation, depth summary
- L2Cache: thread safety, backward-compat top-of-book accessor, stats
- Edge cases: amount=0 (cancel), mid-spread levels, missing side field
"""
import threading
import time
from typing import Any, Dict

import pytest

from feeds.orderbook_l2 import L2Book, L2Cache, PriceLevel, SideBook, _vwap


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def _delta(
    market_id="TEST-MKT",
    price=0.50,
    amount=100.0,
    side="yes",
    yes_best_bid=0.50,
    yes_best_ask=0.55,
    no_best_bid=0.45,
    no_best_ask=0.50,
    sequence=1,
) -> Dict[str, Any]:
    return dict(
        market_id=market_id,
        price=price,
        amount=amount,
        side=side,
        yes_best_bid=yes_best_bid,
        yes_best_ask=yes_best_ask,
        no_best_bid=no_best_bid,
        no_best_ask=no_best_ask,
        sequence=sequence,
        created_at="2026-01-01T00:00:00Z",
    )


# ------------------------------------------------------------------------------
# SideBook tests
# ------------------------------------------------------------------------------

class TestSideBook:

    def test_bid_level_stored_in_bids(self):
        sb = SideBook()
        result = sb.apply_level(0.45, 200.0, best_bid=0.50, best_ask=0.55)
        assert result == "bid"
        assert 0.45 in sb.bids
        assert sb.bids[0.45] == pytest.approx(200.0)
        assert 0.45 not in sb.asks

    def test_best_bid_level_stored_in_bids(self):
        sb = SideBook()
        result = sb.apply_level(0.50, 100.0, best_bid=0.50, best_ask=0.55)
        assert result == "bid"
        assert 0.50 in sb.bids

    def test_ask_level_stored_in_asks(self):
        sb = SideBook()
        result = sb.apply_level(0.55, 150.0, best_bid=0.50, best_ask=0.55)
        assert result == "ask"
        assert 0.55 in sb.asks
        assert sb.asks[0.55] == pytest.approx(150.0)

    def test_ask_above_best_ask(self):
        sb = SideBook()
        result = sb.apply_level(0.65, 50.0, best_bid=0.50, best_ask=0.55)
        assert result == "ask"
        assert 0.65 in sb.asks

    def test_amount_zero_clears_bid(self):
        sb = SideBook()
        sb.apply_level(0.45, 200.0, best_bid=0.50, best_ask=0.55)
        sb.apply_level(0.45, 0.0, best_bid=0.50, best_ask=0.55)
        assert 0.45 not in sb.bids
        assert 0.45 not in sb.asks

    def test_amount_zero_clears_ask(self):
        sb = SideBook()
        sb.apply_level(0.60, 80.0, best_bid=0.50, best_ask=0.55)
        sb.apply_level(0.60, 0.0, best_bid=0.50, best_ask=0.55)
        assert 0.60 not in sb.asks
        assert 0.60 not in sb.bids

    def test_amount_zero_nonexistent_level_is_noop(self):
        sb = SideBook()
        sb.apply_level(0.99, 0.0, best_bid=0.50, best_ask=0.55)
        assert not sb.bids
        assert not sb.asks

    def test_sorted_bids_descending(self):
        sb = SideBook()
        for p in [0.50, 0.40, 0.45]:
            sb.apply_level(p, 10.0, best_bid=0.50, best_ask=0.55)
        bids = sb.sorted_bids()
        prices = [lvl.price for lvl in bids]
        assert prices == sorted(prices, reverse=True)

    def test_sorted_asks_ascending(self):
        sb = SideBook()
        for p in [0.65, 0.55, 0.60]:
            sb.apply_level(p, 10.0, best_bid=0.50, best_ask=0.55)
        asks = sb.sorted_asks()
        prices = [lvl.price for lvl in asks]
        assert prices == sorted(prices)

    def test_best_bid_level_is_highest_bid(self):
        sb = SideBook()
        for p in [0.50, 0.45, 0.40]:
            sb.apply_level(p, 10.0, best_bid=0.50, best_ask=0.55)
        best = sb.best_bid_level()
        assert best is not None
        assert best.price == pytest.approx(0.50)

    def test_best_ask_level_is_lowest_ask(self):
        sb = SideBook()
        for p in [0.55, 0.60, 0.65]:
            sb.apply_level(p, 10.0, best_bid=0.50, best_ask=0.55)
        best = sb.best_ask_level()
        assert best is not None
        assert best.price == pytest.approx(0.55)

    def test_no_levels_returns_none(self):
        sb = SideBook()
        assert sb.best_bid_level() is None
        assert sb.best_ask_level() is None

    def test_mid_spread_level_stored_in_bids(self):
        """A level inside the spread (shouldn't exist in normal market) goes to bids."""
        sb = SideBook()
        result = sb.apply_level(0.52, 10.0, best_bid=0.50, best_ask=0.55)
        assert result == "mid"
        assert 0.52 in sb.bids  # stored as bid since it's below ask

    def test_total_quantities(self):
        sb = SideBook()
        sb.apply_level(0.50, 100.0, best_bid=0.50, best_ask=0.55)
        sb.apply_level(0.45, 200.0, best_bid=0.50, best_ask=0.55)
        sb.apply_level(0.55, 150.0, best_bid=0.50, best_ask=0.55)
        assert sb.total_bid_qty() == pytest.approx(300.0)
        assert sb.total_ask_qty() == pytest.approx(150.0)

    def test_levels_count(self):
        sb = SideBook()
        sb.apply_level(0.50, 100.0, best_bid=0.50, best_ask=0.55)
        sb.apply_level(0.45, 200.0, best_bid=0.50, best_ask=0.55)
        sb.apply_level(0.55, 150.0, best_bid=0.50, best_ask=0.55)
        assert sb.levels() == 3


# ------------------------------------------------------------------------------
# L2Book tests
# ------------------------------------------------------------------------------

class TestL2Book:

    def test_apply_yes_delta(self):
        book = L2Book(market_id="M1")
        d = _delta(price=0.50, amount=100.0, side="yes",
                   yes_best_bid=0.50, yes_best_ask=0.55, sequence=1)
        applied = book.apply_delta(d)
        assert applied is True
        assert book.delta_count == 1
        assert book.sequence == 1
        assert len(book.yes.bids) == 1

    def test_apply_no_delta(self):
        book = L2Book(market_id="M1")
        d = _delta(price=0.45, amount=80.0, side="no",
                   no_best_bid=0.45, no_best_ask=0.50, sequence=1)
        applied = book.apply_delta(d)
        assert applied is True
        assert len(book.no.bids) == 1

    def test_stale_sequence_rejected(self):
        book = L2Book(market_id="M1")
        d1 = _delta(price=0.50, amount=100.0, sequence=10)
        d2 = _delta(price=0.50, amount=999.0, sequence=5)   # stale
        book.apply_delta(d1)
        applied = book.apply_delta(d2)
        assert applied is False
        assert book.yes.bids[0.50] == pytest.approx(100.0)  # unchanged

    def test_same_sequence_applied(self):
        """Same sequence number is allowed (idempotent level update)."""
        book = L2Book(market_id="M1")
        d1 = _delta(price=0.50, amount=100.0, sequence=5)
        d2 = _delta(price=0.45, amount=80.0, sequence=5)
        book.apply_delta(d1)
        applied = book.apply_delta(d2)
        assert applied is True

    def test_amount_zero_removes_level(self):
        book = L2Book(market_id="M1")
        book.apply_delta(_delta(price=0.50, amount=100.0, side="yes", sequence=1))
        book.apply_delta(_delta(price=0.50, amount=0.0, side="yes", sequence=2))
        assert 0.50 not in book.yes.bids
        assert 0.50 not in book.yes.asks

    def test_multiple_levels_build_up(self):
        book = L2Book(market_id="M1")
        levels = [
            (0.50, 100.0, "yes", 0.50, 0.55, 1),
            (0.45, 200.0, "yes", 0.50, 0.55, 2),
            (0.40, 150.0, "yes", 0.50, 0.55, 3),
            (0.55, 80.0,  "yes", 0.50, 0.55, 4),
            (0.60, 60.0,  "yes", 0.50, 0.55, 5),
        ]
        for price, qty, side, ybb, yba, seq in levels:
            book.apply_delta(_delta(price=price, amount=qty, side=side,
                                    yes_best_bid=ybb, yes_best_ask=yba,
                                    sequence=seq))
        assert len(book.yes.bids) == 3  # 0.40, 0.45, 0.50
        assert len(book.yes.asks) == 2  # 0.55, 0.60

    def test_yes_bids_max_levels(self):
        book = L2Book(market_id="M1")
        for i, p in enumerate([0.50, 0.49, 0.48, 0.47, 0.46]):
            book.apply_delta(_delta(price=p, amount=10.0, side="yes",
                                    yes_best_bid=0.50, yes_best_ask=0.55,
                                    sequence=i+1))
        top3 = book.yes_bids(max_levels=3)
        assert len(top3) == 3
        assert top3[0].price == pytest.approx(0.50)

    def test_unknown_side_rejected_gracefully(self):
        book = L2Book(market_id="M1")
        d = _delta(side="unknown", sequence=1)
        # Should not raise; side is just ignored
        book.apply_delta(d)
        assert book.delta_count == 1  # delta counted even if side unknown

    def test_invalid_price_rejected(self):
        book = L2Book(market_id="M1")
        d = dict(market_id="M1", price="invalid", amount=100.0, side="yes",
                 yes_best_bid=0.50, yes_best_ask=0.55, sequence=1)
        applied = book.apply_delta(d)
        assert applied is False

    def test_depth_dict(self):
        book = L2Book(market_id="M1")
        book.apply_delta(_delta(price=0.50, amount=100.0, side="yes",
                                yes_best_bid=0.50, yes_best_ask=0.55, sequence=1))
        book.apply_delta(_delta(price=0.45, amount=80.0, side="no",
                                no_best_bid=0.45, no_best_ask=0.50, sequence=2))
        d = book.depth()
        assert d["yes_bids"] == 1
        assert d["no_bids"] == 1
        assert d["total"] == 2

    def test_summary_string_contains_market_id(self):
        book = L2Book(market_id="KXBTCTEST")
        summary = book.summary()
        assert "KXBTCTEST" in summary


# ------------------------------------------------------------------------------
# VWAP tests
# ------------------------------------------------------------------------------

class TestVWAP:

    def _book_with_yes_asks(self, levels):
        """Levels: list of (price, qty)."""
        book = L2Book(market_id="VW")
        for i, (price, qty) in enumerate(levels):
            book.apply_delta(_delta(
                price=price, amount=qty, side="yes",
                yes_best_bid=min(p for p, _ in levels) - 0.05,
                yes_best_ask=price,
                sequence=i + 1,
            ))
        return book

    def test_single_level_exact_fill(self):
        book = L2Book(market_id="V1")
        book.apply_delta(_delta(price=0.55, amount=100.0, side="yes",
                                yes_best_bid=0.50, yes_best_ask=0.55, sequence=1))
        result = book.vwap_yes_ask(50.0)
        assert result is not None
        vwap, filled = result
        assert filled == pytest.approx(50.0)
        assert vwap == pytest.approx(0.55)

    def test_multi_level_fill(self):
        book = L2Book(market_id="V2")
        # Two ask levels: 100 @ 0.55, 200 @ 0.60
        book.apply_delta(_delta(price=0.55, amount=100.0, side="yes",
                                yes_best_bid=0.50, yes_best_ask=0.55, sequence=1))
        book.apply_delta(_delta(price=0.60, amount=200.0, side="yes",
                                yes_best_bid=0.50, yes_best_ask=0.55, sequence=2))
        result = book.vwap_yes_ask(150.0)
        assert result is not None
        vwap, filled = result
        assert filled == pytest.approx(150.0)
        # 100*0.55 + 50*0.60 = 55 + 30 = 85 / 150 ≈ 0.5667
        assert vwap == pytest.approx(85.0 / 150.0, rel=1e-4)

    def test_partial_fill_when_illiquid(self):
        book = L2Book(market_id="V3")
        book.apply_delta(_delta(price=0.55, amount=50.0, side="yes",
                                yes_best_bid=0.50, yes_best_ask=0.55, sequence=1))
        result = book.vwap_yes_ask(200.0)
        assert result is not None
        vwap, filled = result
        assert filled == pytest.approx(50.0)   # only 50 available
        assert vwap == pytest.approx(0.55)

    def test_empty_book_returns_none(self):
        book = L2Book(market_id="V4")
        assert book.vwap_yes_ask(100.0) is None

    def test_vwap_yes_bid_walks_down_bids(self):
        book = L2Book(market_id="V5")
        book.apply_delta(_delta(price=0.50, amount=100.0, side="yes",
                                yes_best_bid=0.50, yes_best_ask=0.55, sequence=1))
        book.apply_delta(_delta(price=0.45, amount=150.0, side="yes",
                                yes_best_bid=0.50, yes_best_ask=0.55, sequence=2))
        result = book.vwap_yes_bid(120.0)
        assert result is not None
        vwap, filled = result
        assert filled == pytest.approx(120.0)
        # 100*0.50 + 20*0.45 = 50 + 9 = 59 / 120 ≈ 0.4917
        assert vwap == pytest.approx(59.0 / 120.0, rel=1e-4)

    def test_vwap_helper_empty(self):
        assert _vwap([], 100.0) is None

    def test_vwap_helper_zero_qty_after_fill(self):
        """If no level has qty>0 (all zero-qty), returns None."""
        levels = [PriceLevel(0.55, 0.0)]
        assert _vwap(levels, 10.0) is None


# ------------------------------------------------------------------------------
# L2Cache tests
# ------------------------------------------------------------------------------

class TestL2Cache:

    def test_apply_creates_book(self):
        cache = L2Cache()
        cache.apply_delta(_delta(market_id="X1", sequence=1))
        assert cache.get("X1") is not None

    def test_apply_different_markets(self):
        cache = L2Cache()
        cache.apply_delta(_delta(market_id="A", sequence=1))
        cache.apply_delta(_delta(market_id="B", sequence=1))
        assert len(cache) == 2

    def test_stats_tracking(self):
        cache = L2Cache()
        cache.apply_delta(_delta(market_id="S1", sequence=5))
        cache.apply_delta(_delta(market_id="S1", sequence=4))   # stale
        stats = cache.stats()
        assert stats["total_deltas"] == 2
        assert stats["stale_discarded"] == 1
        assert stats["markets_seen"] == 1

    def test_callback_called_on_apply(self):
        received = []
        cache = L2Cache()
        cache.register_callback(received.append)
        cache.apply_delta(_delta(market_id="CB", sequence=1))
        assert len(received) == 1
        assert received[0].market_id == "CB"

    def test_callback_not_called_on_stale(self):
        received = []
        cache = L2Cache()
        cache.register_callback(received.append)
        cache.apply_delta(_delta(market_id="CB", sequence=10))
        cache.apply_delta(_delta(market_id="CB", sequence=1))  # stale
        assert len(received) == 1

    def test_top_of_book_backward_compat(self):
        cache = L2Cache()
        cache.apply_delta(_delta(
            market_id="TOB",
            price=0.50, amount=100.0, side="yes",
            yes_best_bid=0.50, yes_best_ask=0.55,
            no_best_bid=0.45, no_best_ask=0.50,
            sequence=1,
        ))
        tob = cache.top_of_book("TOB")
        assert tob is not None
        assert "yes_bid" in tob
        assert "yes_ask" in tob
        assert "no_bid" in tob
        assert "no_ask" in tob
        assert tob["yes_bid"] == pytest.approx(0.50)

    def test_top_of_book_missing_market(self):
        cache = L2Cache()
        assert cache.top_of_book("NONEXISTENT") is None

    def test_snapshot_returns_copy(self):
        cache = L2Cache()
        cache.apply_delta(_delta(market_id="SNAP", sequence=1))
        snap = cache.snapshot()
        assert "SNAP" in snap
        # Mutating snap doesn't affect cache
        del snap["SNAP"]
        assert cache.get("SNAP") is not None

    def test_thread_safety(self):
        """Concurrent writes to L2Cache should not raise or corrupt state."""
        cache = L2Cache()
        errors = []

        def writer(mid, n_deltas):
            try:
                for i in range(n_deltas):
                    cache.apply_delta(_delta(market_id=mid, sequence=i))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(f"T{i}", 200))
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        assert len(cache) == 8


# ------------------------------------------------------------------------------
# Real-message replay test (uses captured raw data if available)
# ------------------------------------------------------------------------------

class TestRealMessageReplay:
    """
    Replays the first 500 raw messages from the scratchpad capture to verify
    the L2 engine handles real Synthesis messages without crashing.
    Skipped if the capture file doesn't exist.
    """

    CAPTURE = (
        r"C:\Users\burzi\AppData\Local\Temp\claude\C--Users-burzi-OneDrive-"
        r"Desktop-Cyrus-Trading\9c01eb3c-cadb-473c-b96b-2ebf42be62da\scratchpad\raw_ws_messages.jsonl"
    )

    def _load_deltas(self, limit=500):
        import json
        from pathlib import Path
        p = Path(self.CAPTURE)
        if not p.exists():
            return None
        deltas = []
        for line in p.read_text(encoding="utf-8").splitlines()[:limit]:
            try:
                msg = json.loads(line)
                resp = msg.get("response", {})
                if "delta" in resp:
                    deltas.append(resp["delta"])
            except Exception:
                pass
        return deltas

    def test_replay_does_not_crash(self):
        deltas = self._load_deltas()
        if deltas is None:
            pytest.skip("Capture file not found")
        cache = L2Cache()
        for d in deltas:
            cache.apply_delta(d)
        stats = cache.stats()
        # Must have processed some markets
        assert stats["markets_seen"] > 0

    def test_replay_builds_books_with_depth(self):
        deltas = self._load_deltas(limit=2000)
        if deltas is None:
            pytest.skip("Capture file not found")
        cache = L2Cache()
        for d in deltas:
            cache.apply_delta(d)
        # At least some markets should have multiple price levels
        multi_level = sum(
            1 for book in cache.snapshot().values()
            if book.yes.levels() + book.no.levels() > 1
        )
        assert multi_level > 0, "No markets with >1 price level after replay"

    def test_replay_price_consistency(self):
        """Best bid tracked in SideBook should be <= every bid level stored."""
        deltas = self._load_deltas(limit=2000)
        if deltas is None:
            pytest.skip("Capture file not found")
        cache = L2Cache()
        for d in deltas:
            cache.apply_delta(d)
        violations = 0
        for book in cache.snapshot().values():
            for side in (book.yes, book.no):
                if side.best_bid is not None and side.bids:
                    max_bid = max(side.bids)
                    if max_bid > side.best_bid + 1e-4:
                        violations += 1
        # Very few or no violations expected (minor float tolerance)
        assert violations == 0, f"{violations} price consistency violations"
