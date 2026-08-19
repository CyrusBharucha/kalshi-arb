"""
tests/test_research_report_functions.py
=========================================
Unit tests for compute_research_report() in analysis/run_empirical.py
and produce_research_report() in analysis/historical_arb_scanner.py.

Both functions are pure data-processing (no live DB calls for the tested paths).
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch.dict(sys.modules, {
        "sqlalchemy.dialects.postgresql": MagicMock(),
    }):
        with patch("database.repository.session_scope", return_value=mock_ss):
            yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_violation(gross=0.10, fees=0.02, net=0.08, rtype="mutually_exclusive",
                    direction="sell_both_yes", confidence=0.9):
    return {
        "ts": pd.Timestamp("2026-06-23 12:00", tz="UTC"),
        "market_id_1": "A",
        "market_id_2": "B",
        "rtype": rtype,
        "direction": direction,
        "p1": 0.65,
        "p2": 0.45,
        "gross_edge": gross,
        "total_fees": fees,
        "net_edge": net,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# compute_research_report — analysis/run_empirical.py
# ---------------------------------------------------------------------------

class TestComputeResearchReport:
    """compute_research_report() builds a structured dict from violation list."""

    def test_empty_violations_returns_error_dict(self):
        from analysis.run_empirical import compute_research_report
        result = compute_research_report([], period_minutes=60, n_pairs=100,
                                         market_ids=["A", "B"])
        assert result == {"error": "no_violations_found"}

    def test_required_top_level_keys(self):
        from analysis.run_empirical import compute_research_report
        viols = [_make_violation()]
        result = compute_research_report(viols, period_minutes=60,
                                         n_pairs=10, market_ids=["A"])
        for key in ("total_violations_detected", "profitable_after_fees",
                    "pct_profitable_after_fees", "gross_edge", "net_edge",
                    "total_fees", "by_type", "EXECUTION_CAVEATS",
                    "bar_period_minutes", "markets_with_trades",
                    "actionable_relationship_pairs"):
            assert key in result, f"Missing key: {key}"

    def test_total_violations_count(self):
        from analysis.run_empirical import compute_research_report
        viols = [_make_violation(), _make_violation(), _make_violation()]
        result = compute_research_report(viols, 60, 5, ["X"])
        assert result["total_violations_detected"] == 3

    def test_profitable_after_fees(self):
        from analysis.run_empirical import compute_research_report
        # 2 profitable (net > 0), 1 not profitable (net <= 0)
        viols = [
            _make_violation(net=0.05),
            _make_violation(net=0.01),
            _make_violation(net=-0.02),
        ]
        result = compute_research_report(viols, 60, 5, ["X"])
        assert result["profitable_after_fees"] == 2

    def test_pct_profitable_calculation(self):
        from analysis.run_empirical import compute_research_report
        viols = [_make_violation(net=0.05), _make_violation(net=-0.01)]
        result = compute_research_report(viols, 60, 5, ["X"])
        assert abs(result["pct_profitable_after_fees"] - 50.0) < 0.1

    def test_gross_edge_stats_present(self):
        from analysis.run_empirical import compute_research_report
        viols = [_make_violation(gross=0.10), _make_violation(gross=0.20)]
        result = compute_research_report(viols, 60, 5, ["X"])
        g = result["gross_edge"]
        assert "mean" in g and "median" in g and "max" in g

    def test_gross_edge_mean_correct(self):
        from analysis.run_empirical import compute_research_report
        viols = [_make_violation(gross=0.10, fees=0.01, net=0.09),
                 _make_violation(gross=0.20, fees=0.01, net=0.19)]
        result = compute_research_report(viols, 60, 5, ["X"])
        # mean of [0.10, 0.20] = 0.15
        assert abs(result["gross_edge"]["mean"] - 0.15) < 0.001

    def test_by_type_key_present(self):
        from analysis.run_empirical import compute_research_report
        viols = [_make_violation(rtype="mutually_exclusive"),
                 _make_violation(rtype="threshold_order")]
        result = compute_research_report(viols, 60, 5, ["X"])
        by_type = result["by_type"]
        assert "mutually_exclusive" in by_type
        assert "threshold_order" in by_type

    def test_by_type_has_n_key(self):
        from analysis.run_empirical import compute_research_report
        viols = [_make_violation(rtype="mutually_exclusive")] * 3
        result = compute_research_report(viols, 60, 5, ["X"])
        assert result["by_type"]["mutually_exclusive"]["n"] == 3

    def test_period_minutes_propagated(self):
        from analysis.run_empirical import compute_research_report
        viols = [_make_violation()]
        result = compute_research_report(viols, period_minutes=30, n_pairs=5,
                                         market_ids=["X"])
        assert result["bar_period_minutes"] == 30

    def test_markets_with_trades_propagated(self):
        from analysis.run_empirical import compute_research_report
        viols = [_make_violation()]
        result = compute_research_report(viols, 60, n_pairs=5,
                                         market_ids=["X", "Y", "Z"])
        assert result["markets_with_trades"] == 3

    def test_execution_caveats_is_list(self):
        from analysis.run_empirical import compute_research_report
        viols = [_make_violation()]
        result = compute_research_report(viols, 60, 5, ["X"])
        assert isinstance(result["EXECUTION_CAVEATS"], list)
        assert len(result["EXECUTION_CAVEATS"]) >= 1

    def test_fee_killed_count_present(self):
        from analysis.run_empirical import compute_research_report
        # gross >= 0.005, net <= 0 → fee-killed
        viols = [_make_violation(gross=0.01, fees=0.02, net=-0.01)]
        result = compute_research_report(viols, 60, 5, ["X"])
        assert "fee_killed_count" in result
        assert result["fee_killed_count"] >= 1


# ---------------------------------------------------------------------------
# produce_research_report — analysis/historical_arb_scanner.py
# ---------------------------------------------------------------------------

class TestProduceResearchReport:
    """produce_research_report() returns a report dict from opportunities DataFrame."""

    def _make_df_row(self, strategy="mutually_exclusive", classification="B",
                     gross=0.10, fees=0.02, net=0.08, detected_at="2026-06-23"):
        return {
            "strategy_type": strategy,
            "classification": classification,
            "gross_edge": gross,
            "total_fees": fees,
            "estimated_slippage": 0.001,
            "net_edge": net,
            "detected_at": detected_at,
            "status": "historical",
        }

    def test_empty_df_returns_error_dict(self):
        from analysis.historical_arb_scanner import produce_research_report
        empty_df = pd.DataFrame()
        with patch("pandas.read_sql", return_value=empty_df), \
             patch("analysis.historical_arb_scanner.session_scope") as mock_ss:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ss.return_value = mock_ctx
            result = produce_research_report()
        assert result == {"error": "no_opportunities"}

    def test_single_row_report_keys(self):
        from analysis.historical_arb_scanner import produce_research_report
        df = pd.DataFrame([self._make_df_row()])
        with patch("pandas.read_sql", return_value=df), \
             patch("analysis.historical_arb_scanner.session_scope") as mock_ss:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ss.return_value = mock_ctx
            result = produce_research_report()
        for key in ("total_candidates", "profitable_after_fees", "pct_profitable",
                    "avg_gross_edge", "avg_net_edge", "median_net_edge",
                    "max_net_edge", "avg_fees", "by_classification", "by_strategy",
                    "date_range"):
            assert key in result, f"Missing key: {key}"

    def test_total_candidates_count(self):
        from analysis.historical_arb_scanner import produce_research_report
        rows = [self._make_df_row()] * 5
        df = pd.DataFrame(rows)
        with patch("pandas.read_sql", return_value=df), \
             patch("analysis.historical_arb_scanner.session_scope") as mock_ss:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ss.return_value = mock_ctx
            result = produce_research_report()
        assert result["total_candidates"] == 5

    def test_profitable_after_fees_count(self):
        """Rows with net_edge > 0 → profitable."""
        from analysis.historical_arb_scanner import produce_research_report
        rows = [
            self._make_df_row(net=0.05),
            self._make_df_row(net=0.01),
            self._make_df_row(net=-0.02),
        ]
        df = pd.DataFrame(rows)
        with patch("pandas.read_sql", return_value=df), \
             patch("analysis.historical_arb_scanner.session_scope") as mock_ss:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ss.return_value = mock_ctx
            result = produce_research_report()
        assert result["profitable_after_fees"] == 2

    def test_avg_gross_edge_correct(self):
        from analysis.historical_arb_scanner import produce_research_report
        rows = [self._make_df_row(gross=0.10), self._make_df_row(gross=0.20)]
        df = pd.DataFrame(rows)
        with patch("pandas.read_sql", return_value=df), \
             patch("analysis.historical_arb_scanner.session_scope") as mock_ss:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ss.return_value = mock_ctx
            result = produce_research_report()
        assert abs(result["avg_gross_edge"] - 0.15) < 0.001

    def test_pct_profitable_when_all_profitable(self):
        from analysis.historical_arb_scanner import produce_research_report
        rows = [self._make_df_row(net=0.05)] * 4
        df = pd.DataFrame(rows)
        with patch("pandas.read_sql", return_value=df), \
             patch("analysis.historical_arb_scanner.session_scope") as mock_ss:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ss.return_value = mock_ctx
            result = produce_research_report()
        assert abs(result["pct_profitable"] - 100.0) < 0.01
