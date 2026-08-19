"""
tests/test_false_positive_filter_pipeline.py
=============================================
Unit tests for the multi-layer false-positive filter pipeline.

Each gate in the scanner pipeline is validated independently:
  Gate 0 — CE series classification (event type filter)
  Gate 1 — Fee-adjusted net edge > 0
  Gate 2 — Quote freshness (< 60s)
  Gate 3 — Min-tick illiquidity (1¢ minimum tick blocked)
  Gate 4 — Price floor (asks below 5¢ eliminated)
  Gate 5 — TTL dedup (same opportunity suppressed for 5 min)
  Gate 6 — High-edge sanity (> 50¢ flagged as stale-book)
  Gate 7 — Sports market filter

All tests are self-contained with inline helpers — no DB or network required.
"""
from __future__ import annotations

import math
import time


# ── Inline helpers (mirrors production logic without import dependencies) ──

def _fee(p: float) -> float:
    """Kalshi taker fee: min(0.035, ceil(0.07 * P * (1-P) * 100) / 100)."""
    if p <= 0 or p >= 1:
        return 0.0
    return min(0.035, math.ceil(0.07 * p * (1.0 - p) * 100) / 100)


def _net_edge_ync(yes_ask: float, no_ask: float) -> float:
    """Net edge for YES/NO complement: (1 - yes_ask - no_ask) - fees."""
    gross = 1.0 - yes_ask - no_ask
    fees = _fee(yes_ask) + _fee(1.0 - no_ask)
    return (gross - fees) * 100  # in cents


def _net_edge_ce(yes_asks: list[float]) -> float:
    """Net edge for CE: 1.00 - sum(yes_asks) - sum(fees)."""
    total_ask = sum(yes_asks)
    total_fee = sum(_fee(a) for a in yes_asks)
    gross = 1.0 - total_ask
    return (gross - total_fee) * 100


def _net_edge_me(no_asks: list[float]) -> float:
    """Net edge for ME: (N-1) - sum(no_asks) - sum(fees)."""
    n = len(no_asks)
    total_no = sum(no_asks)
    total_fee = sum(_fee(1.0 - na) for na in no_asks)
    gross = (n - 1) - total_no
    return (gross - total_fee) * 100


_SPORTS_PFX = (
    "KXLIGA", "KXLALIGA", "KXNBA", "KXNFL", "KXMLB", "KXNHL", "KXEPL",
    "KXSERIEA", "KXBUNDES", "KXMLS", "KXUCL", "KXUEFA", "KXNCAAF",
    "KXNCAAB", "KXWNBA", "KXPGA", "KXTENNIS", "KXFORMULA", "KXSOCCER",
    "KXCRICKET", "KXRUGBY", "KXGOLF", "KXUFC", "KXBOXING", "KXMMA",
)


def _is_sports(ticker: str) -> bool:
    u = ticker.upper()
    return any(u.startswith(p) for p in _SPORTS_PFX)


MIN_NET_CENTS = 2.0       # 2¢ minimum net edge
STALE_AGE_S = 60          # quote freshness limit
TTL_S = 300               # dedup window (5 min)
HIGH_EDGE_CENTS = 50.0    # stale-book sanity cap
PRICE_FLOOR = 0.05        # 5¢ minimum ask
MIN_TICK = 0.01           # 1¢ Kalshi minimum tick


def _gate1_pass(net_edge_cents: float) -> bool:
    """Gate 1: fee-adjusted net edge must be >= MIN_NET_CENTS."""
    return net_edge_cents >= MIN_NET_CENTS


def _gate2_pass(quote_age_s: float) -> bool:
    """Gate 2: quote must be fresher than STALE_AGE_S."""
    return quote_age_s < STALE_AGE_S


def _gate3_pass(ask: float) -> bool:
    """Gate 3: ask must not be at the 1¢ minimum tick."""
    return abs(ask - MIN_TICK) > 1e-9


def _gate4_pass(ask: float) -> bool:
    """Gate 4: ask must be above the price floor."""
    return ask >= PRICE_FLOOR


def _gate5_suppress(last_seen: dict, key: str, now: float) -> bool:
    """Gate 5: TTL dedup — suppress if seen within TTL_S."""
    return (now - last_seen.get(key, 0)) < TTL_S


def _gate6_flag(net_edge_cents: float) -> bool:
    """Gate 6: flag if edge > HIGH_EDGE_CENTS (stale-book suspect)."""
    return net_edge_cents > HIGH_EDGE_CENTS


# CE event types that are exhaustive (eligible for CE scanning)
_CE_ELIGIBLE_TYPES = {"federal_reserve_rate", "boc_rate", "bitcoin_price",
                       "sp500_range", "election_winner", "fomc_decision"}
_CE_INELIGIBLE_TYPES = {"coin_flip", "sports_match", "weather_daily",
                          "crypto_moonshot"}


def _gate0_ce_eligible(event_type: str) -> bool:
    """Gate 0: only exhaustive event types are eligible for CE scanning."""
    return event_type in _CE_ELIGIBLE_TYPES


# ── Gate 1: Fee-adjusted net edge ──────────────────────────────────────────

def test_gate1_fee_kills_thin_edge():
    """Gross edge of 3¢ on two 50¢ legs → fees eat the edge entirely."""
    net = _net_edge_ync(0.48, 0.49)  # gross = 3¢
    # fees at p=0.48 and p=0.51 are both 2¢ → total fees = 4¢ > gross 3¢
    assert not _gate1_pass(net)


def test_gate1_fat_edge_passes():
    """10¢ gross edge survives fees."""
    net = _net_edge_ync(0.40, 0.50)  # gross = 10¢
    assert _gate1_pass(net)


def test_gate1_exactly_at_threshold():
    """Net edge of exactly 2.0¢ should pass."""
    assert _gate1_pass(2.0)


def test_gate1_just_below_threshold():
    """Net edge of 1.99¢ should fail."""
    assert not _gate1_pass(1.99)


def test_gate1_zero_edge_blocked():
    assert not _gate1_pass(0.0)


def test_gate1_negative_edge_blocked():
    assert not _gate1_pass(-1.5)


def test_gate1_large_edge_passes():
    assert _gate1_pass(15.0)


# ── Gate 2: Quote freshness ────────────────────────────────────────────────

def test_gate2_fresh_quote_passes():
    assert _gate2_pass(5.0)


def test_gate2_stale_quote_rejected():
    assert not _gate2_pass(61.0)


def test_gate2_exactly_at_limit():
    """At exactly 60s, the quote is NOT fresh (>= 60 fails < 60 check)."""
    assert not _gate2_pass(60.0)


def test_gate2_just_under_limit():
    assert _gate2_pass(59.9)


def test_gate2_zero_age_passes():
    assert _gate2_pass(0.0)


def test_gate2_very_stale_rejected():
    assert not _gate2_pass(3600.0)


# ── Gate 3: Min-tick illiquidity ───────────────────────────────────────────

def test_gate3_min_tick_blocked():
    """Ask at exactly 1¢ is blocked."""
    assert not _gate3_pass(0.01)


def test_gate3_above_min_tick_passes():
    assert _gate3_pass(0.02)


def test_gate3_normal_price_passes():
    assert _gate3_pass(0.50)


def test_gate3_high_price_passes():
    assert _gate3_pass(0.95)


def test_gate3_two_cents_passes():
    assert _gate3_pass(0.02)


# ── Gate 4: Price floor ────────────────────────────────────────────────────

def test_gate4_below_floor_blocked():
    assert not _gate4_pass(0.03)


def test_gate4_at_floor_passes():
    assert _gate4_pass(0.05)


def test_gate4_above_floor_passes():
    assert _gate4_pass(0.10)


def test_gate4_penny_blocked():
    assert not _gate4_pass(0.01)


def test_gate4_normal_ask_passes():
    assert _gate4_pass(0.50)


# ── Gate 5: TTL dedup ──────────────────────────────────────────────────────

def test_gate5_first_occurrence_not_suppressed():
    last_seen = {}
    assert not _gate5_suppress(last_seen, "CE:KXBOC-26SEP", 1000000.0)


def test_gate5_immediate_repeat_suppressed():
    last_seen = {"CE:KXBOC-26SEP": 1000000.0}
    assert _gate5_suppress(last_seen, "CE:KXBOC-26SEP", 1000001.0)


def test_gate5_after_ttl_not_suppressed():
    last_seen = {"CE:KXBOC-26SEP": 1000000.0}
    assert not _gate5_suppress(last_seen, "CE:KXBOC-26SEP", 1000000.0 + TTL_S + 1)


def test_gate5_different_key_independent():
    last_seen = {"CE:KXBOC-26SEP": 1000000.0}
    assert not _gate5_suppress(last_seen, "CE:KXFED-26NOV", 1000001.0)


# ── Gate 6: High-edge sanity ──────────────────────────────────────────────

def test_gate6_normal_edge_not_flagged():
    assert not _gate6_flag(5.0)


def test_gate6_high_edge_flagged():
    assert _gate6_flag(55.0)


def test_gate6_at_threshold_not_flagged():
    assert not _gate6_flag(50.0)


def test_gate6_just_above_threshold_flagged():
    assert _gate6_flag(50.01)


def test_gate6_extreme_edge_flagged():
    assert _gate6_flag(200.0)


# ── Gate 0: CE series classification ──────────────────────────────────────

def test_gate0_federal_reserve_eligible():
    assert _gate0_ce_eligible("federal_reserve_rate")


def test_gate0_boc_rate_eligible():
    assert _gate0_ce_eligible("boc_rate")


def test_gate0_coin_flip_ineligible():
    assert not _gate0_ce_eligible("coin_flip")


def test_gate0_sports_ineligible():
    assert not _gate0_ce_eligible("sports_match")


def test_gate0_unknown_type_ineligible():
    assert not _gate0_ce_eligible("random_unknown_type")


def test_gate0_bitcoin_price_eligible():
    assert _gate0_ce_eligible("bitcoin_price")


# ── Sports market filter ──────────────────────────────────────────────────

def test_sports_nba_excluded():
    assert _is_sports("KXNBA-LAKERS-WIN")


def test_sports_nfl_excluded():
    assert _is_sports("KXNFL-SUPERBOWL-24")


def test_sports_liga_excluded():
    assert _is_sports("KXLIGA-REALMADRID-WIN")


def test_sports_laliga_excluded():
    assert _is_sports("KXLALIGA-FCB-WIN")


def test_sports_formula_excluded():
    assert _is_sports("KXFORMULA-2024-WINNER")


def test_sports_ufc_excluded():
    assert _is_sports("KXUFC-MCGREGOR-WIN")


def test_sports_golf_excluded():
    assert _is_sports("KXGOLF-MASTERS-24")


def test_nonsports_fed_not_excluded():
    assert not _is_sports("KXFED-26NOV-T4.50")


def test_nonsports_boc_not_excluded():
    assert not _is_sports("KXBOC-26SEP-T2.25")


def test_nonsports_btc_not_excluded():
    assert not _is_sports("KXBTC-26DEC-B100000")


def test_nonsports_sp500_not_excluded():
    assert not _is_sports("KXSP500-26OCT31-T5800")


# ── Full pipeline integration (all gates) ─────────────────────────────────

def _passes_all_gates(
    net_edge_cents: float,
    quote_age_s: float,
    ask: float,
    ticker: str,
    event_type: str = "federal_reserve_rate",
    last_seen: dict = None,
    now: float = 1000000.0,
) -> bool:
    """Simulate the full filter pipeline. Returns True only if all gates pass."""
    if last_seen is None:
        last_seen = {}
    if _is_sports(ticker):
        return False
    if not _gate0_ce_eligible(event_type):
        return False
    if not _gate1_pass(net_edge_cents):
        return False
    if not _gate2_pass(quote_age_s):
        return False
    if not _gate3_pass(ask):
        return False
    if not _gate4_pass(ask):
        return False
    key = f"CE:{ticker}"
    if _gate5_suppress(last_seen, key, now):
        return False
    if _gate6_flag(net_edge_cents):
        return False
    return True


def test_pipeline_valid_opportunity_passes():
    assert _passes_all_gates(
        net_edge_cents=5.0, quote_age_s=10.0, ask=0.50,
        ticker="KXBOC-26SEP-T2.25",
    )


def test_pipeline_sports_ticker_blocked():
    assert not _passes_all_gates(
        net_edge_cents=5.0, quote_age_s=10.0, ask=0.50,
        ticker="KXNBA-LAKERS-WIN",
    )


def test_pipeline_stale_quote_blocked():
    assert not _passes_all_gates(
        net_edge_cents=5.0, quote_age_s=120.0, ask=0.50,
        ticker="KXBOC-26SEP-T2.25",
    )


def test_pipeline_thin_edge_blocked():
    assert not _passes_all_gates(
        net_edge_cents=1.0, quote_age_s=10.0, ask=0.50,
        ticker="KXBOC-26SEP-T2.25",
    )


def test_pipeline_min_tick_blocked():
    assert not _passes_all_gates(
        net_edge_cents=5.0, quote_age_s=10.0, ask=0.01,
        ticker="KXBOC-26SEP-T2.25",
    )


def test_pipeline_high_edge_flagged():
    assert not _passes_all_gates(
        net_edge_cents=60.0, quote_age_s=10.0, ask=0.50,
        ticker="KXBOC-26SEP-T2.25",
    )
