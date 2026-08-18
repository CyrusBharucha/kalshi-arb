"""
tests/test_run_empirical_main.py
==================================
Unit tests for analysis/run_empirical.run_empirical() orchestration function.

Tests:
  - Calls load_actionable_pairs
  - Calls build_targeted_ohlc
  - Calls scan_violations
  - Calls persist_violations
  - Calls compute_research_report
  - Returns result from compute_research_report
  - Returns dict

All sub-functions mocked.
"""
from __future__ import annotations

import sys
import pandas as pd
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
        "feeds.kalshi_client": MagicMock(),
    }):
        yield


def _fake_rels():
    return pd.DataFrame({
        "market_id_1": ["M1", "M3"],
        "market_id_2": ["M2", "M4"],
        "relationship_type": ["mutually_exclusive", "threshold_order"],
    })


def _fake_report():
    return {
        "total_violations": 5,
        "profitable_after_fees": 3,
        "pct_profitable": 0.6,
        "EXECUTION_CAVEATS": [],
    }


class TestRunEmpirical:
    def _run(self, rels=None):
        from analysis.run_empirical import run_empirical
        if rels is None:
            rels = _fake_rels()
        report = _fake_report()
        violations = [{"net_edge": 0.05}] * 5
        with patch("analysis.run_empirical.load_actionable_pairs", return_value=rels) as mock_lap, \
             patch("analysis.run_empirical.build_targeted_ohlc", return_value={}) as mock_bto, \
             patch("analysis.run_empirical.scan_violations", return_value=violations) as mock_sv, \
             patch("analysis.run_empirical.persist_violations", return_value=3) as mock_pv, \
             patch("analysis.run_empirical.compute_research_report", return_value=report) as mock_crr:
            result = run_empirical()
        return result, mock_lap, mock_bto, mock_sv, mock_pv, mock_crr

    def test_returns_dict(self):
        result, *_ = self._run()
        assert isinstance(result, dict)

    def test_calls_load_actionable_pairs(self):
        _, mock_lap, *_ = self._run()
        mock_lap.assert_called_once()

    def test_calls_build_targeted_ohlc(self):
        _, _, mock_bto, *_ = self._run()
        mock_bto.assert_called_once()

    def test_calls_scan_violations(self):
        _, _, _, mock_sv, *_ = self._run()
        mock_sv.assert_called_once()

    def test_calls_persist_violations(self):
        _, _, _, _, mock_pv, _ = self._run()
        mock_pv.assert_called_once()

    def test_calls_compute_research_report(self):
        _, _, _, _, _, mock_crr = self._run()
        mock_crr.assert_called_once()

    def test_returns_research_report_result(self):
        result, *_ = self._run()
        assert result["total_violations"] == 5
        assert "EXECUTION_CAVEATS" in result
