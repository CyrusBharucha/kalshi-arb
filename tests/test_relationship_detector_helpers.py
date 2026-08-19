"""
tests/test_relationship_detector_helpers.py
=============================================
Unit tests for pure helper functions in markets/relationship_detector.py:
  - _midpoint(): bid/ask average, None when either missing
  - _deduplicate(): deduplicates by (id1, id2, type) key
  - _detect_threshold_direction(): leq/geq classification from ticker/title
  - _markets_have_ordered_strikes(): True when both have distinct float strikes
  - check_logical_price_violations(): threshold, ME, and CE violation detection

No DB or external calls.
"""
from __future__ import annotations

import pytest


class TestMidpoint:
    """_midpoint(): returns average of yes_bid and yes_ask, or None."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from engine.relationship_detector import _midpoint
        self.fn = _midpoint

    def test_valid_prices_returns_average(self):
        result = self.fn({"yes_bid": 0.40, "yes_ask": 0.50})
        assert abs(result - 0.45) < 1e-9

    def test_returns_none_when_bid_missing(self):
        assert self.fn({"yes_ask": 0.50}) is None

    def test_returns_none_when_ask_missing(self):
        assert self.fn({"yes_bid": 0.40}) is None

    def test_returns_none_for_empty_dict(self):
        assert self.fn({}) is None

    def test_symmetric_with_equal_bid_ask(self):
        result = self.fn({"yes_bid": 0.60, "yes_ask": 0.60})
        assert abs(result - 0.60) < 1e-9

    def test_handles_string_prices(self):
        result = self.fn({"yes_bid": "0.30", "yes_ask": "0.70"})
        assert abs(result - 0.50) < 1e-9


class TestDeduplicate:
    """_deduplicate(): removes repeated (id1, id2, type) triples."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from engine.relationship_detector import _deduplicate
        self.fn = _deduplicate

    def _rel(self, id1, id2, rtype):
        return {"market_id_1": id1, "market_id_2": id2, "relationship_type": rtype}

    def test_empty_list_returns_empty(self):
        assert self.fn([]) == []

    def test_no_duplicates_unchanged(self):
        rels = [
            self._rel("A", "B", "mutually_exclusive"),
            self._rel("A", "C", "mutually_exclusive"),
        ]
        result = self.fn(rels)
        assert len(result) == 2

    def test_duplicates_removed(self):
        rels = [
            self._rel("A", "B", "mutually_exclusive"),
            self._rel("A", "B", "mutually_exclusive"),
        ]
        result = self.fn(rels)
        assert len(result) == 1

    def test_different_type_not_deduped(self):
        rels = [
            self._rel("A", "B", "mutually_exclusive"),
            self._rel("A", "B", "threshold_order"),
        ]
        result = self.fn(rels)
        assert len(result) == 2

    def test_first_occurrence_preserved(self):
        r1 = self._rel("X", "Y", "me")
        r1["confidence"] = 0.9
        r2 = self._rel("X", "Y", "me")
        r2["confidence"] = 0.5
        result = self.fn([r1, r2])
        assert result[0]["confidence"] == 0.9


class TestDetectThresholdDirection:
    """_detect_threshold_direction(): 'leq' or 'geq' from market text."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from engine.relationship_detector import _detect_threshold_direction
        self.fn = _detect_threshold_direction

    def test_above_title_returns_geq(self):
        result = self.fn({"ticker": "KXBOC", "title": "Will rate above 4.5%?"})
        assert result == "geq"

    def test_exceed_title_returns_geq(self):
        result = self.fn({"ticker": "KXTEST", "title": "Will streams exceed 3B?"})
        assert result == "geq"

    def test_below_title_returns_leq(self):
        result = self.fn({"ticker": "KXBOC", "title": "Will rate be below 4.5%?"})
        assert result == "leq"

    def test_at_most_returns_leq(self):
        result = self.fn({"ticker": "KXTEST", "title": "Price at most 100?"})
        assert result == "leq"

    def test_empty_market_defaults_to_leq(self):
        result = self.fn({})
        assert result == "leq"

    def test_ambiguous_ticker_defaults_to_leq(self):
        result = self.fn({"ticker": "KXAMBIGUOUS-2500"})
        assert result == "leq"

    def test_geq_beats_no_leq_indicator(self):
        result = self.fn({"title": "reaches 3.5B"})
        assert result == "geq"


class TestMarketsHaveOrderedStrikes:
    """_markets_have_ordered_strikes(): True when both have distinct float strikes."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from engine.relationship_detector import _markets_have_ordered_strikes
        self.fn = _markets_have_ordered_strikes

    def test_both_none_returns_false(self):
        assert self.fn({}, {}) is False

    def test_one_none_returns_false(self):
        assert self.fn({"floor_strike": 4.5}, {}) is False

    def test_distinct_strikes_returns_true(self):
        assert self.fn({"floor_strike": 4.5}, {"floor_strike": 4.75}) is True

    def test_same_strike_returns_false(self):
        assert self.fn({"floor_strike": 4.5}, {"floor_strike": 4.5}) is False

    def test_string_strikes_coerced(self):
        assert self.fn({"floor_strike": "4.50"}, {"floor_strike": "4.75"}) is True


class TestCheckLogicalPriceViolations:
    """check_logical_price_violations(): detects ME/threshold/CE violations."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from engine.relationship_detector import check_logical_price_violations
        self.fn = check_logical_price_violations

    def _me_rel(self, id1="A", id2="B"):
        return {
            "market_id_1": id1,
            "market_id_2": id2,
            "relationship_type": "mutually_exclusive",
        }

    def _thresh_rel(self, id1="HI", id2="LO"):
        return {
            "market_id_1": id1,
            "market_id_2": id2,
            "relationship_type": "threshold_order",
        }

    def _ce_rel(self, id1="A", id2="B"):
        return {
            "market_id_1": id1,
            "market_id_2": id2,
            "relationship_type": "collectively_exhaustive",
        }

    def test_empty_rels_returns_empty(self):
        assert self.fn([], {}) == []

    def test_me_no_violation_sum_below_one(self):
        rels = [self._me_rel()]
        prices = {"A": {"yes_bid": 0.40, "yes_ask": 0.42},
                  "B": {"yes_bid": 0.35, "yes_ask": 0.37}}
        result = self.fn(rels, prices)
        assert result == []

    def test_me_violation_sum_above_one(self):
        rels = [self._me_rel()]
        prices = {"A": {"yes_bid": 0.65, "yes_ask": 0.67},
                  "B": {"yes_bid": 0.45, "yes_ask": 0.47}}
        result = self.fn(rels, prices)
        assert len(result) == 1
        assert result[0]["type"] == "mutually_exclusive"

    def test_me_violation_has_magnitude(self):
        rels = [self._me_rel()]
        prices = {"A": {"yes_bid": 0.65, "yes_ask": 0.67},
                  "B": {"yes_bid": 0.45, "yes_ask": 0.47}}
        result = self.fn(rels, prices)
        assert result[0]["magnitude"] > 0

    def test_me_missing_bid_skipped(self):
        rels = [self._me_rel()]
        prices = {"A": {"yes_ask": 0.67}, "B": {"yes_bid": 0.45, "yes_ask": 0.47}}
        result = self.fn(rels, prices)
        assert result == []

    def test_threshold_no_violation_hi_ge_lo(self):
        rels = [self._thresh_rel("HI", "LO")]
        prices = {"HI": {"yes_bid": 0.70, "yes_ask": 0.72},
                  "LO": {"yes_bid": 0.40, "yes_ask": 0.42}}
        result = self.fn(rels, prices)
        assert result == []

    def test_threshold_violation_hi_less_than_lo(self):
        rels = [self._thresh_rel("HI", "LO")]
        prices = {"HI": {"yes_bid": 0.30, "yes_ask": 0.32},
                  "LO": {"yes_bid": 0.60, "yes_ask": 0.62}}
        result = self.fn(rels, prices)
        assert len(result) == 1
        assert result[0]["type"] == "threshold_order"

    def test_ce_no_violation_sum_ge_one(self):
        rels = [self._ce_rel()]
        prices = {"A": {"yes_bid": 0.50, "yes_ask": 0.60},
                  "B": {"yes_bid": 0.40, "yes_ask": 0.50}}
        result = self.fn(rels, prices)
        assert result == []

    def test_ce_violation_buy_both_cheaper_than_one(self):
        rels = [self._ce_rel()]
        prices = {"A": {"yes_bid": 0.30, "yes_ask": 0.35},
                  "B": {"yes_bid": 0.30, "yes_ask": 0.35}}
        result = self.fn(rels, prices)
        assert len(result) == 1
        assert result[0]["type"] == "collectively_exhaustive"

    def test_unknown_rtype_produces_no_violation(self):
        rels = [{"market_id_1": "X", "market_id_2": "Y", "relationship_type": "unknown"}]
        prices = {"X": {"yes_bid": 0.9, "yes_ask": 0.95},
                  "Y": {"yes_bid": 0.9, "yes_ask": 0.95}}
        result = self.fn(rels, prices)
        assert result == []
