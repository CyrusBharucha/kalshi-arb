"""
tests/test_integrity_audit.py
==============================
Unit tests for analysis/integrity_audit.py.

All individual check functions call the DB via session_scope.
Tests here verify:
  1. Each check returns a dict with a 'pass' key when the DB is mocked.
  2. run_integrity_audit() produces a summary dict with the right structure.
  3. summary 'overall' is "PASS" when no checks fail, "FAIL" otherwise.

No real DB connection is used.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock, call
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_scope(scalar_returns=None, df_returns=None):
    """Build a mock session_scope context manager."""
    import pandas as pd

    mock_session = MagicMock()
    # .execute().scalar() chain
    mock_result = MagicMock()
    scalar_val = scalar_returns if scalar_returns is not None else 0
    mock_result.scalar.return_value = scalar_val
    mock_session.execute.return_value = mock_result

    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=mock_session)
    mock_ss.__exit__ = MagicMock(return_value=False)
    return mock_ss


# ---------------------------------------------------------------------------
# Patch at module level to prevent import-time DB import failure
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_db_import():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


# ---------------------------------------------------------------------------
# run_integrity_audit() summary structure
# ---------------------------------------------------------------------------

class TestRunIntegrityAuditSummary:
    """run_integrity_audit() returns a report dict with 'summary' key."""

    def _patch_all_checks(self, pass_val=True):
        """Patch every individual check function to return a stub dict."""
        check_names = [
            "check_table_counts",
            "check_orphaned_relationships",
            "check_market_duplicates",
            "check_price_bounds",
            "check_candlestick_ohlc_ordering",
            "check_timestamp_sanity",
            "check_arbitrage_consistency",
            "check_relationship_type_distribution",
            "check_trade_volume_sanity",
            # Book-level and lifecycle checks. These must be stubbed too, or
            # they run against the live database and the summary arithmetic
            # below stops being a function of the stubs alone.
            "check_crossed_books",
            "check_stale_books",
            "check_duplicate_trade_ids",
            "check_missing_close_time",
            "check_l2_sequence_gaps",
            "check_markets_without_trades",
        ]
        stubs = {}
        for name in check_names:
            stub = {"pass": pass_val, "stub_key": name}
            stubs[name] = stub
        # patch.multiple uses bare attribute names (not module-qualified)
        patches = {
            name: MagicMock(return_value=stubs[name])
            for name in check_names
        }
        return patches

    def test_returns_dict(self):
        patches = self._patch_all_checks(pass_val=True)
        with patch.multiple("analysis.integrity_audit", **patches):
            from analysis import integrity_audit
            result = integrity_audit.run_integrity_audit()
        assert isinstance(result, dict)

    def test_has_summary_key(self):
        patches = self._patch_all_checks(pass_val=True)
        with patch.multiple("analysis.integrity_audit", **patches):
            from analysis import integrity_audit
            result = integrity_audit.run_integrity_audit()
        assert "summary" in result

    def test_summary_overall_pass_when_all_pass(self):
        patches = self._patch_all_checks(pass_val=True)
        with patch.multiple("analysis.integrity_audit", **patches):
            from analysis import integrity_audit
            result = integrity_audit.run_integrity_audit()
        assert result["summary"]["overall"] == "PASS"

    def test_summary_overall_fail_when_one_fails(self):
        patches = self._patch_all_checks(pass_val=True)
        patches["check_market_duplicates"] = MagicMock(
            return_value={"pass": False, "duplicate_market_ids": 5}
        )
        with patch.multiple("analysis.integrity_audit", **patches):
            from analysis import integrity_audit
            result = integrity_audit.run_integrity_audit()
        assert result["summary"]["overall"] == "FAIL"

    def test_summary_checks_passed_count(self):
        patches = self._patch_all_checks(pass_val=True)
        with patch.multiple("analysis.integrity_audit", **patches):
            from analysis import integrity_audit
            result = integrity_audit.run_integrity_audit()
        assert result["summary"]["checks_passed"] == len(patches)

    def test_summary_checks_failed_count_zero_when_all_pass(self):
        patches = self._patch_all_checks(pass_val=True)
        with patch.multiple("analysis.integrity_audit", **patches):
            from analysis import integrity_audit
            result = integrity_audit.run_integrity_audit()
        assert result["summary"]["checks_failed"] == 0

    def test_summary_checks_unavailable_for_none_pass(self):
        """pass=None counts as 'unavailable'."""
        patches = self._patch_all_checks(pass_val=None)
        with patch.multiple("analysis.integrity_audit", **patches):
            from analysis import integrity_audit
            result = integrity_audit.run_integrity_audit()
        assert result["summary"]["checks_unavailable"] == len(patches)
        assert result["summary"]["checks_failed"] == 0
        assert result["summary"]["overall"] == "PASS"

    def test_summary_keys_present(self):
        patches = self._patch_all_checks()
        with patch.multiple("analysis.integrity_audit", **patches):
            from analysis import integrity_audit
            result = integrity_audit.run_integrity_audit()
        for k in ("checks_passed", "checks_failed", "checks_unavailable", "overall"):
            assert k in result["summary"]

    def test_report_has_expected_top_level_keys(self):
        patches = self._patch_all_checks()
        with patch.multiple("analysis.integrity_audit", **patches):
            from analysis import integrity_audit
            result = integrity_audit.run_integrity_audit()
        expected_keys = {
            "table_counts", "orphaned_relationships", "market_duplicates",
            "price_bounds", "ohlc_ordering", "timestamp_sanity",
            "arb_consistency", "relationship_types", "trade_volume_sanity",
            "summary",
        }
        assert expected_keys.issubset(set(result.keys()))


# ---------------------------------------------------------------------------
# Individual check functions — error path (DB exception)
# ---------------------------------------------------------------------------

class TestCheckFunctionsErrorPath:
    """When DB raises, each check returns a dict with pass=False and error key."""

    def _raise_side_effect(self, *args, **kwargs):
        raise RuntimeError("DB unavailable")

    def _mock_ss_raises(self):
        mock_ss = MagicMock()
        mock_ss.__enter__ = MagicMock(side_effect=self._raise_side_effect)
        mock_ss.__exit__ = MagicMock(return_value=False)
        return mock_ss

    def test_check_orphaned_relationships_error_returns_dict(self):
        ss = self._mock_ss_raises()
        with patch("analysis.integrity_audit.session_scope", return_value=ss):
            from analysis.integrity_audit import check_orphaned_relationships
            result = check_orphaned_relationships()
        assert isinstance(result, dict)
        assert "pass" in result
        assert result["pass"] is False

    def test_check_market_duplicates_error_returns_dict(self):
        ss = self._mock_ss_raises()
        with patch("analysis.integrity_audit.session_scope", return_value=ss):
            from analysis.integrity_audit import check_market_duplicates
            result = check_market_duplicates()
        assert isinstance(result, dict)
        assert result["pass"] is False

    def test_check_price_bounds_error_returns_dict(self):
        ss = self._mock_ss_raises()
        with patch("analysis.integrity_audit.session_scope", return_value=ss):
            from analysis.integrity_audit import check_price_bounds
            result = check_price_bounds()
        assert isinstance(result, dict)
        assert "pass" in result

    def test_check_candlestick_ohlc_error_returns_dict(self):
        ss = self._mock_ss_raises()
        with patch("analysis.integrity_audit.session_scope", return_value=ss):
            from analysis.integrity_audit import check_candlestick_ohlc_ordering
            result = check_candlestick_ohlc_ordering()
        assert isinstance(result, dict)
        assert "pass" in result

    def test_check_timestamp_sanity_error_returns_dict(self):
        ss = self._mock_ss_raises()
        with patch("analysis.integrity_audit.session_scope", return_value=ss):
            from analysis.integrity_audit import check_timestamp_sanity
            result = check_timestamp_sanity()
        assert isinstance(result, dict)
        assert "pass" in result

    def test_check_arbitrage_consistency_error_returns_dict(self):
        ss = self._mock_ss_raises()
        with patch("analysis.integrity_audit.session_scope", return_value=ss):
            from analysis.integrity_audit import check_arbitrage_consistency
            result = check_arbitrage_consistency()
        assert isinstance(result, dict)
        assert "pass" in result

    def test_check_trade_volume_sanity_error_returns_dict(self):
        ss = self._mock_ss_raises()
        with patch("analysis.integrity_audit.session_scope", return_value=ss):
            from analysis.integrity_audit import check_trade_volume_sanity
            result = check_trade_volume_sanity()
        assert isinstance(result, dict)
        assert "pass" in result

    def test_check_relationship_type_distribution_error_returns_dict(self):
        ss = self._mock_ss_raises()
        with patch("analysis.integrity_audit.session_scope", return_value=ss):
            from analysis.integrity_audit import check_relationship_type_distribution
            result = check_relationship_type_distribution()
        assert isinstance(result, dict)
        assert "pass" in result
        assert result["pass"] is False
