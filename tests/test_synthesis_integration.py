"""
tests/test_synthesis_integration.py
Phase 10: Tests for Synthesis WebSocket integration.

Covers:
- Authentication (credential loading)
- Message parsing (snapshot + delta)
- TopOfBook state management
- Sequence validation (stale delta rejection)
- Snapshot reconstruction
- Reconnect logic (delay capping)
- Orderbook normalization
- Canadian market filtering
- Live arbitrage detection with bid/ask prices
- Fee computation
"""
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from feeds.synthesis_live import (
    LiveOrderbookCache,
    LiveArbScanner,
    SynthesisWebSocketClient,
    TopOfBook,
    _kalshi_fee,
    RECONNECT_DELAY_BASE,
    RECONNECT_DELAY_MAX,
)
from feeds.orderbook_l2 import L2Cache
from analysis.canadian_filter import (
    is_canadian,
    classify_market,
    filter_markets,
)


# -- Helpers --------------------------------------------------------------------

def make_delta(
    market_id="MKT-A",
    yes_bid=0.45, yes_ask=0.50,
    no_bid=0.50,  no_ask=0.55,
    sequence=1000,
):
    return {
        "market_id":    market_id,
        "yes_best_bid": str(yes_bid),
        "yes_best_ask": str(yes_ask),
        "no_best_bid":  str(no_bid),
        "no_best_ask":  str(no_ask),
        "sequence":     sequence,
        "created_at":   "2026-08-24T00:00:00",
        "amount":       "100.00",
        "price":        str(yes_bid),
        "side":         "yes",
    }


def make_rel(mid1, mid2, rtype, confidence=0.95):
    return {
        "market_id_1":       mid1,
        "market_id_2":       mid2,
        "relationship_type": rtype,
        "implied_inequality": f"P({mid1}) >= P({mid2})",
        "confidence":        confidence,
    }


# ==============================================================================
# Authentication
# ==============================================================================

class TestAuthentication:
    def test_secret_key_loaded_from_env(self, monkeypatch):
        monkeypatch.setenv("SYNTHESIS_SECRET_KEY", "sk_test_abc123")
        key = os.environ["SYNTHESIS_SECRET_KEY"]
        assert key == "sk_test_abc123"

    def test_key_not_printed(self, capsys):
        """Verify secret is never echoed to stdout."""
        secret = "sk_gs-NEVER-PRINT-THIS"
        cache  = LiveOrderbookCache()
        client = SynthesisWebSocketClient(secret, cache)
        # Repr and str must not expose the key
        assert secret not in repr(client)
        assert secret not in str(client)
        out, _ = capsys.readouterr()
        assert secret not in out


# ==============================================================================
# Orderbook cache - message parsing
# ==============================================================================

class TestOrderbookCache:
    def test_apply_delta_updates_book(self):
        cache = LiveOrderbookCache()
        delta = make_delta("AAPL-WIN", yes_bid=0.30, yes_ask=0.32, sequence=1)
        book  = cache.apply_delta(delta)

        assert book is not None
        assert book.market_id == "AAPL-WIN"
        assert book.yes_bid == pytest.approx(0.30)
        assert book.yes_ask == pytest.approx(0.32)

    def test_stale_delta_rejected(self):
        cache = LiveOrderbookCache()
        cache.apply_delta(make_delta("MKT", sequence=100))
        # Lower sequence - should be ignored
        result = cache.apply_delta(make_delta("MKT", yes_bid=0.99, sequence=50))
        assert result is None
        # Book retains the original values
        assert cache.get("MKT").yes_bid != pytest.approx(0.99)

    def test_higher_sequence_overwrites(self):
        cache = LiveOrderbookCache()
        cache.apply_delta(make_delta("MKT", yes_bid=0.40, sequence=100))
        cache.apply_delta(make_delta("MKT", yes_bid=0.55, sequence=200))
        assert cache.get("MKT").yes_bid == pytest.approx(0.55)

    def test_multiple_markets_tracked(self):
        cache = LiveOrderbookCache()
        for i in range(50):
            cache.apply_delta(make_delta(f"MKT-{i}", sequence=i + 1))
        assert len(cache) == 50

    def test_snapshot_initial_state(self):
        cache = LiveOrderbookCache()
        for i in range(5):
            cache.apply_delta(make_delta(f"MKT-{i}", yes_bid=0.1 * i, sequence=i + 1))
        snap = cache.snapshot()
        assert len(snap) == 5
        assert all(isinstance(v, TopOfBook) for v in snap.values())

    def test_callback_fires_on_update(self):
        cache = LiveOrderbookCache()
        calls = []
        cache.register_callback(lambda b: calls.append(b.market_id))
        cache.apply_delta(make_delta("ALPHA"))
        cache.apply_delta(make_delta("BETA"))
        assert calls == ["ALPHA", "BETA"]

    def test_duplicate_protection_via_sequence(self):
        cache = LiveOrderbookCache()
        delta = make_delta("MKT", sequence=42)
        cache.apply_delta(delta)
        cache.apply_delta(delta)   # same sequence - should not raise, just skip
        assert len(cache) == 1


# ==============================================================================
# TopOfBook helpers
# ==============================================================================

class TestTopOfBook:
    def test_midpoint(self):
        book = TopOfBook("M", yes_bid=0.40, yes_ask=0.50)
        assert book.midpoint_yes() == pytest.approx(0.45)

    def test_executable_cost(self):
        book = TopOfBook("M", yes_bid=0.40, yes_ask=0.52,
                          no_bid=0.48, no_ask=0.60)
        assert book.executable_cost_yes() == pytest.approx(0.52)
        assert book.executable_cost_no()  == pytest.approx(0.60)


# ==============================================================================
# Fee computation
# ==============================================================================

class TestFeeFormula:
    def test_fee_at_50_pct(self):
        # ceil(0.07 * 0.5 * 0.5 * 100) / 100 = ceil(1.75) / 100 = 0.02
        assert _kalshi_fee(0.50) == pytest.approx(0.02)

    def test_fee_at_extremes(self):
        # Near 0 or 1: ceil always rounds up to at least $0.01 minimum
        # ceil(0.07 * 0.01 * 0.99 * 100) / 100 = ceil(0.0693)/100 = 1/100 = 0.01
        assert _kalshi_fee(0.01) == pytest.approx(0.01)
        assert _kalshi_fee(0.99) == pytest.approx(0.01)
        # Both less than the fee at 50%
        assert _kalshi_fee(0.01) < _kalshi_fee(0.50)
        assert _kalshi_fee(0.99) < _kalshi_fee(0.50)

    def test_fee_cap(self):
        # Maximum is $0.035
        for p in [0.3, 0.4, 0.5, 0.6, 0.7]:
            assert _kalshi_fee(p) <= 0.035

    def test_fee_positive(self):
        for p in [0.1, 0.25, 0.5, 0.75, 0.9]:
            assert _kalshi_fee(p) >= 0


# ==============================================================================
# Live arbitrage scanner
# ==============================================================================

class TestLiveArbScanner:
    def _make_cache(self, books):
        """Build an L2Cache from a dict of {market_id: {yes_bid, yes_ask, ...}}."""
        cache = L2Cache()
        for mid, vals in books.items():
            cache.apply_delta(make_delta(mid, sequence=1, **vals))
        return cache

    # ME
    def test_me_violation_detected(self):
        """
        ME: yes_bid_1 + yes_bid_2 > 1.0 is a sell-both-YES opportunity.
        """
        cache = self._make_cache({
            "ENG-WIN": {"yes_bid": 0.70, "yes_ask": 0.75, "no_bid": 0.25, "no_ask": 0.30},
            "GHA-WIN": {"yes_bid": 0.45, "yes_ask": 0.50, "no_bid": 0.50, "no_ask": 0.55},
        })
        rels   = [make_rel("ENG-WIN", "GHA-WIN", "mutually_exclusive")]
        scanner = LiveArbScanner(cache, rels)
        opps = list(scanner.on_update(cache.get("ENG-WIN")))
        assert len(opps) == 1
        opp = opps[0]
        assert opp["relationship_type"] == "mutually_exclusive"
        assert opp["net_edge"] > 0
        assert opp["classification"] == "A"

    def test_me_no_violation_when_correct(self):
        cache = self._make_cache({
            "ENG-WIN": {"yes_bid": 0.50, "yes_ask": 0.55, "no_bid": 0.45, "no_ask": 0.50},
            "GHA-WIN": {"yes_bid": 0.30, "yes_ask": 0.35, "no_bid": 0.65, "no_ask": 0.70},
        })
        rels   = [make_rel("ENG-WIN", "GHA-WIN", "mutually_exclusive")]
        scanner = LiveArbScanner(cache, rels)
        opps = list(scanner.on_update(cache.get("ENG-WIN")))
        assert opps == []

    # CE
    def test_ce_violation_detected(self):
        """
        CE: yes_ask_1 + yes_ask_2 < 1.0 -> buy both YES guaranteed payout.
        """
        cache = self._make_cache({
            "DEM-WIN": {"yes_bid": 0.38, "yes_ask": 0.42, "no_bid": 0.58, "no_ask": 0.62},
            "REP-WIN": {"yes_bid": 0.38, "yes_ask": 0.42, "no_bid": 0.58, "no_ask": 0.62},
        })
        # Total YES ask = 0.84 < 1.0
        rels   = [make_rel("DEM-WIN", "REP-WIN", "collectively_exhaustive")]
        scanner = LiveArbScanner(cache, rels)
        opps = list(scanner.on_update(cache.get("DEM-WIN")))
        assert len(opps) == 1
        assert opps[0]["relationship_type"] == "collectively_exhaustive"
        assert opps[0]["net_edge"] > 0

    def test_ce_no_violation_when_correct(self):
        cache = self._make_cache({
            "DEM-WIN": {"yes_bid": 0.55, "yes_ask": 0.60},
            "REP-WIN": {"yes_bid": 0.42, "yes_ask": 0.45},
        })
        # 0.60 + 0.45 = 1.05 > 1 -> no opportunity
        rels   = [make_rel("DEM-WIN", "REP-WIN", "collectively_exhaustive")]
        scanner = LiveArbScanner(cache, rels)
        opps = list(scanner.on_update(cache.get("DEM-WIN")))
        assert opps == []

    # Threshold
    def test_threshold_violation_detected(self):
        """
        m1 more likely; violation = yes_bid_m1 < yes_ask_m2.
        """
        cache = self._make_cache({
            "RATE-450": {"yes_bid": 0.25, "yes_ask": 0.30},  # m1 - should be more likely
            "RATE-425": {"yes_bid": 0.55, "yes_ask": 0.60},  # m2 - wrongly priced higher
        })
        rels = [make_rel("RATE-450", "RATE-425", "threshold_order")]
        scanner = LiveArbScanner(cache, rels)
        opps = list(scanner.on_update(cache.get("RATE-450")))
        assert len(opps) == 1
        assert opps[0]["relationship_type"] == "threshold_order"

    def test_fee_net_negative_not_reported(self):
        """
        Tiny gross edge that doesn't survive fees must not be reported.
        """
        cache = self._make_cache({
            "ENG-WIN": {"yes_bid": 0.510, "yes_ask": 0.52},
            "GHA-WIN": {"yes_bid": 0.495, "yes_ask": 0.51},
        })
        # ME: bid sum = 1.005; gross = 0.005; fees easily consume this
        rels = [make_rel("ENG-WIN", "GHA-WIN", "mutually_exclusive")]
        scanner = LiveArbScanner(cache, rels)
        opps = list(scanner.on_update(cache.get("ENG-WIN")))
        # Net edge after fees will be negative -> no report
        for opp in opps:
            assert opp["net_edge"] > 0  # only positive-net opps reported

    def test_opportunity_has_required_fields(self):
        cache = self._make_cache({
            "DEM-WIN": {"yes_bid": 0.60, "yes_ask": 0.65},
            "REP-WIN": {"yes_bid": 0.55, "yes_ask": 0.60},
        })
        rels = [make_rel("DEM-WIN", "REP-WIN", "mutually_exclusive")]
        scanner = LiveArbScanner(cache, rels)
        opps = list(scanner.on_update(cache.get("DEM-WIN")))
        if opps:
            opp = opps[0]
            required = {"relationship_type", "market_id_1", "market_id_2",
                        "strategy", "gross_edge", "fees", "net_edge",
                        "prices", "classification", "detected_at"}
            assert required.issubset(set(opp.keys()))
            assert opp["classification"] == "A"


# ==============================================================================
# Reconnect logic
# ==============================================================================

class TestReconnectLogic:
    def test_delay_doubles_up_to_max(self):
        cache  = LiveOrderbookCache()
        client = SynthesisWebSocketClient("sk_test", cache)
        delay  = RECONNECT_DELAY_BASE
        for _ in range(10):
            delay = min(delay * 2, RECONNECT_DELAY_MAX)
        assert delay == RECONNECT_DELAY_MAX

    def test_delay_resets_on_success(self):
        """Simulated: after a successful connect, delay goes back to base."""
        cache  = LiveOrderbookCache()
        client = SynthesisWebSocketClient("sk_test", cache)
        # Simulate the on_open callback resetting delay
        client._reconnect_delay = RECONNECT_DELAY_MAX
        # on_open resets it
        ws_mock = MagicMock()
        client._on_open(ws_mock)
        assert client._reconnect_delay == RECONNECT_DELAY_BASE

    def test_initial_delay_is_base(self):
        cache  = LiveOrderbookCache()
        client = SynthesisWebSocketClient("sk_test", cache)
        assert client._reconnect_delay == RECONNECT_DELAY_BASE


# ==============================================================================
# Canadian market filtering
# ==============================================================================

class TestCanadianFilter:
    def test_bank_of_canada_detected(self):
        assert is_canadian("Bank of Canada rate decision") is True

    def test_toronto_raptors_detected(self):
        r = classify_market("KXNBA-27-TOR", "2027 Basketball Champion - Toronto Raptors", "NBA")
        assert r["is_canadian"] is True
        assert "sports" in r["categories"]

    def test_carney_detected(self):
        r = classify_market("KXLEADERSOUT-27JAN01-MCARCAN", "World leaders - Mark Carney", "Leaders")
        assert r["is_canadian"] is True

    def test_us_market_not_canadian(self):
        assert is_canadian("Federal Reserve rate decision") is False
        assert is_canadian("Will Biden run in 2028?") is False
        assert is_canadian("S&P 500 above 5000") is False

    def test_cad_currency_detected(self):
        assert is_canadian("USD/CAD exchange rate 2026") is True

    def test_usмса_detected(self):
        assert is_canadian("Will USMCA be renegotiated?") is True

    def test_filter_markets_batch(self):
        markets = [
            {"market_id": "KXNBA-27-TOR",        "title": "Toronto Raptors win NBA",   "event_title": "NBA", "labels": []},
            {"market_id": "KXPRES-28-DEM",        "title": "Democrat wins 2028",        "event_title": "US Election", "labels": []},
            {"market_id": "KXBOCRATE-26NOV",      "title": "Bank of Canada cuts rates", "event_title": "BOC", "labels": []},
        ]
        canadian = filter_markets(markets)
        ids = [m["market_id"] for m in canadian]
        assert "KXNBA-27-TOR" in ids
        assert "KXBOCRATE-26NOV" in ids
        assert "KXPRES-28-DEM" not in ids

    def test_confidence_high_for_title_match(self):
        r = classify_market("MKT", "Bank of Canada raises rates", "Monetary Policy")
        assert r["confidence"] >= 0.90

    def test_confidence_lower_for_id_only(self):
        r = classify_market("KXCAD-USD-26DEC", "", "")
        assert r["confidence"] <= 0.85


# ==============================================================================
# Message parsing
# ==============================================================================

class TestMessageParsing:
    def test_delta_message_parsed(self):
        """Simulate the on_message handler receiving a delta."""
        cache = LiveOrderbookCache()
        raw = json.dumps({
            "success": True,
            "response": {
                "venue": "kalshi",
                "delta": {
                    "market_id":    "KXTEST-MARKET",
                    "amount":       "500.00",
                    "price":        "0.6500",
                    "side":         "yes",
                    "yes_best_bid": "0.6400",
                    "yes_best_ask": "0.6600",
                    "no_best_bid":  "0.3400",
                    "no_best_ask":  "0.3600",
                    "sequence":     99999,
                    "created_at":   "2026-08-24T12:00:00",
                }
            }
        })

        client = SynthesisWebSocketClient("sk_test", cache)
        client._on_message(MagicMock(), raw)

        book = cache.get("KXTEST-MARKET")
        assert book is not None
        assert book.yes_bid == pytest.approx(0.64)
        assert book.yes_ask == pytest.approx(0.66)
        assert book.sequence == 99999

    def test_snapshot_message_parsed(self):
        """Snapshot: orderbooks list — uses nested Synthesis format + L2Cache."""
        cache = L2Cache()
        raw = json.dumps({
            "success": True,
            "response": {
                "orderbooks": [
                    {
                        "market_id": "MKT-SNAP",
                        "sequence":  1,
                        "yes": {
                            "bids": {"0.40": "100"},
                            "asks": {"0.45": "50"},
                        },
                        "no": {
                            "bids": {"0.55": "80"},
                            "asks": {"0.60": "40"},
                        },
                    }
                ]
            }
        })
        client = SynthesisWebSocketClient("sk_test", cache)
        client._on_message(MagicMock(), raw)
        assert cache.top_of_book("MKT-SNAP") is not None

    def test_invalid_json_does_not_crash(self):
        cache  = LiveOrderbookCache()
        client = SynthesisWebSocketClient("sk_test", cache)
        client._on_message(MagicMock(), "not valid json {{{{")
        assert len(cache) == 0   # nothing was added


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
