"""
tests/test_boc_panel.py
=======================
Unit tests for dashboard/boc_panel.py pure helpers:
  - next_boc_meeting() date arithmetic and edge cases
  - build_boc_rows() formatting of decimal vs percent inputs
  - has_any_rate() emptiness detection
  - countdown_tone() colour banding
  - fetch_boc_rates() never raises
"""
from __future__ import annotations

from datetime import date

import pytest

from dashboard import boc_panel as bp


class TestNextBocMeeting:
    def test_returns_tuple_of_date_and_int(self):
        result = bp.next_boc_meeting(date(2026, 1, 1))
        assert result is not None
        meeting, days = result
        assert isinstance(meeting, date)
        assert isinstance(days, int)

    def test_picks_earliest_upcoming_meeting(self):
        meeting, _ = bp.next_boc_meeting(date(2026, 1, 1))
        assert meeting == min(bp.BOC_MEETING_DATES)

    def test_days_until_is_correct(self):
        meeting, days = bp.next_boc_meeting(date(2026, 1, 1))
        assert days == (meeting - date(2026, 1, 1)).days

    def test_meeting_today_returns_zero_days(self):
        target = bp.BOC_MEETING_DATES[0]
        meeting, days = bp.next_boc_meeting(target)
        assert meeting == target
        assert days == 0

    def test_skips_past_meetings(self):
        first, second = bp.BOC_MEETING_DATES[0], bp.BOC_MEETING_DATES[1]
        meeting, _ = bp.next_boc_meeting(first)
        assert meeting == first
        day_after = date.fromordinal(first.toordinal() + 1)
        meeting2, _ = bp.next_boc_meeting(day_after)
        assert meeting2 == second

    def test_returns_none_when_schedule_exhausted(self):
        past_end = date.fromordinal(max(bp.BOC_MEETING_DATES).toordinal() + 1)
        assert bp.next_boc_meeting(past_end) is None

    def test_default_today_does_not_raise(self):
        bp.next_boc_meeting()  # uses utcnow; may be None once schedule lapses

    def test_schedule_is_sorted_and_unique(self):
        assert bp.BOC_MEETING_DATES == sorted(bp.BOC_MEETING_DATES)
        assert len(set(bp.BOC_MEETING_DATES)) == len(bp.BOC_MEETING_DATES)


class TestBuildBocRows:
    def test_returns_one_row_per_field(self):
        rows = bp.build_boc_rows({})
        assert len(rows) == len(bp._FIELDS)

    def test_missing_values_render_as_dashes(self):
        rows = dict(bp.build_boc_rows({}))
        assert rows["POLICY RATE"] == "--"

    def test_decimal_fraction_scaled_to_percent(self):
        rows = dict(bp.build_boc_rows({"corra": 0.0275}))
        assert rows["CORRA"] == "2.75%"

    def test_percent_value_passed_through(self):
        rows = dict(bp.build_boc_rows({"corra": 2.75}))
        assert rows["CORRA"] == "2.75%"

    def test_zero_renders_as_zero_percent(self):
        rows = dict(bp.build_boc_rows({"policy_rate": 0.0}))
        assert rows["POLICY RATE"] == "0.00%"

    def test_non_numeric_value_renders_as_dashes(self):
        rows = dict(bp.build_boc_rows({"corra": "n/a"}))
        assert rows["CORRA"] == "--"

    def test_labels_are_uppercase_strings(self):
        for label, _ in bp.build_boc_rows({}):
            assert label == label.upper()


class TestHasAnyRate:
    def test_empty_dict_is_false(self):
        assert bp.has_any_rate({}) is False

    def test_all_none_is_false(self):
        assert bp.has_any_rate({k: None for k, _ in bp._FIELDS}) is False

    def test_error_only_is_false(self):
        assert bp.has_any_rate({"error": "boom"}) is False

    def test_single_value_is_true(self):
        assert bp.has_any_rate({"corra": 0.0275}) is True

    def test_zero_counts_as_present(self):
        assert bp.has_any_rate({"corra": 0.0}) is True


class TestCountdownTone:
    def test_none_returns_muted(self):
        assert bp.countdown_tone(None) == bp.TEXT3

    @pytest.mark.parametrize("days", [0, 1, 2])
    def test_imminent_is_amber(self, days):
        assert bp.countdown_tone(days) == bp.AMBER

    @pytest.mark.parametrize("days", [3, 7])
    def test_this_week_is_blue(self, days):
        assert bp.countdown_tone(days) == bp.BLUE

    @pytest.mark.parametrize("days", [8, 30, 365])
    def test_distant_is_green(self, days):
        assert bp.countdown_tone(days) == bp.GREEN


class TestFetchBocRates:
    def test_returns_dict_on_success(self, monkeypatch):
        import analysis.live_data as ld
        monkeypatch.setattr(ld, "get_boc_rates", lambda: {"corra": 0.03})
        assert bp.fetch_boc_rates() == {"corra": 0.03}

    def test_none_result_becomes_empty_dict(self, monkeypatch):
        import analysis.live_data as ld
        monkeypatch.setattr(ld, "get_boc_rates", lambda: None)
        assert bp.fetch_boc_rates() == {}

    def test_exception_is_captured_not_raised(self, monkeypatch):
        import analysis.live_data as ld

        def _boom():
            raise RuntimeError("network down")

        monkeypatch.setattr(ld, "get_boc_rates", _boom)
        out = bp.fetch_boc_rates()
        assert "error" in out
        assert "network down" in out["error"]
