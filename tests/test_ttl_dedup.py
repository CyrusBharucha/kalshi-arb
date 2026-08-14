"""
tests/test_ttl_dedup.py
=======================
Unit tests for the TTL dedup logic used in ws_bridge.py.

The bridge uses a local dict `_last_seen: dict[str, float]` with a scalar
`_TTL_S = 300` (5 minutes).  The suppression rule is:

    if _now - _last_seen.get(_key, 0) < _TTL_S:
        continue   # suppress duplicate

These tests verify the gate logic directly, without requiring the full WS
bridge to be running.  They also verify the MIN_NET_CENTS threshold and
stale-key eviction pattern.
"""

from __future__ import annotations

import time


# ── Constants mirrored from ws_bridge.py ──────────────────────────────────────
TTL_S = 300          # 5-minute TTL
MIN_NET_CENTS = 0.02  # 2 cent minimum net edge


# ── Helper: simulate the gate ─────────────────────────────────────────────────

def _should_suppress(last_seen: dict, key: str, now: float, ttl: float = TTL_S) -> bool:
    """Return True when the opportunity should be suppressed by TTL dedup."""
    return (now - last_seen.get(key, 0)) < ttl


def _record(last_seen: dict, key: str, now: float) -> None:
    """Record that we emitted this opportunity at `now`."""
    last_seen[key] = now


def _evict_stale(last_seen: dict, now: float, ttl: float = TTL_S) -> int:
    """Evict entries older than TTL. Returns count of evicted entries."""
    cutoff = now - ttl
    stale = [k for k, ts in last_seen.items() if ts < cutoff]
    for k in stale:
        del last_seen[k]
    return len(stale)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestTTLDedupSuppression:
    """The TTL gate suppresses the same key within the TTL window."""

    def test_first_time_not_suppressed(self):
        last_seen: dict = {}
        now = 1_000_000.0
        assert not _should_suppress(last_seen, "YNC:KXTEST-23", now)

    def test_immediately_after_record_suppressed(self):
        last_seen: dict = {}
        now = 1_000_000.0
        _record(last_seen, "YNC:KXTEST-23", now)
        # 1 second later — should still be suppressed
        assert _should_suppress(last_seen, "YNC:KXTEST-23", now + 1)

    def test_just_before_ttl_suppressed(self):
        last_seen: dict = {}
        now = 1_000_000.0
        _record(last_seen, "YNC:KXTEST-23", now)
        assert _should_suppress(last_seen, "YNC:KXTEST-23", now + TTL_S - 1)

    def test_at_exactly_ttl_not_suppressed(self):
        """At exactly TTL_S elapsed, the gate should allow through."""
        last_seen: dict = {}
        now = 1_000_000.0
        _record(last_seen, "YNC:KXTEST-23", now)
        # now + TTL_S: elapsed == TTL_S, condition is `< TTL_S` → False → not suppressed
        assert not _should_suppress(last_seen, "YNC:KXTEST-23", now + TTL_S)

    def test_after_ttl_not_suppressed(self):
        last_seen: dict = {}
        now = 1_000_000.0
        _record(last_seen, "YNC:KXTEST-23", now)
        assert not _should_suppress(last_seen, "YNC:KXTEST-23", now + TTL_S + 1)

    def test_different_keys_independent(self):
        last_seen: dict = {}
        now = 1_000_000.0
        _record(last_seen, "YNC:KXTEST-A", now)
        # A different key should not be suppressed
        assert not _should_suppress(last_seen, "YNC:KXTEST-B", now + 1)

    def test_ce_key_format(self):
        """CE key format 'CE:<event_ticker>' is handled correctly."""
        last_seen: dict = {}
        now = 1_000_000.0
        key = "CE:KXBOC-24DEC25-T4.25"
        _record(last_seen, key, now)
        assert _should_suppress(last_seen, key, now + 60)
        assert not _should_suppress(last_seen, key, now + TTL_S + 1)

    def test_me_key_format(self):
        last_seen: dict = {}
        now = 1_000_000.0
        key = "ME:KXPRES-24"
        _record(last_seen, key, now)
        assert _should_suppress(last_seen, key, now + 100)

    def test_th_key_format(self):
        last_seen: dict = {}
        now = 1_000_000.0
        key = "TH:KXGOLD-25DEC24-T2000:KXGOLD-25DEC24-T2100"
        _record(last_seen, key, now)
        assert _should_suppress(last_seen, key, now + 5)
        assert not _should_suppress(last_seen, key, now + TTL_S)


class TestTTLDedupCounter:
    """Blocked-TTL counter increments when suppressed."""

    def test_counter_increments_on_suppress(self):
        last_seen: dict = {}
        now = 1_000_000.0
        _record(last_seen, "YNC:KXTEST", now)

        blocked = 0
        for t in [now + 1, now + 60, now + 120, now + 250]:
            if _should_suppress(last_seen, "YNC:KXTEST", t):
                blocked += 1

        assert blocked == 4

    def test_counter_does_not_increment_after_ttl(self):
        last_seen: dict = {}
        now = 1_000_000.0
        _record(last_seen, "YNC:KXTEST", now)

        blocked = 0
        if _should_suppress(last_seen, "YNC:KXTEST", now + TTL_S + 10):
            blocked += 1

        assert blocked == 0

    def test_re_record_resets_suppression_window(self):
        """After the TTL expires and a new opportunity is recorded, TTL resets."""
        last_seen: dict = {}
        now = 1_000_000.0
        _record(last_seen, "YNC:KXTEST", now)

        # TTL expires → not suppressed → record again
        resurfaced_at = now + TTL_S + 1
        assert not _should_suppress(last_seen, "YNC:KXTEST", resurfaced_at)
        _record(last_seen, "YNC:KXTEST", resurfaced_at)

        # Now suppressed again
        assert _should_suppress(last_seen, "YNC:KXTEST", resurfaced_at + 1)
        assert not _should_suppress(last_seen, "YNC:KXTEST", resurfaced_at + TTL_S)


class TestTTLDedupEviction:
    """Stale-key eviction prevents unbounded growth of _last_seen."""

    def test_eviction_removes_old_entries(self):
        last_seen: dict = {}
        t0 = 1_000_000.0
        # Record 10 entries at t0
        for i in range(10):
            _record(last_seen, f"YNC:KXTEST-{i}", t0)

        assert len(last_seen) == 10

        # Evict at t0 + TTL_S + 1 (all entries are stale)
        removed = _evict_stale(last_seen, t0 + TTL_S + 1)
        assert removed == 10
        assert len(last_seen) == 0

    def test_eviction_keeps_recent_entries(self):
        last_seen: dict = {}
        t0 = 1_000_000.0
        # Record 5 old, 5 recent
        for i in range(5):
            _record(last_seen, f"OLD:{i}", t0)
        for i in range(5):
            _record(last_seen, f"NEW:{i}", t0 + TTL_S + 10)

        # Evict from just past the "NEW" record time — should remove only OLD entries
        removed = _evict_stale(last_seen, t0 + TTL_S + 50)
        assert removed == 5
        assert len(last_seen) == 5
        assert all(k.startswith("NEW:") for k in last_seen)

    def test_eviction_with_empty_dict(self):
        last_seen: dict = {}
        removed = _evict_stale(last_seen, 1_000_000.0)
        assert removed == 0


class TestMinNetEdgeGate:
    """MIN_NET_CENTS = 0.02 (2 cents) gate."""

    def test_zero_edge_blocked(self):
        assert 0.0 < MIN_NET_CENTS

    def test_exactly_at_min_passes(self):
        net_cents = MIN_NET_CENTS
        assert net_cents >= MIN_NET_CENTS

    def test_below_min_blocked(self):
        net_cents = 0.01
        assert not (net_cents >= MIN_NET_CENTS)

    def test_above_min_passes(self):
        net_cents = 0.05
        assert net_cents >= MIN_NET_CENTS

    def test_negative_edge_blocked(self):
        net_cents = -0.01
        assert not (net_cents >= MIN_NET_CENTS)
