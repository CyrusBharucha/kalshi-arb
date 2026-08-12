"""
scripts/verify_live_arbs.py
============================
Cross-check detected arb opportunities against Kalshi's live REST API.

Usage:
    python scripts/verify_live_arbs.py

Reads today's JSONL arb log, then for each opportunity hits Kalshi's public
API to verify prices are real right now. Prints a verdict for each entry.
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"


def _kalshi_get(path: str, timeout: int = 6) -> dict:
    url = f"{_KALSHI_BASE}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-verify"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _fee(p: float) -> float:
    return min(0.035, math.ceil(0.07 * p * (1.0 - p) * 100) / 100)


def _get_yes_ask(m: dict) -> float | None:
    """Extract YES ask from a Kalshi market dict (handles dollars vs legacy)."""
    v = m.get("yes_ask_dollars") or m.get("yes_ask")
    if v is None:
        return None
    f = float(v)
    # If value > 1.0 it's in cents (legacy), convert to dollars
    return f / 100.0 if f > 1.0 else f


def _get_no_ask(m: dict) -> float | None:
    v = m.get("no_ask_dollars") or m.get("no_ask")
    if v is None:
        return None
    f = float(v)
    return f / 100.0 if f > 1.0 else f


def verify_ync(ticker: str) -> dict:
    """Verify a yes_no_complement arb against Kalshi REST."""
    try:
        data = _kalshi_get(f"/markets/{ticker}")
        m = data.get("market", {})
        status  = m.get("status", "?")
        ya = _get_yes_ask(m)
        na = _get_no_ask(m)
        if ya is None or na is None:
            return {"verdict": "NO_QUOTES", "yes_ask": None, "no_ask": None, "status": status}
        gross = 1.0 - ya - na
        fees  = _fee(ya) + _fee(na)
        net   = gross - fees
        return {
            "verdict": "REAL_ARB" if net >= 0.005 else "FALSE_POS" if gross > 0 else "NO_ARB",
            "yes_ask_api": ya,
            "no_ask_api":  na,
            "gross_c":     round(gross * 100, 2),
            "net_c":       round(net   * 100, 2),
            "status":      status,
        }
    except Exception as exc:
        return {"verdict": "API_ERROR", "error": str(exc)}


def verify_ce(event_ticker: str, our_legs: list[str]) -> dict:
    """Verify a collectively_exhaustive arb against Kalshi REST."""
    try:
        data = _kalshi_get(f"/markets?event_ticker={event_ticker}&status=open&limit=200")
        markets = data.get("markets", [])
        if not markets:
            return {"verdict": "EVENT_EMPTY", "api_count": 0, "our_count": len(our_legs)}

        api_count  = len(markets)
        our_count  = len(our_legs)
        api_tickers = {m["ticker"] for m in markets if "ticker" in m}
        missing     = [t for t in our_legs if t not in api_tickers]

        # Build YES ask sum from API
        sum_yes = 0.0
        missing_quotes = []
        per_leg = {}
        for m in markets:
            t   = m.get("ticker", "")
            ya  = _get_yes_ask(m)
            if ya is None or ya <= 0:
                missing_quotes.append(t)
                per_leg[t] = None
            else:
                sum_yes += ya
                per_leg[t] = ya

        gross = 1.0 - sum_yes
        fees  = sum(_fee(ya) for ya in per_leg.values() if ya is not None)
        net   = gross - fees

        verdict = "REAL_ARB" if (
            api_count == our_count
            and not missing
            and not missing_quotes
            and net >= 0.005
        ) else "FALSE_POS"

        return {
            "verdict":       verdict,
            "api_count":     api_count,
            "our_count":     our_count,
            "missing_legs":  missing,
            "missing_quotes": missing_quotes,
            "sum_yes_api":   round(sum_yes, 4),
            "gross_c":       round(gross * 100, 2),
            "net_c":         round(net   * 100, 2),
            "per_leg_api":   {t: f"{v*100:.1f}c" if v else "—" for t, v in per_leg.items()},
        }
    except Exception as exc:
        return {"verdict": "API_ERROR", "error": str(exc)}


def verify_threshold(yes_ticker: str, no_ticker: str) -> dict:
    """Verify a threshold/superset arb: buy YES(low) + NO(high) < $1."""
    try:
        d_yes = _kalshi_get(f"/markets/{yes_ticker}")
        d_no  = _kalshi_get(f"/markets/{no_ticker}")
        m_yes = d_yes.get("market", {})
        m_no  = d_no.get("market", {})
        ya = _get_yes_ask(m_yes)
        na = _get_no_ask(m_no)
        if ya is None or na is None:
            return {"verdict": "NO_QUOTES", "yes_ticker": yes_ticker, "no_ticker": no_ticker}
        combined = ya + na
        gross    = 1.0 - combined
        fees     = _fee(ya) + _fee(na)
        net      = gross - fees
        return {
            "verdict":   "REAL_ARB" if net >= 0.005 else ("FALSE_POS" if gross > 0 else "NO_ARB"),
            "yes_ask":   ya,
            "no_ask":    na,
            "combined":  round(combined, 4),
            "gross_c":   round(gross * 100, 2),
            "net_c":     round(net * 100, 2),
            "yes_status": m_yes.get("status", "?"),
            "no_status":  m_no.get("status", "?"),
        }
    except Exception as exc:
        return {"verdict": "API_ERROR", "error": str(exc)}


def verify_me(event_ticker: str, our_legs: list[str]) -> dict:
    """Verify a mutually_exclusive arb against Kalshi REST."""
    try:
        data = _kalshi_get(f"/markets?event_ticker={event_ticker}&status=open&limit=200")
        markets = data.get("markets", [])
        if not markets:
            return {"verdict": "EVENT_EMPTY", "api_count": 0, "our_count": len(our_legs)}

        api_count = len(markets)
        our_count = len(our_legs)
        N = api_count

        sum_no = 0.0
        missing_quotes = []
        per_leg = {}
        for m in markets:
            t  = m.get("ticker", "")
            na = _get_no_ask(m)
            if na is None or na <= 0:
                missing_quotes.append(t)
                per_leg[t] = None
            else:
                sum_no += na
                per_leg[t] = na

        gross = (N - 1) - sum_no
        fees  = sum(_fee(na) for na in per_leg.values() if na is not None)
        net   = gross - fees

        verdict = "REAL_ARB" if (
            api_count == our_count
            and not missing_quotes
            and net >= 0.005
        ) else "FALSE_POS"

        return {
            "verdict":        verdict,
            "api_count":      api_count,
            "our_count":      our_count,
            "missing_quotes": missing_quotes,
            "sum_no_api":     round(sum_no, 4),
            "gross_c":        round(gross * 100, 2),
            "net_c":          round(net   * 100, 2),
        }
    except Exception as exc:
        return {"verdict": "API_ERROR", "error": str(exc)}


def load_today_log() -> list[dict]:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = Path(f"/tmp/kalshi_arb_log_{date_str}.jsonl")
    if not path.exists():
        print(f"No log found at {path}")
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def main():
    rows = load_today_log()
    if not rows:
        print("No arbs to verify today. JSONL log is empty or missing.")
        print("Either no arbs have been detected yet, or the app isn't running.")
        return

    print(f"\n{'='*70}")
    print(f"KALSHI ARB VERIFIER — {len(rows)} entries in today's log")
    print(f"{'='*70}\n")

    real = 0
    false_pos = 0
    api_errors = 0

    for i, opp in enumerate(rows, 1):
        strat   = opp.get("strategy", "unknown")
        ticker  = opp.get("ticker", "?")
        legs    = opp.get("legs", [])
        det_ts  = opp.get("detected_at_ts")
        t_str   = datetime.fromtimestamp(det_ts, tz=timezone.utc).strftime("%H:%M:%S") if det_ts else "?"
        net_ws  = float(opp.get("net_edge_cents", 0))

        print(f"[{i:02d}] {t_str}  {strat.upper():<25}  {ticker}")
        print(f"      WS net edge: {net_ws:+.2f}c  |  legs: {len(legs)}")

        # Derive event ticker for multi-leg arbs
        if strat == "yes_no_complement":
            result = verify_ync(ticker)
        elif strat == "collectively_exhaustive":
            evt_tk = ticker.split(" (")[0]  # strip " (N legs)" suffix
            result = verify_ce(evt_tk, legs)
        elif strat == "mutually_exclusive":
            evt_tk = ticker.split(" (")[0]
            result = verify_me(evt_tk, legs)
        elif strat in ("threshold_order", "superset"):
            if len(legs) >= 2:
                # legs[0] = YES leg (lower threshold), legs[1] = NO leg (higher threshold)
                result = verify_threshold(legs[0], legs[1])
                time.sleep(0.5)  # extra sleep for 2 API calls
            else:
                result = {"verdict": "UNKNOWN_STRATEGY", "note": "need_2_legs"}
        else:
            result = {"verdict": "UNKNOWN_STRATEGY"}

        verdict = result.get("verdict", "?")
        verdict_label = {
            "REAL_ARB":  "✅ REAL ARB",
            "FALSE_POS": "❌ FALSE POSITIVE",
            "API_ERROR": "⚠️  API ERROR",
            "NO_ARB":    "❌ EDGE GONE",
            "NO_QUOTES": "⚠️  NO QUOTES",
            "EVENT_EMPTY": "❌ EVENT EMPTY",
            "MANUAL":    "🔍 MANUAL CHECK",
            "UNKNOWN_STRATEGY": "❓ UNKNOWN",
        }.get(verdict, f"? {verdict}")

        print(f"      API check: {verdict_label}")

        # Print detail
        for k, v in result.items():
            if k == "verdict":
                continue
            print(f"        {k}: {v}")

        print()

        if verdict == "REAL_ARB":
            real += 1
        elif verdict in ("FALSE_POS", "NO_ARB", "EVENT_EMPTY"):
            false_pos += 1
        elif "ERROR" in verdict or "NO_QUOTES" in verdict:
            api_errors += 1

        # Rate limit
        time.sleep(0.5)

    print(f"{'='*70}")
    print(f"SUMMARY: {real} real arbs  |  {false_pos} false positives  |  {api_errors} API errors")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
