"""
tests/test_ws_bridge.py
========================
Unit tests for dashboard/ws_bridge.py:
  - ensure_ws_running() returns None when SYNTHESIS_SECRET_KEY is absent
  - singleton: calling twice returns the same object (when key is present)
  - import error path returns None gracefully
  - module-level _client starts as None

No real WebSocket connection — all external dependencies are mocked.
"""
from __future__ import annotations

import threading
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_ws_singleton():
    """Reset the ws_bridge singleton before and after each test."""
    import importlib
    import dashboard.ws_bridge as bridge
    bridge._client = None
    yield
    bridge._client = None


class TestEnsureWsRunningNoKey:
    """When SYNTHESIS_SECRET_KEY is not set, ensure_ws_running() returns None."""

    def test_returns_none_when_no_key(self):
        with patch.dict("os.environ", {"SYNTHESIS_SECRET_KEY": ""}, clear=False):
            from dashboard.ws_bridge import ensure_ws_running
            result = ensure_ws_running()
        assert result is None

    def test_returns_none_when_key_missing_from_env(self):
        import os
        env = {k: v for k, v in os.environ.items() if k != "SYNTHESIS_SECRET_KEY"}
        with patch.dict("os.environ", env, clear=True):
            from dashboard.ws_bridge import ensure_ws_running
            result = ensure_ws_running()
        assert result is None

    def test_returns_none_when_key_is_whitespace(self):
        with patch.dict("os.environ", {"SYNTHESIS_SECRET_KEY": "   "}, clear=False):
            from dashboard.ws_bridge import ensure_ws_running
            result = ensure_ws_running()
        assert result is None


class TestEnsureWsRunningWithKey:
    """When SYNTHESIS_SECRET_KEY is set, ensure_ws_running() attempts to start client."""

    def _make_mock_client(self):
        mock_client = MagicMock()
        mock_client._on_open = MagicMock()
        mock_client._on_close = MagicMock()
        mock_client._on_error = MagicMock()
        return mock_client

    def _patches(self, mock_client, mock_cache, mock_state, tickers=None):
        """
        ws_bridge uses local `from X import Y` inside the function body,
        so we must patch the source modules, not attributes of ws_bridge.
        """
        mock_l2_module = MagicMock()
        mock_l2_module.L2Cache.return_value = mock_cache

        mock_synth_module = MagicMock()
        mock_synth_module.SynthesisWebSocketClient.return_value = mock_client

        mock_livestate_module = MagicMock()
        mock_livestate_module.get_live_state.return_value = mock_state

        mock_discovery_module = MagicMock()
        mock_discovery_module.fetch_liquid_tickers.return_value = tickers or []
        # fetch_synthesis_markets returns (tickers, event_groups) tuple
        mock_discovery_module.fetch_synthesis_markets.return_value = (tickers or [], {})

        return {
            "feeds.orderbook_l2": mock_l2_module,
            "feeds.synthesis_live": mock_synth_module,
            "dashboard.live_state": mock_livestate_module,
            "dashboard.market_discovery": mock_discovery_module,
        }

    def test_returns_client_when_key_set(self):
        mock_client = self._make_mock_client()
        mock_cache = MagicMock()
        mock_state = MagicMock()
        mods = self._patches(mock_client, mock_cache, mock_state, tickers=["A-YES"])

        with patch.dict("os.environ", {"SYNTHESIS_SECRET_KEY": "sk_test123"}, clear=False), \
             patch.dict("sys.modules", mods):
            from dashboard.ws_bridge import ensure_ws_running
            result = ensure_ws_running()
        assert result is mock_client

    def test_singleton_returns_same_client(self):
        mock_client = self._make_mock_client()
        mock_cache = MagicMock()
        mock_state = MagicMock()
        mods = self._patches(mock_client, mock_cache, mock_state)

        with patch.dict("os.environ", {"SYNTHESIS_SECRET_KEY": "sk_test123"}, clear=False), \
             patch.dict("sys.modules", mods):
            from dashboard.ws_bridge import ensure_ws_running
            r1 = ensure_ws_running()
            r2 = ensure_ws_running()
        assert r1 is r2

    def test_client_start_called(self):
        mock_client = self._make_mock_client()
        mock_cache = MagicMock()
        mock_state = MagicMock()
        mods = self._patches(mock_client, mock_cache, mock_state)

        with patch.dict("os.environ", {"SYNTHESIS_SECRET_KEY": "sk_test123"}, clear=False), \
             patch.dict("sys.modules", mods):
            from dashboard.ws_bridge import ensure_ws_running
            ensure_ws_running()
        mock_client.start.assert_called_once()

    def test_callback_registered_on_cache(self):
        mock_client = self._make_mock_client()
        mock_cache = MagicMock()
        mock_state = MagicMock()
        mods = self._patches(mock_client, mock_cache, mock_state)

        with patch.dict("os.environ", {"SYNTHESIS_SECRET_KEY": "sk_test123"}, clear=False), \
             patch.dict("sys.modules", mods):
            from dashboard.ws_bridge import ensure_ws_running
            ensure_ws_running()
        mock_cache.register_callback.assert_called_once()


class TestEnsureWsRunningImportError:
    """When imports fail, ensure_ws_running() returns None gracefully."""

    def test_import_error_returns_none(self):
        """Simulate ImportError by injecting a module that raises on attribute access."""
        import sys
        bad_module = MagicMock()
        bad_module.L2Cache.side_effect = ImportError("no module")

        with patch.dict("os.environ", {"SYNTHESIS_SECRET_KEY": "sk_test123"}, clear=False), \
             patch.dict("sys.modules", {"feeds.orderbook_l2": bad_module}):
            import dashboard.ws_bridge as bridge
            bridge._client = None
            result = bridge.ensure_ws_running()
        assert result is None

    def test_general_exception_returns_none(self):
        mock_cache = MagicMock()
        mock_state = MagicMock()

        mock_l2 = MagicMock()
        mock_l2.L2Cache.return_value = mock_cache

        mock_synth = MagicMock()
        mock_synth.SynthesisWebSocketClient.side_effect = RuntimeError("crash")

        mock_ls = MagicMock()
        mock_ls.get_live_state.return_value = mock_state

        mock_disc = MagicMock()
        mock_disc.fetch_liquid_tickers.return_value = []

        mods = {
            "feeds.orderbook_l2": mock_l2,
            "feeds.synthesis_live": mock_synth,
            "dashboard.live_state": mock_ls,
            "dashboard.market_discovery": mock_disc,
        }

        with patch.dict("os.environ", {"SYNTHESIS_SECRET_KEY": "sk_test123"}, clear=False), \
             patch.dict("sys.modules", mods):
            import dashboard.ws_bridge as bridge
            bridge._client = None
            result = bridge.ensure_ws_running()
        assert result is None


class TestModuleLevelState:
    """Module-level _client starts as None."""

    def test_client_starts_as_none(self):
        import dashboard.ws_bridge as bridge
        # The fixture resets _client to None before each test
        assert bridge._client is None

    def test_client_lock_is_threading_lock(self):
        import dashboard.ws_bridge as bridge
        assert isinstance(bridge._client_lock, type(threading.Lock()))
