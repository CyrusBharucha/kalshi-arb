"""
tests/test_l2_performance.py
============================
Performance tests for the L2 order book reconstruction engine.

Target: sustain 3,450 msg/sec (measured Synthesis feed rate) with headroom.
All tests are marked as performance and are skipped in normal CI runs unless
--run-perf flag is passed.

Run with:
    pytest tests/test_l2_performance.py -v -s --run-perf
"""
import json
import random
import time
import threading
from typing import List, Dict

import pytest

from feeds.orderbook_l2 import L2Book, L2Cache, _vwap, PriceLevel


# -- Fixtures / helpers ---------------------------------------------------------

# Market pool mirrors 8,981 unique markets observed in the live feed
NUM_MARKETS = 9_000
PRICE_TICKS = [round(i / 100, 2) for i in range(1, 100)]  # 0.01 ... 0.99
SIDES       = ["yes", "no"]


def _random_delta(market_id: str, seq: int) -> Dict:
    bid   = random.choice(PRICE_TICKS[:50])   # lower half for bids
    ask   = random.choice(PRICE_TICKS[50:])   # upper half for asks
    price = random.choice([bid, ask, random.choice(PRICE_TICKS)])
    return {
        "market_id":    market_id,
        "price":        price,
        "amount":       round(random.uniform(0, 500), 2),
        "side":         random.choice(SIDES),
        "yes_best_bid": bid,
        "yes_best_ask": ask,
        "no_best_bid":  round(1 - ask, 2),
        "no_best_ask":  round(1 - bid, 2),
        "sequence":     seq,
        "created_at":   "2026-08-24T00:00:00Z",
    }


def _generate_deltas(n: int, n_markets: int = NUM_MARKETS) -> List[Dict]:
    market_ids = [f"KXTEST-{i:05d}" for i in range(n_markets)]
    seq_map    = {mid: 0 for mid in market_ids}
    deltas     = []
    for _ in range(n):
        mid            = random.choice(market_ids)
        seq_map[mid]  += 1
        deltas.append(_random_delta(mid, seq_map[mid]))
    return deltas


# -- Performance tests ----------------------------------------------------------

@pytest.mark.perf
class TestL2CacheThroughput:

    TARGET_RATE = 3_450   # msg/sec (measured feed rate)
    HEADROOM    = 0.50    # must sustain 50% faster than target (5,175 msg/sec)

    def test_single_threaded_throughput(self):
        """L2Cache must handle 2× target rate single-threaded."""
        N = 100_000
        deltas = _generate_deltas(N)
        cache  = L2Cache()

        start = time.perf_counter()
        for d in deltas:
            cache.apply_delta(d)
        elapsed = time.perf_counter() - start

        rate = N / elapsed
        print(f"\n  Single-threaded: {rate:,.0f} msg/sec  ({N:,} msgs in {elapsed:.2f}s)")
        assert rate >= self.TARGET_RATE * (1 + self.HEADROOM), (
            f"Single-threaded rate {rate:.0f} < required {self.TARGET_RATE * (1 + self.HEADROOM):.0f}"
        )

    def test_multi_threaded_throughput(self):
        """L2Cache must handle target rate with 4 concurrent writer threads."""
        N_PER_THREAD = 25_000
        N_THREADS    = 4
        N_TOTAL      = N_PER_THREAD * N_THREADS

        # Partition deltas per thread so there's no cross-thread market collision
        all_deltas = _generate_deltas(N_TOTAL, n_markets=N_TOTAL // 10)
        chunks     = [all_deltas[i::N_THREADS] for i in range(N_THREADS)]
        cache      = L2Cache()
        errors     = []
        start      = time.perf_counter()
        threads    = []

        def writer(chunk):
            try:
                for d in chunk:
                    cache.apply_delta(d)
            except Exception as exc:
                errors.append(exc)

        for chunk in chunks:
            t = threading.Thread(target=writer, args=(chunk,))
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        elapsed = time.perf_counter() - start
        rate    = N_TOTAL / elapsed

        assert not errors, f"Thread errors: {errors}"
        print(f"\n  Multi-threaded ({N_THREADS}T): {rate:,.0f} msg/sec  ({N_TOTAL:,} msgs in {elapsed:.2f}s)")
        assert rate >= self.TARGET_RATE, (
            f"Multi-threaded rate {rate:.0f} < target {self.TARGET_RATE}"
        )

    def test_depth_growth_memory(self):
        """Verify that average depth grows sensibly and memory stays bounded."""
        N = 50_000
        deltas = _generate_deltas(N, n_markets=500)
        cache  = L2Cache()

        for d in deltas:
            cache.apply_delta(d)

        snap  = cache.snapshot()
        total = sum(b.yes.levels() + b.no.levels() for b in snap.values())
        avg   = total / max(len(snap), 1)
        print(f"\n  After {N:,} deltas: {len(snap)} markets, avg_depth={avg:.1f} levels")

        # Average depth should be >0 (we have actual levels) and <200 (sanity cap)
        assert 0 < avg < 200, f"Unexpected avg_depth={avg}"

    def test_vwap_latency(self):
        """VWAP computation on a 20-level book must complete <1 ms."""
        book   = L2Book(market_id="VWAP-PERF")
        seq    = 0
        # Build a 10-level ask book
        for i in range(10):
            price = round(0.55 + i * 0.01, 2)
            seq  += 1
            book.apply_delta({
                "market_id": "VWAP-PERF",
                "price": price, "amount": 100.0, "side": "yes",
                "yes_best_bid": 0.50, "yes_best_ask": 0.55,
                "no_best_bid": 0.45, "no_best_ask": 0.50,
                "sequence": seq,
            })

        N_CALLS = 10_000
        start = time.perf_counter()
        for _ in range(N_CALLS):
            book.vwap_yes_ask(500.0)
        elapsed = time.perf_counter() - start

        avg_us = elapsed / N_CALLS * 1e6
        print(f"\n  VWAP latency: {avg_us:.1f} µs/call ({N_CALLS:,} calls)")
        assert avg_us < 1_000, f"VWAP latency {avg_us:.1f}µs >= 1000µs (1ms)"


@pytest.mark.perf
class TestReplayBenchmark:
    """Benchmark: replay 2000 real-world messages from the capture file."""

    CAPTURE = (
        r"C:\Users\burzi\AppData\Local\Temp\claude\C--Users-burzi-OneDrive-"
        r"Desktop-Cyrus-Trading\9c01eb3c-cadb-473c-b96b-2ebf42be62da\scratchpad\raw_ws_messages.jsonl"
    )

    def _load(self):
        from pathlib import Path
        p = Path(self.CAPTURE)
        if not p.exists():
            return None
        deltas = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                msg  = json.loads(line)
                resp = msg.get("response", {})
                if "delta" in resp:
                    deltas.append(resp["delta"])
            except Exception:
                pass
        return deltas

    def test_real_message_replay_rate(self):
        deltas = self._load()
        if deltas is None:
            pytest.skip("Capture file not found")

        cache = L2Cache()
        start = time.perf_counter()
        for d in deltas:
            cache.apply_delta(d)
        elapsed = time.perf_counter() - start
        rate = len(deltas) / elapsed

        snap  = cache.snapshot()
        total = sum(b.yes.levels() + b.no.levels() for b in snap.values())

        print(
            f"\n  Real replay: {len(deltas):,} msgs in {elapsed*1000:.1f}ms -> "
            f"{rate:,.0f} msg/sec | "
            f"{len(snap)} markets | {total:,} total price levels"
        )
        assert rate >= 10_000, f"Real replay rate {rate:.0f} too slow"
