"""
tests/test_deadline_ladder_guard.py
===================================
Regression guards for the cumulative-deadline false-positive filter in
markets/relationship_detector.py.

Background
----------
Kalshi lists many "Will X happen by <date>?" series. Every rung of such a series
is nested inside the next one out: if GTA6 ships by Nov 30 2026 it has also
shipped by Jul 1 2027. Their probabilities therefore form a monotone ladder and
may legitimately sum far above 1.

These markets carry no ``floor_strike``, so the original strike-ladder guard
could not see them and classified every pair mutually exclusive. The trade-tape
scanner then reported the entirely coherent quotes $0.92 and $0.97 as an
89-cent "arbitrage". The prices were right; the relationship label was wrong.
"""

from __future__ import annotations

import datetime as dt

import pytest

from engine.relationship_detector import (
    _is_deadline_market,
    _markets_have_ordered_dates,
    detect_mutually_exclusive_set,
)


def _mkt(market_id, title, close_time=None, floor_strike=None):
    return {
        "market_id": market_id,
        "ticker": market_id,
        "title": title,
        "close_time": close_time,
        "floor_strike": floor_strike,
    }


class TestIsDeadlineMarket:
    @pytest.mark.parametrize("title", [
        "WIll GTA6 be released by Dec 31, 2026?",
        "Will GTA6 be released by Nov 30, 2026?",
        "Will the Fed cut before March 2027?",
        "Will it ship on or before Jun 2027?",
        "Resolved prior to 2028?",
        "Will X happen by 12/31?",
    ])
    def test_recognises_deadline_titles(self, title):
        assert _is_deadline_market({"title": title}) is True

    @pytest.mark.parametrize("title", [
        "Will England win the World Cup?",
        "Who wins the Kentucky Derby?",
        "Will Derby County win the league?",   # 'by' inside a word must not match
        "Will CPI exceed 3%?",
        "",
    ])
    def test_rejects_non_deadline_titles(self, title):
        assert _is_deadline_market({"title": title}) is False

    def test_missing_title_is_not_a_deadline(self):
        assert _is_deadline_market({}) is False

    def test_word_boundary_prevents_substring_match(self):
        # "by" appears only inside "Derby"/"Bybee" -- must not trigger.
        assert _is_deadline_market({"title": "Bybee wins in Jan 2027"}) is False


class TestMarketsHaveOrderedDates:
    def test_deadline_pair_with_distinct_close_times_is_a_ladder(self):
        a = _mkt("A", "released by Nov 30, 2026?", dt.datetime(2026, 11, 30))
        b = _mkt("B", "released by Jul 1, 2027?", dt.datetime(2027, 7, 1))
        assert _markets_have_ordered_dates(a, b) is True

    def test_same_close_time_is_not_a_ladder(self):
        ts = dt.datetime(2026, 11, 30)
        a = _mkt("A", "released by Nov 30, 2026?", ts)
        b = _mkt("B", "released by Nov 30, 2026?", ts)
        assert _markets_have_ordered_dates(a, b) is False

    def test_non_deadline_market_is_not_a_ladder(self):
        a = _mkt("A", "released by Nov 30, 2026?", dt.datetime(2026, 11, 30))
        b = _mkt("B", "England wins", dt.datetime(2027, 7, 1))
        assert _markets_have_ordered_dates(a, b) is False

    def test_missing_close_time_is_not_a_ladder(self):
        a = _mkt("A", "released by Nov 30, 2026?", None)
        b = _mkt("B", "released by Jul 1, 2027?", dt.datetime(2027, 7, 1))
        assert _markets_have_ordered_dates(a, b) is False


class TestMutuallyExclusiveSkipsDeadlineLadders:
    """The real payoff: the GTA6 family must not produce ME pairs."""

    GTA6 = [
        _mkt("KXGTA6-26NOV30", "WIll GTA6 be released by Nov 30, 2026?",
             dt.datetime(2026, 11, 30)),
        _mkt("GTA6-26DEC31", "WIll GTA6 be released by Dec 31, 2026?",
             dt.datetime(2026, 12, 31)),
        _mkt("KXGTA6-27JUL01", "WIll GTA6 be released by Jul 1, 2027?",
             dt.datetime(2027, 7, 1)),
        _mkt("KXGTA6-28JAN01", "WIll GTA6 be released by Jan 1, 2028?",
             dt.datetime(2028, 1, 1)),
    ]

    def test_no_me_relationships_for_a_deadline_ladder(self):
        rels = detect_mutually_exclusive_set(self.GTA6, "KXGTA6")
        assert rels == [], (
            "cumulative 'released by <date>' markets are nested, not mutually "
            f"exclusive, but {len(rels)} ME pairs were produced"
        )

    def test_genuine_partition_still_produces_me_pairs(self):
        """The guard must not suppress real mutually-exclusive outcomes."""
        winners = [
            _mkt("W-ENG", "Will England win the World Cup?",
                 dt.datetime(2026, 7, 19)),
            _mkt("W-GHA", "Will Ghana win the World Cup?",
                 dt.datetime(2026, 7, 19)),
            _mkt("W-BRA", "Will Brazil win the World Cup?",
                 dt.datetime(2026, 7, 19)),
        ]
        rels = detect_mutually_exclusive_set(winners, "WORLDCUP")
        assert len(rels) == 3          # 3 choose 2
        assert all(r["relationship_type"] == "mutually_exclusive" for r in rels)

    def test_mixed_set_keeps_only_the_non_ladder_pairs(self):
        markets = self.GTA6[:2] + [
            _mkt("W-ENG", "Will England win the World Cup?",
                 dt.datetime(2026, 7, 19)),
        ]
        rels = detect_mutually_exclusive_set(markets, "MIXED")
        pairs = {frozenset((r["market_id_1"], r["market_id_2"])) for r in rels}
        # The two GTA6 rungs must not be paired with each other.
        assert frozenset(("KXGTA6-26NOV30", "GTA6-26DEC31")) not in pairs
        # Each GTA6 rung against the unrelated winner market is still ME.
        assert len(rels) == 2
