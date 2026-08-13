"""
tests/test_me_strategy_extended.py
=====================================
Extended unit tests for arbitrage/mutually_exclusive.py covering:
  - _safe_float(): None, valid, invalid conversions
  - analyze_event_probability_sum(): diagnostics dict structure
  - check_overpriced_set(): below/above threshold, None guards

Also tests arbitrage/yes_no.py:
  - _safe_float(), _best_ask_depth(), _best_bid_depth()

No DB, no network.
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


# ---------------------------------------------------------------------------
# arbitrage/mutually_exclusive.py helpers
# ---------------------------------------------------------------------------

class TestMeSafeFloat:
    """_safe_float in mutually_exclusive.py."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from engine.mutually_exclusive import _safe_float
        self.fn = _safe_float

    def test_none_returns_none(self):
        assert self.fn(None) is None

    def test_valid_float_string(self):
        assert self.fn("0.45") == 0.45

    def test_valid_int(self):
        assert self.fn(1) == 1.0

    def test_invalid_string_returns_none(self):
        assert self.fn("abc") is None

    def test_zero(self):
        assert self.fn(0) == 0.0

    def test_list_returns_none(self):
        assert self.fn([]) is None


class TestAnalyzeEventProbabilitySum:
    """analyze_event_probability_sum(): diagnostic dict."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from engine.mutually_exclusive import analyze_event_probability_sum
        self.fn = analyze_event_probability_sum

    def _markets(self, bids, asks):
        return [{"yes_bid": b, "yes_ask": a} for b, a in zip(bids, asks)]

    def test_returns_dict(self):
        result = self.fn("KXEVENT", self._markets([0.40, 0.45], [0.42, 0.47]))
        assert isinstance(result, dict)

    def test_event_ticker_preserved(self):
        result = self.fn("MYEVENT", self._markets([0.40], [0.42]))
        assert result["event_ticker"] == "MYEVENT"

    def test_n_markets_count(self):
        result = self.fn("X", self._markets([0.40, 0.45], [0.42, 0.47]))
        assert result["n_markets"] == 2

    def test_sum_bids_correct(self):
        result = self.fn("X", self._markets([0.40, 0.45], [0.42, 0.47]))
        assert abs(result["sum_bids"] - 0.85) < 1e-9

    def test_sum_asks_correct(self):
        result = self.fn("X", self._markets([0.40, 0.45], [0.42, 0.47]))
        assert abs(result["sum_asks"] - 0.89) < 1e-9

    def test_underpriced_flag_true(self):
        # sum asks = 0.40 + 0.40 = 0.80 < 1.0
        result = self.fn("X", self._markets([0.38, 0.38], [0.40, 0.40]))
        assert result["underpriced"] is True

    def test_overpriced_flag_true(self):
        # sum bids = 0.60 + 0.60 = 1.20 > 1.0
        result = self.fn("X", self._markets([0.60, 0.60], [0.62, 0.62]))
        assert result["overpriced"] is True

    def test_gross_edge_buyside_correct(self):
        result = self.fn("X", self._markets([0.38, 0.38], [0.40, 0.40]))
        assert abs(result["gross_edge_buyside"] - 0.20) < 1e-9

    def test_gross_edge_sellside_correct(self):
        result = self.fn("X", self._markets([0.60, 0.60], [0.62, 0.62]))
        assert abs(result["gross_edge_sellside"] - 0.20) < 1e-9

    def test_empty_markets_returns_nones(self):
        result = self.fn("X", [])
        assert result["sum_bids"] is None
        assert result["sum_asks"] is None
        assert result["underpriced"] is False
        assert result["overpriced"] is False

    def test_sum_mids_key_present(self):
        result = self.fn("X", self._markets([0.40], [0.42]))
        assert "sum_mids" in result


class TestCheckOverpricedSet:
    """check_overpriced_set(): ME overpriced signal."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from engine.mutually_exclusive import check_overpriced_set
        self.fn = check_overpriced_set

    def _markets(self, bids):
        return [{"market_id": f"MKT-{i}", "yes_bid": b} for i, b in enumerate(bids)]

    def test_below_threshold_returns_none(self):
        # sum bids = 0.45 + 0.45 = 0.90 < 1.0, no arb
        result = self.fn("EVT", self._markets([0.45, 0.45]))
        assert result is None

    def test_single_market_returns_none(self):
        result = self.fn("EVT", self._markets([0.99]))
        assert result is None

    def test_zero_bid_ignored(self):
        # Only one valid market after filtering zero bids
        result = self.fn("EVT", self._markets([0.70, 0.0]))
        assert result is None

    def test_above_threshold_returns_dict(self):
        # sum bids = 0.70 + 0.70 = 1.40 >> 1.0, large arb
        result = self.fn("EVT", self._markets([0.70, 0.70]))
        assert result is not None
        assert result["strategy_type"] == "me_overpriced"

    def test_result_has_gross_edge(self):
        result = self.fn("EVT", self._markets([0.70, 0.70]))
        assert "gross_edge" in result
        assert result["gross_edge"] > 0

    def test_result_has_net_edge(self):
        result = self.fn("EVT", self._markets([0.70, 0.70]))
        assert "net_edge" in result

    def test_markets_involved_lists_all(self):
        result = self.fn("EVT", self._markets([0.70, 0.70]))
        assert len(result["markets_involved"]) == 2


# ---------------------------------------------------------------------------
# arbitrage/yes_no.py helpers
# ---------------------------------------------------------------------------

class TestYesNoSafeFloat:
    """_safe_float in yes_no.py."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from engine.yes_no import _safe_float
        self.fn = _safe_float

    def test_none_returns_none(self):
        assert self.fn(None) is None

    def test_valid_numeric_string(self):
        assert self.fn("0.55") == 0.55

    def test_invalid_returns_none(self):
        assert self.fn("xyz") is None

    def test_int_coerced(self):
        assert self.fn(1) == 1.0


class TestBestDepth:
    """_best_ask_depth / _best_bid_depth in yes_no.py."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from engine.yes_no import _best_ask_depth, _best_bid_depth
        self.ask_fn = _best_ask_depth
        self.bid_fn = _best_bid_depth

    def test_ask_empty_orderbook_returns_none(self):
        assert self.ask_fn({}, "yes") is None

    def test_ask_no_yes_asks_key_returns_none(self):
        assert self.ask_fn({"yes_bids": [[0.40, 5]]}, "yes") is None

    def test_ask_returns_quantity(self):
        ob = {"yes_asks": [[0.45, 10]]}
        result = self.ask_fn(ob, "yes")
        assert result == 10.0

    def test_ask_single_element_level_returns_none(self):
        ob = {"yes_asks": [[0.45]]}
        result = self.ask_fn(ob, "yes")
        assert result is None

    def test_bid_empty_orderbook_returns_none(self):
        assert self.bid_fn({}, "yes") is None

    def test_bid_returns_quantity(self):
        ob = {"yes_bids": [[0.40, 7]]}
        result = self.bid_fn(ob, "yes")
        assert result == 7.0

    def test_bid_single_element_level_returns_none(self):
        ob = {"yes_bids": [[0.40]]}
        result = self.bid_fn(ob, "yes")
        assert result is None

    def test_ask_multiple_levels_uses_best(self):
        # best = first element
        ob = {"yes_asks": [[0.42, 5], [0.45, 8]]}
        assert self.ask_fn(ob, "yes") == 5.0

    def test_bid_multiple_levels_uses_best(self):
        ob = {"yes_bids": [[0.40, 3], [0.38, 12]]}
        assert self.bid_fn(ob, "yes") == 3.0
