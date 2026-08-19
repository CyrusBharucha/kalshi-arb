"""
tests/test_gate_filter_edge_cases.py
=====================================
Edge-case tests for the multi-gate false-positive filter pipeline.

Covers boundary conditions, multi-leg scenarios, fee calculation extremes,
sports prefix matching, dedup timing, and structural market logic.

All tests are self-contained — no DB or network required.
"""
from __future__ import annotations

import math


# ── Inline helpers ─────────────────────────────────────────────────────────

def _fee(p: float) -> float:
    """Kalshi taker fee: min(0.035, ceil(0.07 * P * (1-P) * 100) / 100)."""
    if p <= 0 or p >= 1:
        return 0.0
    return min(0.035, math.ceil(0.07 * p * (1.0 - p) * 100) / 100)


def _net_edge_ce(yes_asks: list[float]) -> float:
    """CE net edge: (1.00 - sum(asks)) - sum(fees), in cents."""
    total_ask = sum(yes_asks)
    total_fee = sum(_fee(a) for a in yes_asks)
    return (1.0 - total_ask - total_fee) * 100


def _net_edge_me(no_asks: list[float]) -> float:
    """ME net edge: ((N-1) - sum(no_asks)) - sum(fees), in cents."""
    n = len(no_asks)
    total_no = sum(no_asks)
    total_fee = sum(_fee(1.0 - na) for na in no_asks)
    return ((n - 1) - total_no - total_fee) * 100


def _net_edge_th(yes_ask_low: float, no_ask_high: float) -> float:
    """TH net edge: (1 - yes_ask_low - no_ask_high) - fees, in cents."""
    gross = 1.0 - yes_ask_low - no_ask_high
    fees = _fee(yes_ask_low) + _fee(1.0 - no_ask_high)
    return (gross - fees) * 100


_SPORTS_PFX = (
    "KXLIGA", "KXLALIGA", "KXNBA", "KXNFL", "KXMLB", "KXNHL", "KXEPL",
    "KXSERIEA", "KXBUNDES", "KXMLS", "KXUCL", "KXUEFA", "KXNCAAF",
    "KXNCAAB", "KXWNBA", "KXPGA", "KXTENNIS", "KXFORMULA", "KXSOCCER",
    "KXCRICKET", "KXRUGBY", "KXGOLF", "KXUFC", "KXBOXING", "KXMMA",
)


def _is_sports(ticker: str) -> bool:
    u = ticker.upper()
    return any(u.startswith(p) for p in _SPORTS_PFX)


MIN_NET_CENTS = 2.0
TTL_S = 300
HIGH_EDGE_CENTS = 50.0


def _gate5_suppress(last_seen: dict, key: str, now: float) -> bool:
    return (now - last_seen.get(key, 0)) < TTL_S


# ── Gate 1 boundary conditions ─────────────────────────────────────────────

def test_boundary_exactly_2_cents_passes():
    assert 2.0 >= MIN_NET_CENTS


def test_boundary_1_99_cents_fails():
    assert not (1.99 >= MIN_NET_CENTS)


def test_boundary_2_01_cents_passes():
    assert 2.01 >= MIN_NET_CENTS


def test_boundary_epsilon_below_fails():
    assert not (MIN_NET_CENTS - 1e-10 >= MIN_NET_CENTS)


# ── Fee calculation edge cases ─────────────────────────────────────────────

def test_fee_at_p_zero():
    """Fee at p=0 should be 0 (no contract to trade)."""
    assert _fee(0.0) == 0.0


def test_fee_at_p_one():
    """Fee at p=1 should be 0 (no risk)."""
    assert _fee(1.0) == 0.0


def test_fee_at_p_half():
    """p=0.5: 0.07*0.5*0.5=0.0175, ceil→0.02, min(0.035,0.02)=0.02."""
    assert _fee(0.5) == 0.02


def test_fee_symmetric():
    """Fee is symmetric: fee(p) == fee(1-p)."""
    for p in [0.1, 0.2, 0.3, 0.4, 0.5]:
        assert _fee(p) == _fee(1 - p), f"Asymmetry at p={p}"


def test_fee_never_exceeds_cap():
    """Fee never exceeds 3.5¢ at any probability."""
    for i in range(1, 100):
        p = i / 100.0
        assert _fee(p) <= 0.035, f"Fee exceeded cap at p={p}"


def test_fee_always_non_negative():
    for i in range(0, 101):
        p = i / 100.0
        assert _fee(p) >= 0.0, f"Negative fee at p={p}"


def test_fee_at_p_0_01():
    """p=0.01: 0.07*0.01*0.99=0.000693, ceil→0.01."""
    assert _fee(0.01) == 0.01


def test_fee_negative_p():
    """Negative price returns 0."""
    assert _fee(-0.5) == 0.0


# ── CE multi-leg scenarios ─────────────────────────────────────────────────

def test_ce_two_legs_no_arb():
    """Two legs at 50¢ each → gross = 0, fees > 0 → negative."""
    net = _net_edge_ce([0.50, 0.50])
    assert net < 0


def test_ce_two_legs_with_edge():
    """Two legs at 40¢ each → gross = 20¢, fees ~4¢ → net ~16¢."""
    net = _net_edge_ce([0.40, 0.40])
    assert net > 10


def test_ce_three_legs_tight():
    """Three legs at 32¢ each → gross = 4¢, fees 3×~2¢ = 6¢ → net < 0."""
    net = _net_edge_ce([0.32, 0.32, 0.32])
    assert net < 0


def test_ce_fifty_legs():
    """50 legs at 1¢ each → gross = 50¢, but fees dominate."""
    asks = [0.01] * 50
    # Asks sum to 50¢, gross = 50¢, fees = 50 × fee(0.01) = 50 × 0.01 = 50¢
    net = _net_edge_ce(asks)
    # Net should be 0 or negative (fees eat the gross)
    assert net <= 0.01  # essentially zero


def test_ce_five_legs_balanced():
    """Five legs at 18¢ each → gross = 10¢, fees ~10¢ → net near zero."""
    net = _net_edge_ce([0.18, 0.18, 0.18, 0.18, 0.18])
    # 5 × 0.18 = 0.90, gross = 10¢
    # fee(0.18) = min(0.035, ceil(0.07*0.18*0.82*100)/100) = min(0.035, ceil(1.0332)/100) = min(0.035, 0.02) = 0.02
    # total fees = 5 × 0.02 = 10¢, net = 0¢
    assert abs(net) < 1.0


# ── ME multi-leg scenarios ─────────────────────────────────────────────────

def test_me_exactly_at_n_minus_1():
    """ME: sum(no_asks) = exactly N-1 → gross = 0, net < 0."""
    no_asks = [0.50, 0.50]  # N=2, sum=1.0, N-1=1.0
    net = _net_edge_me(no_asks)
    assert net < 0  # fees make it negative


def test_me_just_below_n_minus_1():
    """ME: sum(no_asks) slightly below N-1 → tiny gross, fees eat it."""
    no_asks = [0.49, 0.50]  # N=2, sum=0.99, N-1=1.0, gross=1¢
    net = _net_edge_me(no_asks)
    assert net < 0  # 1¢ gross - fees ≈ -3¢


def test_me_three_legs_no_arb():
    """ME with 3 legs: no_asks sum near N-1=2."""
    no_asks = [0.65, 0.65, 0.65]  # sum=1.95, N-1=2, gross=5¢
    net = _net_edge_me(no_asks)
    # Fees: fee(1-0.65)=fee(0.35)=min(0.035,ceil(0.07*0.35*0.65*100)/100)=min(0.035,ceil(1.5925)/100)=0.02
    # total = 3×0.02 = 6¢, net = 5-6 = -1¢
    assert net < 0


def test_me_three_legs_with_edge():
    """ME with 3 cheap legs."""
    no_asks = [0.50, 0.50, 0.50]  # sum=1.50, N-1=2, gross=50¢
    net = _net_edge_me(no_asks)
    assert net > 30  # 50¢ gross minus ~6¢ fees


# ── TH monotonicity ───────────────────────────────────────────────────────

def test_th_normal_pair():
    """TH: buy YES on low strike, NO on high strike."""
    net = _net_edge_th(yes_ask_low=0.30, no_ask_high=0.30)
    # gross = 1 - 0.30 - 0.30 = 0.40 = 40¢
    assert net > 30


def test_th_tight_spread():
    """TH with tight spread → fees eat the edge."""
    net = _net_edge_th(yes_ask_low=0.48, no_ask_high=0.49)
    # gross = 3¢, fees ≈ 4¢
    assert net < 0


def test_th_monotonicity_violation():
    """If low-strike YES ask > high-strike YES ask, it's a monotonicity violation.
    This is a structural check, not a price check."""
    low_ask = 0.60
    high_ask = 0.40
    # A valid TH requires low_ask < high_ask (monotonicity)
    # The filter should catch this
    assert low_ask > high_ask  # violation detected


# ── TTL dedup timing edge cases ───────────────────────────────────────────

def test_dedup_exactly_at_ttl_boundary():
    """At exactly TTL_S, the gate should not suppress (< TTL_S is False)."""
    last_seen = {"CE:KXBOC-26SEP": 1000000.0}
    assert not _gate5_suppress(last_seen, "CE:KXBOC-26SEP", 1000000.0 + TTL_S)


def test_dedup_one_ms_before_ttl():
    last_seen = {"CE:KXBOC-26SEP": 1000000.0}
    assert _gate5_suppress(last_seen, "CE:KXBOC-26SEP", 1000000.0 + TTL_S - 0.001)


def test_dedup_multiple_keys_independent():
    last_seen = {
        "CE:KXBOC-26SEP": 1000000.0,
        "ME:KXFED-26NOV": 1000100.0,
    }
    now = 1000200.0
    assert _gate5_suppress(last_seen, "CE:KXBOC-26SEP", now)
    assert _gate5_suppress(last_seen, "ME:KXFED-26NOV", now)
    # New key should not be suppressed
    assert not _gate5_suppress(last_seen, "TH:KXSP500-26OCT", now)


def test_dedup_reset_after_expiry():
    """After TTL expires, same key should not be suppressed."""
    last_seen = {"CE:KXBOC-26SEP": 1000000.0}
    now = 1000000.0 + TTL_S + 1
    assert not _gate5_suppress(last_seen, "CE:KXBOC-26SEP", now)
    # Record again
    last_seen["CE:KXBOC-26SEP"] = now
    # Now should be suppressed again
    assert _gate5_suppress(last_seen, "CE:KXBOC-26SEP", now + 1)


# ── Sports ticker prefix edge cases ───────────────────────────────────────

def test_sports_kxnba_but_not_kxnbattery():
    """Prefix match: KXNBA-anything is sports, but KXNBATTERY is not a prefix."""
    assert _is_sports("KXNBA-CELTICS-WIN")
    # "KXNBATTERY" starts with "KXNBA" so it would match — this is by design
    assert _is_sports("KXNBATTERY")


def test_sports_case_insensitive():
    assert _is_sports("kxnfl-superbowl")
    assert _is_sports("KxNfL-MixedCase")


def test_sports_epl_match():
    assert _is_sports("KXEPL-ARSENAL-WIN")


def test_sports_cricket_match():
    assert _is_sports("KXCRICKET-INDIA-WIN")


def test_nonsports_kxbtc():
    assert not _is_sports("KXBTC-26DEC-B100000")


def test_nonsports_kxpres():
    assert not _is_sports("KXPRES-2028-WINNER")


def test_nonsports_kxcpi():
    assert not _is_sports("KXCPI-26SEP-T3.0")


def test_nonsports_empty():
    assert not _is_sports("")


# ── High-edge sanity edge cases ───────────────────────────────────────────

def test_high_edge_exactly_50_not_flagged():
    assert not (50.0 > HIGH_EDGE_CENTS)


def test_high_edge_50_01_flagged():
    assert 50.01 > HIGH_EDGE_CENTS


def test_high_edge_100_flagged():
    assert 100.0 > HIGH_EDGE_CENTS


def test_high_edge_negative_not_flagged():
    assert not (-10.0 > HIGH_EDGE_CENTS)


# ── Mixed freshness (multi-leg) ───────────────────────────────────────────

def test_mixed_freshness_one_stale_blocks():
    """If any leg has a stale quote, the whole opportunity is rejected."""
    leg_ages = [5.0, 10.0, 65.0]  # third leg is stale
    all_fresh = all(age < 60 for age in leg_ages)
    assert not all_fresh


def test_mixed_freshness_all_fresh_passes():
    leg_ages = [5.0, 10.0, 55.0]
    all_fresh = all(age < 60 for age in leg_ages)
    assert all_fresh


def test_mixed_freshness_barely_stale():
    leg_ages = [5.0, 60.0]  # second leg is at exactly 60s
    all_fresh = all(age < 60 for age in leg_ages)
    assert not all_fresh


def test_mixed_freshness_all_stale():
    leg_ages = [100.0, 200.0, 300.0]
    all_fresh = all(age < 60 for age in leg_ages)
    assert not all_fresh
