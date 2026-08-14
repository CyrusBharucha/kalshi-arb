"""
tests/test_orderbook_snapshots.py
====================================
Unit tests for data/websocket_client.py OrderBook.to_snapshot_rows()
and OrderBook.to_ticker_snap() methods.

Tests cover:
  - to_snapshot_rows(): empty book → empty list
  - to_snapshot_rows(): yes bids → side="yes", sorted by price desc
  - to_snapshot_rows(): no asks → side="no", price is complement
  - to_snapshot_rows(): level_rank starts at 1, increments correctly
  - to_snapshot_rows(): respects OB_DEPTH cap
  - to_snapshot_rows(): market_id and snapshot_ts set correctly
  - to_ticker_snap(): empty book → None bid/ask
  - to_ticker_snap(): correct yes_bid, yes_ask, no_bid, no_ask
  - to_ticker_snap(): source="websocket"
  - to_ticker_snap(): snapshot_ts and market_id set correctly

No DB, no network.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
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


def _make_book(market_id="MKT-TEST"):
    from feeds.websocket_client import OrderBook
    return OrderBook(market_id)


TS = datetime(2026, 6, 23, 14, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# to_snapshot_rows
# ---------------------------------------------------------------------------

class TestToSnapshotRows:
    def test_empty_book_returns_empty_list(self):
        b = _make_book()
        rows = b.to_snapshot_rows(TS)
        assert rows == []

    def test_yes_bids_produce_side_yes_rows(self):
        b = _make_book()
        b._yes_bids = {0.45: 10, 0.40: 5}
        rows = b.to_snapshot_rows(TS)
        yes_rows = [r for r in rows if r["side"] == "yes"]
        assert len(yes_rows) == 2

    def test_yes_asks_produce_side_no_rows(self):
        b = _make_book()
        b._yes_asks = {0.55: 8}
        rows = b.to_snapshot_rows(TS)
        no_rows = [r for r in rows if r["side"] == "no"]
        assert len(no_rows) == 1

    def test_yes_bid_prices_sorted_descending(self):
        b = _make_book()
        b._yes_bids = {0.42: 5, 0.45: 10, 0.40: 3}
        rows = b.to_snapshot_rows(TS)
        yes_rows = [r for r in rows if r["side"] == "yes"]
        prices = [r["price"] for r in yes_rows]
        assert prices == sorted(prices, reverse=True)

    def test_yes_ask_complement_price_in_no_side(self):
        """No-side price = 1.0 - yes_ask price."""
        b = _make_book()
        b._yes_asks = {0.60: 5}
        rows = b.to_snapshot_rows(TS)
        no_rows = [r for r in rows if r["side"] == "no"]
        assert len(no_rows) == 1
        assert abs(no_rows[0]["price"] - (1.0 - 0.60)) < 1e-6

    def test_level_rank_starts_at_1(self):
        b = _make_book()
        b._yes_bids = {0.45: 10}
        rows = b.to_snapshot_rows(TS)
        yes_rows = [r for r in rows if r["side"] == "yes"]
        assert yes_rows[0]["level_rank"] == 1

    def test_level_rank_increments(self):
        b = _make_book()
        b._yes_bids = {0.45: 10, 0.42: 5, 0.40: 3}
        rows = b.to_snapshot_rows(TS)
        yes_rows = [r for r in rows if r["side"] == "yes"]
        ranks = [r["level_rank"] for r in yes_rows]
        assert ranks == [1, 2, 3]

    def test_market_id_set(self):
        b = _make_book("SPECIFIC-MKT")
        b._yes_bids = {0.45: 10}
        rows = b.to_snapshot_rows(TS)
        assert all(r["market_id"] == "SPECIFIC-MKT" for r in rows)

    def test_snapshot_ts_set(self):
        b = _make_book()
        b._yes_bids = {0.45: 10}
        rows = b.to_snapshot_rows(TS)
        assert all(r["snapshot_ts"] == TS for r in rows)

    def test_quantity_preserved(self):
        b = _make_book()
        b._yes_bids = {0.45: 42}
        rows = b.to_snapshot_rows(TS)
        yes_rows = [r for r in rows if r["side"] == "yes"]
        assert yes_rows[0]["quantity"] == 42

    def test_mixed_bids_and_asks(self):
        b = _make_book()
        b._yes_bids = {0.45: 10}
        b._yes_asks = {0.55: 5}
        rows = b.to_snapshot_rows(TS)
        assert len(rows) == 2
        sides = {r["side"] for r in rows}
        assert sides == {"yes", "no"}

    def test_ob_depth_cap_applied(self):
        """Should not exceed OB_DEPTH levels per side."""
        from feeds.websocket_client import OB_DEPTH
        b = _make_book()
        # Add OB_DEPTH + 5 bids — only OB_DEPTH should appear
        for i in range(OB_DEPTH + 5):
            b._yes_bids[round(0.40 + i * 0.01, 2)] = i + 1
        rows = b.to_snapshot_rows(TS)
        yes_rows = [r for r in rows if r["side"] == "yes"]
        assert len(yes_rows) <= OB_DEPTH


# ---------------------------------------------------------------------------
# to_ticker_snap
# ---------------------------------------------------------------------------

class TestToTickerSnap:
    def test_returns_dict(self):
        b = _make_book()
        result = b.to_ticker_snap(TS)
        assert isinstance(result, dict)

    def test_empty_book_bid_is_none(self):
        b = _make_book()
        result = b.to_ticker_snap(TS)
        assert result["yes_bid"] is None

    def test_empty_book_ask_is_none(self):
        b = _make_book()
        result = b.to_ticker_snap(TS)
        assert result["yes_ask"] is None

    def test_empty_book_no_bid_is_none(self):
        b = _make_book()
        result = b.to_ticker_snap(TS)
        assert result["no_bid"] is None

    def test_yes_bid_set_correctly(self):
        b = _make_book()
        b._yes_bids = {0.44: 10, 0.42: 5}
        result = b.to_ticker_snap(TS)
        assert result["yes_bid"] == pytest.approx(0.44)

    def test_yes_ask_set_correctly(self):
        b = _make_book()
        b._yes_asks = {0.46: 5, 0.48: 3}
        result = b.to_ticker_snap(TS)
        assert result["yes_ask"] == pytest.approx(0.46)

    def test_no_bid_is_complement_of_yes_ask(self):
        b = _make_book()
        b._yes_asks = {0.55: 5}
        result = b.to_ticker_snap(TS)
        assert result["no_bid"] == pytest.approx(1.0 - 0.55)

    def test_no_ask_is_complement_of_yes_bid(self):
        b = _make_book()
        b._yes_bids = {0.45: 10}
        result = b.to_ticker_snap(TS)
        assert result["no_ask"] == pytest.approx(1.0 - 0.45)

    def test_source_is_websocket(self):
        b = _make_book()
        result = b.to_ticker_snap(TS)
        assert result["source"] == "websocket"

    def test_market_id_set(self):
        b = _make_book("SPECIFIC-MKT")
        result = b.to_ticker_snap(TS)
        assert result["market_id"] == "SPECIFIC-MKT"

    def test_snapshot_ts_set(self):
        b = _make_book()
        result = b.to_ticker_snap(TS)
        assert result["snapshot_ts"] == TS

    def test_has_required_keys(self):
        b = _make_book()
        result = b.to_ticker_snap(TS)
        for key in ["snapshot_ts", "market_id", "yes_bid", "yes_ask",
                    "no_bid", "no_ask", "source"]:
            assert key in result, f"Missing key: {key}"
