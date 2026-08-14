"""
tests/test_live_arb_scanner_extended.py
=========================================
Extended unit tests for data/synthesis_live.py LiveArbScanner._evaluate()
and on_update() generator.

Covers paths not tested in test_live_arb_scanner.py:
  - _evaluate(): book1 missing from cache → None
  - _evaluate(): book2 missing from cache → None
  - _evaluate(): tob1 missing → None
  - _evaluate(): tob2 missing → None
  - _evaluate(): "complement" type → None (no handler)
  - _evaluate(): me violation augmented with depth/vwap/max_qty keys
  - _evaluate(): no violation → None returned (no augmentation)
  - on_update(): empty rels → yields nothing
  - on_update(): rel triggers violation → yields opp dict

No DB connection; all cache interactions mocked.
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


def _make_scanner_with_cache(cache):
    """Build LiveArbScanner with a real or mock L2Cache, no rels."""
    from feeds.synthesis_live import LiveArbScanner
    scanner = LiveArbScanner.__new__(LiveArbScanner)
    scanner.cache = cache
    scanner._by_market = {}
    return scanner


def _mock_book(depth_val=5, vwap_result=(0.42, 42.0), total_ask=50.0):
    """Build a mock L2Book object."""
    book = MagicMock()
    book.depth.return_value = depth_val
    book.vwap_yes_ask.return_value = vwap_result
    book.yes.total_ask_qty.return_value = total_ask
    return book


def _me_rel(id1="A", id2="B"):
    return {"market_id_1": id1, "market_id_2": id2,
            "relationship_type": "mutually_exclusive"}


# ---------------------------------------------------------------------------
# _evaluate: cache / tob None guards
# ---------------------------------------------------------------------------

class TestEvaluateNoneGuards:
    """_evaluate() returns None when cache or tob data is unavailable."""

    @pytest.fixture(autouse=True)
    def _scanner(self):
        self.cache = MagicMock()
        self.scanner = _make_scanner_with_cache(self.cache)

    def test_book1_missing_returns_none(self):
        """cache.get(id1) returns None → early exit."""
        self.cache.get.side_effect = lambda mid: None if mid == "A" else _mock_book()
        result = self.scanner._evaluate(_me_rel())
        assert result is None

    def test_book2_missing_returns_none(self):
        """cache.get(id2) returns None → early exit."""
        self.cache.get.side_effect = lambda mid: None if mid == "B" else _mock_book()
        result = self.scanner._evaluate(_me_rel())
        assert result is None

    def test_tob1_missing_returns_none(self):
        """cache.top_of_book(id1) returns None → early exit."""
        self.cache.get.return_value = _mock_book()
        self.cache.top_of_book.side_effect = lambda mid: None if mid == "A" else {"yes_bid": 0.40, "yes_ask": 0.42}
        result = self.scanner._evaluate(_me_rel())
        assert result is None

    def test_tob2_missing_returns_none(self):
        """cache.top_of_book(id2) returns None → early exit."""
        self.cache.get.return_value = _mock_book()
        self.cache.top_of_book.side_effect = lambda mid: None if mid == "B" else {"yes_bid": 0.40, "yes_ask": 0.42}
        result = self.scanner._evaluate(_me_rel())
        assert result is None

    def test_both_books_and_tobs_none_returns_none(self):
        """All missing → None."""
        self.cache.get.return_value = None
        self.cache.top_of_book.return_value = None
        result = self.scanner._evaluate(_me_rel())
        assert result is None


# ---------------------------------------------------------------------------
# _evaluate: unknown / complement relationship type
# ---------------------------------------------------------------------------

class TestEvaluateUnknownType:
    """_evaluate() with relationship type not in if/elif chain → None."""

    @pytest.fixture(autouse=True)
    def _scanner(self):
        self.cache = MagicMock()
        self.cache.get.return_value = _mock_book()
        self.cache.top_of_book.return_value = {"yes_bid": 0.40, "yes_ask": 0.42}
        self.scanner = _make_scanner_with_cache(self.cache)

    def test_complement_type_returns_none(self):
        rel = {"market_id_1": "A", "market_id_2": "B",
               "relationship_type": "complement"}
        result = self.scanner._evaluate(rel)
        assert result is None

    def test_unknown_type_returns_none(self):
        rel = {"market_id_1": "A", "market_id_2": "B",
               "relationship_type": "does_not_exist"}
        result = self.scanner._evaluate(rel)
        assert result is None


# ---------------------------------------------------------------------------
# _evaluate: opportunity augmented with depth/vwap/max_qty
# ---------------------------------------------------------------------------

class TestEvaluateAugmentation:
    """When _evaluate finds an opportunity, it augments with depth/vwap/max_qty keys."""

    @pytest.fixture(autouse=True)
    def _scanner(self):
        self.cache = MagicMock()
        book = _mock_book(depth_val=10, vwap_result=(0.66, 66.0), total_ask=80.0)
        self.cache.get.return_value = book
        # ME violation: yes_bid_sum = 0.65 + 0.45 = 1.10 > 1.0
        self.cache.top_of_book.side_effect = lambda mid: (
            {"yes_bid": 0.65, "yes_ask": 0.67} if mid == "A"
            else {"yes_bid": 0.45, "yes_ask": 0.47}
        )
        self.scanner = _make_scanner_with_cache(self.cache)

    def test_augmented_opp_has_depth_key(self):
        result = self.scanner._evaluate(_me_rel())
        assert result is not None
        assert "depth" in result

    def test_augmented_opp_depth_has_both_markets(self):
        result = self.scanner._evaluate(_me_rel())
        assert "A" in result["depth"]
        assert "B" in result["depth"]

    def test_augmented_opp_has_vwap_yes_ask(self):
        result = self.scanner._evaluate(_me_rel())
        assert "vwap_yes_ask" in result

    def test_augmented_opp_vwap_not_empty(self):
        result = self.scanner._evaluate(_me_rel())
        vwap = result["vwap_yes_ask"]
        assert vwap.get("A") is not None or vwap.get("B") is not None

    def test_augmented_opp_has_max_qty(self):
        result = self.scanner._evaluate(_me_rel())
        assert "max_qty" in result

    def test_augmented_opp_max_qty_both_markets(self):
        result = self.scanner._evaluate(_me_rel())
        assert "A" in result["max_qty"]
        assert "B" in result["max_qty"]

    def test_no_violation_no_augmentation(self):
        """When _check_me returns None, opp stays None → no augmentation."""
        self.cache.top_of_book.side_effect = lambda mid: (
            {"yes_bid": 0.40, "yes_ask": 0.42}   # sum = 0.80, no violation
        )
        result = self.scanner._evaluate(_me_rel())
        assert result is None


# ---------------------------------------------------------------------------
# on_update: generator behaviour
# ---------------------------------------------------------------------------

class TestOnUpdate:
    """on_update() is a generator yielding opportunities for each related market."""

    def _scanner_with_rels(self, rels):
        from feeds.synthesis_live import LiveArbScanner
        cache = MagicMock()
        scanner = LiveArbScanner.__new__(LiveArbScanner)
        scanner.cache = cache
        scanner._by_market = {}
        for rel in rels:
            for mid in (rel["market_id_1"], rel["market_id_2"]):
                scanner._by_market.setdefault(mid, []).append(rel)
        return scanner, cache

    def test_on_update_no_rels_yields_nothing(self):
        scanner, cache = self._scanner_with_rels([])
        book = MagicMock()
        book.market_id = "X"
        results = list(scanner.on_update(book))
        assert results == []

    def test_on_update_market_not_in_index_yields_nothing(self):
        rels = [_me_rel("A", "B")]
        scanner, cache = self._scanner_with_rels(rels)
        book = MagicMock()
        book.market_id = "Z"   # not in rels
        results = list(scanner.on_update(book))
        assert results == []

    def test_on_update_no_violation_yields_nothing(self):
        rels = [_me_rel("A", "B")]
        scanner, cache = self._scanner_with_rels(rels)
        # Cache returns None for both books → _evaluate returns None
        cache.get.return_value = None
        book = MagicMock()
        book.market_id = "A"
        results = list(scanner.on_update(book))
        assert results == []

    def test_on_update_violation_yields_opp(self):
        rels = [_me_rel("A", "B")]
        scanner, cache = self._scanner_with_rels(rels)
        mock_book = _mock_book(depth_val=5, vwap_result=(0.66, 66.0), total_ask=50.0)
        cache.get.return_value = mock_book
        # ME violation
        cache.top_of_book.side_effect = lambda mid: (
            {"yes_bid": 0.65, "yes_ask": 0.67} if mid == "A"
            else {"yes_bid": 0.45, "yes_ask": 0.47}
        )
        book = MagicMock()
        book.market_id = "A"
        results = list(scanner.on_update(book))
        assert len(results) == 1
        assert results[0]["relationship_type"] == "mutually_exclusive"

    def test_on_update_multiple_rels_can_yield_multiple(self):
        """Market A is in two ME relationships; both trigger violations."""
        rels = [_me_rel("A", "B"), _me_rel("A", "C")]
        scanner, cache = self._scanner_with_rels(rels)
        mock_book = _mock_book(depth_val=5, vwap_result=(0.66, 66.0), total_ask=50.0)
        cache.get.return_value = mock_book
        # Both pairs violate
        cache.top_of_book.side_effect = lambda mid: {"yes_bid": 0.65, "yes_ask": 0.67}
        book = MagicMock()
        book.market_id = "A"
        results = list(scanner.on_update(book))
        # Two rels → at most 2 opps
        assert len(results) == 2
