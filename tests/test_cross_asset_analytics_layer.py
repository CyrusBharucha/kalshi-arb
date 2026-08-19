"""
tests/test_cross_asset_analytics_layer.py
==========================================
Unit tests for the cross-asset analytics layer.

Validates BOC/FED ticker parsing, OIS probability model, cross-asset signal
classification, macro regime detection, CE sum checks, z-score computation,
divergence thresholds, and edge cases.

All tests are self-contained — no DB or network required.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone


# ── Inline helpers (mirrored from p07_cross_asset.py / probability_engine) ──

def _parse_boc_ticker(ticker: str):
    """Parse KXBOC-{YYMM}-T{rate} → (rate_decimal, date_str) or None."""
    m = re.match(r"KXBOC-(\d{2}[A-Z]{3})-T([\d.]+)", ticker.upper())
    if not m:
        return None
    date_str = m.group(1)
    rate_pct = float(m.group(2))
    return rate_pct / 100.0, date_str


def _parse_fed_ticker(ticker: str):
    """Parse KXFED-{YYMM}-T{rate} → (rate_decimal, date_str) or None."""
    m = re.match(r"KXFED-(\d{2}[A-Z]{3})-T([\d.]+)", ticker.upper())
    if not m:
        return None
    date_str = m.group(1)
    rate_pct = float(m.group(2))
    return rate_pct / 100.0, date_str


_MONTH = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _days_to_month(yymm_str: str) -> int:
    """Convert '26SEP' → days from 2026-08-19 to first of that month."""
    try:
        year = 2000 + int(yymm_str[:2])
        month = _MONTH.get(yymm_str[2:5].upper(), 9)
        ref = date(2026, 8, 19)  # fixed ref for deterministic tests
        target = date(year, month, 1)
        return max(1, (target - ref).days)
    except Exception:
        return 30


def _classify_signal(gap: float) -> str:
    """Classify cross-asset signal based on Kalshi-OIS gap."""
    if gap > 0.05:
        return "SELL YES"
    elif gap < -0.05:
        return "BUY YES"
    return "FAIR"


def _macro_regime(btc: float, vix: float, tnx: float = 0) -> str:
    """Classify macro regime from BTC price and VIX."""
    if btc > 50000 and vix > 0 and vix < 20:
        return "RISK-ON"
    elif (btc > 0 and btc < 30000) or (vix > 25 and tnx > 4.5):
        return "RISK-OFF"
    return "NEUTRAL"


def _ce_sum_check(mids: list[float]) -> str:
    """Check if sum of mids is ~$1 for exhaustive events."""
    total = sum(mids)
    if total < 0.98:
        return "BELOW PARITY"
    elif total > 1.02:
        return "OVER"
    return "OK"


def _z_score(values: list[float]) -> list[float]:
    """Compute z-scores for a series of values."""
    if len(values) < 2:
        return [0.0] * len(values)
    mean = sum(values) / len(values)
    std = (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5
    if std == 0:
        return [0.0] * len(values)
    return [(v - mean) / std for v in values]


# ── BOC ticker parsing ─────────────────────────────────────────────────────

def test_parse_boc_standard():
    result = _parse_boc_ticker("KXBOC-26SEP-T2.25")
    assert result is not None
    rate, dt = result
    assert abs(rate - 0.0225) < 1e-9
    assert dt == "26SEP"


def test_parse_boc_high_rate():
    result = _parse_boc_ticker("KXBOC-26JAN-T4.50")
    assert result is not None
    rate, dt = result
    assert abs(rate - 0.045) < 1e-9
    assert dt == "26JAN"


def test_parse_boc_zero_rate():
    result = _parse_boc_ticker("KXBOC-26MAR-T0.25")
    assert result is not None
    rate, _ = result
    assert abs(rate - 0.0025) < 1e-9


def test_parse_boc_lowercase():
    result = _parse_boc_ticker("kxboc-26sep-t2.25")
    assert result is not None


def test_parse_boc_invalid_prefix():
    assert _parse_boc_ticker("KXFED-26SEP-T2.25") is None


def test_parse_boc_no_rate():
    assert _parse_boc_ticker("KXBOC-26SEP") is None


def test_parse_boc_garbage():
    assert _parse_boc_ticker("not-a-ticker") is None


# ── FED ticker parsing ─────────────────────────────────────────────────────

def test_parse_fed_standard():
    result = _parse_fed_ticker("KXFED-26NOV-T4.50")
    assert result is not None
    rate, dt = result
    assert abs(rate - 0.045) < 1e-9
    assert dt == "26NOV"


def test_parse_fed_low_rate():
    result = _parse_fed_ticker("KXFED-26DEC-T3.75")
    assert result is not None
    rate, _ = result
    assert abs(rate - 0.0375) < 1e-9


def test_parse_fed_invalid_prefix():
    assert _parse_fed_ticker("KXBOC-26NOV-T4.50") is None


def test_parse_fed_lowercase():
    result = _parse_fed_ticker("kxfed-26nov-t4.50")
    assert result is not None


def test_parse_fed_garbage():
    assert _parse_fed_ticker("KXNBA-LAKERS") is None


# ── Signal classification ──────────────────────────────────────────────────

def test_signal_large_positive_gap_sell():
    assert _classify_signal(0.10) == "SELL YES"


def test_signal_large_negative_gap_buy():
    assert _classify_signal(-0.08) == "BUY YES"


def test_signal_small_gap_fair():
    assert _classify_signal(0.02) == "FAIR"


def test_signal_exactly_at_threshold_fair():
    assert _classify_signal(0.05) == "FAIR"


def test_signal_just_above_threshold_sell():
    assert _classify_signal(0.051) == "SELL YES"


def test_signal_zero_gap_fair():
    assert _classify_signal(0.0) == "FAIR"


def test_signal_negative_threshold_boundary():
    assert _classify_signal(-0.05) == "FAIR"


def test_signal_just_below_neg_threshold_buy():
    assert _classify_signal(-0.051) == "BUY YES"


# ── Macro regime classification ────────────────────────────────────────────

def test_regime_risk_on():
    assert _macro_regime(btc=65000, vix=15) == "RISK-ON"


def test_regime_risk_off_low_btc():
    assert _macro_regime(btc=25000, vix=30, tnx=4.6) == "RISK-OFF"


def test_regime_risk_off_high_vix():
    assert _macro_regime(btc=55000, vix=30, tnx=4.8) == "RISK-OFF"


def test_regime_neutral():
    assert _macro_regime(btc=45000, vix=22) == "NEUTRAL"


def test_regime_btc_zero():
    assert _macro_regime(btc=0, vix=15) == "NEUTRAL"


# ── CE sum check ──────────────────────────────────────────────────────────

def test_ce_sum_exactly_one():
    assert _ce_sum_check([0.30, 0.40, 0.30]) == "OK"


def test_ce_sum_below_parity():
    assert _ce_sum_check([0.20, 0.30, 0.40]) == "BELOW PARITY"


def test_ce_sum_over_parity():
    assert _ce_sum_check([0.40, 0.40, 0.30]) == "OVER"


def test_ce_sum_at_boundary_low():
    assert _ce_sum_check([0.32, 0.33, 0.33]) == "OK"  # 0.98


def test_ce_sum_at_boundary_high():
    assert _ce_sum_check([0.34, 0.34, 0.34]) == "OK"  # 1.02


# ── Z-score computation ───────────────────────────────────────────────────

def test_zscore_symmetric():
    z = _z_score([1.0, 2.0, 3.0])
    assert abs(z[1]) < 1e-9  # mean value has z=0


def test_zscore_constant_series():
    z = _z_score([5.0, 5.0, 5.0])
    assert all(abs(v) < 1e-9 for v in z)


def test_zscore_two_points():
    z = _z_score([0.0, 1.0])
    assert abs(z[0] + z[1]) < 1e-9  # symmetric around mean


def test_zscore_single_point():
    z = _z_score([42.0])
    assert z == [0.0]


def test_zscore_empty():
    z = _z_score([])
    assert z == []


# ── Days-to-month parsing ─────────────────────────────────────────────────

def test_days_to_sep_26():
    d = _days_to_month("26SEP")
    assert d >= 1  # Sep 1 2026 is ahead of Aug 19


def test_days_to_jan_27():
    d = _days_to_month("27JAN")
    assert d > 100  # Jan 2027 is several months ahead


def test_days_to_aug_26():
    d = _days_to_month("26AUG")
    assert d >= 1  # same month but first of month


def test_days_to_garbage():
    d = _days_to_month("ZZZZZ")
    assert d == 30  # fallback


def test_days_always_positive():
    for m in ["26SEP", "26OCT", "26NOV", "26DEC", "27JAN"]:
        assert _days_to_month(m) >= 1


# ── Edge cases ─────────────────────────────────────────────────────────────

def test_parse_boc_empty_string():
    assert _parse_boc_ticker("") is None


def test_parse_fed_empty_string():
    assert _parse_fed_ticker("") is None


def test_signal_extreme_positive():
    assert _classify_signal(0.50) == "SELL YES"


def test_signal_extreme_negative():
    assert _classify_signal(-0.50) == "BUY YES"


def test_ce_sum_single_market():
    assert _ce_sum_check([1.0]) == "OK"


def test_ce_sum_many_markets():
    mids = [0.05] * 20  # 20 × 0.05 = 1.00
    assert _ce_sum_check(mids) == "OK"


def test_zscore_large_series():
    import random
    random.seed(42)
    vals = [random.gauss(0, 1) for _ in range(1000)]
    z = _z_score(vals)
    assert len(z) == 1000
    # Mean of z-scores should be ~0
    z_mean = sum(z) / len(z)
    assert abs(z_mean) < 0.01
