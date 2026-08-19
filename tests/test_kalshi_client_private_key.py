"""
tests/test_kalshi_client_private_key.py
==========================================
Unit tests for data/kalshi_client.py _load_private_key():
  - Raises FileNotFoundError when path does not exist
  - Loads a real PEM key bytes when file exists (mocked open)
  - Error message contains the missing path

No network, no real key file.
"""
from __future__ import annotations

from unittest.mock import patch, mock_open, MagicMock
import pytest


class TestLoadPrivateKeyErrors:
    """_load_private_key(): error paths."""

    def test_missing_file_raises_file_not_found(self, tmp_path):
        from feeds.kalshi_client import _load_private_key
        missing = str(tmp_path / "nonexistent.pem")
        with pytest.raises(FileNotFoundError):
            _load_private_key(missing)

    def test_error_message_contains_path(self, tmp_path):
        from feeds.kalshi_client import _load_private_key
        missing = str(tmp_path / "nonexistent.pem")
        with pytest.raises(FileNotFoundError) as exc_info:
            _load_private_key(missing)
        assert str(missing) in str(exc_info.value)

    def test_valid_path_calls_load_pem(self, tmp_path):
        from feeds.kalshi_client import _load_private_key
        # Create a real (but dummy) file so exists() returns True
        pem_file = tmp_path / "key.pem"
        pem_file.write_bytes(b"FAKEPEM")
        mock_key = MagicMock()
        with patch(
            "feeds.kalshi_client.serialization.load_pem_private_key",
            return_value=mock_key,
        ) as mock_load:
            result = _load_private_key(str(pem_file))
        mock_load.assert_called_once()
        assert result is mock_key

    def test_pem_content_passed_to_loader(self, tmp_path):
        pem_file = tmp_path / "key.pem"
        pem_file.write_bytes(b"MYPEMDATA")
        mock_key = MagicMock()
        with patch(
            "feeds.kalshi_client.serialization.load_pem_private_key",
            return_value=mock_key,
        ) as mock_load:
            from feeds.kalshi_client import _load_private_key
            _load_private_key(str(pem_file))
        args, kwargs = mock_load.call_args
        assert args[0] == b"MYPEMDATA"


class TestSignRequestHeaders:
    """_sign_request(): header structure (mocked private key)."""

    def test_returns_dict_with_three_headers(self):
        from feeds.kalshi_client import _sign_request
        mock_key = MagicMock()
        mock_key.sign.return_value = b"\x00" * 32
        headers = _sign_request("my-key-id", mock_key, "GET", "/trade-api/v2/markets")
        assert "KALSHI-ACCESS-KEY" in headers
        assert "KALSHI-ACCESS-TIMESTAMP" in headers
        assert "KALSHI-ACCESS-SIGNATURE" in headers

    def test_key_id_in_headers(self):
        from feeds.kalshi_client import _sign_request
        mock_key = MagicMock()
        mock_key.sign.return_value = b"\x01" * 32
        headers = _sign_request("abc-key", mock_key, "GET", "/path")
        assert headers["KALSHI-ACCESS-KEY"] == "abc-key"

    def test_timestamp_is_numeric_string(self):
        from feeds.kalshi_client import _sign_request
        mock_key = MagicMock()
        mock_key.sign.return_value = b"\x02" * 32
        headers = _sign_request("k", mock_key, "POST", "/path")
        ts = headers["KALSHI-ACCESS-TIMESTAMP"]
        assert ts.isdigit()

    def test_signature_is_base64_string(self):
        import base64
        from feeds.kalshi_client import _sign_request
        mock_key = MagicMock()
        mock_key.sign.return_value = b"\x03" * 32
        headers = _sign_request("k", mock_key, "GET", "/path")
        sig = headers["KALSHI-ACCESS-SIGNATURE"]
        # Should decode cleanly
        decoded = base64.b64decode(sig)
        assert decoded == b"\x03" * 32

    def test_sign_called_with_correct_method_upper(self):
        from feeds.kalshi_client import _sign_request
        mock_key = MagicMock()
        mock_key.sign.return_value = b"\x00" * 32
        _sign_request("k", mock_key, "get", "/path")
        call_args = mock_key.sign.call_args
        # Message should have "GET" (uppercase)
        msg_bytes = call_args.args[0]
        assert b"GET" in msg_bytes
