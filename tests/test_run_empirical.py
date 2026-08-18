"""
tests/test_run_empirical.py
============================
Unit tests for analysis/run_empirical.py:
  - Constants: MIN_NET_EDGE, PERIOD_MINUTES
  - compute_research_report(): output shape when violations present and absent

No DB — only pure-Python dictionary/DataFrame logic tested.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


class TestConstants:
    """MIN_NET_EDGE and PERIOD_MINUTES constants."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.run_empirical import MIN_NET_EDGE, PERIOD_MINUTES
        self.min_edge      = MIN_NET_EDGE
        self.period_min    = PERIOD_MINUTES

    def test_min_net_edge_is_float(self):
        assert isinstance(self.min_edge, float)

    def test_min_net_edge_positive(self):
        assert self.min_edge > 0

    def test_min_net_edge_small(self):
        """Should be a small threshold (sub-cent)."""
        assert self.min_edge < 0.01

    def test_period_minutes_is_int(self):
        assert isinstance(self.period_min, int)

    def test_period_minutes_positive(self):
        assert self.period_min > 0


class TestComputeResearchReport:
    """compute_research_report() output shape."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.run_empirical import compute_research_report
        self.fn = compute_research_report

    def _sample_violations(self, n=5, profitable=3):
        """Build minimal violation dicts."""
        rows = []
        for i in range(n):
            net_edge = 0.02 if i < profitable else -0.01
            rows.append({
                "market_id_1":  f"MKT{i}-YES",
                "market_id_2":  f"MKT{i}-NO",
                "rtype":        "mutually_exclusive",
                "gross_edge":   0.05,
                "net_edge":     net_edge,
                "total_fees":   0.03,
                "ts":           "2026-06-23T12:00:00",
            })
        return rows

    def test_empty_violations_returns_error_key(self):
        result = self.fn([], period_minutes=60, n_pairs=100, market_ids=["M1"])
        assert isinstance(result, dict)
        assert "error" in result

    def test_nonempty_returns_dict(self):
        violations = self._sample_violations()
        result = self.fn(violations, period_minutes=60, n_pairs=100, market_ids=["M1", "M2"])
        assert isinstance(result, dict)

    def test_required_keys_present(self):
        violations = self._sample_violations()
        result = self.fn(violations, period_minutes=60, n_pairs=10, market_ids=["M1"])
        for key in ("total_violations_detected", "profitable_after_fees",
                    "pct_profitable_after_fees", "gross_edge", "total_fees"):
            assert key in result, f"Missing key: {key}"

    def test_total_violations_matches_input(self):
        violations = self._sample_violations(n=7)
        result = self.fn(violations, period_minutes=60, n_pairs=10, market_ids=["M1"])
        assert result["total_violations_detected"] == 7

    def test_profitable_after_fees_correct(self):
        violations = self._sample_violations(n=10, profitable=4)
        result = self.fn(violations, period_minutes=60, n_pairs=10, market_ids=["M1"])
        assert result["profitable_after_fees"] == 4

    def test_pct_profitable_is_float(self):
        violations = self._sample_violations(n=10, profitable=4)
        result = self.fn(violations, period_minutes=60, n_pairs=10, market_ids=["M1"])
        assert isinstance(result["pct_profitable_after_fees"], float)

    def test_gross_edge_has_stat_keys(self):
        violations = self._sample_violations()
        result = self.fn(violations, period_minutes=60, n_pairs=10, market_ids=["M1"])
        for stat in ("mean", "median", "max"):
            assert stat in result["gross_edge"]

    def test_bar_period_minutes_in_result(self):
        violations = self._sample_violations()
        result = self.fn(violations, period_minutes=15, n_pairs=10, market_ids=[])
        assert result.get("bar_period_minutes") == 15

    def test_actionable_pairs_in_result(self):
        violations = self._sample_violations()
        result = self.fn(violations, period_minutes=60, n_pairs=42, market_ids=["M1"])
        assert result.get("actionable_relationship_pairs") == 42

    def test_sample_warning_present(self):
        violations = self._sample_violations()
        result = self.fn(violations, period_minutes=60, n_pairs=10, market_ids=[])
        assert "SAMPLE_WARNING" in result

    def test_zero_profitable_gives_zero_pct(self):
        violations = self._sample_violations(n=5, profitable=0)
        result = self.fn(violations, period_minutes=60, n_pairs=10, market_ids=[])
        assert result["profitable_after_fees"] == 0
        assert result["pct_profitable_after_fees"] == 0.0
