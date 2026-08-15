"""
tests/test_ws_client_callbacks.py
===================================
Unit tests for the KalshiWebSocketClient callback interface.

Tests only _handle_message dispatch and callback invocation — no live WS,
no PostgreSQL, no API keys. The WS client is instantiated with
authenticated=False and mocked DB dependencies.
"""
from __future__ import annotations

import asyncio
from typing import List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

# Patch heavy dependencies before import so they don't fail without DB/keys
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _patch_config_and_db(monkeypatch):
    """Patch config and DB so the WS client can be instantiated without credentials."""
    monkeypatch.setenv("KALSHI_KEY_ID", "")
    monkeypatch.setenv("KALSHI_PRIVKEY_PATH", "")
    with (
        patch("feeds.websocket_client.session_scope") as mock_scope,
        patch("feeds.websocket_client.insert_snapshot"),
        patch("feeds.websocket_client.insert_order_book"),
        patch("feeds.websocket_client.upsert_trade"),
    ):
        mock_session = MagicMock()
        mock_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_scope.return_value.__exit__ = MagicMock(return_value=False)
        yield


def _make_client(on_seq_gap=None, on_price_update=None):
    """Build a KalshiWebSocketClient with test tickers (no auth, no DB)."""
    from feeds.websocket_client import KalshiWebSocketClient
    return KalshiWebSocketClient(
        tickers=["TICKER-A", "TICKER-B"],
        authenticated=False,
        on_seq_gap=on_seq_gap,
        on_price_update=on_price_update,
    )


# ---------------------------------------------------------------------------
# on_price_update callback
# ---------------------------------------------------------------------------

class TestOnPriceUpdateCallback:
    def test_called_on_ticker_message(self):
        updates: List[Tuple[str, Optional[int]]] = []

        def cb(ticker, seq):
            updates.append((ticker, seq))

        client = _make_client(on_price_update=cb)
        msg = {
            "type": "ticker",
            "seq":  42,
            "msg":  {
                "market_ticker": "TICKER-A",
                "yes_bid": 0.45,
                "yes_ask": 0.50,
            },
        }
        asyncio.run(client._handle_message(msg))
        assert len(updates) == 1
        assert updates[0] == ("TICKER-A", 42)

    def test_called_on_orderbook_snapshot(self):
        updates: List[Tuple] = []
        client = _make_client(on_price_update=lambda t, s: updates.append((t, s)))

        msg = {
            "type": "orderbook_snapshot",
            "seq":  10,
            "msg":  {
                "market_ticker": "TICKER-A",
                "yes": [[0.50, 100]],
                "no":  [[0.48, 200]],
            },
        }
        asyncio.run(client._handle_message(msg))
        assert any(t == "TICKER-A" for t, _ in updates)

    def test_called_on_orderbook_delta(self):
        updates: List[Tuple] = []
        client = _make_client(on_price_update=lambda t, s: updates.append((t, s)))

        # First give it a snapshot so book.seq is set
        snap_msg = {
            "type": "orderbook_snapshot",
            "seq":  1,
            "msg":  {"market_ticker": "TICKER-A", "yes": [[0.50, 100]], "no": []},
        }
        asyncio.run(client._handle_message(snap_msg))
        updates.clear()

        delta_msg = {
            "type": "orderbook_delta",
            "seq":  2,
            "msg":  {"market_ticker": "TICKER-A", "yes": [[0.51, 50]], "no": []},
        }
        asyncio.run(client._handle_message(delta_msg))
        assert any(t == "TICKER-A" for t, _ in updates)

    def test_no_callback_does_not_raise(self):
        """If on_price_update is None, processing must not raise."""
        client = _make_client()
        msg = {"type": "ticker", "seq": 1, "msg": {"market_ticker": "TICKER-A", "yes_bid": 0.40}}
        asyncio.run(client._handle_message(msg))

    def test_callback_exception_does_not_propagate(self):
        """A raising callback must not crash the message handler."""
        def bad_cb(t, s):
            raise RuntimeError("callback error")

        client = _make_client(on_price_update=bad_cb)
        msg = {"type": "ticker", "seq": 1, "msg": {"market_ticker": "TICKER-A", "yes_bid": 0.40}}
        # Should not raise:
        asyncio.run(client._handle_message(msg))

    def test_seq_passed_to_callback(self):
        seqs: List[Optional[int]] = []
        client = _make_client(on_price_update=lambda t, s: seqs.append(s))
        msg = {"type": "ticker", "seq": 99, "msg": {"market_ticker": "TICKER-A"}}
        asyncio.run(client._handle_message(msg))
        assert 99 in seqs

    def test_none_seq_passed_when_absent(self):
        seqs: List[Optional[int]] = []
        client = _make_client(on_price_update=lambda t, s: seqs.append(s))
        msg = {"type": "ticker", "msg": {"market_ticker": "TICKER-A"}}
        asyncio.run(client._handle_message(msg))
        assert None in seqs


# ---------------------------------------------------------------------------
# on_seq_gap callback
# ---------------------------------------------------------------------------

class TestOnSeqGapCallback:
    def _prime_seq(self, client, ticker: str, seq: int):
        """Apply a snapshot to set the book's seq baseline."""
        msg = {
            "type": "orderbook_snapshot",
            "seq":  seq,
            "msg":  {"market_ticker": ticker, "yes": [], "no": []},
        }
        asyncio.run(client._handle_message(msg))

    def test_gap_callback_fired_on_snapshot(self):
        gaps: List[Tuple] = []
        client = _make_client(on_seq_gap=lambda t, g, e: gaps.append((t, g, e)))

        self._prime_seq(client, "TICKER-A", 10)
        gaps.clear()

        # Jump from 10 to 15 (gap of 4)
        msg = {
            "type": "orderbook_snapshot",
            "seq":  15,
            "msg":  {"market_ticker": "TICKER-A", "yes": [], "no": []},
        }
        asyncio.run(client._handle_message(msg))
        assert len(gaps) == 1
        t, got, expected = gaps[0]
        assert t == "TICKER-A"
        assert got == 15
        assert expected == 11

    def test_gap_callback_fired_on_delta(self):
        gaps: List[Tuple] = []
        client = _make_client(on_seq_gap=lambda t, g, e: gaps.append((t, g, e)))

        self._prime_seq(client, "TICKER-A", 5)
        gaps.clear()

        msg = {
            "type": "orderbook_delta",
            "seq":  9,
            "msg":  {"market_ticker": "TICKER-A", "yes": [], "no": []},
        }
        asyncio.run(client._handle_message(msg))
        assert len(gaps) == 1

    def test_no_gap_callback_on_consecutive_seq(self):
        gaps: List[Tuple] = []
        client = _make_client(on_seq_gap=lambda t, g, e: gaps.append((t, g, e)))

        self._prime_seq(client, "TICKER-A", 5)
        gaps.clear()

        msg = {
            "type": "orderbook_delta",
            "seq":  6,
            "msg":  {"market_ticker": "TICKER-A", "yes": [], "no": []},
        }
        asyncio.run(client._handle_message(msg))
        assert len(gaps) == 0

    def test_gap_callback_exception_does_not_propagate(self):
        def bad_cb(t, g, e):
            raise RuntimeError("gap callback error")

        client = _make_client(on_seq_gap=bad_cb)
        self._prime_seq(client, "TICKER-A", 10)

        msg = {
            "type": "orderbook_snapshot",
            "seq":  20,
            "msg":  {"market_ticker": "TICKER-A", "yes": [], "no": []},
        }
        asyncio.run(client._handle_message(msg))  # should not raise

    def test_no_callback_no_raise(self):
        client = _make_client()
        self._prime_seq(client, "TICKER-A", 5)
        msg = {
            "type": "orderbook_snapshot",
            "seq":  10,
            "msg":  {"market_ticker": "TICKER-A", "yes": [], "no": []},
        }
        asyncio.run(client._handle_message(msg))
