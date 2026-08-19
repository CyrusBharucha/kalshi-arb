"""
tests/test_kalshi_client_helpers.py
=====================================
Unit tests for non-HTTP helper functions in data/kalshi_client.py:
  - TokenBucket (token-bucket rate limiter)
  - KalshiClient.paginate (cursor pagination logic)
  - KalshiClient constructor (unauthenticated path)

No real HTTP calls are made; requests.Session is monkeypatched.
"""
from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# TokenBucket tests
# ---------------------------------------------------------------------------

class TestTokenBucket:
    """TokenBucket: simple token-bucket rate limiter."""

    @pytest.fixture(autouse=True)
    def _import(self):
        # kalshi_client imports cryptography + config; patch config env vars
        with patch.dict("os.environ", {
            "KALSHI_KEY_ID": "",
            "KALSHI_PRIVKEY_PATH": "",
        }):
            from feeds.kalshi_client import TokenBucket
            self.TokenBucket = TokenBucket

    def test_instantiates(self):
        tb = self.TokenBucket(rate=10.0)
        assert tb is not None

    def test_initial_tokens_equal_rate(self):
        tb = self.TokenBucket(rate=5.0)
        assert tb._tokens == 5.0
        assert tb._rate == 5.0

    def test_consume_without_sleep_decrements_tokens(self):
        tb = self.TokenBucket(rate=10.0)
        # Tokens start at 10; consuming 1 should leave ~9
        with patch("time.sleep") as mock_sleep:
            tb.consume(1.0)
            mock_sleep.assert_not_called()

    def test_consume_when_empty_triggers_sleep(self):
        tb = self.TokenBucket(rate=1.0)
        tb._tokens = 0.0  # drain the bucket manually
        with patch("time.sleep") as mock_sleep, \
             patch("time.monotonic", return_value=time.monotonic()):
            tb.consume(1.0)
            mock_sleep.assert_called_once()
            sleep_arg = mock_sleep.call_args[0][0]
            assert sleep_arg > 0

    def test_consume_sets_tokens_to_zero_after_sleep(self):
        tb = self.TokenBucket(rate=1.0)
        tb._tokens = 0.0
        now = time.monotonic()
        with patch("time.sleep"), \
             patch("time.monotonic", return_value=now):
            tb.consume(1.0)
        assert tb._tokens == 0.0

    def test_tokens_replenish_over_time(self):
        """After elapsed time, tokens increase (capped at rate)."""
        tb = self.TokenBucket(rate=10.0)
        tb._tokens = 0.0
        start = time.monotonic()
        tb._last = start - 2.0  # simulate 2 seconds elapsed
        with patch("time.sleep"), \
             patch("time.monotonic", return_value=start):
            tb.consume(0.0)  # consume 0 to trigger replenishment check
        # After 2s at rate=10, tokens should be min(10, 0+2*10) = 10
        assert tb._tokens >= 9.0  # allow small float rounding

    def test_tokens_capped_at_rate(self):
        tb = self.TokenBucket(rate=5.0)
        tb._tokens = 0.0
        start = time.monotonic()
        tb._last = start - 100.0  # simulate long elapsed time
        with patch("time.sleep"), \
             patch("time.monotonic", return_value=start):
            tb.consume(0.0)
        assert tb._tokens <= 5.0  # capped at rate

    def test_high_rate_bucket_handles_burst(self):
        tb = self.TokenBucket(rate=100.0)
        with patch("time.sleep") as mock_sleep, \
             patch("time.monotonic", return_value=time.monotonic()):
            for _ in range(50):
                tb.consume(1.0)
            mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# KalshiClient constructor (unauthenticated) tests
# ---------------------------------------------------------------------------

class TestKalshiClientUnauthenticated:
    """KalshiClient(authenticated=False) — no credentials needed."""

    @pytest.fixture(autouse=True)
    def _import(self):
        with patch.dict("os.environ", {
            "KALSHI_KEY_ID": "",
            "KALSHI_PRIVKEY_PATH": "",
        }):
            from feeds.kalshi_client import KalshiClient
            self.KalshiClient = KalshiClient

    def test_instantiates_unauthenticated(self):
        client = self.KalshiClient(authenticated=False)
        assert client is not None

    def test_private_key_none_when_unauthenticated(self):
        client = self.KalshiClient(authenticated=False)
        assert client._private_key is None

    def test_authenticated_flag_false(self):
        client = self.KalshiClient(authenticated=False)
        assert client._authenticated is False

    def test_authenticated_raises_without_key_id(self):
        with pytest.raises((ValueError, FileNotFoundError)):
            self.KalshiClient(authenticated=True)


# ---------------------------------------------------------------------------
# KalshiClient.paginate (mocked HTTP) tests
# ---------------------------------------------------------------------------

class TestKalshiClientPaginate:
    """paginate() yields pages and stops when cursor is absent."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch.dict("os.environ", {
            "KALSHI_KEY_ID": "",
            "KALSHI_PRIVKEY_PATH": "",
        }):
            from feeds.kalshi_client import KalshiClient
            self.client = KalshiClient(authenticated=False)

    def test_single_page_no_cursor(self):
        items = [{"id": 1}, {"id": 2}]
        self.client.get = MagicMock(return_value={"markets": items, "cursor": None})
        pages = list(self.client.paginate("/markets", "markets"))
        assert len(pages) == 1
        assert pages[0] == items

    def test_two_pages_then_stops(self):
        page1 = [{"id": 1}]
        page2 = [{"id": 2}]
        self.client.get = MagicMock(side_effect=[
            {"markets": page1, "cursor": "tok1"},
            {"markets": page2, "cursor": None},
        ])
        pages = list(self.client.paginate("/markets", "markets"))
        assert len(pages) == 2
        assert pages[0] == page1
        assert pages[1] == page2

    def test_cursor_sent_in_subsequent_request(self):
        page1 = [{"id": 1}]
        page2 = [{"id": 2}]
        calls = []

        def fake_get(path, params=None):
            calls.append(dict(params or {}))
            if len(calls) == 1:
                return {"markets": page1, "cursor": "cursor_abc"}
            return {"markets": page2, "cursor": None}

        self.client.get = fake_get
        list(self.client.paginate("/markets", "markets"))
        assert "cursor" not in calls[0]
        assert calls[1].get("cursor") == "cursor_abc"

    def test_empty_items_not_yielded(self):
        self.client.get = MagicMock(return_value={"markets": [], "cursor": None})
        pages = list(self.client.paginate("/markets", "markets"))
        assert pages == []

    def test_page_size_sent_as_limit_param(self):
        captured = {}

        def fake_get(path, params=None):
            captured.update(params or {})
            return {"markets": [{"id": 1}], "cursor": None}

        self.client.get = fake_get
        list(self.client.paginate("/markets", "markets", page_size=50))
        assert captured.get("limit") == 50

    def test_extra_params_forwarded(self):
        captured = {}

        def fake_get(path, params=None):
            captured.update(params or {})
            return {"events": [{"x": 1}], "cursor": None}

        self.client.get = fake_get
        list(self.client.paginate("/events", "events", params={"status": "open"}))
        assert captured.get("status") == "open"

    def test_missing_result_key_yields_no_pages(self):
        self.client.get = MagicMock(return_value={"other": [1, 2], "cursor": None})
        pages = list(self.client.paginate("/markets", "markets"))
        assert pages == []

    def test_three_pages_total_items_correct(self):
        responses = [
            {"markets": [{"id": i}], "cursor": f"c{i}"}
            for i in range(1, 3)
        ] + [{"markets": [{"id": 3}], "cursor": None}]
        self.client.get = MagicMock(side_effect=responses)
        pages = list(self.client.paginate("/markets", "markets"))
        all_ids = [item["id"] for page in pages for item in page]
        assert sorted(all_ids) == [1, 2, 3]
