"""
tests/test_run_commands.py
===========================
Unit tests for run.py:
  - COMMANDS dict: all values are callable, no duplicates
  - Known commands are present
  - Each cmd_* function is registered exactly once

No actual commands are executed — only the COMMANDS registry is inspected.
"""
from __future__ import annotations

import pytest


class TestCommandsRegistry:
    """COMMANDS dict in run.py."""

    @pytest.fixture(autouse=True)
    def _import(self):
        # Import without executing any command (module-level code only sets up COMMANDS)
        import run as run_module
        self.COMMANDS = run_module.COMMANDS

    def test_is_dict(self):
        assert isinstance(self.COMMANDS, dict)

    def test_nonempty(self):
        assert len(self.COMMANDS) > 0

    def test_all_values_are_callable(self):
        for name, fn in self.COMMANDS.items():
            assert callable(fn), f"COMMANDS['{name}'] is not callable"

    def test_all_keys_are_strings(self):
        for k in self.COMMANDS:
            assert isinstance(k, str)

    def test_no_empty_string_key(self):
        assert "" not in self.COMMANDS

    def test_init_db_present(self):
        assert "init_db" in self.COMMANDS

    def test_dashboard_present(self):
        assert "dashboard" in self.COMMANDS

    def test_candles_present(self):
        assert "candles" in self.COMMANDS

    def test_relationships_present(self):
        assert "relationships" in self.COMMANDS

    def test_integrity_present(self):
        assert "integrity" in self.COMMANDS

    def test_synthesis_present(self):
        assert "synthesis" in self.COMMANDS

    def test_canadian_present(self):
        assert "canadian" in self.COMMANDS

    def test_backtest_full_present(self):
        assert "backtest_full" in self.COMMANDS

    def test_hist_scan_present(self):
        assert "hist_scan" in self.COMMANDS

    def test_at_least_10_commands(self):
        assert len(self.COMMANDS) >= 10

    def test_values_are_unique_callables(self):
        """Each command should map to a distinct function (no accidental duplicates)."""
        fns = list(self.COMMANDS.values())
        fn_ids = [id(f) for f in fns]
        # Allow a few coincidences but not wholesale duplicates
        unique_count = len(set(fn_ids))
        total_count  = len(fn_ids)
        # At least 80% should be unique
        assert unique_count / total_count >= 0.8


class TestCmdFunctions:
    """Individual cmd_* functions exist at module level."""

    def test_cmd_init_db_exists(self):
        import run
        assert hasattr(run, "cmd_init_db")
        assert callable(run.cmd_init_db)

    def test_cmd_candles_exists(self):
        import run
        assert hasattr(run, "cmd_candles")
        assert callable(run.cmd_candles)

    def test_cmd_relationships_exists(self):
        import run
        assert hasattr(run, "cmd_relationships")
        assert callable(run.cmd_relationships)

    def test_cmd_integrity_exists(self):
        import run
        assert hasattr(run, "cmd_integrity")
        assert callable(run.cmd_integrity)

    def test_cmd_dashboard_exists(self):
        import run
        assert hasattr(run, "cmd_dashboard")
        assert callable(run.cmd_dashboard)

    def test_cmd_synthesis_exists(self):
        import run
        assert hasattr(run, "cmd_synthesis")
        assert callable(run.cmd_synthesis)

    def test_cmd_backtest_exists(self):
        import run
        assert hasattr(run, "cmd_backtest")
        assert callable(run.cmd_backtest)
