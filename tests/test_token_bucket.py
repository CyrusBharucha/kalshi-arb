"""
tests/test_token_bucket.py
============================
Unit tests for data/kalshi_client.py TokenBucket class.

TokenBucket implements a simple token bucket for rate-limiting.
Tests cover:
  - Initial state: tokens == rate
  - consume(): deducts tokens
  - consume(): refills tokens based on elapsed time
  - consume(): sleeps when tokens insufficient
  - consume(): does not over-fill beyond rate

No DB, no network.
"""
from __future__ import annotations

import sys
import time
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_config():
    """Patch config so importing kalshi_client works without .env."""
    with patch.dict(sys.modules, {
        "config": MagicMock(
            KALSHI_BASE_URL="https://trading-api.kalshi.com/trade-api/v2",
            KALSHI_READ_RPS=10.0,
            KALSHI_WRITE_RPS=1.0,
            KALSHI_KEY_ID="",
            KALSHI_PRIVKEY_PATH="",
        )
    }):
        yield


def _make_bucket(rate=5.0):
    from feeds.kalshi_client import TokenBucket
    return TokenBucket(rate=rate)


class TestTokenBucketInit:
    def test_initial_tokens_equals_rate(self):
        b = _make_bucket(rate=10.0)
        assert b._tokens == pytest.approx(10.0)

    def test_rate_stored(self):
        b = _make_bucket(rate=3.0)
        assert b._rate == pytest.approx(3.0)

    def test_last_is_recent(self):
        b = _make_bucket(rate=5.0)
        assert b._last <= time.monotonic()


class TestTokenBucketConsume:
    def test_consume_deducts_tokens(self):
        b = _make_bucket(rate=10.0)
        b._tokens = 5.0  # set directly to avoid time effects
        b._last = time.monotonic()  # no elapsed time
        with patch("time.monotonic", side_effect=[b._last, b._last]):
            b.consume(1.0)
        assert b._tokens == pytest.approx(4.0)

    def test_consume_no_sleep_when_tokens_sufficient(self):
        b = _make_bucket(rate=10.0)
        b._tokens = 5.0
        b._last = time.monotonic()
        with patch("time.monotonic", side_effect=[b._last, b._last]):
            with patch("time.sleep") as mock_sleep:
                b.consume(1.0)
        mock_sleep.assert_not_called()

    def test_consume_sleeps_when_tokens_insufficient(self):
        b = _make_bucket(rate=1.0)
        b._tokens = 0.0
        b._last = time.monotonic()
        with patch("time.monotonic", side_effect=[b._last, b._last]):
            with patch("time.sleep") as mock_sleep:
                b.consume(1.0)
        mock_sleep.assert_called_once()

    def test_consume_sleep_duration_proportional(self):
        b = _make_bucket(rate=2.0)  # 2 tokens/sec
        b._tokens = 0.0
        b._last = time.monotonic()
        sleep_times = []
        with patch("time.monotonic", side_effect=[b._last, b._last]):
            with patch("time.sleep", side_effect=lambda t: sleep_times.append(t)):
                b.consume(1.0)  # need 1 token at 2/sec → sleep 0.5s
        assert len(sleep_times) == 1
        assert sleep_times[0] == pytest.approx(0.5)

    def test_tokens_refill_over_time(self):
        """After 1 second with rate=5, tokens should increase by ~5."""
        b = _make_bucket(rate=5.0)
        b._tokens = 0.0
        t0 = time.monotonic()
        b._last = t0
        # Simulate 1 second elapsed
        with patch("time.monotonic", return_value=t0 + 1.0):
            with patch("time.sleep"):  # consume may sleep; don't actually sleep
                b.consume(1.0)
        # After consuming 1 from 5 refilled tokens, should have ~4
        assert b._tokens == pytest.approx(4.0)

    def test_tokens_capped_at_rate(self):
        """Tokens should not exceed rate even after long elapsed time."""
        b = _make_bucket(rate=5.0)
        b._tokens = 3.0
        t0 = time.monotonic()
        b._last = t0
        # Simulate 100 seconds elapsed → would refill 500 tokens, but capped at 5
        with patch("time.monotonic", return_value=t0 + 100.0):
            with patch("time.sleep"):
                b.consume(1.0)
        assert b._tokens == pytest.approx(4.0)  # capped at 5.0, then consumed 1.0

    def test_after_sleep_tokens_zero(self):
        """After sleeping for insufficient tokens, _tokens should be 0."""
        b = _make_bucket(rate=1.0)
        b._tokens = 0.0
        b._last = time.monotonic()
        with patch("time.monotonic", side_effect=[b._last, b._last]):
            with patch("time.sleep"):
                b.consume(1.0)
        assert b._tokens == pytest.approx(0.0)
