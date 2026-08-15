"""
tests/test_ws_helpers.py
==========================
Unit tests for helper functions in data/websocket_client.py:
  - _sf(): safe float conversion with Kalshi price normalization
  - _ws_auth_headers(): RSA-PSS auth headers for WS handshake
  - KalshiWebSocketClient._next_id(): increments cmd_id counter
  - KalshiWebSocketClient.stop(): sets _running to False

No DB, no network.
"""
from __future__ import annotations

import sys
import base64
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(
            KALSHI_WS_URL="wss://trading-api.kalshi.com/trade-api/ws/v2",
            KALSHI_KEY_ID="",
            KALSHI_PRIVKEY_PATH="",
        ),
    }):
        yield


# ---------------------------------------------------------------------------
# _sf — safe float with Kalshi normalization
# ---------------------------------------------------------------------------

class TestSf:
    def test_none_returns_none(self):
        from feeds.websocket_client import _sf
        assert _sf(None) is None

    def test_normal_float_unchanged(self):
        from feeds.websocket_client import _sf
        assert _sf(0.45) == pytest.approx(0.45)

    def test_int_cents_gt_1_normalized(self):
        """Kalshi sends prices as cents (e.g. 45) → should become 0.45."""
        from feeds.websocket_client import _sf
        assert _sf(45) == pytest.approx(0.45)

    def test_float_as_string(self):
        from feeds.websocket_client import _sf
        assert _sf("0.55") == pytest.approx(0.55)

    def test_string_cents(self):
        from feeds.websocket_client import _sf
        assert _sf("60") == pytest.approx(0.60)

    def test_zero_returns_zero(self):
        from feeds.websocket_client import _sf
        assert _sf(0) == pytest.approx(0.0)

    def test_one_returns_one(self):
        """1.0 is NOT > 1.0, so stays at 1.0."""
        from feeds.websocket_client import _sf
        assert _sf(1.0) == pytest.approx(1.0)

    def test_invalid_string_returns_none(self):
        from feeds.websocket_client import _sf
        assert _sf("not-a-number") is None

    def test_type_error_returns_none(self):
        from feeds.websocket_client import _sf
        assert _sf([1, 2, 3]) is None

    def test_boundary_99_normalized(self):
        """Price of 99 → 0.99."""
        from feeds.websocket_client import _sf
        assert _sf(99) == pytest.approx(0.99)


# ---------------------------------------------------------------------------
# _ws_auth_headers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rsa_key():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )


class TestWsAuthHeaders:
    def test_returns_dict(self, rsa_key):
        from feeds.websocket_client import _ws_auth_headers
        result = _ws_auth_headers("test-key", rsa_key)
        assert isinstance(result, dict)

    def test_has_kalshi_access_key(self, rsa_key):
        from feeds.websocket_client import _ws_auth_headers
        result = _ws_auth_headers("test-key-123", rsa_key)
        assert "KALSHI-ACCESS-KEY" in result
        assert result["KALSHI-ACCESS-KEY"] == "test-key-123"

    def test_has_timestamp_header(self, rsa_key):
        from feeds.websocket_client import _ws_auth_headers
        result = _ws_auth_headers("k", rsa_key)
        assert "KALSHI-ACCESS-TIMESTAMP" in result
        assert result["KALSHI-ACCESS-TIMESTAMP"].isdigit()

    def test_has_signature_header(self, rsa_key):
        from feeds.websocket_client import _ws_auth_headers
        result = _ws_auth_headers("k", rsa_key)
        assert "KALSHI-ACCESS-SIGNATURE" in result

    def test_signature_is_base64(self, rsa_key):
        from feeds.websocket_client import _ws_auth_headers
        result = _ws_auth_headers("k", rsa_key)
        sig = result["KALSHI-ACCESS-SIGNATURE"]
        decoded = base64.b64decode(sig)
        assert len(decoded) > 0


# ---------------------------------------------------------------------------
# KalshiWebSocketClient._next_id
# ---------------------------------------------------------------------------

class TestNextId:
    def _make_client(self):
        from feeds.websocket_client import KalshiWebSocketClient
        # Use unauthenticated client (no private key)
        client = KalshiWebSocketClient.__new__(KalshiWebSocketClient)
        client._cmd_id = 0
        client._running = True
        return client

    def test_first_call_returns_one(self):
        client = self._make_client()
        assert client._next_id() == 1

    def test_increments_on_each_call(self):
        client = self._make_client()
        ids = [client._next_id() for _ in range(5)]
        assert ids == [1, 2, 3, 4, 5]

    def test_cmd_id_updated(self):
        client = self._make_client()
        client._next_id()
        client._next_id()
        assert client._cmd_id == 2


# ---------------------------------------------------------------------------
# KalshiWebSocketClient.stop
# ---------------------------------------------------------------------------

class TestWsClientStop:
    def _make_client(self):
        from feeds.websocket_client import KalshiWebSocketClient
        client = KalshiWebSocketClient.__new__(KalshiWebSocketClient)
        client._running = True
        client._cmd_id = 0
        return client

    def test_stop_sets_running_false(self):
        client = self._make_client()
        client.stop()
        assert client._running is False

    def test_stop_idempotent(self):
        client = self._make_client()
        client.stop()
        client.stop()
        assert client._running is False
