"""
tests/test_historical_arb_scanner.py
======================================
Unit tests for analysis/historical_arb_scanner.py:
  - Constants: MIN_GROSS_EDGE, MIN_NET_EDGE, BATCH_SIZE
  - detect_me_violations(): correct detection of mutually-exclusive violations
  - detect_threshold_violations(): correct detection of threshold violations
  - detect_superset_violations(): delegates to threshold logic
  - _flush_opps([]): returns 0 without opening DB session
  - load_price_series([]): returns empty DataFrame without DB call

No DB connection required.
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


class TestConstants:
    """Module-level tuning constants."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.historical_arb_scanner import MIN_GROSS_EDGE, MIN_NET_EDGE, BATCH_SIZE
        self.min_gross = MIN_GROSS_EDGE
        self.min_net   = MIN_NET_EDGE
        self.batch     = BATCH_SIZE

    def test_min_gross_edge_positive(self):
        assert isinstance(self.min_gross, float) and self.min_gross > 0

    def test_min_net_edge_positive(self):
        assert isinstance(self.min_net, float) and self.min_net > 0

    def test_min_net_edge_less_than_gross(self):
        assert self.min_net < self.min_gross

    def test_batch_size_positive(self):
        assert isinstance(self.batch, int) and self.batch > 0

    def test_batch_size_large(self):
        assert self.batch >= 100


class TestDetectMeViolations:
    """detect_me_violations(): mutually-exclusive price-sum violations."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.historical_arb_scanner import detect_me_violations
        self.fn = detect_me_violations

    def _rel(self):
        return {
            "market_id_1": "MKT-A-YES",
            "market_id_2": "MKT-B-YES",
            "relationship_type": "mutually_exclusive",
        }

    def _series(self, values, idx=None):
        if idx is None:
            idx = pd.date_range("2026-01-01", periods=len(values), freq="h")
        return pd.Series(values, index=idx)

    def test_empty_series_returns_empty(self):
        result = self.fn(self._series([]), self._series([]), self._rel())
        assert result == []

    def test_no_violation_when_sum_below_one(self):
        s1 = self._series([0.4, 0.3, 0.5])
        s2 = self._series([0.4, 0.3, 0.4])
        result = self.fn(s1, s2, self._rel())
        assert result == []

    def test_sum_exactly_one_no_violation(self):
        s1 = self._series([0.5])
        s2 = self._series([0.5])
        result = self.fn(s1, s2, self._rel())
        assert result == []

    def test_violation_when_sum_exceeds_one_above_min_edge(self):
        # sum = 0.65 + 0.45 = 1.10 -> gross_edge = 0.10, well above 0.005
        s1 = self._series([0.65])
        s2 = self._series([0.45])
        result = self.fn(s1, s2, self._rel())
        assert len(result) == 1

    def test_violation_has_required_keys(self):
        s1 = self._series([0.65])
        s2 = self._series([0.45])
        result = self.fn(s1, s2, self._rel())
        row = result[0]
        for key in ("ts", "strategy", "gross_edge", "total_fees", "net_edge", "p1", "p2"):
            assert key in row, f"Missing key: {key}"

    def test_strategy_label_is_mutually_exclusive(self):
        s1 = self._series([0.65])
        s2 = self._series([0.45])
        result = self.fn(s1, s2, self._rel())
        assert result[0]["strategy"] == "mutually_exclusive"

    def test_gross_edge_correct(self):
        s1 = self._series([0.70])
        s2 = self._series([0.40])
        result = self.fn(s1, s2, self._rel())
        assert len(result) == 1
        assert abs(result[0]["gross_edge"] - 0.10) < 1e-9

    def test_multiple_violations_counted(self):
        s1 = self._series([0.65, 0.55, 0.70])
        s2 = self._series([0.45, 0.30, 0.40])
        result = self.fn(s1, s2, self._rel())
        # first and third are violations (0.65+0.45=1.10, 0.70+0.40=1.10)
        # second: 0.55+0.30=0.85 no violation
        assert len(result) == 2

    def test_below_min_gross_edge_not_recorded(self):
        # sum = 1.001 -> gross_edge = 0.001 < MIN_GROSS_EDGE(0.005)
        s1 = self._series([0.501])
        s2 = self._series([0.500])
        result = self.fn(s1, s2, self._rel())
        assert result == []

    def test_misaligned_indices_produce_empty(self):
        idx1 = pd.date_range("2026-01-01", periods=3, freq="h")
        idx2 = pd.date_range("2026-06-01", periods=3, freq="h")
        s1 = pd.Series([0.7, 0.7, 0.7], index=idx1)
        s2 = pd.Series([0.5, 0.5, 0.5], index=idx2)
        result = self.fn(s1, s2, self._rel())
        assert result == []


class TestDetectThresholdViolations:
    """detect_threshold_violations(): higher-strike should cost >= lower-strike."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from analysis.historical_arb_scanner import detect_threshold_violations
        self.fn = detect_threshold_violations

    def _rel(self):
        return {"market_id_1": "MKT-HI", "market_id_2": "MKT-LO"}

    def _series(self, values):
        idx = pd.date_range("2026-01-01", periods=len(values), freq="h")
        return pd.Series(values, index=idx)

    def test_empty_returns_empty(self):
        result = self.fn(self._series([]), self._series([]), self._rel())
        assert result == []

    def test_no_violation_hi_ge_lo(self):
        hi = self._series([0.6, 0.7])
        lo = self._series([0.4, 0.5])
        result = self.fn(hi, lo, self._rel())
        assert result == []

    def test_violation_when_lo_exceeds_hi(self):
        # lo=0.70 > hi=0.50 -> gross_edge=0.20 >> 0.005
        hi = self._series([0.50])
        lo = self._series([0.70])
        result = self.fn(hi, lo, self._rel())
        assert len(result) == 1

    def test_violation_strategy_label(self):
        hi = self._series([0.40])
        lo = self._series([0.60])
        result = self.fn(hi, lo, self._rel())
        assert result[0]["strategy"] == "threshold_order"

    def test_violation_side_label(self):
        hi = self._series([0.40])
        lo = self._series([0.60])
        result = self.fn(hi, lo, self._rel())
        assert result[0]["side"] == "buy_hi_sell_lo"

    def test_gross_edge_correct(self):
        hi = self._series([0.30])
        lo = self._series([0.50])
        result = self.fn(hi, lo, self._rel())
        assert abs(result[0]["gross_edge"] - 0.20) < 1e-9

    def test_below_min_not_recorded(self):
        hi = self._series([0.500])
        lo = self._series([0.501])
        result = self.fn(hi, lo, self._rel())
        assert result == []


class TestDetectSupersetViolations:
    """detect_superset_violations(): delegates to threshold logic."""

    def test_superset_behaves_like_threshold(self):
        from analysis.historical_arb_scanner import detect_superset_violations
        idx = pd.date_range("2026-01-01", periods=1, freq="h")
        sup = pd.Series([0.40], index=idx)
        sub = pd.Series([0.60], index=idx)
        result = detect_superset_violations(sup, sub, {})
        assert len(result) == 1
        assert result[0]["strategy"] == "threshold_order"


class TestFlushOppsEmpty:
    """_flush_opps([]) returns 0 without touching DB."""

    def test_empty_returns_zero(self):
        from analysis.historical_arb_scanner import _flush_opps
        mock_ss = MagicMock()
        with patch("analysis.historical_arb_scanner.session_scope", return_value=mock_ss):
            result = _flush_opps([])
        assert result == 0

    def test_empty_does_not_open_session(self):
        from analysis.historical_arb_scanner import _flush_opps
        mock_ss = MagicMock()
        with patch("analysis.historical_arb_scanner.session_scope", return_value=mock_ss):
            _flush_opps([])
        mock_ss.__enter__.assert_not_called()


class TestLoadPriceSeriesEmpty:
    """load_price_series([]): returns empty DataFrame without DB call."""

    def test_empty_market_ids_returns_empty_df(self):
        from analysis.historical_arb_scanner import load_price_series
        mock_ss = MagicMock()
        with patch("analysis.historical_arb_scanner.session_scope", return_value=mock_ss):
            result = load_price_series([])
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_empty_does_not_open_session(self):
        from analysis.historical_arb_scanner import load_price_series
        mock_ss = MagicMock()
        with patch("analysis.historical_arb_scanner.session_scope", return_value=mock_ss):
            load_price_series([])
        mock_ss.__enter__.assert_not_called()
