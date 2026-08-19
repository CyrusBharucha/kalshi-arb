"""
tests/test_compute_research_report.py
=======================================
Unit tests for analysis/run_empirical.py compute_research_report().

Tests cover:
  - Empty violations → returns error dict
  - Populated violations → returns dict with required keys
  - gross_edge stats computed correctly
  - net_edge stats computed correctly
  - profitable_after_fees count
  - by_type breakdown present
  - pct_profitable in [0, 100]

Pure computation — no DB, no network.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
        "engine.fees": MagicMock(taker_fee_per_contract=lambda p: 0.035),
        "backtest.metrics": MagicMock(),
    }):
        yield


def _make_violation(gross=0.10, net=0.03, rtype="mutually_exclusive", m1="A", m2="B"):
    return {
        "market_id_1": m1, "market_id_2": m2,
        "rtype": rtype,
        "gross_edge": gross,
        "total_fees": gross - net,
        "net_edge": net,
        "direction": "sell_both_yes",
    }


class TestComputeResearchReportEmpty:
    def test_empty_violations_returns_error(self):
        from analysis.run_empirical import compute_research_report
        result = compute_research_report([], period_minutes=5, n_pairs=10, market_ids=["A"])
        assert "error" in result

    def test_empty_error_value(self):
        from analysis.run_empirical import compute_research_report
        result = compute_research_report([], period_minutes=5, n_pairs=10, market_ids=[])
        assert result["error"] == "no_violations_found"


class TestComputeResearchReportKeys:
    def _report(self):
        from analysis.run_empirical import compute_research_report
        violations = [_make_violation(0.10, 0.03), _make_violation(0.08, -0.01)]
        return compute_research_report(violations, period_minutes=5, n_pairs=2, market_ids=["A", "B"])

    def test_returns_dict(self):
        assert isinstance(self._report(), dict)

    def test_has_total_violations(self):
        report = self._report()
        assert "total_violations_detected" in report

    def test_has_profitable_after_fees(self):
        report = self._report()
        assert "profitable_after_fees" in report

    def test_has_gross_edge_stats(self):
        report = self._report()
        assert "gross_edge" in report
        assert "mean" in report["gross_edge"]

    def test_has_net_edge_stats(self):
        report = self._report()
        assert "net_edge" in report
        assert "mean" in report["net_edge"]

    def test_has_by_type(self):
        report = self._report()
        assert "by_type" in report

    def test_has_markets_count(self):
        report = self._report()
        assert "markets_with_trades" in report
        assert report["markets_with_trades"] == 2


class TestComputeResearchReportValues:
    def test_total_violations_count(self):
        from analysis.run_empirical import compute_research_report
        violations = [_make_violation()] * 5
        report = compute_research_report(violations, 5, 3, ["X"])
        assert report["total_violations_detected"] == 5

    def test_profitable_count_correct(self):
        from analysis.run_empirical import compute_research_report
        # 3 profitable (net > 0), 2 not
        violations = [_make_violation(net=0.03)] * 3 + [_make_violation(net=-0.01)] * 2
        report = compute_research_report(violations, 5, 5, ["X"])
        assert report["profitable_after_fees"] == 3

    def test_pct_profitable_in_range(self):
        from analysis.run_empirical import compute_research_report
        violations = [_make_violation(net=0.05)] * 3 + [_make_violation(net=-0.02)] * 2
        report = compute_research_report(violations, 5, 5, ["X"])
        pct = report["pct_profitable_after_fees"]
        assert 0 <= pct <= 100

    def test_gross_edge_mean_correct(self):
        from analysis.run_empirical import compute_research_report
        violations = [
            _make_violation(gross=0.10, net=0.03),
            _make_violation(gross=0.20, net=0.12),
        ]
        report = compute_research_report(violations, 5, 1, ["X"])
        assert report["gross_edge"]["mean"] == pytest.approx(0.15, abs=1e-4)

    def test_max_gross_edge_correct(self):
        from analysis.run_empirical import compute_research_report
        violations = [
            _make_violation(gross=0.10, net=0.03),
            _make_violation(gross=0.30, net=0.22),
        ]
        report = compute_research_report(violations, 5, 1, ["X"])
        assert report["gross_edge"]["max"] == pytest.approx(0.30, abs=1e-4)

    def test_by_type_includes_mutually_exclusive(self):
        from analysis.run_empirical import compute_research_report
        violations = [_make_violation(rtype="mutually_exclusive")]
        report = compute_research_report(violations, 5, 1, ["X"])
        assert "mutually_exclusive" in report["by_type"]

    def test_has_bar_period_minutes(self):
        from analysis.run_empirical import compute_research_report
        violations = [_make_violation()]
        report = compute_research_report(violations, period_minutes=15, n_pairs=1, market_ids=["X"])
        assert report["bar_period_minutes"] == 15

    def test_has_actionable_pairs(self):
        from analysis.run_empirical import compute_research_report
        violations = [_make_violation()]
        report = compute_research_report(violations, 5, n_pairs=42, market_ids=["X"])
        assert report["actionable_relationship_pairs"] == 42

    def test_all_profitable_pct_100(self):
        from analysis.run_empirical import compute_research_report
        violations = [_make_violation(net=0.05)] * 4
        report = compute_research_report(violations, 5, 1, ["X"])
        assert report["pct_profitable_after_fees"] == pytest.approx(100.0)

    def test_none_profitable_pct_zero(self):
        from analysis.run_empirical import compute_research_report
        violations = [_make_violation(gross=0.10, net=-0.05)] * 3
        report = compute_research_report(violations, 5, 1, ["X"])
        assert report["pct_profitable_after_fees"] == pytest.approx(0.0)
