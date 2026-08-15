"""
tests/test_websocket_client_helpers.py
========================================
Unit tests for data/websocket_client.py pure helpers:
  - _sf(): None guard, normal decimal, cents normalization (>1.0 -> /100),
    invalid string returns None
  - _ws_auth_headers(): header structure, key_id in headers, ts numeric,
    sig is base64, method is always GET (WS handshake)
  - _load_private_key(): FileNotFoundError on missing file

No network, no real WS connection.
"""
from __future__ import annotations

import base64
import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


class TestSfHelper:
    """_sf(): safe float with cents normalization."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from feeds.websocket_client import _sf
        self.fn = _sf

    def test_none_returns_none(self):
        assert self.fn(None) is None

    def test_valid_decimal_below_one(self):
        assert abs(self.fn(0.55) - 0.55) < 1e-9

    def test_zero_returns_zero(self):
        assert self.fn(0) == 0.0

    def test_cents_int_normalized(self):
        # 55 cents -> 0.55
        assert abs(self.fn(55) - 0.55) < 1e-9

    def test_cents_99_normalized(self):
        assert abs(self.fn(99) - 0.99) < 1e-9

    def test_value_at_boundary_one(self):
        # f = 1.0, not > 1.0, so returned as 1.0
        assert self.fn(1.0) == 1.0

    def test_value_just_above_one_normalized(self):
        # f = 2.0 -> 2.0/100 = 0.02
        assert abs(self.fn(2.0) - 0.02) < 1e-9

    def test_string_decimal_parsed(self):
        assert abs(self.fn("0.45") - 0.45) < 1e-9

    def test_invalid_string_returns_none(self):
        assert self.fn("abc") is None

    def test_empty_string_returns_none(self):
        assert self.fn("") is None

    def test_returns_float_or_none(self):
        result = self.fn(0.50)
        assert isinstance(result, float)


class TestWsAuthHeaders:
    """_ws_auth_headers(): builds auth headers for WS upgrade."""

    def test_returns_three_headers(self):
        from feeds.websocket_client import _ws_auth_headers
        mock_key = MagicMock()
        mock_key.sign.return_value = b"\x00" * 32
        headers = _ws_auth_headers("my-key-id", mock_key)
        assert len(headers) == 3

    def test_access_key_header_present(self):
        from feeds.websocket_client import _ws_auth_headers
        mock_key = MagicMock()
        mock_key.sign.return_value = b"\x00" * 32
        headers = _ws_auth_headers("test-id", mock_key)
        assert "KALSHI-ACCESS-KEY" in headers

    def test_access_key_matches_key_id(self):
        from feeds.websocket_client import _ws_auth_headers
        mock_key = MagicMock()
        mock_key.sign.return_value = b"\x00" * 32
        headers = _ws_auth_headers("abc-123", mock_key)
        assert headers["KALSHI-ACCESS-KEY"] == "abc-123"

    def test_timestamp_is_numeric_string(self):
        from feeds.websocket_client import _ws_auth_headers
        mock_key = MagicMock()
        mock_key.sign.return_value = b"\x00" * 32
        headers = _ws_auth_headers("k", mock_key)
        assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()

    def test_signature_is_valid_base64(self):
        from feeds.websocket_client import _ws_auth_headers
        mock_key = MagicMock()
        raw_sig = b"\xAB\xCD\xEF" * 10
        mock_key.sign.return_value = raw_sig
        headers = _ws_auth_headers("k", mock_key)
        decoded = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
        assert decoded == raw_sig

    def test_message_contains_GET(self):
        from feeds.websocket_client import _ws_auth_headers
        mock_key = MagicMock()
        mock_key.sign.return_value = b"\x00" * 32
        _ws_auth_headers("k", mock_key)
        msg_bytes = mock_key.sign.call_args.args[0]
        assert b"GET" in msg_bytes


class TestWsClientLoadPrivateKey:
    """_load_private_key() from websocket_client: error path."""

    def test_missing_file_raises_file_not_found(self, tmp_path):
        from feeds.websocket_client import _load_private_key
        missing = str(tmp_path / "nope.pem")
        with pytest.raises(FileNotFoundError):
            _load_private_key(missing)

    def test_error_message_contains_path(self, tmp_path):
        from feeds.websocket_client import _load_private_key
        missing = str(tmp_path / "nope.pem")
        with pytest.raises(FileNotFoundError) as exc_info:
            _load_private_key(missing)
        assert str(missing) in str(exc_info.value)
