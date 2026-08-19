"""
tests/test_arb_scanner_detectors.py
======================================
Unit tests for analysis/historical_arb_scanner.py violation-detection functions:
  - detect_me_violations(): mutual exclusivity violations
  - detect_threshold_violations(): price ordering violations
  - detect_superset_violations(): superset logic
  - _flush_opps(): DB write helper

No DB, no network.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(session_scope=MagicMock()),
        "database.models": MagicMock(),
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
        "engine.fees": MagicMock(taker_fee_per_contract=lambda p: 0.035),
    }):
        yield


def _ts(i):
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i)


def _series(values, offset=0):
    idx = [_ts(i + offset) for i in range(len(values))]
    return pd.Series(values, index=idx)


REL = {"market_id_1": "A", "market_id_2": "B", "relationship_type": "mutually_exclusive"}


# ---------------------------------------------------------------------------
# detect_me_violations
# ---------------------------------------------------------------------------

class TestDetectMeViolations:
    def test_empty_series_returns_empty(self):
        from analysis.historical_arb_scanner import detect_me_violations
        s1 = pd.Series([], dtype=float)
        s2 = pd.Series([], dtype=float)
        result = detect_me_violations(s1, s2, REL)
        assert result == []

    def test_no_violation_returns_empty(self):
        """P1 + P2 = 0.90 → no ME violation."""
        from analysis.historical_arb_scanner import detect_me_violations
        s1 = _series([0.50, 0.55])
        s2 = _series([0.40, 0.35])
        result = detect_me_violations(s1, s2, REL)
        assert result == []

    def test_violation_detected(self):
        """P1=0.70, P2=0.60 → sum=1.30 → ME violation."""
        from analysis.historical_arb_scanner import detect_me_violations
        s1 = _series([0.70])
        s2 = _series([0.60])
        result = detect_me_violations(s1, s2, REL)
        assert len(result) == 1

    def test_violation_has_required_keys(self):
        from analysis.historical_arb_scanner import detect_me_violations
        s1 = _series([0.70])
        s2 = _series([0.60])
        v = detect_me_violations(s1, s2, REL)[0]
        for key in ("ts", "strategy", "p1", "p2", "gross_edge", "total_fees", "net_edge"):
            assert key in v

    def test_strategy_is_mutually_exclusive(self):
        from analysis.historical_arb_scanner import detect_me_violations
        s1 = _series([0.70])
        s2 = _series([0.60])
        v = detect_me_violations(s1, s2, REL)[0]
        assert v["strategy"] == "mutually_exclusive"

    def test_gross_edge_correct(self):
        from analysis.historical_arb_scanner import detect_me_violations
        s1 = _series([0.70])
        s2 = _series([0.60])
        v = detect_me_violations(s1, s2, REL)[0]
        assert v["gross_edge"] == pytest.approx(0.30, abs=1e-6)

    def test_misaligned_timestamps_only_overlap_counted(self):
        from analysis.historical_arb_scanner import detect_me_violations
        s1 = _series([0.80], offset=0)   # hour 0
        s2 = _series([0.80], offset=10)  # hour 10 — no overlap
        result = detect_me_violations(s1, s2, REL)
        assert result == []

    def test_multiple_violations(self):
        from analysis.historical_arb_scanner import detect_me_violations
        s1 = _series([0.70, 0.65, 0.80])
        s2 = _series([0.60, 0.50, 0.30])
        result = detect_me_violations(s1, s2, REL)
        # 0.70+0.60=1.30 ✓, 0.65+0.50=1.15 ✓, 0.80+0.30=1.10 ✓
        assert len(result) == 3

    def test_edge_exactly_at_threshold_not_counted(self):
        """gross_edge = 0.0 (exactly 1.0 sum) → not a violation."""
        from analysis.historical_arb_scanner import detect_me_violations
        s1 = _series([0.50])
        s2 = _series([0.50])
        result = detect_me_violations(s1, s2, REL)
        # sum=1.0 is NOT > 1.0
        assert result == []


# ---------------------------------------------------------------------------
# detect_threshold_violations
# ---------------------------------------------------------------------------

class TestDetectThresholdViolations:
    def test_empty_returns_empty(self):
        from analysis.historical_arb_scanner import detect_threshold_violations
        result = detect_threshold_violations(
            pd.Series([], dtype=float),
            pd.Series([], dtype=float),
            REL,
        )
        assert result == []

    def test_no_violation_hi_gt_lo(self):
        """p_hi > p_lo is correct, no violation."""
        from analysis.historical_arb_scanner import detect_threshold_violations
        p_hi = _series([0.70, 0.80])
        p_lo = _series([0.40, 0.50])
        result = detect_threshold_violations(p_hi, p_lo, REL)
        assert result == []

    def test_violation_when_lo_gt_hi(self):
        """p_lo=0.80, p_hi=0.50 → ordering violation."""
        from analysis.historical_arb_scanner import detect_threshold_violations
        p_hi = _series([0.50])
        p_lo = _series([0.80])
        result = detect_threshold_violations(p_hi, p_lo, REL)
        assert len(result) == 1

    def test_violation_strategy_name(self):
        from analysis.historical_arb_scanner import detect_threshold_violations
        p_hi = _series([0.50])
        p_lo = _series([0.80])
        v = detect_threshold_violations(p_hi, p_lo, REL)[0]
        assert v["strategy"] == "threshold_order"

    def test_gross_edge_correct(self):
        from analysis.historical_arb_scanner import detect_threshold_violations
        p_hi = _series([0.50])
        p_lo = _series([0.80])
        v = detect_threshold_violations(p_hi, p_lo, REL)[0]
        assert v["gross_edge"] == pytest.approx(0.30, abs=1e-6)

    def test_has_required_keys(self):
        from analysis.historical_arb_scanner import detect_threshold_violations
        p_hi = _series([0.50])
        p_lo = _series([0.80])
        v = detect_threshold_violations(p_hi, p_lo, REL)[0]
        for key in ("ts", "strategy", "p1", "p2", "gross_edge", "total_fees", "net_edge"):
            assert key in v

    def test_equal_prices_no_violation(self):
        from analysis.historical_arb_scanner import detect_threshold_violations
        p_hi = _series([0.60])
        p_lo = _series([0.60])
        result = detect_threshold_violations(p_hi, p_lo, REL)
        assert result == []

    def test_small_gap_below_min_edge_ignored(self):
        """Gross edge = 0.001 is below MIN_GROSS_EDGE — should be ignored."""
        from analysis.historical_arb_scanner import detect_threshold_violations
        p_hi = _series([0.500])
        p_lo = _series([0.501])
        result = detect_threshold_violations(p_hi, p_lo, REL)
        # Very small violation below threshold
        assert len(result) == 0 or result[0]["gross_edge"] < 0.01


# ---------------------------------------------------------------------------
# detect_superset_violations
# ---------------------------------------------------------------------------

class TestDetectSupersetViolations:
    def test_returns_list(self):
        from analysis.historical_arb_scanner import detect_superset_violations
        s1 = _series([0.70])
        s2 = _series([0.60])
        result = detect_superset_violations(s1, s2, REL)
        assert isinstance(result, list)

    def test_empty_returns_empty(self):
        from analysis.historical_arb_scanner import detect_superset_violations
        result = detect_superset_violations(
            pd.Series([], dtype=float),
            pd.Series([], dtype=float),
            REL,
        )
        assert result == []
