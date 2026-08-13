"""
tests/test_sql_files_valid.py
=============================
Static + planner validation of the hand-written SQL layer.

Two tiers:

1. Structural checks that run everywhere (no database needed) -- every query
   file must exist, be non-trivial, and actually demonstrate the CTE/window
   techniques the SQL layer is meant to showcase.

2. Planner checks that run only when a live PostgreSQL is reachable. Every
   SELECT is passed to EXPLAIN and every CREATE VIEW/INDEX is reduced to an
   equivalent SELECT and planned. Nothing is created and nothing is executed,
   so this is safe to run against the production database.

Tier 2 exists because a query file that references a dropped column is worse
than no query file at all: it looks authoritative and fails at the worst time.
Six indexes and three views were broken this way before these tests existed.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent.parent / "database"
SQL_DIR = DB_DIR / "sql"

# Query libraries -- collections of standalone SELECTs.
QUERY_FILES = [
    DB_DIR / "analytics.sql",
    DB_DIR / "arbitrage_queries.sql",
    DB_DIR / "historical_queries.sql",
    DB_DIR / "market_queries.sql",
    DB_DIR / "performance_queries.sql",
    SQL_DIR / "arbitrage_queries.sql",
    SQL_DIR / "historical_queries.sql",
    SQL_DIR / "l2_orderbook_queries.sql",
    SQL_DIR / "market_queries.sql",
    SQL_DIR / "performance_queries.sql",
]

# Documentation query files under docs/sql/. These are short, standalone
# illustrations rather than full libraries, so the "at least five queries"
# structural rule does not apply -- but they must still plan against the live
# schema, since a docs query that references a dropped column teaches the
# wrong thing.
DOCS_SQL_DIR = Path(__file__).resolve().parent.parent / "docs" / "sql"
DOCS_QUERY_FILES = sorted(DOCS_SQL_DIR.glob("*.sql"))

# DDL files -- views and indexes.
DDL_FILES = [
    DB_DIR / "views.sql",
    DB_DIR / "indexes.sql",
    DB_DIR / "schema.sql",
    DB_DIR / "l2_schema.sql",
    SQL_DIR / "views.sql",
    SQL_DIR / "indexes.sql",
]

# Materialized views declared WITH NO DATA are never created by these tests, so
# indexes naming them cannot be planned. They are not errors.
UNPLANNABLE_INDEX_TARGETS = {
    "mv_daily_arb_summary_pk",
    "mv_market_quality_scores_pk",
}


# --------------------------------------------------------------------------
# SQL splitting helpers
# --------------------------------------------------------------------------

def split_statements(sql: str) -> list[str]:
    """Split on semicolons that are at paren-depth 0 and outside string literals."""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    in_string = False
    while i < len(sql):
        ch = sql[i]
        if in_string:
            buf.append(ch)
            if ch == "'":
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            buf.append(ch)
            i += 1
            continue
        if sql[i:i + 2] == "--":                     # line comment
            end = sql.find("\n", i)
            end = len(sql) if end < 0 else end
            buf.append(sql[i:end])
            i = end
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth == 0:
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf))
    return out


def strip_comments(stmt: str) -> str:
    return re.sub(r"--[^\n]*", "", stmt).strip()


def bind_placeholders(sql: str) -> str:
    """Replace :named placeholders with a literal so the planner can parse.

    The negative lookbehind keeps ``::type`` casts intact.
    """
    return re.sub(r"(?<!:):([a-z_][a-z0-9_]*)", r"'\1'", sql)


def _match_paren(s: str, start: int) -> int:
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def ddl_to_select(body: str) -> str | None:
    """Reduce a CREATE VIEW / CREATE INDEX statement to an equivalent SELECT."""
    if re.match(r"^\s*CREATE\s+(OR\s+REPLACE\s+)?(MATERIALIZED\s+)?VIEW\b", body, re.I):
        m = re.search(r"\bAS\b", body, re.I)
        if not m:
            return None
        query = body[m.end():]
        # trailing WITH [NO] DATA is storage config, not part of the query
        return re.sub(r"\bWITH\s+(NO\s+)?DATA\s*$", "", query, flags=re.I).strip()

    # table name stops at whitespace or '(' -- "ON markets(col)" has no space
    m = re.match(
        r"^\s*CREATE\s+(UNIQUE\s+)?INDEX\s+(CONCURRENTLY\s+)?"
        r"(IF\s+NOT\s+EXISTS\s+)?[^\s(]+\s+ON\s+([^\s(]+)",
        body, re.I,
    )
    if not m:
        return None
    table = m.group(4)
    rest = re.sub(r"^\s*USING\s+\w+", "", body[m.end():], flags=re.I)
    open_paren = rest.find("(")
    if open_paren < 0:
        return None
    close = _match_paren(rest, open_paren)
    if close < 0:
        return None
    cols = rest[open_paren + 1:close]
    # index-only modifiers are not valid inside a select list
    cols = re.sub(r"\b(ASC|DESC|NULLS\s+(FIRST|LAST)|\w+_ops)\b", "", cols, flags=re.I)
    tail = rest[close + 1:]
    where = re.search(r"\bWHERE\b", tail, re.I)
    predicate = f" WHERE {tail[where.end():]}" if where else ""
    return f"SELECT {cols} FROM {table}{predicate}"


def ddl_label(body: str) -> str:
    m = re.search(
        r"(VIEW|INDEX)\s+(?:CONCURRENTLY\s+)?(?:IF NOT EXISTS\s+)?(\S+)", body, re.I
    )
    return m.group(2) if m else "?"


# --------------------------------------------------------------------------
# Tier 1 -- structural checks (no database required)
# --------------------------------------------------------------------------

class TestSqlFilesExist(unittest.TestCase):
    """Every SQL file the project documents must be present and non-trivial."""

    def test_all_query_files_exist(self):
        for path in QUERY_FILES:
            with self.subTest(sql=path.name):
                self.assertTrue(path.is_file(), f"missing SQL file: {path}")

    def test_all_ddl_files_exist(self):
        for path in DDL_FILES:
            with self.subTest(sql=path.name):
                self.assertTrue(path.is_file(), f"missing SQL file: {path}")

    def test_query_files_have_at_least_five_queries(self):
        """The SQL layer is a showcase; a two-query file is not one."""
        for path in QUERY_FILES:
            with self.subTest(sql=path.name):
                selects = [
                    s for s in split_statements(path.read_text(encoding="utf-8"))
                    if re.match(r"^\s*(WITH|SELECT)\b", strip_comments(s), re.I)
                ]
                self.assertGreaterEqual(
                    len(selects), 5,
                    f"{path.name} has only {len(selects)} queries",
                )

    def test_query_files_are_commented(self):
        """Each query needs a comment saying what it answers."""
        for path in QUERY_FILES:
            with self.subTest(sql=path.name):
                text = path.read_text(encoding="utf-8")
                comment_lines = len(re.findall(r"^\s*--", text, re.M))
                self.assertGreaterEqual(
                    comment_lines, 10,
                    f"{path.name} has only {comment_lines} comment lines",
                )


class TestSqlDemonstratesTechniques(unittest.TestCase):
    """The analytics files must actually use the techniques they advertise."""

    def test_analytics_uses_ctes_and_windows(self):
        text = DB_DIR.joinpath("analytics.sql").read_text(encoding="utf-8").upper()
        for token in ("WITH ", "OVER (", "PARTITION BY", "LAG(", "PERCENT_RANK("):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_analytics_covers_required_topics(self):
        text = DB_DIR.joinpath("analytics.sql").read_text(encoding="utf-8").lower()
        for topic in (
            "rolling 7-day",        # rolling volume
            "heatmap",              # hourly activity
            "spread trend",         # LAG spread
            "vwap",                 # per-market VWAP
            "effective spread",     # microstructure
            "persistence",          # opportunity lifetime
            "edge frequency",       # top markets
        ):
            with self.subTest(topic=topic):
                self.assertIn(topic, text, f"analytics.sql is missing: {topic}")

    def test_arbitrage_queries_cover_required_topics(self):
        text = DB_DIR.joinpath("arbitrage_queries.sql").read_text(encoding="utf-8").lower()
        for topic in (
            "rank()",
            "lead(",
            "hit rate",             # rolling 30d win rate
            "fee sensitivity",
            "percent_rank(",
            "liquidity at detection",
            "disappeared",
        ):
            with self.subTest(topic=topic):
                self.assertIn(topic, text, f"arbitrage_queries.sql is missing: {topic}")


class TestNoStaleColumnReferences(unittest.TestCase):
    """Guard the specific columns that were wrong before, without needing a DB.

    ``markets`` carries contract definitions only. Live quotes, volume and open
    interest are point-in-time observations that live in ``market_snapshots``;
    Canadian relevance is scored on ``events``. Re-introducing them on
    ``markets`` is the exact regression this catches.
    """

    PHANTOM_MARKET_COLUMNS = [
        "m.is_canadian",
        "m.canadian_confidence",
        "m.yes_bid",
        "m.yes_ask",
        "m.volume",
        "m.open_interest",
        "markets (is_canadian)",
        "markets (volume",
    ]

    def test_no_phantom_markets_columns(self):
        for path in QUERY_FILES + DDL_FILES:
            text = path.read_text(encoding="utf-8")
            uncommented = "\n".join(
                re.sub(r"--.*$", "", line) for line in text.splitlines()
            ).lower()
            for phantom in self.PHANTOM_MARKET_COLUMNS:
                with self.subTest(sql=path.name, column=phantom):
                    self.assertNotIn(
                        phantom.lower(), uncommented,
                        f"{path.name} references '{phantom}', which does not "
                        f"exist on the markets table",
                    )

    def test_no_renamed_columns(self):
        """Columns that were renamed in the schema must not reappear."""
        renamed = {
            "period_interval_min": "period_interval",   # candlesticks
            "seq_num": "sequence",                      # l2_snapshots
            "traded_at": "entry_ts",                    # backtest_trades
        }
        for path in QUERY_FILES + DDL_FILES:
            text = path.read_text(encoding="utf-8")
            uncommented = "\n".join(
                re.sub(r"--.*$", "", line) for line in text.splitlines()
            )
            for old, new in renamed.items():
                with self.subTest(sql=path.name, column=old):
                    self.assertNotRegex(
                        uncommented, rf"\b{old}\b",
                        f"{path.name} uses removed column '{old}' (now '{new}')",
                    )


# --------------------------------------------------------------------------
# Tier 2 -- planner validation (requires a live database)
# --------------------------------------------------------------------------

def _connection():
    """Return a live psycopg2 connection, or None if one is not available."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        except ImportError:
            pass
        dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    try:
        import psycopg2
        return psycopg2.connect(dsn, connect_timeout=5)
    except Exception:
        return None


_CONN = _connection()
_NO_DB = _CONN is None


@unittest.skipIf(_NO_DB, "no live PostgreSQL available (set DATABASE_URL)")
class TestSqlPlansAgainstLiveSchema(unittest.TestCase):
    """EXPLAIN every statement. Plans only -- nothing is executed or created."""

    def _explain(self, sql: str) -> str | None:
        """Return None if the statement plans, else the planner error."""
        cur = _CONN.cursor()
        try:
            cur.execute("EXPLAIN " + sql)
            cur.fetchall()
            return None
        except Exception as exc:
            return str(exc).strip().splitlines()[0]
        finally:
            _CONN.rollback()
            cur.close()

    def test_every_query_plans(self):
        checked = 0
        for path in QUERY_FILES:
            for stmt in split_statements(path.read_text(encoding="utf-8")):
                body = strip_comments(stmt)
                if not re.match(r"^\s*(WITH|SELECT)\b", body, re.I):
                    continue
                checked += 1
                with self.subTest(sql=path.name, query=" ".join(body.split())[:70]):
                    err = self._explain(bind_placeholders(body))
                    self.assertIsNone(err, err)
        self.assertGreater(checked, 40, "expected far more queries than this")

    def test_every_docs_query_plans(self):
        """docs/sql/*.sql must plan too -- a broken example is worse than none."""
        checked = 0
        self.assertTrue(DOCS_QUERY_FILES, "no docs/sql/*.sql files were found")
        for path in DOCS_QUERY_FILES:
            for stmt in split_statements(path.read_text(encoding="utf-8")):
                body = strip_comments(stmt)
                if not re.match(r"^\s*(WITH|SELECT)\b", body, re.I):
                    continue
                checked += 1
                with self.subTest(sql=path.name, query=" ".join(body.split())[:70]):
                    err = self._explain(bind_placeholders(body))
                    self.assertIsNone(err, err)
        self.assertGreater(checked, 10, "expected more docs queries than this")

    def test_every_view_and_index_plans(self):
        checked = 0
        for path in DDL_FILES:
            for stmt in split_statements(path.read_text(encoding="utf-8")):
                body = strip_comments(stmt)
                if not re.match(r"^\s*CREATE\b", body, re.I):
                    continue
                if not re.search(r"\b(VIEW|INDEX)\b", body[:120], re.I):
                    continue
                label = ddl_label(body)
                if label in UNPLANNABLE_INDEX_TARGETS:
                    continue
                select = ddl_to_select(body)
                self.assertIsNotNone(
                    select, f"{path.name}: could not reduce {label} to a SELECT"
                )
                checked += 1
                with self.subTest(sql=path.name, ddl=label):
                    err = self._explain(bind_placeholders(select))
                    self.assertIsNone(err, err)
        self.assertGreater(checked, 60, "expected far more DDL than this")


if __name__ == "__main__":
    unittest.main()
