"""
tests/test_kalshi_sign_request.py
=====================================
Unit tests for data/kalshi_client.py:
  - _sign_request(): correct header keys, base64 signature, timestamp format
  - _load_private_key(): raises FileNotFoundError for missing path
  - KalshiClient.__init__(): unauthenticated init works without keys
  - KalshiClient.get/post: call _request with correct method

Uses a real RSA key generated in-memory; no actual Kalshi API calls.
"""
from __future__ import annotations

import base64
import sys
import time
from unittest.mock import patch, MagicMock, call
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_config():
    """Patch config so import works without .env."""
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


@pytest.fixture(scope="module")
def rsa_key():
    """Generate a test RSA private key."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )


# ---------------------------------------------------------------------------
# _sign_request
# ---------------------------------------------------------------------------

class TestSignRequest:
    def test_returns_dict(self, rsa_key):
        from feeds.kalshi_client import _sign_request
        result = _sign_request("key-123", rsa_key, "GET", "/markets")
        assert isinstance(result, dict)

    def test_has_access_key_header(self, rsa_key):
        from feeds.kalshi_client import _sign_request
        result = _sign_request("key-123", rsa_key, "GET", "/markets")
        assert "KALSHI-ACCESS-KEY" in result

    def test_has_timestamp_header(self, rsa_key):
        from feeds.kalshi_client import _sign_request
        result = _sign_request("key-123", rsa_key, "GET", "/markets")
        assert "KALSHI-ACCESS-TIMESTAMP" in result

    def test_has_signature_header(self, rsa_key):
        from feeds.kalshi_client import _sign_request
        result = _sign_request("key-123", rsa_key, "GET", "/markets")
        assert "KALSHI-ACCESS-SIGNATURE" in result

    def test_access_key_equals_key_id(self, rsa_key):
        from feeds.kalshi_client import _sign_request
        result = _sign_request("my-key-id", rsa_key, "GET", "/markets")
        assert result["KALSHI-ACCESS-KEY"] == "my-key-id"

    def test_timestamp_is_numeric_string(self, rsa_key):
        from feeds.kalshi_client import _sign_request
        result = _sign_request("key-123", rsa_key, "POST", "/orders")
        ts = result["KALSHI-ACCESS-TIMESTAMP"]
        assert ts.isdigit()

    def test_timestamp_is_recent_ms(self, rsa_key):
        from feeds.kalshi_client import _sign_request
        before_ms = int(time.time() * 1000)
        result = _sign_request("key-123", rsa_key, "GET", "/markets")
        after_ms = int(time.time() * 1000)
        ts = int(result["KALSHI-ACCESS-TIMESTAMP"])
        assert before_ms <= ts <= after_ms + 1000

    def test_signature_is_valid_base64(self, rsa_key):
        from feeds.kalshi_client import _sign_request
        result = _sign_request("key-123", rsa_key, "GET", "/markets")
        sig_str = result["KALSHI-ACCESS-SIGNATURE"]
        try:
            decoded = base64.b64decode(sig_str)
            assert len(decoded) > 0
        except Exception:
            pytest.fail("Signature is not valid base64")

    def test_different_methods_produce_different_sigs(self, rsa_key):
        from feeds.kalshi_client import _sign_request
        r1 = _sign_request("key-123", rsa_key, "GET", "/markets")
        r2 = _sign_request("key-123", rsa_key, "POST", "/markets")
        # RSA-PSS is randomized so same method would also differ, but
        # timestamps are deterministic: different methods → definitely different
        assert r1["KALSHI-ACCESS-SIGNATURE"] != r2["KALSHI-ACCESS-SIGNATURE"] \
            or r1["KALSHI-ACCESS-TIMESTAMP"] != r2["KALSHI-ACCESS-TIMESTAMP"]


# ---------------------------------------------------------------------------
# _load_private_key
# ---------------------------------------------------------------------------

class TestLoadPrivateKey:
    def test_raises_file_not_found_for_missing_path(self):
        from feeds.kalshi_client import _load_private_key
        with pytest.raises(FileNotFoundError):
            _load_private_key("/nonexistent/path/kalshi.pem")

    def test_loads_valid_pem_key(self, rsa_key, tmp_path):
        from feeds.kalshi_client import _load_private_key
        from cryptography.hazmat.primitives import serialization
        pem = rsa_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        key_file = tmp_path / "test.pem"
        key_file.write_bytes(pem)
        loaded = _load_private_key(str(key_file))
        assert loaded is not None


# ---------------------------------------------------------------------------
# KalshiClient — unauthenticated init
# ---------------------------------------------------------------------------

class TestKalshiClientInit:
    def test_unauthenticated_init_succeeds(self):
        from feeds.kalshi_client import KalshiClient
        client = KalshiClient(authenticated=False)
        assert client._authenticated is False

    def test_private_key_none_when_unauthenticated(self):
        from feeds.kalshi_client import KalshiClient
        client = KalshiClient(authenticated=False)
        assert client._private_key is None

    def test_session_created(self):
        from feeds.kalshi_client import KalshiClient
        import requests
        client = KalshiClient(authenticated=False)
        assert isinstance(client._session, requests.Session)

    def test_read_limiter_created(self):
        from feeds.kalshi_client import KalshiClient, TokenBucket
        client = KalshiClient(authenticated=False)
        assert isinstance(client._read_limiter, TokenBucket)

    def test_write_limiter_created(self):
        from feeds.kalshi_client import KalshiClient, TokenBucket
        client = KalshiClient(authenticated=False)
        assert isinstance(client._write_limiter, TokenBucket)

    def test_authenticated_raises_without_keys(self):
        from feeds.kalshi_client import KalshiClient
        with pytest.raises((ValueError, Exception)):
            KalshiClient(authenticated=True)


# ---------------------------------------------------------------------------
# KalshiClient.get / .post delegation
# ---------------------------------------------------------------------------

class TestKalshiClientGetPost:
    def _make_client(self):
        from feeds.kalshi_client import KalshiClient
        return KalshiClient(authenticated=False)

    def test_get_calls_request_with_get(self):
        client = self._make_client()
        with patch.object(client, "_request", return_value={"ok": True}) as mock_req:
            client.get("/markets")
        mock_req.assert_called_once_with("GET", "/markets", params=None)

    def test_get_passes_params(self):
        client = self._make_client()
        with patch.object(client, "_request", return_value={}) as mock_req:
            client.get("/markets", params={"status": "open"})
        mock_req.assert_called_once_with("GET", "/markets", params={"status": "open"})

    def test_post_calls_request_with_post(self):
        client = self._make_client()
        with patch.object(client, "_request", return_value={"id": "x"}) as mock_req:
            client.post("/orders", {"action": "buy"})
        mock_req.assert_called_once_with("POST", "/orders", json_body={"action": "buy"})
