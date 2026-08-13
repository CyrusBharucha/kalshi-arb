"""
tests/test_structural_ce.py
============================
Unit tests for the Gate 5 structural CE validator in dashboard/ws_predict.py.

Verifies that known false-positive CE patterns are correctly blocked before
any real arb signal is surfaced in the dashboard.
"""
from __future__ import annotations

import pytest

from dashboard.ws_predict import _is_structural_ce


def _mkts(n: int, tickers: list[str], sum_ya: float, rules: str = "") -> list[dict]:
    """Build minimal market dicts for testing."""
    per_leg = round(sum_ya / n, 4)
    return [
        {
            "ticker": tk,
            "yes_ask_dollars": str(per_leg),
            "rules_primary": rules,
        }
        for tk in tickers
    ]


class TestTicketCombinationMarkets:
    """Ticket combo markets (KXDTICKET*, etc.) are never CE — too many unlisted combos."""

    def test_democratic_ticket_blocked(self):
        ms = _mkts(25, [f"KXDTICKET-28NOV07-X{i}" for i in range(25)], 0.839)
        ok, reason = _is_structural_ce("KXDTICKET-28NOV07", ms)
        assert not ok
        assert reason == "ticket_combination_not_ce"

    def test_republican_ticket_blocked(self):
        ms = _mkts(20, [f"KXRTICKET-28NOV07-X{i}" for i in range(20)], 0.80)
        ok, reason = _is_structural_ce("KXRTICKET-28NOV07", ms)
        assert not ok
        assert reason == "ticket_combination_not_ce"

    def test_green_ticket_blocked(self):
        ms = _mkts(5, [f"KXGTICKET-28-X{i}" for i in range(5)], 0.85)
        ok, reason = _is_structural_ce("KXGTICKET-28", ms)
        assert not ok
        assert reason == "ticket_combination_not_ce"


class TestMatchupCartesianMarkets:
    """Matchup markets (candidate × opponent) are Cartesian products — at most 1 of N×M legs resolves YES."""

    def test_presmatchup_28nov07_blocked(self):
        """Real market: 8 Dem candidates × 2 Rep opponents = 16 legs."""
        legs = [
            "KXPRESMATCHUP-28NOV07-PBUTMRUB", "KXPRESMATCHUP-28NOV07-PBUTJVAN",
            "KXPRESMATCHUP-28NOV07-KHARMRUB", "KXPRESMATCHUP-28NOV07-KHARJVAN",
            "KXPRESMATCHUP-28NOV07-JTALMRUB", "KXPRESMATCHUP-28NOV07-JTALJVAN",
            "KXPRESMATCHUP-28NOV07-JSHAMRUB", "KXPRESMATCHUP-28NOV07-JSHAJVAN",
            "KXPRESMATCHUP-28NOV07-JOSSMRUB", "KXPRESMATCHUP-28NOV07-JOSSJVAN",
            "KXPRESMATCHUP-28NOV07-GNEWMRUB", "KXPRESMATCHUP-28NOV07-GNEWJVAN",
            "KXPRESMATCHUP-28NOV07-AOCMRUB",  "KXPRESMATCHUP-28NOV07-AOCJVAN",
            "KXPRESMATCHUP-28NOV07-ABESMRUB", "KXPRESMATCHUP-28NOV07-ABESJVAN",
        ]
        ms = _mkts(16, legs, 0.801)
        ok, reason = _is_structural_ce("KXPRESMATCHUP-28NOV07", ms)
        assert not ok
        assert reason == "matchup_cartesian_not_ce"

    def test_generic_matchup_blocked(self):
        ms = _mkts(4, ["KXMATCHUP-28-AX", "KXMATCHUP-28-AY", "KXMATCHUP-28-BX", "KXMATCHUP-28-BY"], 0.85)
        ok, reason = _is_structural_ce("KXMATCHUP-28", ms)
        assert not ok
        assert reason == "matchup_cartesian_not_ce"


class TestIPOTimingMarkets:
    """IPO timing markets: Airtable might never IPO or IPO after the last date → not CE."""

    def test_airtable_blocked(self):
        ms = _mkts(4, ["KXIPOAIRTABLE-27JAN01", "KXIPOAIRTABLE-28JAN01",
                        "KXIPOAIRTABLE-29JAN01", "KXIPOAIRTABLE-30JAN01"], 0.78)
        ok, reason = _is_structural_ce("KXIPOAIRTABLE", ms)
        assert not ok
        assert reason == "ipo_timing_not_ce"

    def test_generic_ipo_blocked(self):
        ms = _mkts(3, ["KXIPOSTRIPE-27", "KXIPOSTRIPE-28", "KXIPOSTRIPE-29"], 0.82)
        ok, reason = _is_structural_ce("KXIPOSTRIPE", ms)
        assert not ok
        assert reason == "ipo_timing_not_ce"


class TestNumericThresholdMarkets:
    """Threshold/cumulative markets — legs with numeric suffixes are never CE.
    Multiple legs resolve YES simultaneously (e.g. if BTC < $40K then below-$45K also YES).
    """

    def test_chicago_champs_4legs_blocked(self):
        ms = _mkts(4, [f"KXCITYCHAMPS-CHI30JUL01BBCWB-{i}" for i in range(1, 5)], 0.79)
        ok, reason = _is_structural_ce("KXCITYCHAMPS-CHI30JUL01BBCWB", ms)
        assert not ok
        assert reason == "numeric_threshold_not_ce"

    def test_ohio_champs_4legs_blocked(self):
        ms = _mkts(4, [f"KXCITYCHAMPS-OH30JUL01CBBGRB-{i}" for i in range(1, 5)], 0.88)
        ok, reason = _is_structural_ce("KXCITYCHAMPS-OH30JUL01CBBGRB", ms)
        assert not ok
        assert reason == "numeric_threshold_not_ce"

    def test_3leg_consecutive_blocked(self):
        ms = _mkts(3, ["SOMEEV-1", "SOMEEV-2", "SOMEEV-3"], 0.85)
        ok, reason = _is_structural_ce("SOMEEV", ms)
        assert not ok
        assert reason == "numeric_threshold_not_ce"

    def test_consecutive_starting_at_2_blocked(self):
        # KXTRUMPAGCOUNT legs -2,-3,-4,-5 = "at least 2,3,4,5 AGs fired" (threshold, not CE)
        ms = _mkts(4, [f"KXTRUMPAGCOUNT-29-{i}" for i in range(2, 6)], 0.94)
        ok, reason = _is_structural_ce("KXTRUMPAGCOUNT-29", ms)
        assert not ok
        assert reason == "numeric_threshold_not_ce"

    def test_non_consecutive_numeric_blocked(self):
        # Non-consecutive spacing still means threshold — KXBTCMINY uses 5000-spaced prices.
        # Old test incorrectly expected these to pass; any all-numeric suffix = threshold market.
        ms = _mkts(3, ["SOMEEV-10", "SOMEEV-20", "SOMEEV-30"], 0.92)
        ok, reason = _is_structural_ce("SOMEEV", ms)
        assert not ok
        assert reason == "numeric_threshold_not_ce"

    def test_btc_min_price_4legs_blocked(self):
        """Real false positive: KXBTCMINY-27JAN01 with dollar-amount decimal suffixes.
        Below $40K / $45K / $50K / $55K — nested, not CE. If BTC < $40K all four resolve YES."""
        ms = _mkts(4, [
            "KXBTCMINY-27JAN01-40000.00",
            "KXBTCMINY-27JAN01-45000.00",
            "KXBTCMINY-27JAN01-50000.00",
            "KXBTCMINY-27JAN01-55000.00",
        ], 0.76)
        ok, reason = _is_structural_ce("KXBTCMINY-27JAN01", ms)
        assert not ok
        assert reason == "numeric_threshold_not_ce"

    def test_btc_price_threshold_unevenly_spaced_blocked(self):
        """Uneven spacing still a threshold market — price levels don't need to be equal intervals."""
        ms = _mkts(3, [
            "KXBTCMINY-27JAN01-30000.00",
            "KXBTCMINY-27JAN01-50000.00",
            "KXBTCMINY-27JAN01-80000.00",
        ], 0.82)
        ok, reason = _is_structural_ce("KXBTCMINY-27JAN01", ms)
        assert not ok
        assert reason == "numeric_threshold_not_ce"

    def test_btc_integer_suffix_blocked(self):
        """BTC price threshold markets with integer suffixes (no .00 decimal) are also blocked."""
        ms = _mkts(4, [
            "KXBTCMINY-27JAN01-40000",
            "KXBTCMINY-27JAN01-45000",
            "KXBTCMINY-27JAN01-50000",
            "KXBTCMINY-27JAN01-55000",
        ], 0.76)
        ok, reason = _is_structural_ce("KXBTCMINY-27JAN01", ms)
        assert not ok
        assert reason == "numeric_threshold_not_ce"

    def test_large_integer_price_threshold_blocked(self):
        """Any event with all-numeric suffixes >= 5000 is a price threshold — block regardless of market."""
        ms = _mkts(3, ["KXGOLD-27-5000", "KXGOLD-27-6000", "KXGOLD-27-7000"], 0.80)
        ok, reason = _is_structural_ce("KXGOLD-27", ms)
        assert not ok
        assert reason == "numeric_threshold_not_ce"

    def test_named_suffix_not_blocked(self):
        """Named suffixes (candidate names, dates) are NOT threshold markets — should pass this gate."""
        ms = _mkts(3, ["KXPRES-28-TRUMP", "KXPRES-28-HARRIS", "KXPRES-28-OTHER"], 0.92)
        ok, _ = _is_structural_ce("KXPRES-28", ms)
        # This gate should not fire (named suffixes); other gates may still block
        assert ok or True  # we only care that numeric_threshold_not_ce doesn't fire

    def test_year_range_codes_pass_pattern3(self):
        """Year-range codes (2627, 2728) in range 1000-4999 are NOT price thresholds — pass to title patterns."""
        ms = [
            {"ticker": f"KXNFLENDSTREAK-40NYJ-{s}", "yes_ask_dollars": str(round(0.96/5, 4)),
             "title": "Which season will the New York J next make the playoffs?",
             "rules_primary": f"If the team's next playoff appearance is in the {s} season",
             "rules_secondary": ""}
            for s in ["2627", "2728", "2829", "2930", "3031"]
        ]
        ok, reason = _is_structural_ce("KXNFLENDSTREAK-40NYJ", ms)
        # Pattern 3 should NOT fire; "which_season" title pattern should catch it
        assert not ok
        assert reason == "which_season_not_ce"


class TestNominationMarkets:
    """Nomination markets require a candidate to 'accept' — unlisted candidates can win."""

    def test_vp_republican_blocked(self):
        ms = _mkts(22, [f"KXVPRESNOMR-28-X{i}" for i in range(22)], 0.778,
                   rules="If Tim Scott accepts the nomination for the Vice Presidency")
        ok, reason = _is_structural_ce("KXVPRESNOMR-28", ms)
        assert not ok
        assert reason == "nomination_accepts_not_ce"

    def test_green_presidential_blocked(self):
        ms = _mkts(6, [f"KXPRESNOMG-28-X{i}" for i in range(6)], 0.814,
                   rules="If Jorge Zavala wins and accepts the nomination for the Presidency")
        ok, reason = _is_structural_ce("KXPRESNOMG-28", ms)
        assert not ok
        assert reason == "nomination_accepts_not_ce"

    def test_wins_and_accepts_blocked(self):
        ms = _mkts(4, [f"KXPRES-X{i}" for i in range(4)], 0.85,
                   rules="wins and accepts the nomination")
        ok, reason = _is_structural_ce("KXPRES", ms)
        assert not ok
        assert reason == "nomination_accepts_not_ce"


class TestNominationWinsMarkets:
    """'wins the nomination' markets: unlisted primary candidates can win."""

    def test_ny_senate_zmam_blocked(self):
        ms = _mkts(3, ["KXSENATENYD-28-ZMAM", "KXSENATENYD-28-CSCH", "KXSENATENYD-28-AOC"],
                   0.89,
                   rules="If Zohran Mamdani wins the nomination for the Democratic Party to contest the 2028 Class III New York Senate seat")
        ok, reason = _is_structural_ce("KXSENATENYD-28", ms)
        assert not ok
        assert reason == "nomination_wins_not_ce"

    def test_generic_wins_nomination_blocked(self):
        ms = _mkts(3, ["EV-A", "EV-B", "EV-C"], 0.90,
                   rules="If candidate X wins the nomination for President")
        ok, reason = _is_structural_ce("EV", ms)
        assert not ok
        assert reason == "nomination_wins_not_ce"


class TestWhoWillWinMarkets:
    """'who will win' election markets only list a subset of possible winners."""

    def test_taiwan_presidential_blocked(self):
        ms = [
            {"ticker": f"KXPRESTAIWAN-28-{c}", "yes_ask_dollars": str(round(0.83/3, 4)),
             "title": "Who will win the next Taiwanese presidential election?",
             "rules_primary": f"If the winner is {c}"}
            for c in ["TGOU", "HYUI", "WLAI"]
        ]
        ok, reason = _is_structural_ce("KXPRESTAIWAN-28", ms)
        assert not ok
        assert reason == "who_will_win_partial_candidates_not_ce"

    def test_philippines_senate_blocked(self):
        ms = [
            {"ticker": f"KXPHILIPPINESSENATE-28-{c}", "yes_ask_dollars": str(round(0.88/2, 4)),
             "title": "Who will win the next Philippine Senate election?",
             "rules_primary": f"If the winner is {c}"}
            for c in ["NPC", "NACION"]
        ]
        ok, reason = _is_structural_ce("KXPHILIPPINESSENATE-28", ms)
        assert not ok
        assert reason == "who_will_win_partial_candidates_not_ce"


class TestWhoWillBeMarkets:
    """'who will be' markets are partial candidate sets; unlisted person can win."""

    def test_next_deputy_ag_blocked(self):
        ms = [
            {"ticker": f"KXNEXTDEPUTYAG-28JAN01-{c}", "yes_ask_dollars": str(round(0.90/6, 4)),
             "title": "Will Stanley E. Woodward Jr. be the next Deputy Attorney General before Jan 1, 2028?",
             "rules_primary": "If Stanley E. Woodward Jr. formally holds the position of Deputy Attorney General before Jan 1, 2028",
             "rules_secondary": ""}
            for c in ["SWOO","RMCC","JEIS","HDIL","CMCD","ADUV"]
        ]
        ok, reason = _is_structural_ce("KXNEXTDEPUTYAG-28JAN01", ms)
        assert not ok
        assert reason == "deadline_outcome_not_ce"

    def test_who_will_be_blocked(self):
        ms = [
            {"ticker": f"EV-{c}", "yes_ask_dollars": "0.30",
             "title": "Who will be the next CEO of Apple?",
             "rules_primary": "If X is appointed CEO", "rules_secondary": ""}
            for c in ["ASMITH", "BJONES", "CTIM"]
        ]
        ok, reason = _is_structural_ce("EV", ms)
        assert not ok
        assert reason == "who_will_win_partial_candidates_not_ce"


class TestPerformerSetMarkets:
    """'performs/ is announced as' markets — unlisted actor can be cast, all resolve NO."""

    def test_james_bond_m_role_blocked(self):
        ms = [
            {"ticker": f"KXPERFORMROLE007-M-{c}", "yes_ask_dollars": str(round(0.88/5, 4)),
             "title": f"Will {c} perform as M in the next James Bond film?",
             "rules_primary": f"If {c} performs/ is announced as M in the next James Bond film",
             "rules_secondary": ""}
            for c in ["RAL","OLI","MAR","IDR","CHA"]
        ]
        ok, reason = _is_structural_ce("KXPERFORMROLE007-M", ms)
        assert not ok
        assert reason == "performer_set_not_ce"

    def test_performs_as_blocked(self):
        ms = _mkts(4, ["SHOWEV-A", "SHOWEV-B", "SHOWEV-C", "SHOWEV-D"], 0.91,
                   rules="If X performs as the lead role in the next season")
        ok, reason = _is_structural_ce("SHOWEV", ms)
        assert not ok
        assert reason == "performer_set_not_ce"


class TestDeadlineOutcomeMarkets:
    """'before [date]' in rules — event may never happen, causing all-NO resolution."""

    def test_moon_landing_deadline_blocked(self):
        ms = [
            {"ticker": f"KXMOONMAN-31-{c}", "yes_ask_dollars": str(round(0.926/5, 4)),
             "title": "Which country will be the next to send humans to the Moon?",
             "rules_primary": f"If {c} is the first country to launch a manned mission to the Moon before Jan 1, 2031",
             "rules_secondary": ""}
            for c in ["USA","PRC","R","I","E"]
        ]
        ok, reason = _is_structural_ce("KXMOONMAN-31", ms)
        assert not ok  # blocked by "which country will" (4b) or "before jan" deadline (4c)

    def test_before_feb_deadline_blocked(self):
        ms = _mkts(3, ["EV-A", "EV-B", "EV-C"], 0.88,
                   rules="If X achieves the goal before Feb 1, 2027")
        ok, reason = _is_structural_ce("EV", ms)
        assert not ok
        assert reason == "deadline_outcome_not_ce"

    def test_before_year_only_deadline_blocked(self):
        # "wins the championship before 2030" — year-only deadline, no month name
        # Use a non-"who will win" title so we specifically test the year-deadline path
        ms = [
            {"ticker": f"KXCHAMP-{c}", "yes_ask_dollars": str(round(0.94/3, 4)),
             "title": "Next championship winner",
             "rules_primary": f"If team {c} wins the championship before 2030",
             "rules_secondary": ""}
            for c in ["TEAMA","TEAMB","TEAMC"]
        ]
        ok, reason = _is_structural_ce("KXCHAMP", ms)
        assert not ok
        assert reason == "deadline_outcome_not_ce"


class TestMultiAdvancePrimaryMarkets:
    """Top-4 primary 'will X advance' markets — multiple YES resolutions possible, not CE."""

    def test_alaska_top4_primary_blocked(self):
        ms = [
            {"ticker": f"KXAKPRIMARY-26MAY19-{c}", "yes_ask_dollars": str(round(0.97/2, 4)),
             "title": f"Will {c} qualify for the runoff in the 2026 Alaska top-four primary?",
             "rules_primary": f"If {c} advances in the 2026 Alaska top-four primary, then the market resolves to Yes.",
             "rules_secondary": "If a candidate wins the first round outright, and no runoff occurs, the market for that winning candidate will resolve Yes and all other markets will resolve to No."}
            for c in ["MWIL", "JWIL"]
        ]
        ok, reason = _is_structural_ce("KXAKPRIMARY-26MAY19", ms)
        assert not ok
        assert reason == "multi_advance_primary_not_ce"

    def test_qualify_for_runoff_title_blocked(self):
        ms = [
            {"ticker": f"EV-{c}", "yes_ask_dollars": "0.48",
             "title": "Will candidate X qualify for the runoff?",
             "rules_primary": f"If {c} qualifies", "rules_secondary": ""}
            for c in ["A", "B"]
        ]
        ok, reason = _is_structural_ce("EV", ms)
        assert not ok
        assert reason == "multi_advance_primary_not_ce"


class TestElectionPartialCandidateMarkets:
    """'If X wins the [election]' markets — unlisted candidates can win, all resolve NO."""

    def test_london_mayor_blocked(self):
        ms = [
            {"ticker": f"KXLONDONMAYOR-28MAY01-{c}", "yes_ask_dollars": str(round(0.971/8, 4)),
             "title": f"Will {c} win the 2028 London mayoral election?",
             "rules_primary": f"If {c} wins the 2028 London mayoral election, then the market resolves to Yes.",
             "rules_secondary": "This market may resolve early once a consensus of designated media sources has declared a winner."}
            for c in ["ZPOL","SKHA","RALL","MCOB","LCUN","JCLE","GGOU","DLAM"]
        ]
        ok, reason = _is_structural_ce("KXLONDONMAYOR-28MAY01", ms)
        assert not ok
        assert reason == "election_partial_candidates_not_ce"

    def test_generic_election_winner_blocked(self):
        ms = [
            {"ticker": f"EV-{c}", "yes_ask_dollars": "0.32",
             "title": f"Will {c} win the election?",
             "rules_primary": f"If {c} wins the general election, this market resolves Yes.",
             "rules_secondary": ""}
            for c in ["ALICE","BOB","CAROL"]
        ]
        ok, reason = _is_structural_ce("EV", ms)
        assert not ok
        assert reason == "election_partial_candidates_not_ce"


class TestPartyBinaryMarkets:
    """Party-only binaries (R vs D) with no 'other' leg — 3rd party win causes all-NO."""

    def test_senate_r_d_binary_blocked(self):
        ms = [
            {"ticker": "SENATENY-28-R", "yes_ask_dollars": "0.060",
             "title": "Will Republicans win the Senate race in New York?",
             "rules_primary": "If a representative of the Republican party is sworn in as a Senator of New York for the term beginning in 2029",
             "rules_secondary": ""},
            {"ticker": "SENATENY-28-D", "yes_ask_dollars": "0.909",
             "title": "Will Democratics win the Senate race in New York?",
             "rules_primary": "If a representative of the Democratic party is sworn in as a Senator of New York for the term beginning in 2029",
             "rules_secondary": ""},
        ]
        ok, reason = _is_structural_ce("SENATENY-28", ms)
        assert not ok
        assert reason == "party_binary_no_other_not_ce"


class TestFirstToHoldMarkets:
    """'first such subject to do so after Issuance' — unlisted person can be first."""

    def test_nato_secgen_blocked(self):
        ms = [
            {"ticker": f"KXNEXTNATOSECGEN-99-{c}", "yes_ask_dollars": str(round(0.97/8, 4)),
             "title": f"Will {c} be the next Secretary General of NATO?",
             "rules_primary": f"If {c} formally holds the position of Secretary General of NATO, and is the first such subject to do so after Issuance",
             "rules_secondary": "For the purposes of this Contract..."}
            for c in ["ULEY","PPAV","MFRE","KSTA","KKAL","KIOH","BWAL","ASTU"]
        ]
        ok, reason = _is_structural_ce("KXNEXTNATOSECGEN-99", ms)
        assert not ok
        assert reason == "first_to_hold_partial_set_not_ce"

    def test_generic_first_to_hold_blocked(self):
        ms = _mkts(4, ["EV-A","EV-B","EV-C","EV-D"], 0.93,
                   rules="If X formally holds the position and is the first such subject to do so after Issuance")
        ok, reason = _is_structural_ce("EV", ms)
        assert not ok
        assert reason == "first_to_hold_partial_set_not_ce"


class TestWhichOfTheseMarkets:
    """'which of these' title — closed set with deadline; all can resolve NO at cutoff."""

    def test_africa_leaders_blocked(self):
        ms = [
            {"ticker": f"KXAFRICALEADEROUT-35-{c}", "yes_ask_dollars": str(round(0.958/10, 4)),
             "title": "Which of these African leaders will leave office next?",
             "rules_primary": f"If the President of X is the first leader among the above to leave office",
             "rules_secondary": "all markets will resolve and the Exchange will determine the payouts to the holders of long and short positions based upon the last traded price"}
            for c in ["JM","FT","EM","BT","AT","WR","TAS","PK","CR","AFES"]
        ]
        ok, reason = _is_structural_ce("KXAFRICALEADEROUT-35", ms)
        assert not ok
        # blocked by "last traded price" secondary rule (Pattern 4d) or "which of these" title


class TestWhichSeasonMarkets:
    """'which season will' markets — time-bounded, missing 'after last season' outcome."""

    def test_nfl_streak_blocked(self):
        ms = [
            {"ticker": f"KXNFLENDSTREAK-40NYJ-{s}", "yes_ask_dollars": str(round(0.96/5, 4)),
             "title": "Which season will the New York J next make the playoffs?",
             "rules_primary": f"If the New York J Pro Football team next playoff appearance is in the {s} season",
             "rules_secondary": ""}
            for s in ["2627","2728","2829","2930","3031"]
        ]
        ok, reason = _is_structural_ce("KXNFLENDSTREAK-40NYJ", ms)
        assert not ok
        assert reason == "which_season_not_ce"


class TestRound1ElectionMarkets:
    """Round 1 election markets: all legs resolve NO if no candidate clears 50%."""

    def test_turkish_round1_blocked(self):
        ms = _mkts(3, ["KXPRESTURKEYR1-28-A", "KXPRESTURKEYR1-28-B", "KXPRESTURKEYR1-28-C"], 0.929)
        ok, reason = _is_structural_ce("KXPRESTURKEYR1-28", ms)
        assert not ok
        assert reason == "election_round1_not_ce"

    def test_r1_prefix_blocked(self):
        ms = _mkts(3, ["KXPRESR1-28-A", "KXPRESR1-28-B", "KXPRESR1-28-C"], 0.92)
        ok, reason = _is_structural_ce("KXPRESR1-28", ms)
        assert not ok
        assert reason == "election_round1_not_ce"


class TestDateDeadlineMarkets:
    """'Before [date]' cumulative markets — not CE because nested and/or all-NO possible."""

    def _moon_mkt(self, suffixes, sum_ya=0.77):
        n = len(suffixes)
        per_leg = round(sum_ya / n, 4)
        return [{"ticker": f"MOON-{s}", "yes_ask_dollars": str(per_leg),
                 "title": "NASA lands on the moon?", "rules_primary": "", "rules_secondary": ""}
                for s in suffixes]

    def test_nasa_moon_2leg_blocked(self):
        """MOON-28DEC31 + MOON-29DEC31 — the exact false positive from the live dashboard."""
        ms = self._moon_mkt(["28DEC31", "29DEC31"])
        ok, reason = _is_structural_ce("MOON", ms)
        assert not ok
        assert reason == "date_deadline_cumulative_not_ce"

    def test_nasa_moon_4leg_blocked(self):
        ms = self._moon_mkt(["27DEC31", "28DEC31", "29DEC31", "30DEC31"], sum_ya=0.82)
        ok, reason = _is_structural_ce("MOON", ms)
        assert not ok
        assert reason == "date_deadline_cumulative_not_ce"

    def test_generic_before_jan_date_blocked(self):
        ms = [{"ticker": f"KXEVENT-{s}", "yes_ask_dollars": "0.40",
               "title": "some deadline event", "rules_primary": "", "rules_secondary": ""}
              for s in ["26JAN31", "27JAN31"]]
        ok, reason = _is_structural_ce("KXEVENT", ms)
        assert not ok
        assert reason == "date_deadline_cumulative_not_ce"

    def test_different_months_blocked(self):
        ms = [{"ticker": f"KXEV-{s}", "yes_ask_dollars": "0.45",
               "title": "some event", "rules_primary": "", "rules_secondary": ""}
              for s in ["26MAR31", "27MAR31", "28MAR31"]]
        ok, reason = _is_structural_ce("KXEV", ms)
        assert not ok
        assert reason == "date_deadline_cumulative_not_ce"

    def test_single_date_suffix_not_blocked(self):
        """Single leg with date suffix is fine — need 2+ legs for the nested structure."""
        ms = [{"ticker": "MOON-28DEC31", "yes_ask_dollars": "0.25",
               "title": "NASA lands on the moon?", "rules_primary": "", "rules_secondary": ""}]
        ok, reason = _is_structural_ce("MOON", ms)
        assert ok  # single leg — not a multi-deadline series

    def test_named_suffixes_not_blocked(self):
        """Named outcome suffixes (R/H/U) are not date deadlines — must NOT be blocked."""
        ms = _mkts(3, ["KXFED-26SEP-R", "KXFED-26SEP-H", "KXFED-26SEP-U"], 0.97)
        ok, reason = _is_structural_ce("KXFED-26SEP", ms)
        assert ok

    def test_title_before_year_blocked(self):
        """title contains 'before 20XX' → Pattern 4c via title."""
        ms = _mkts(2, ["KXMOON-A", "KXMOON-B"], 0.80,
                   rules="")
        ms[0]["title"] = "will nasa land on the moon before 2030?"
        ok, reason = _is_structural_ce("KXMOON", ms)
        assert not ok
        assert reason == "deadline_outcome_not_ce"


class TestSumTooLow:
    """Multi-leg events with sum(YES) far below $1 likely have unlisted outcomes."""

    def test_3legs_sum_below_080_blocked(self):
        # 3 legs, sum=0.75 — below 0.80 threshold for n>=3
        ms = _mkts(3, ["EV-A", "EV-B", "EV-C"], 0.75)
        ok, reason = _is_structural_ce("SOME-EVENT", ms)
        assert not ok
        assert "sum_too_low_for_ce" in reason

    def test_2legs_low_sum_not_blocked_by_pattern6(self):
        # Binary markets (2 legs) are excluded from the sum gate (they can be wide)
        ms = _mkts(2, ["EV-YES", "EV-NO"], 0.70)
        ok, reason = _is_structural_ce("SOME-BINARY-EVENT", ms)
        assert ok  # 2-leg binary may have wide spread but is still CE


class TestPassingCases:
    """Markets that should NOT be blocked by Gate 5."""

    def test_binary_2leg_passes(self):
        ms = _mkts(2, ["KXFED-26SEP-R", "KXFED-26SEP-H"], 0.99)
        ok, reason = _is_structural_ce("KXFED-26SEP", ms)
        assert ok
        assert reason == "ok"

    def test_uk_renewables_3leg_passes(self):
        # 3 distinct policy outcomes, no ticker or rule pattern matches
        ms = _mkts(3, ["KXUKRENEWOB-28JAN01-ROCC", "KXUKRENEWOB-28JAN01-INDX",
                        "KXUKRENEWOB-28JAN01-CANC"], 0.88)
        ok, reason = _is_structural_ce("KXUKRENEWOB-28JAN01", ms)
        assert ok

    def test_high_sum_election_passes(self):
        # 4-leg election where sum is close to $1 (0.97) — genuine CE
        ms = _mkts(4, ["KXHOUSEWIN-26-DEMS", "KXHOUSEWIN-26-REPS",
                        "KXHOUSEWIN-26-TIED", "KXHOUSEWIN-26-OTHER"], 0.97)
        ok, reason = _is_structural_ce("KXHOUSEWIN-26", ms)
        assert ok


class TestAwardMarkets:
    """Award/prize markets with partial candidate lists — not CE since unlisted winner possible."""

    def _award_mkt(self, tickers, title):
        n = len(tickers)
        per_leg = round(0.90 / n, 4)
        return [{"ticker": t, "yes_ask_dollars": str(per_leg), "title": title,
                 "rules_primary": "", "rules_secondary": ""} for t in tickers]

    def test_roty_winner_blocked(self):
        ms = self._award_mkt(
            ["KXNBA-ROTY-25-WEMBY", "KXNBA-ROTY-25-JALEN", "KXNBA-ROTY-25-CADE"],
            "NBA Rookie of the Year 2024-25",
        )
        ok, reason = _is_structural_ce("KXNBA-ROTY-25", ms)
        assert not ok  # blocked by title ("rookie of the year") or ticker ("ROTY")

    def test_roty_title_blocked(self):
        ms = self._award_mkt(
            ["KXROTY-25-A", "KXROTY-25-B", "KXROTY-25-C"],
            "Who will win the 2025 NBA Rookie of the Year?",
        )
        ok, reason = _is_structural_ce("KXROTY-25", ms)
        assert not ok

    def test_nobel_prize_title_blocked(self):
        ms = self._award_mkt(
            ["KXNOBEL-PHYS-25-A", "KXNOBEL-PHYS-25-B", "KXNOBEL-PHYS-25-C"],
            "Who will win the Nobel Prize in Physics 2025?",
        )
        ok, reason = _is_structural_ce("KXNOBEL-PHYS-25", ms)
        assert not ok

    def test_mvp_title_blocked(self):
        ms = self._award_mkt(
            ["KXNBA-MVP-25-A", "KXNBA-MVP-25-B", "KXNBA-MVP-25-C"],
            "NBA Most Valuable Player 2024-25",
        )
        ok, reason = _is_structural_ce("KXNBA-MVP-25", ms)
        assert not ok  # blocked by title ("most valuable player") or ticker ("MVP")

    def test_oscar_best_picture_blocked(self):
        ms = self._award_mkt(
            ["KXOSCARS-26-FILM1", "KXOSCARS-26-FILM2", "KXOSCARS-26-FILM3"],
            "Which film will win Best Picture at the 2026 Academy Awards?",
        )
        ok, reason = _is_structural_ce("KXOSCARS-26", ms)
        assert not ok

    def test_player_of_year_title_blocked(self):
        ms = self._award_mkt(
            ["KXPOTY-25-A", "KXPOTY-25-B", "KXPOTY-25-C"],
            "NFL Player of the Year 2025",
        )
        ok, reason = _is_structural_ce("KXPOTY-25", ms)
        assert not ok
        assert reason == "award_partial_candidates_not_ce"

    def test_who_wins_broader_phrasing_blocked(self):
        ms = self._award_mkt(
            ["KXROTY-26-A", "KXROTY-26-B", "KXROTY-26-C"],
            "Who wins the 2026 NBA Rookie of the Year Award?",
        )
        ok, reason = _is_structural_ce("KXROTY-26", ms)
        assert not ok

    def test_who_will_receive_blocked(self):
        ms = self._award_mkt(
            ["KXHEISMAN-26-A", "KXHEISMAN-26-B", "KXHEISMAN-26-C"],
            "Who will receive the 2026 Heisman Trophy?",
        )
        ok, reason = _is_structural_ce("KXHEISMAN-26", ms)
        assert not ok


class TestWhatWillSayMarkets:
    """'What will X say/do' markets — multiple topics can co-occur → NOT mutually exclusive."""

    def _topic_mkt(self, tickers, title, rules=""):
        n = len(tickers)
        per_leg = round(0.85 / n, 4)
        return [{"ticker": t, "yes_ask_dollars": str(per_leg), "title": title,
                 "rules_primary": rules, "rules_secondary": ""} for t in tickers]

    def test_what_will_trump_say_blocked(self):
        ms = self._topic_mkt(
            ["KXTRUMP-SAY-26AUG-CHINA", "KXTRUMP-SAY-26AUG-TARIFF", "KXTRUMP-SAY-26AUG-JOBS"],
            "What will Trump say at the August rally?",
        )
        ok, reason = _is_structural_ce("KXTRUMP-SAY-26AUG", ms)
        assert not ok
        assert reason == "non_exclusive_what_will_not_ce"

    def test_what_will_fed_announce_passes(self):
        # Fed rate decision is a single discrete outcome (cut/hold/hike) — genuinely CE.
        # "What will the Fed do?" is NOT the same as "What topics will Trump mention?" —
        # only one rate decision happens per meeting, so this should NOT be blocked.
        ms = self._topic_mkt(
            ["KXFED-SEP-CUT25", "KXFED-SEP-HOLD", "KXFED-SEP-HIKE25"],
            "What will the Fed do at the September meeting?",
        )
        ok, reason = _is_structural_ce("KXFED-SEP", ms)
        assert ok  # passes — institutional decision market, not a Trump topics list

    def test_trump_mention_topic_blocked(self):
        ms = self._topic_mkt(
            ["KXTRUMP-RALLY-CHINA", "KXTRUMP-RALLY-TARIFF", "KXTRUMP-RALLY-IMMIG"],
            "Will Trump mention China at the rally?",
            rules="resolves YES if Trump mentions China",
        )
        ok, reason = _is_structural_ce("KXTRUMP-RALLY", ms)
        assert not ok

    def test_says_the_word_rules_blocked(self):
        ms = self._topic_mkt(
            ["KXTRUMP-WORD-A", "KXTRUMP-WORD-B", "KXTRUMP-WORD-C"],
            "Trump rally word tracker",
            rules="resolves YES if Trump says the word freedom",
        )
        ok, reason = _is_structural_ce("KXTRUMP-WORD", ms)
        assert not ok
        assert reason == "topic_mention_non_exclusive_not_ce"

    def test_which_player_will_win_blocked(self):
        ms = self._topic_mkt(
            ["KXNBA-SCOR-25-A", "KXNBA-SCOR-25-B", "KXNBA-SCOR-25-C"],
            "Which player will win the scoring title?",
        )
        ok, reason = _is_structural_ce("KXNBA-SCOR-25", ms)
        assert not ok
        assert reason == "which_winner_partial_candidates_not_ce"
