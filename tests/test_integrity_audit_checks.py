"""
tests/test_integrity_audit_checks.py
======================================
Unit tests for individual check functions in analysis/integrity_audit.py —
focusing on SUCCESS paths (mocked DB returning valid data).

The error paths are already covered in test_integrity_audit.py.
These tests cover:
  - check_table_counts(): returns dict with integer values; -1 for errors
  - check_orphaned_relationships(): pass=True when counts are 0
  - check_market_duplicates(): pass=True when no duplicates
  - check_price_bounds(): pass=True when out_of_bounds=0; None for DB error
  - check_candlestick_ohlc_ordering(): pass=True when all 0; skip when no candles
  - check_timestamp_sanity(): pass=True when counts=0
  - check_arbitrage_consistency(): skip when no opps; pass=True when consistent
  - check_trade_volume_sanity(): pass=True when neg_qty=0
  - check_relationship_type_distribution(): dict with by_type; pass=False when empty
  - run_integrity_audit(): summary structure and counting

No PostgreSQL connection — all DB calls mocked.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_db():
    """Patch session_scope so no real DB is needed."""
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=MagicMock())
    mock_ss.__exit__ = MagicMock(return_value=False)
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(session_scope=mock_ss),
    }):
        yield


def _make_scalar_mock(value):
    """Create a session_scope mock that returns `value` from .scalar()."""
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = value
    mock_ss = MagicMock()
    mock_ss.__enter__ = MagicMock(return_value=mock_session)
    mock_ss.__exit__ = MagicMock(return_value=False)
    return mock_ss


# ---------------------------------------------------------------------------
# check_table_counts
# ---------------------------------------------------------------------------

class TestCheckTableCounts:
    def test_returns_dict(self):
        from analysis.integrity_audit import check_table_counts
        with patch("analysis.integrity_audit._scalar", return_value=42):
            result = check_table_counts()
        assert isinstance(result, dict)

    def test_has_all_table_keys(self):
        from analysis.integrity_audit import check_table_counts
        expected = {"events", "markets", "trades", "candlesticks",
                    "contract_relationships", "arbitrage_opportunities",
                    "backtest_trades", "market_snapshots"}
        with patch("analysis.integrity_audit._scalar", return_value=10):
            result = check_table_counts()
        assert expected.issubset(set(result.keys()))

    def test_values_are_ints(self):
        from analysis.integrity_audit import check_table_counts
        with patch("analysis.integrity_audit._scalar", return_value=5):
            result = check_table_counts()
        for v in result.values():
            assert isinstance(v, int)

    def test_error_returns_minus_one_for_that_table(self):
        from analysis.integrity_audit import check_table_counts
        call_count = {"n": 0}
        def _side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("DB error")
            return 10
        with patch("analysis.integrity_audit._scalar", side_effect=_side_effect):
            result = check_table_counts()
        assert -1 in result.values()


# ---------------------------------------------------------------------------
# check_orphaned_relationships
# ---------------------------------------------------------------------------

class TestCheckOrphanedRelationships:
    def test_pass_true_when_no_orphans(self):
        from analysis.integrity_audit import check_orphaned_relationships
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_orphaned_relationships()
        assert result["pass"] is True

    def test_pass_false_when_orphans_exist(self):
        from analysis.integrity_audit import check_orphaned_relationships
        with patch("analysis.integrity_audit._scalar", return_value=5):
            result = check_orphaned_relationships()
        assert result["pass"] is False

    def test_has_required_keys(self):
        from analysis.integrity_audit import check_orphaned_relationships
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_orphaned_relationships()
        assert "orphaned_market_id_1" in result
        assert "orphaned_market_id_2" in result
        assert "pass" in result


# ---------------------------------------------------------------------------
# check_market_duplicates
# ---------------------------------------------------------------------------

class TestCheckMarketDuplicates:
    def test_pass_true_when_no_duplicates(self):
        from analysis.integrity_audit import check_market_duplicates
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_market_duplicates()
        assert result["pass"] is True

    def test_pass_false_when_duplicates_exist(self):
        from analysis.integrity_audit import check_market_duplicates
        with patch("analysis.integrity_audit._scalar", return_value=3):
            result = check_market_duplicates()
        assert result["pass"] is False

    def test_has_duplicate_count_key(self):
        from analysis.integrity_audit import check_market_duplicates
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_market_duplicates()
        assert "duplicate_market_ids" in result


# ---------------------------------------------------------------------------
# check_price_bounds
# ---------------------------------------------------------------------------

class TestCheckPriceBounds:
    def test_pass_true_when_zero_out_of_bounds(self):
        from analysis.integrity_audit import check_price_bounds
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_price_bounds()
        assert result["pass"] is True

    def test_pass_false_when_out_of_bounds(self):
        from analysis.integrity_audit import check_price_bounds
        call_count = {"n": 0}
        def _side(sql, params=None):
            call_count["n"] += 1
            return 10 if call_count["n"] == 1 else 0
        with patch("analysis.integrity_audit._scalar", side_effect=_side):
            result = check_price_bounds()
        assert result["pass"] is False

    def test_has_required_keys(self):
        from analysis.integrity_audit import check_price_bounds
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_price_bounds()
        assert "prices_out_of_bounds" in result
        assert "prices_null" in result
        assert "pass" in result


# ---------------------------------------------------------------------------
# check_candlestick_ohlc_ordering
# ---------------------------------------------------------------------------

class TestCheckCandlestickOHLC:
    def test_pass_true_when_no_candles(self):
        from analysis.integrity_audit import check_candlestick_ohlc_ordering
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_candlestick_ohlc_ordering()
        assert result["pass"] is True
        assert "note" in result

    def test_pass_true_when_all_clean(self):
        from analysis.integrity_audit import check_candlestick_ohlc_ordering
        call_count = {"n": 0}
        def _side(sql, params=None):
            call_count["n"] += 1
            return 100 if call_count["n"] == 1 else 0  # n_total=100, rest=0
        with patch("analysis.integrity_audit._scalar", side_effect=_side):
            result = check_candlestick_ohlc_ordering()
        assert result["pass"] is True

    def test_pass_false_when_bad_ordering(self):
        from analysis.integrity_audit import check_candlestick_ohlc_ordering
        call_count = {"n": 0}
        def _side(sql, params=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return 100  # n_total
            elif call_count["n"] == 2:
                return 5    # bad_hl
            return 0
        with patch("analysis.integrity_audit._scalar", side_effect=_side):
            result = check_candlestick_ohlc_ordering()
        assert result["pass"] is False


# ---------------------------------------------------------------------------
# check_timestamp_sanity
# ---------------------------------------------------------------------------

class TestCheckTimestampSanity:
    def test_pass_true_when_clean(self):
        from analysis.integrity_audit import check_timestamp_sanity
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_timestamp_sanity()
        assert result["pass"] is True

    def test_pass_false_when_future_timestamps(self):
        from analysis.integrity_audit import check_timestamp_sanity
        call_count = {"n": 0}
        def _side(sql, params=None):
            call_count["n"] += 1
            return 0 if call_count["n"] == 1 else 3
        with patch("analysis.integrity_audit._scalar", side_effect=_side):
            result = check_timestamp_sanity()
        assert result["pass"] is False

    def test_has_required_keys(self):
        from analysis.integrity_audit import check_timestamp_sanity
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_timestamp_sanity()
        assert "trades_before_2020" in result
        assert "trades_in_future" in result


# ---------------------------------------------------------------------------
# check_arbitrage_consistency
# ---------------------------------------------------------------------------

class TestCheckArbitrageConsistency:
    def test_skip_when_no_opportunities(self):
        from analysis.integrity_audit import check_arbitrage_consistency
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_arbitrage_consistency()
        assert result["pass"] is True
        assert "note" in result

    def test_pass_true_when_consistent(self):
        from analysis.integrity_audit import check_arbitrage_consistency
        call_count = {"n": 0}
        def _side(sql, params=None):
            call_count["n"] += 1
            return 50 if call_count["n"] == 1 else 0  # n=50, inconsistent=0, neg=0
        with patch("analysis.integrity_audit._scalar", side_effect=_side):
            result = check_arbitrage_consistency()
        assert result["pass"] is True

    def test_pass_false_when_net_gt_gross(self):
        from analysis.integrity_audit import check_arbitrage_consistency
        call_count = {"n": 0}
        def _side(sql, params=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return 50   # n_total
            elif call_count["n"] == 2:
                return 3    # inconsistent (net > gross)
            return 0
        with patch("analysis.integrity_audit._scalar", side_effect=_side):
            result = check_arbitrage_consistency()
        assert result["pass"] is False


# ---------------------------------------------------------------------------
# check_trade_volume_sanity
# ---------------------------------------------------------------------------

class TestCheckTradeVolumeSanity:
    def test_pass_true_when_clean(self):
        from analysis.integrity_audit import check_trade_volume_sanity
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_trade_volume_sanity()
        assert result["pass"] is True

    def test_pass_false_when_negative_qty(self):
        from analysis.integrity_audit import check_trade_volume_sanity
        call_count = {"n": 0}
        def _side(sql, params=None):
            call_count["n"] += 1
            return 2 if call_count["n"] == 1 else 0
        with patch("analysis.integrity_audit._scalar", side_effect=_side):
            result = check_trade_volume_sanity()
        assert result["pass"] is False

    def test_has_required_keys(self):
        from analysis.integrity_audit import check_trade_volume_sanity
        with patch("analysis.integrity_audit._scalar", return_value=0):
            result = check_trade_volume_sanity()
        assert "negative_quantity" in result
        assert "zero_quantity" in result


# ---------------------------------------------------------------------------
# check_relationship_type_distribution
# ---------------------------------------------------------------------------

class TestCheckRelationshipTypeDistribution:
    def test_pass_false_when_no_relationships(self):
        from analysis.integrity_audit import check_relationship_type_distribution
        import pandas as pd
        with patch("analysis.integrity_audit._query", return_value=pd.DataFrame({"relationship_type": [], "n": []})):
            result = check_relationship_type_distribution()
        assert result["pass"] is False

    def test_pass_true_when_relationships_exist(self):
        from analysis.integrity_audit import check_relationship_type_distribution
        import pandas as pd
        df = pd.DataFrame({"relationship_type": ["complement", "ME"], "n": [100, 200]})
        with patch("analysis.integrity_audit._query", return_value=df):
            result = check_relationship_type_distribution()
        assert result["pass"] is True

    def test_by_type_is_dict(self):
        from analysis.integrity_audit import check_relationship_type_distribution
        import pandas as pd
        df = pd.DataFrame({"relationship_type": ["complement"], "n": [50]})
        with patch("analysis.integrity_audit._query", return_value=df):
            result = check_relationship_type_distribution()
        assert isinstance(result["by_type"], dict)

    def test_total_sums_counts(self):
        from analysis.integrity_audit import check_relationship_type_distribution
        import pandas as pd
        df = pd.DataFrame({"relationship_type": ["complement", "ME"], "n": [300, 200]})
        with patch("analysis.integrity_audit._query", return_value=df):
            result = check_relationship_type_distribution()
        assert result["total_relationships"] == 500


# ---------------------------------------------------------------------------
# Book-level and lifecycle checks
# ---------------------------------------------------------------------------
# Each of these calls _scalar() several times in a fixed order, so the tests
# drive them with side_effect sequences.

class TestCheckCrossedBooks:
    def test_unavailable_when_no_l2_rows(self):
        from analysis.integrity_audit import check_crossed_books
        with patch("analysis.integrity_audit._scalar", return_value=0):
            r = check_crossed_books()
        assert r["pass"] is None
        assert "note" in r
        assert r["l2_snapshot_rows"] == 0

    def test_passes_when_no_crossed_books(self):
        from analysis.integrity_audit import check_crossed_books
        # total, crossed, locked
        with patch("analysis.integrity_audit._scalar", side_effect=[500, 0, 3]):
            r = check_crossed_books()
        assert r["pass"] is True
        assert r["crossed_books"] == 0
        assert r["locked_books"] == 3

    def test_fails_when_book_is_crossed(self):
        from analysis.integrity_audit import check_crossed_books
        with patch("analysis.integrity_audit._scalar", side_effect=[500, 7, 0]):
            r = check_crossed_books()
        assert r["pass"] is False
        assert r["crossed_books"] == 7

    def test_db_error_is_unavailable_not_failure(self):
        from analysis.integrity_audit import check_crossed_books
        with patch("analysis.integrity_audit._scalar", side_effect=RuntimeError("boom")):
            r = check_crossed_books()
        assert r["pass"] is None
        assert "error" in r


class TestCheckStaleBooks:
    def test_unavailable_when_no_l2_rows(self):
        from analysis.integrity_audit import check_stale_books
        with patch("analysis.integrity_audit._scalar", return_value=0):
            r = check_stale_books()
        assert r["pass"] is None

    def test_passes_when_nothing_stale(self):
        from analysis.integrity_audit import check_stale_books
        # total, stale, tracked
        with patch("analysis.integrity_audit._scalar", side_effect=[100, 0, 40]):
            r = check_stale_books()
        assert r["pass"] is True
        assert r["stale_over_5min"] == 0
        assert r["open_markets_with_books"] == 40

    def test_fails_when_books_are_stale(self):
        from analysis.integrity_audit import check_stale_books
        with patch("analysis.integrity_audit._scalar", side_effect=[100, 12, 40]):
            r = check_stale_books()
        assert r["pass"] is False
        assert r["stale_over_5min"] == 12


class TestCheckDuplicateTradeIds:
    def test_passes_when_no_duplicates(self):
        from analysis.integrity_audit import check_duplicate_trade_ids
        with patch("analysis.integrity_audit._scalar", side_effect=[0, 0]):
            r = check_duplicate_trade_ids()
        assert r["pass"] is True
        assert r["duplicate_trade_ids"] == 0

    def test_fails_when_duplicates_exist(self):
        from analysis.integrity_audit import check_duplicate_trade_ids
        with patch("analysis.integrity_audit._scalar", side_effect=[4, 1]):
            r = check_duplicate_trade_ids()
        assert r["pass"] is False
        assert r["duplicate_trade_ids"] == 4
        assert r["null_trade_ids"] == 1

    def test_null_ids_do_not_by_themselves_fail(self):
        from analysis.integrity_audit import check_duplicate_trade_ids
        with patch("analysis.integrity_audit._scalar", side_effect=[0, 9]):
            r = check_duplicate_trade_ids()
        assert r["pass"] is True
        assert r["null_trade_ids"] == 9


class TestCheckMissingCloseTime:
    def test_passes_when_all_closed_markets_have_close_time(self):
        from analysis.integrity_audit import check_missing_close_time
        with patch("analysis.integrity_audit._scalar", side_effect=[0, 1000]):
            r = check_missing_close_time()
        assert r["pass"] is True
        assert r["closed_markets"] == 1000

    def test_fails_when_close_time_missing(self):
        from analysis.integrity_audit import check_missing_close_time
        with patch("analysis.integrity_audit._scalar", side_effect=[5, 1000]):
            r = check_missing_close_time()
        assert r["pass"] is False
        assert r["missing_close_time"] == 5


class TestCheckL2SequenceGaps:
    def test_unavailable_when_no_sequenced_rows(self):
        from analysis.integrity_audit import check_l2_sequence_gaps
        with patch("analysis.integrity_audit._scalar", return_value=0):
            r = check_l2_sequence_gaps()
        assert r["pass"] is None
        assert "note" in r

    def test_passes_when_sequence_is_contiguous(self):
        from analysis.integrity_audit import check_l2_sequence_gaps
        with patch("analysis.integrity_audit._scalar", side_effect=[900, 0]):
            r = check_l2_sequence_gaps()
        assert r["pass"] is True
        assert r["sequence_gaps"] == 0

    def test_fails_when_deltas_were_dropped(self):
        from analysis.integrity_audit import check_l2_sequence_gaps
        with patch("analysis.integrity_audit._scalar", side_effect=[900, 23]):
            r = check_l2_sequence_gaps()
        assert r["pass"] is False
        assert r["sequence_gaps"] == 23


class TestCheckMarketsWithoutTrades:
    def test_is_informational_and_always_passes(self):
        from analysis.integrity_audit import check_markets_without_trades
        with patch("analysis.integrity_audit._scalar", side_effect=[1000, 10]):
            r = check_markets_without_trades()
        assert r["pass"] is True
        assert r["without_trades"] == 990
        assert r["pct_ever_traded"] == 1.0

    def test_handles_zero_open_markets_without_dividing_by_zero(self):
        from analysis.integrity_audit import check_markets_without_trades
        with patch("analysis.integrity_audit._scalar", side_effect=[0, 0]):
            r = check_markets_without_trades()
        assert r["pct_ever_traded"] == 0.0
        assert r["without_trades"] == 0

    def test_db_error_is_unavailable(self):
        from analysis.integrity_audit import check_markets_without_trades
        with patch("analysis.integrity_audit._scalar", side_effect=RuntimeError("x")):
            r = check_markets_without_trades()
        assert r["pass"] is None
