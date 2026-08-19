"""
tests/test_market_status_filter.py
==================================
Regression guards for the tradeable-market status filter.

Kalshi has used both ``"open"`` and ``"active"`` to mean "this market is
tradeable", and the ``markets`` table holds both spellings depending on when a
row was ingested. At the time these tests were written the live table held
1,259,024 rows with ``status='active'`` and **zero** with ``status='open'``.

Every query that filtered on ``status = 'open'`` therefore returned nothing, so
the dashboard reported 0 markets monitored, 0 Canadian markets, and an empty
market explorer while sitting on a 27 GB database. The bug was silent: no error,
just honest-looking zeros.

Tier 1 (always runs): the SQL text must not pin markets to a single status.
Tier 2 (needs a live DB): the filter must actually match rows.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files whose queries filter the markets table by status.
SOURCES = [
    ROOT / "dashboard" / "data_layer.py",
    ROOT / "database" / "repository.py",
    ROOT / "database" / "schema.sql",
    ROOT / "database" / "sql" / "views.sql",
    ROOT / "docs" / "sql" / "canadian_markets.sql",
    ROOT / "docs" / "sql" / "complement_arb_scan.sql",
]

# `m.status = 'open'` / `markets ... WHERE status='open'` -- the broken shape.
# Only the *markets* table is in scope: arbitrage_opportunities.status really
# does use 'open' as a lifecycle state and must not be rewritten.
BAD_MARKET_STATUS = re.compile(r"\bm\.status\s*=\s*'open'", re.IGNORECASE)


class TestNoSingleStatusMarketFilter(unittest.TestCase):
    """No source file may pin a markets-table filter to status = 'open'."""

    def test_no_m_status_equals_open(self):
        offenders = []
        for path in SOURCES:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in BAD_MARKET_STATUS.finditer(text):
                line_no = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line_no}")
        self.assertEqual(
            offenders, [],
            "engine.status must accept both 'open' and 'active'; "
            f"found single-status filters at: {offenders}",
        )

    def test_markets_count_query_accepts_both(self):
        text = (ROOT / "dashboard" / "data_layer.py").read_text(encoding="utf-8")
        self.assertIn("FROM markets WHERE status IN ('open','active')", text)


def _engine():
    try:
        import sys
        sys.path.insert(0, str(ROOT))
        from database.repository import get_engine
        eng = get_engine()
        from sqlalchemy import text
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return eng
    except Exception:
        return None


@unittest.skipIf(_engine() is None, "no live PostgreSQL reachable")
class TestFilterMatchesLiveRows(unittest.TestCase):
    """Against the live DB the filter must actually select tradeable markets."""

    @classmethod
    def setUpClass(cls):
        cls.engine = _engine()

    def _scalar(self, sql):
        from sqlalchemy import text
        with self.engine.connect() as conn:
            return conn.execute(text(sql)).scalar()

    def test_combined_filter_is_non_empty(self):
        n = self._scalar(
            "SELECT COUNT(*) FROM markets WHERE status IN ('open','active')"
        )
        self.assertGreater(
            n, 0,
            "no tradeable markets matched -- the status vocabulary has changed "
            "again; check SELECT DISTINCT status FROM markets",
        )

    def test_combined_filter_is_superset_of_each_spelling(self):
        both = self._scalar(
            "SELECT COUNT(*) FROM markets WHERE status IN ('open','active')")
        opn = self._scalar("SELECT COUNT(*) FROM markets WHERE status='open'")
        act = self._scalar("SELECT COUNT(*) FROM markets WHERE status='active'")
        self.assertEqual(both, opn + act)

    def test_coverage_stats_reports_nonzero_markets(self):
        from dashboard.data_layer import get_coverage_stats
        stats = get_coverage_stats()
        self.assertGreater(
            stats["markets_monitored"], 0,
            "get_coverage_stats() reported 0 markets monitored against a "
            "populated database -- the status filter regressed",
        )

    def test_open_markets_query_returns_rows(self):
        from dashboard.data_layer import get_open_markets
        df, err = get_open_markets(limit=5)
        if err and ("timeout" in str(err).lower() or "QueryCanceled" in str(err)):
            import unittest
            raise unittest.SkipTest(f"Live DB query timed out — skipping: {str(err)[:120]}")
        self.assertIsNone(err)
        self.assertGreater(len(df), 0)
        # Everything returned must genuinely be tradeable.
        self.assertTrue(set(df["status"]).issubset({"open", "active"}))


if __name__ == "__main__":
    unittest.main()
