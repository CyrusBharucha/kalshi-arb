"""
tests/test_relationship_runner_stats.py
=========================================
Unit tests for analysis/relationship_runner.py RunStats dataclass.

Tests cover:
  - Default values for all fields
  - elapsed(): returns float >= 0
  - elapsed(): increases over time
  - summary(): returns a string with key fields
  - summary(): includes rels_found and rels_written counts

No DB, no network.
"""
from __future__ import annotations

import sys
import time
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "engine.relationship_detector": MagicMock(),
    }):
        yield


def _make_stats():
    from analysis.relationship_runner import RunStats
    return RunStats()


class TestRunStatsDefaults:
    def test_events_seen_default_zero(self):
        s = _make_stats()
        assert s.events_seen == 0

    def test_events_with_multi_default_zero(self):
        s = _make_stats()
        assert s.events_with_multi == 0

    def test_rels_found_default_zero(self):
        s = _make_stats()
        assert s.rels_found == 0

    def test_rels_written_default_zero(self):
        s = _make_stats()
        assert s.rels_written == 0

    def test_start_is_float(self):
        s = _make_stats()
        assert isinstance(s.start, float)

    def test_start_is_recent(self):
        s = _make_stats()
        assert time.monotonic() - s.start < 5.0


class TestRunStatsElapsed:
    def test_elapsed_returns_float(self):
        s = _make_stats()
        assert isinstance(s.elapsed(), float)

    def test_elapsed_non_negative(self):
        s = _make_stats()
        assert s.elapsed() >= 0.0

    def test_elapsed_increases(self):
        s = _make_stats()
        t1 = s.elapsed()
        time.sleep(0.05)
        t2 = s.elapsed()
        assert t2 > t1

    def test_elapsed_zero_at_creation(self):
        s = _make_stats()
        assert s.elapsed() < 1.0  # created just now


class TestRunStatsSummary:
    def test_summary_returns_str(self):
        s = _make_stats()
        assert isinstance(s.summary(), str)

    def test_summary_contains_events_seen(self):
        s = _make_stats()
        s.events_seen = 1234
        assert "1,234" in s.summary() or "events_seen" in s.summary()

    def test_summary_contains_rels_found(self):
        s = _make_stats()
        s.rels_found = 500
        summary = s.summary()
        assert "500" in summary or "rels_found" in summary

    def test_summary_contains_rels_written(self):
        s = _make_stats()
        s.rels_written = 300
        summary = s.summary()
        assert "300" in summary or "rels_written" in summary

    def test_summary_contains_elapsed(self):
        s = _make_stats()
        summary = s.summary()
        assert "elapsed" in summary

    def test_summary_nonempty(self):
        s = _make_stats()
        assert len(s.summary()) > 10

    def test_mutated_fields_reflected_in_summary(self):
        s = _make_stats()
        s.events_seen = 9999
        s.rels_found = 777
        summary = s.summary()
        # Both large numbers should appear
        assert "9,999" in summary or "9999" in summary
        assert "777" in summary
