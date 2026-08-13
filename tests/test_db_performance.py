"""
tests/test_db_performance.py
=============================
Unit tests for database/performance.py:
  - INDEXES: list of tuples with expected shape (14 entries)
  - TABLES_TO_ANALYZE: list of table name strings
  - create_indexes(): returns dict with created/skipped/errors keys
  - run_performance_optimization(): returns dict including analyze_tables

No real DB connection — session_scope is mocked.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


# ---------------------------------------------------------------------------
# Patch session_scope to prevent DB import errors
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch("database.repository.session_scope", return_value=mock_ss):
        yield


# ---------------------------------------------------------------------------
# INDEXES constant
# ---------------------------------------------------------------------------

class TestIndexesConstant:
    """INDEXES list structure."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from database.performance import INDEXES
        self.indexes = INDEXES

    def test_is_list(self):
        assert isinstance(self.indexes, list)

    def test_has_14_entries(self):
        assert len(self.indexes) == 14

    def test_each_entry_is_5_tuple(self):
        for i, entry in enumerate(self.indexes):
            assert len(entry) == 5, f"Entry {i} has {len(entry)} elements (expected 5)"

    def test_first_element_is_string_name(self):
        for entry in self.indexes:
            name, table, cols, unique, condition = entry
            assert isinstance(name, str)
            assert name.startswith("idx_")

    def test_second_element_is_table_name(self):
        for entry in self.indexes:
            _, table, _, _, _ = entry
            assert isinstance(table, str)
            assert table.isidentifier()

    def test_third_element_is_column_spec(self):
        for entry in self.indexes:
            _, _, cols, _, _ = entry
            assert isinstance(cols, str)
            assert cols.startswith("(")
            assert cols.endswith(")")

    def test_fourth_element_is_bool(self):
        for entry in self.indexes:
            _, _, _, unique, _ = entry
            assert isinstance(unique, bool)

    def test_fifth_element_is_none_or_string(self):
        for entry in self.indexes:
            _, _, _, _, condition = entry
            assert condition is None or isinstance(condition, str)

    def test_index_names_are_unique(self):
        names = [e[0] for e in self.indexes]
        assert len(names) == len(set(names)), "Duplicate index names in INDEXES"

    def test_tables_covered_include_markets(self):
        tables = {e[1] for e in self.indexes}
        assert "markets" in tables

    def test_tables_covered_include_trades(self):
        tables = {e[1] for e in self.indexes}
        assert "trades" in tables

    def test_tables_covered_include_contract_relationships(self):
        tables = {e[1] for e in self.indexes}
        assert "contract_relationships" in tables


# ---------------------------------------------------------------------------
# TABLES_TO_ANALYZE constant
# ---------------------------------------------------------------------------

class TestTablesToAnalyze:
    """TABLES_TO_ANALYZE list."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from database.performance import TABLES_TO_ANALYZE
        self.tables = TABLES_TO_ANALYZE

    def test_is_list(self):
        assert isinstance(self.tables, list)

    def test_nonempty(self):
        assert len(self.tables) > 0

    def test_all_strings(self):
        for t in self.tables:
            assert isinstance(t, str)

    def test_markets_included(self):
        assert "markets" in self.tables

    def test_trades_included(self):
        assert "trades" in self.tables

    def test_no_duplicates(self):
        assert len(self.tables) == len(set(self.tables))


# ---------------------------------------------------------------------------
# create_indexes() return value
# ---------------------------------------------------------------------------

class TestCreateIndexes:
    """create_indexes() returns dict with created/skipped/errors.

    create_indexes() now uses get_engine() + AUTOCOMMIT connection (not session_scope)
    so tests patch get_engine instead.
    """

    def _mock_engine_ok(self):
        """Engine whose AUTOCOMMIT connection.execute() succeeds."""
        mock_conn = MagicMock()
        mock_conn_cm = MagicMock()
        mock_conn_cm.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn_cm.__exit__ = MagicMock(return_value=False)
        mock_engine = MagicMock()
        mock_engine.connect.return_value.execution_options.return_value = mock_conn_cm
        return mock_engine

    def _mock_engine_raises(self, msg="some error"):
        """Engine whose connection.execute() raises."""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError(msg)
        mock_conn_cm = MagicMock()
        mock_conn_cm.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn_cm.__exit__ = MagicMock(return_value=False)
        mock_engine = MagicMock()
        mock_engine.connect.return_value.execution_options.return_value = mock_conn_cm
        return mock_engine

    def test_returns_dict(self):
        eng = self._mock_engine_ok()
        with patch("database.performance.get_engine", return_value=eng):
            from database.performance import create_indexes
            result = create_indexes(concurrent=False)
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        eng = self._mock_engine_ok()
        with patch("database.performance.get_engine", return_value=eng):
            from database.performance import create_indexes
            result = create_indexes(concurrent=False)
        for k in ("created", "skipped", "errors"):
            assert k in result

    def test_all_created_when_no_errors(self):
        eng = self._mock_engine_ok()
        with patch("database.performance.get_engine", return_value=eng):
            from database.performance import INDEXES, create_indexes
            result = create_indexes(concurrent=False)
        assert result["created"] == len(INDEXES)
        assert result["errors"] == 0
        assert result["skipped"] == 0

    def test_skipped_when_already_exists(self):
        eng = self._mock_engine_raises("index already exists")
        with patch("database.performance.get_engine", return_value=eng):
            from database.performance import INDEXES, create_indexes
            result = create_indexes(concurrent=False)
        assert result["skipped"] == len(INDEXES)
        assert result["errors"] == 0

    def test_errors_when_other_exception(self):
        eng = self._mock_engine_raises("some unexpected failure")
        with patch("database.performance.get_engine", return_value=eng):
            from database.performance import INDEXES, create_indexes
            result = create_indexes(concurrent=False)
        assert result["errors"] == len(INDEXES)
        assert result["skipped"] == 0

    def test_counts_sum_to_total_indexes(self):
        eng = self._mock_engine_ok()
        with patch("database.performance.get_engine", return_value=eng):
            from database.performance import INDEXES, create_indexes
            result = create_indexes(concurrent=False)
        total = result["created"] + result["skipped"] + result["errors"]
        assert total == len(INDEXES)


# ---------------------------------------------------------------------------
# run_performance_optimization()
# ---------------------------------------------------------------------------

class TestRunPerformanceOptimization:
    """run_performance_optimization() wraps create_indexes + run_analyze."""

    def test_returns_dict(self):
        with patch("database.performance.create_indexes", return_value={"created": 14, "skipped": 0, "errors": 0}), \
             patch("database.performance.run_analyze"):
            from database.performance import run_performance_optimization
            result = run_performance_optimization()
        assert isinstance(result, dict)

    def test_has_analyze_tables_key(self):
        with patch("database.performance.create_indexes", return_value={"created": 14, "skipped": 0, "errors": 0}), \
             patch("database.performance.run_analyze"):
            from database.performance import run_performance_optimization
            result = run_performance_optimization()
        assert "analyze_tables" in result

    def test_analyze_tables_matches_constant(self):
        with patch("database.performance.create_indexes", return_value={"created": 14, "skipped": 0, "errors": 0}), \
             patch("database.performance.run_analyze"):
            from database.performance import run_performance_optimization, TABLES_TO_ANALYZE
            result = run_performance_optimization()
        assert result["analyze_tables"] == TABLES_TO_ANALYZE

    def test_created_key_propagated(self):
        with patch("database.performance.create_indexes", return_value={"created": 7, "skipped": 7, "errors": 0}), \
             patch("database.performance.run_analyze"):
            from database.performance import run_performance_optimization
            result = run_performance_optimization()
        assert result["created"] == 7
        assert result["skipped"] == 7
