"""
tests/test_fee_consistency.py
==============================
Cross-module fee consistency tests.

Verifies that every importable implementation of the Kalshi taker fee formula
produces identical results. The canonical formula is:

    min(0.035, math.ceil(0.07 * p * (1-p) * 100) / 100)

Implementations tested:
  1. execution/fees.py   -> taker_fee_per_contract(price)
  2. arbitrage/trade_derived_scanner.py -> kalshi_fee(prob)

dashboard/ws_bridge.py inner _fee() is not importable directly and is skipped.
dashboard/live_arb_store.py is checked for a local fee helper but none is
imported here — its integration is covered by the ws_bridge tests elsewhere.
"""
from __future__ import annotations

import math

import pytest

from engine.fees import taker_fee_per_contract
from engine.trade_derived_scanner import kalshi_fee


# ---------------------------------------------------------------------------
# Reference implementation (inline, no dependencies)
# ---------------------------------------------------------------------------

def _canonical_fee(p: float) -> float:
    """Canonical formula used as the reference oracle."""
    return min(0.035, math.ceil(0.07 * p * (1.0 - p) * 100) / 100)


# ---------------------------------------------------------------------------
# Parametrized cross-module agreement test
# ---------------------------------------------------------------------------

PROBE_PRICES = [0.10, 0.25, 0.50, 0.75, 0.90]


@pytest.mark.parametrize("p", PROBE_PRICES)
def test_implementations_agree(p: float):
    """engine.fees and trade_derived_scanner must return the same fee."""
    fee_exec = taker_fee_per_contract(p)
    fee_scan = kalshi_fee(p)
    assert fee_exec == fee_scan, (
        f"p={p}: execution/fees={fee_exec}, trade_derived_scanner={fee_scan}"
    )


@pytest.mark.parametrize("p", PROBE_PRICES)
def test_matches_canonical(p: float):
    """Both implementations must match the canonical reference formula."""
    expected = _canonical_fee(p)
    assert taker_fee_per_contract(p) == expected, (
        f"p={p}: execution/fees={taker_fee_per_contract(p)}, expected={expected}"
    )
    assert kalshi_fee(p) == expected, (
        f"p={p}: trade_derived_scanner={kalshi_fee(p)}, expected={expected}"
    )


# ---------------------------------------------------------------------------
# Specific spot-check values
# ---------------------------------------------------------------------------

def test_p50_gives_two_cents():
    """p=0.50: 0.07*0.5*0.5=0.0175, ceil to cent=0.02, min(0.035,0.02)=0.02."""
    assert taker_fee_per_contract(0.50) == 0.02
    assert kalshi_fee(0.50) == 0.02


def test_p10_gives_one_cent():
    """p=0.10: 0.07*0.10*0.90=0.0063, ceil to cent=0.01, min(0.035,0.01)=0.01."""
    assert taker_fee_per_contract(0.10) == 0.01
    assert kalshi_fee(0.10) == 0.01


def test_cap_not_hit_at_p50():
    """At p=0.50 the raw result (0.02) is well under the cap (0.035)."""
    fee = taker_fee_per_contract(0.50)
    assert fee < 0.035


def test_cap_enforced_at_extreme():
    """Verify cap is enforced: no fee may exceed $0.035."""
    for p in [0.01, 0.10, 0.25, 0.49, 0.50, 0.51, 0.75, 0.90, 0.99]:
        assert taker_fee_per_contract(p) <= 0.035, f"fee exceeded cap at p={p}"
        assert kalshi_fee(p) <= 0.035, f"kalshi_fee exceeded cap at p={p}"
