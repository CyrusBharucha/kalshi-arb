"""
tests/test_l2_snapshot_writer.py
=================================
Unit tests for data/l2_snapshot_writer.py:
  - L2SnapshotWriter: construction, start returns thread, stop sets event,
    stats dict shape, flush calls write_batch, write_batch error increments count
  - _book_to_row(): dict shape, required keys, json-serializable book_json

No real DB or WebSocket — all dependencies are mocked.
"""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers: build a mock L2Book
# ---------------------------------------------------------------------------

def _make_book(
    sequence=100,
    delta_count=50,
    yes_bids=None,    # dict price -> qty
    yes_asks=None,
    no_bids=None,
    no_asks=None,
):
    """Create a minimal mock L2Book compatible with _book_to_row()."""
    book = MagicMock()
    book.sequence    = sequence
    book.delta_count = delta_count

    def _make_side(bids_dict, asks_dict):
        side = MagicMock()
        side.bids = bids_dict or {}
        side.asks = asks_dict or {}
        # best_bid_level / best_ask_level return None when empty, else mock PriceLevel
        def _best_bid():
            if not side.bids:
                return None
            p = max(side.bids)
            pl = MagicMock()
            pl.price = p
            return pl
        def _best_ask():
            if not side.asks:
                return None
            p = min(side.asks)
            pl = MagicMock()
            pl.price = p
            return pl
        side.best_bid_level = _best_bid
        side.best_ask_level = _best_ask
        return side

    book.yes = _make_side(yes_bids, yes_asks)
    book.no  = _make_side(no_bids, no_asks)
    return book


# ---------------------------------------------------------------------------
# L2SnapshotWriter construction and lifecycle
# ---------------------------------------------------------------------------

class TestL2SnapshotWriterLifecycle:
    """Construction, start, stop, stats."""

    def _make_writer(self, interval=60):
        from feeds.l2_snapshot_writer import L2SnapshotWriter
        mock_sf = MagicMock()
        mock_cache = MagicMock()
        mock_cache.snapshot.return_value = {}
        return L2SnapshotWriter(mock_sf, mock_cache, interval_seconds=interval)

    def test_instantiates(self):
        w = self._make_writer()
        assert w is not None

    def test_stats_initial_snapshots_written_zero(self):
        w = self._make_writer()
        assert w.stats["snapshots_written"] == 0

    def test_stats_initial_write_errors_zero(self):
        w = self._make_writer()
        assert w.stats["write_errors"] == 0

    def test_stats_returns_dict(self):
        w = self._make_writer()
        assert isinstance(w.stats, dict)

    def test_stats_is_copy(self):
        """Mutating the returned stats dict should not affect internal stats."""
        w = self._make_writer()
        s = w.stats
        s["snapshots_written"] = 9999
        assert w.stats["snapshots_written"] == 0

    def test_start_returns_thread(self):
        w = self._make_writer()
        t = w.start()
        assert isinstance(t, threading.Thread)
        w.stop()
        t.join(timeout=2)

    def test_start_thread_is_daemon(self):
        w = self._make_writer()
        t = w.start()
        assert t.daemon is True
        w.stop()
        t.join(timeout=2)

    def test_stop_sets_stop_event(self):
        from feeds.l2_snapshot_writer import L2SnapshotWriter
        mock_sf = MagicMock()
        mock_cache = MagicMock()
        mock_cache.snapshot.return_value = {}
        w = L2SnapshotWriter(mock_sf, mock_cache)
        assert not w._stop_evt.is_set()
        w.stop()
        assert w._stop_evt.is_set()

    def test_thread_name_is_l2_snapshot_writer(self):
        w = self._make_writer()
        t = w.start()
        assert "l2-snapshot-writer" in t.name
        w.stop()
        t.join(timeout=2)


# ---------------------------------------------------------------------------
# write_batch error handling
# ---------------------------------------------------------------------------

class TestWriteBatchErrors:
    """When DB raises, write_errors counter increments."""

    def _make_writer_with_bad_session(self):
        from feeds.l2_snapshot_writer import L2SnapshotWriter
        mock_sf = MagicMock()
        mock_ss = MagicMock()
        mock_ss.__enter__ = MagicMock(side_effect=RuntimeError("DB down"))
        mock_ss.__exit__ = MagicMock(return_value=False)
        mock_sf.return_value = mock_ss
        mock_cache = MagicMock()
        return L2SnapshotWriter(mock_sf, mock_cache)

    def test_write_errors_incremented_on_exception(self):
        w = self._make_writer_with_bad_session()
        book = _make_book()
        w._write_batch([("KXTEST-YES", book)])
        assert w.stats["write_errors"] == 1

    def test_snapshots_written_not_incremented_on_exception(self):
        w = self._make_writer_with_bad_session()
        book = _make_book()
        w._write_batch([("KXTEST-YES", book)])
        assert w.stats["snapshots_written"] == 0

    def test_write_errors_accumulate(self):
        w = self._make_writer_with_bad_session()
        book = _make_book()
        w._write_batch([("M1", book)])
        w._write_batch([("M2", book)])
        assert w.stats["write_errors"] == 2


# ---------------------------------------------------------------------------
# flush()
# ---------------------------------------------------------------------------

class TestFlush:
    """flush() forces immediate snapshot for one market."""

    def test_flush_calls_write_batch_when_book_exists(self):
        from feeds.l2_snapshot_writer import L2SnapshotWriter
        mock_sf = MagicMock()
        mock_cache = MagicMock()
        book = _make_book()
        mock_cache.get.return_value = book

        w = L2SnapshotWriter(mock_sf, mock_cache)
        w._write_batch = MagicMock()
        w.flush("KXTEST-YES")

        w._write_batch.assert_called_once()
        call_args = w._write_batch.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0][0] == "KXTEST-YES"

    def test_flush_does_nothing_when_book_not_found(self):
        from feeds.l2_snapshot_writer import L2SnapshotWriter
        mock_sf = MagicMock()
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        w = L2SnapshotWriter(mock_sf, mock_cache)
        w._write_batch = MagicMock()
        w.flush("KXTEST-YES")

        w._write_batch.assert_not_called()

    def test_flush_updates_last_snap_time(self):
        from feeds.l2_snapshot_writer import L2SnapshotWriter
        mock_sf = MagicMock()
        mock_cache = MagicMock()
        mock_cache.get.return_value = _make_book()

        w = L2SnapshotWriter(mock_sf, mock_cache)
        w._write_batch = MagicMock()
        before = time.time()
        w.flush("KXTEST-YES")
        after = time.time()

        ts = w._last_snap.get("KXTEST-YES", -1)
        assert before <= ts <= after


# ---------------------------------------------------------------------------
# _book_to_row()
# ---------------------------------------------------------------------------

class TestBookToRow:
    """_book_to_row() produces a correctly shaped row dict."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from feeds.l2_snapshot_writer import _book_to_row
        self.fn = _book_to_row

    def test_returns_dict(self):
        result = self.fn("KXTEST-YES", _make_book())
        assert isinstance(result, dict)

    def test_has_all_required_keys(self):
        result = self.fn("KXTEST-YES", _make_book())
        required = {
            "market_id", "sequence", "delta_count",
            "yes_best_bid", "yes_best_ask",
            "no_best_bid", "no_best_ask",
            "book_json",
            "yes_bid_levels", "yes_ask_levels",
            "no_bid_levels", "no_ask_levels",
        }
        assert required.issubset(set(result.keys()))

    def test_market_id_correct(self):
        result = self.fn("KXBOC-YES", _make_book())
        assert result["market_id"] == "KXBOC-YES"

    def test_sequence_correct(self):
        result = self.fn("M", _make_book(sequence=999))
        assert result["sequence"] == 999

    def test_delta_count_correct(self):
        result = self.fn("M", _make_book(delta_count=42))
        assert result["delta_count"] == 42

    def test_book_json_is_valid_json_string(self):
        result = self.fn("M", _make_book())
        parsed = json.loads(result["book_json"])
        assert isinstance(parsed, dict)

    def test_book_json_has_required_keys(self):
        result = self.fn("M", _make_book())
        parsed = json.loads(result["book_json"])
        for key in ("yes_bids", "yes_asks", "no_bids", "no_asks"):
            assert key in parsed

    def test_yes_bid_levels_counted(self):
        book = _make_book(yes_bids={0.5: 100, 0.49: 200})
        result = self.fn("M", book)
        assert result["yes_bid_levels"] == 2

    def test_empty_book_best_bid_is_none(self):
        book = _make_book()
        result = self.fn("M", book)
        assert result["yes_best_bid"] is None

    def test_nonempty_yes_bids_best_bid_present(self):
        book = _make_book(yes_bids={0.5: 100, 0.49: 200})
        result = self.fn("M", book)
        assert result["yes_best_bid"] == 0.5

    def test_zero_level_counts_for_empty_book(self):
        book = _make_book()
        result = self.fn("M", book)
        assert result["yes_bid_levels"] == 0
        assert result["yes_ask_levels"] == 0
        assert result["no_bid_levels"]  == 0
        assert result["no_ask_levels"]  == 0
