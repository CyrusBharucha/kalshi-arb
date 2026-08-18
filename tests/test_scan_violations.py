"""
tests/test_scan_violations.py
================================
Unit tests for analysis/run_empirical.scan_violations() violation types:

  - ME violation (p1 + p2 > 1.0)
  - threshold_order violation (p2 > p1)
  - collectively_exhaustive violation (p1 + p2 < 1.0)
  - Market not in price_index → skipped (no violation)
  - Empty combined timestamps → skipped
  - No violation for valid prices (ME sum < 1.0)
  - All required keys present in violation dict
  - Below gross_edge threshold (< 0.005) → not included

No DB; no network.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest
import pandas as pd


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


def _make_rels(rtype: str, m1: str = "A", m2: str = "B", confidence: float = 0.9) -> pd.DataFrame:
    return pd.DataFrame([{
        "market_id_1": m1,
        "market_id_2": m2,
        "relationship_type": rtype,
        "confidence": confidence,
    }])


def _series(values, ts_base="2026-01-01") -> pd.Series:
    idx = pd.date_range(ts_base, periods=len(values), freq="1h", tz="UTC")
    return pd.Series(values, index=idx)


class TestScanViolationsME:
    """mutually_exclusive: violation when p1 + p2 > 1.0."""

    def test_me_violation_detected(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive")
        # p1=0.60, p2=0.55 → sum=1.15 > 1.0 → violation
        pi = {
            "A": _series([0.60]),
            "B": _series([0.55]),
        }
        result = scan_violations(rels, pi)
        assert len(result) == 1

    def test_me_no_violation_when_sum_below_one(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive")
        # p1=0.40, p2=0.40 → sum=0.80 < 1.0 → no violation
        pi = {
            "A": _series([0.40]),
            "B": _series([0.40]),
        }
        result = scan_violations(rels, pi)
        assert result == []

    def test_me_violation_direction(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive")
        pi = {"A": _series([0.65]), "B": _series([0.45])}
        result = scan_violations(rels, pi)
        assert result[0]["direction"] == "sell_both_yes"

    def test_me_gross_edge_correct(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive")
        pi = {"A": _series([0.65]), "B": _series([0.45])}
        result = scan_violations(rels, pi)
        # gross_edge = 0.65 + 0.45 - 1.0 = 0.10
        assert abs(result[0]["gross_edge"] - 0.10) < 1e-5


class TestScanViolationsThreshold:
    """threshold_order: violation when p2 > p1."""

    def test_threshold_violation_detected(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("threshold_order")
        # p1=0.30, p2=0.55 → p2 > p1 → violation
        pi = {"A": _series([0.30]), "B": _series([0.55])}
        result = scan_violations(rels, pi)
        assert len(result) == 1

    def test_threshold_no_violation_when_p1_above_p2(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("threshold_order")
        pi = {"A": _series([0.70]), "B": _series([0.40])}
        result = scan_violations(rels, pi)
        assert result == []

    def test_threshold_direction_label(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("threshold_order")
        pi = {"A": _series([0.30]), "B": _series([0.55])}
        result = scan_violations(rels, pi)
        assert result[0]["direction"] == "buy_m1_sell_m2"

    def test_superset_type_treated_same_as_threshold(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("superset")
        pi = {"A": _series([0.30]), "B": _series([0.55])}
        result = scan_violations(rels, pi)
        assert len(result) == 1


class TestScanViolationsCE:
    """collectively_exhaustive: violation when p1 + p2 < 1.0."""

    def test_ce_violation_detected(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("collectively_exhaustive")
        # p1=0.30, p2=0.30 → sum=0.60 < 1.0 → violation
        pi = {"A": _series([0.30]), "B": _series([0.30])}
        result = scan_violations(rels, pi)
        assert len(result) == 1

    def test_ce_no_violation_when_sum_above_one(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("collectively_exhaustive")
        pi = {"A": _series([0.55]), "B": _series([0.55])}
        result = scan_violations(rels, pi)
        assert result == []

    def test_ce_direction_label(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("collectively_exhaustive")
        pi = {"A": _series([0.30]), "B": _series([0.30])}
        result = scan_violations(rels, pi)
        assert result[0]["direction"] == "buy_both_yes"


class TestScanViolationsSkips:
    """Markets not in price_index or with empty overlap are skipped."""

    def test_missing_market1_skipped(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive")
        # Only B in price_index, not A
        pi = {"B": _series([0.55])}
        result = scan_violations(rels, pi)
        assert result == []

    def test_missing_market2_skipped(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive")
        pi = {"A": _series([0.60])}
        result = scan_violations(rels, pi)
        assert result == []

    def test_empty_price_index_skipped(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive")
        result = scan_violations(rels, {})
        assert result == []

    def test_below_gross_edge_min_excluded(self):
        """gross_edge < 0.005 → not included."""
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive")
        # p1+p2 = 1.003 → gross_edge = 0.003 < 0.005 → excluded
        pi = {
            "A": _series([0.503]),
            "B": _series([0.500]),
        }
        result = scan_violations(rels, pi)
        assert result == []


class TestScanViolationsResultShape:
    """Violation dicts contain all required keys."""

    def test_required_keys_present(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive")
        pi = {"A": _series([0.65]), "B": _series([0.45])}
        result = scan_violations(rels, pi)
        assert len(result) == 1
        v = result[0]
        for key in ("ts", "market_id_1", "market_id_2", "rtype",
                    "direction", "p1", "p2",
                    "gross_edge", "total_fees", "net_edge", "confidence"):
            assert key in v, f"Missing key: {key}"

    def test_net_edge_less_than_gross_edge(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive")
        pi = {"A": _series([0.65]), "B": _series([0.45])}
        result = scan_violations(rels, pi)
        assert result[0]["net_edge"] < result[0]["gross_edge"]

    def test_market_ids_propagated(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive", m1="MKT-X", m2="MKT-Y")
        pi = {"MKT-X": _series([0.65]), "MKT-Y": _series([0.45])}
        result = scan_violations(rels, pi)
        assert result[0]["market_id_1"] == "MKT-X"
        assert result[0]["market_id_2"] == "MKT-Y"

    def test_confidence_propagated(self):
        from analysis.run_empirical import scan_violations
        rels = _make_rels("mutually_exclusive", confidence=0.75)
        pi = {"A": _series([0.65]), "B": _series([0.45])}
        result = scan_violations(rels, pi)
        assert abs(result[0]["confidence"] - 0.75) < 1e-9
