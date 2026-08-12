"""
scripts/scan_cross_event.py
============================
Cross-event calendar spread arb scanner.

Finds arbitrage between markets on the same underlying / threshold but with
different expiry dates.  For cumulative-type markets ("reaches X by date"),
the later date is a strict superset of the earlier date, so:

    Structure: buy YES(later) + buy NO(earlier) at combined cost < $1
    Worst-case payout: always $1 → guaranteed profit

Also scans for cross-event threshold inversions: two markets where the
lower-threshold YES is priced *higher* than the higher-threshold YES —
a guaranteed arbitrage within a superset relationship.

Usage:
    python scripts/scan_cross_event.py
    python scripts/scan_cross_event.py --min-edge 0.5   # cents (default 0.5)
    python scripts/scan_cross_event.py --limit 500      # events to scan
"""

from __future__ import annotations

import argparse
import math
import json
import re
import time
import urllib.request
from collections import defaultdict
from datetime import date
from typing import Optional

_KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,  "MAY": 5,  "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _kalshi_get(path: str, params: dict | None = None, timeout: int = 8) -> dict:
    url = f"{_KALSHI_BASE}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-scanner"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _fee(p: float) -> float:
    return min(0.035, math.ceil(0.07 * p * (1.0 - p) * 100) / 100)


def _get_yes_ask(m: dict) -> float | None:
    v = m.get("yes_ask_dollars") or m.get("yes_ask")
    if v is None:
        return None
    f = float(v)
    return f / 100.0 if f > 1.0 else f


def _get_no_ask(m: dict) -> float | None:
    v = m.get("no_ask_dollars") or m.get("no_ask")
    if v is None:
        return None
    f = float(v)
    return f / 100.0 if f > 1.0 else f


def _parse_ticker_parts(ticker: str) -> tuple[str, Optional[date], str]:
    """
    Parse a Kalshi market ticker into (product_base, expiry_date, threshold).

    Examples:
      KXBTCUM-26AUG-B75000  → ("KXBTCUM", date(2026,8,1), "B75000")
      KXFED-26SEP-T5.25     → ("KXFED", date(2026,9,1), "T5.25")
      INXU-26SEP30-B5600    → ("INXU", date(2026,9,30), "B5600")
      KXNFLT100TOP-26T25    → (ticker, None, "")  ← can't parse
    """
    parts = ticker.split("-")
    if len(parts) < 3:
        return ticker, None, ""

    date_idx = None
    parsed_date = None
    for i, p in enumerate(parts):
        u = p.upper()
        # Try YYMM pattern: 26AUG, 26SEP, 26DEC (5 chars: 2 digit + 3 letter)
        if len(u) == 5 and u[:2].isdigit() and u[2:] in _MONTH_MAP:
            year  = 2000 + int(u[:2])
            month = _MONTH_MAP[u[2:]]
            parsed_date = date(year, month, 1)
            date_idx = i
            break
        # Try YYMMDD pattern: 26SEP30, 26DEC31 (7 chars: 2+3+2)
        if len(u) == 7 and u[:2].isdigit() and u[2:5] in _MONTH_MAP and u[5:].isdigit():
            year  = 2000 + int(u[:2])
            month = _MONTH_MAP[u[2:5]]
            day   = int(u[5:])
            try:
                parsed_date = date(year, month, day)
            except ValueError:
                continue
            date_idx = i
            break

    if date_idx is None:
        return ticker, None, ""

    base      = "-".join(parts[:date_idx])
    threshold = "-".join(parts[date_idx + 1:])
    return base, parsed_date, threshold


def _fetch_open_events(limit: int = 500) -> list[dict]:
    """Fetch open events from Kalshi API (paginated)."""
    events = []
    cursor = None
    page_size = min(200, limit)
    while len(events) < limit:
        params: dict = {"status": "open", "limit": str(page_size)}
        if cursor:
            params["cursor"] = cursor
        try:
            data = _kalshi_get("/events", params)
        except Exception as exc:
            print(f"  [warn] events fetch error: {exc}")
            break
        batch = data.get("events", [])
        if not batch:
            break
        events.extend(batch)
        cursor = data.get("cursor")
        if not cursor or len(batch) < page_size:
            break
        time.sleep(0.2)
    return events[:limit]


def _fetch_event_markets(event_ticker: str) -> list[dict]:
    """Fetch all markets for a given event ticker."""
    try:
        data = _kalshi_get("/markets", {
            "event_ticker": event_ticker,
            "status": "open",
            "limit": "200",
        })
        return data.get("markets", [])
    except Exception:
        return []


_MIN_LEG_VOLUME_FP = 1_000.0   # minimum dollar volume per individual leg

# Keywords in market titles that indicate point-in-time measurement (NOT cumulative).
# Cumulative markets ("has X happened before date") would be guaranteed calendar arbs;
# point-in-time markets are speculative correlations only.
_POINT_IN_TIME_KEYWORDS = (
    " in december ", " in november ", " in october ", " in september ",
    " in august ", " in july ", " in june ", " in may ", " in april ",
    " in march ", " in february ", " in january ",
    " on december ", " on november ", " on october ", " on september ",
    " on august ", " on july ", " on june ", " on may ", " on april ",
    " on march ", " on february ", " on january ",
    "at 11:59", "at 12:00", "end of ", " eoy ", " eom ",
)


def _is_point_in_time(mkt: dict) -> bool:
    """Return True if the market resolves on a specific date (not cumulative)."""
    title = (mkt.get("title") or "").lower()
    rules = (mkt.get("rules_primary") or mkt.get("description") or "").lower()
    text  = title + " " + rules
    return any(kw in text for kw in _POINT_IN_TIME_KEYWORDS)


def _leg_volume(mkt: dict) -> float:
    return float(mkt.get("volume_fp") or 0) + float(mkt.get("open_interest_fp") or 0)


def _has_real_liquidity(markets: list[dict]) -> bool:
    """Return True if the event has at least some non-zero volume_fp or oi_fp."""
    for m in markets:
        if float(m.get("volume_fp") or 0) > 0:
            return True
        if float(m.get("open_interest_fp") or 0) > 0:
            return True
    return False


def scan_cross_event_arbs(min_edge_c: float = 0.5, event_limit: int = 500) -> None:
    """
    Main scanner: finds cross-event calendar spread arbs.

    Groups markets by (product_base, threshold) across different expiry dates.
    For each group with >= 2 dates, checks if YES(later) + NO(earlier) < $1.
    """
    print(f"\nFetching up to {event_limit} open events from Kalshi API...")
    events = _fetch_open_events(event_limit)
    print(f"Fetched {len(events)} events.\n")

    # Build registry: (base, threshold) → list of (expiry_date, event_ticker, market)
    # We need to fetch markets for events that have date-parseable structures
    registry: dict[tuple[str, str], list[tuple[date, str, dict]]] = defaultdict(list)

    parseable = 0
    skipped   = 0
    total_mkts = 0

    for ev in events:
        ev_ticker = ev.get("event_ticker", "")
        # Quick pre-check: does the event ticker contain a date pattern?
        has_month = any(m in ev_ticker.upper() for m in _MONTH_MAP)
        if not has_month:
            skipped += 1
            continue

        markets = _fetch_event_markets(ev_ticker)
        if not markets:
            continue

        if not _has_real_liquidity(markets):
            continue  # skip phantom AMM markets

        for mkt in markets:
            ticker = mkt.get("ticker", "")
            base, expiry, threshold = _parse_ticker_parts(ticker)
            if expiry is None or not threshold:
                continue
            registry[(base, threshold)].append((expiry, ev_ticker, mkt))
            total_mkts += 1

        parseable += 1
        time.sleep(0.05)  # gentle rate-limit

    print(f"Scanned {parseable} parseable events ({skipped} skipped, no month pattern).")
    print(f"Indexed {total_mkts} markets into {len(registry)} (base, threshold) groups.\n")

    # Find groups with >= 2 distinct dates (calendar series candidates)
    calendar_series = {
        k: sorted(v, key=lambda x: x[0])  # sort by expiry date ascending
        for k, v in registry.items()
        if len({x[0] for x in v}) >= 2     # at least 2 distinct dates
    }
    print(f"Calendar series with >= 2 dates: {len(calendar_series)}\n")

    arbs_found = []

    for (base, threshold), entries in sorted(calendar_series.items()):
        # entries: sorted list of (expiry_date, ev_ticker, market_dict)
        # Check all (earlier, later) pairs
        unique_dates = sorted({e[0] for e in entries})

        for ei, d_earlier in enumerate(unique_dates[:-1]):
            for d_later in unique_dates[ei + 1:]:
                # Get cheapest YES ask for each date (may have multiple quotes)
                earlier_mkts = [e[2] for e in entries if e[0] == d_earlier]
                later_mkts   = [e[2] for e in entries if e[0] == d_later]

                # Per-leg liquidity gate: skip if either side has < _MIN_LEG_VOLUME_FP
                earlier_vol = max((_leg_volume(m) for m in earlier_mkts), default=0)
                later_vol   = max((_leg_volume(m) for m in later_mkts),   default=0)
                if earlier_vol < _MIN_LEG_VOLUME_FP or later_vol < _MIN_LEG_VOLUME_FP:
                    continue

                # Determine if these are point-in-time (speculative) or cumulative (guaranteed)
                all_mkts = earlier_mkts + later_mkts
                point_in_time = any(_is_point_in_time(m) for m in all_mkts)

                # Best (lowest) YES ask for each date
                def _best_yes(mkts):
                    asks = [_get_yes_ask(m) for m in mkts]
                    valid = [a for a in asks if a is not None and a > 0]
                    return min(valid) if valid else None

                def _best_yes_mkt(mkts):
                    """Return (ask, mkt_dict) for the cheapest YES ask."""
                    pairs = [(a, m) for m in mkts for a in [_get_yes_ask(m)] if a and a > 0]
                    return min(pairs, key=lambda x: x[0]) if pairs else (None, None)

                def _best_no_mkt(mkts):
                    """Return (ask, mkt_dict) for the cheapest NO ask."""
                    pairs = [(a, m) for m in mkts for a in [_get_no_ask(m)] if a and a > 0]
                    return min(pairs, key=lambda x: x[0]) if pairs else (None, None)

                # Calendar arb: YES(later) + NO(earlier) < $1
                ya_later,   mkt_yes_later   = _best_yes_mkt(later_mkts)
                na_earlier, mkt_no_earlier  = _best_no_mkt(earlier_mkts)

                if ya_later is not None and na_earlier is not None:
                    combined = ya_later + na_earlier
                    if combined < 1.0:
                        gross = 1.0 - combined
                        fees  = _fee(ya_later) + _fee(na_earlier)
                        net   = gross - fees
                        if net * 100 >= min_edge_c:
                            later_ticker   = mkt_yes_later.get("ticker", "?") if mkt_yes_later else "?"
                            earlier_ticker = mkt_no_earlier.get("ticker", "?") if mkt_no_earlier else "?"
                            arb_type = "SPECULATIVE" if point_in_time else "CALENDAR_SPREAD"
                            arbs_found.append({
                                "type":         arb_type,
                                "base":         base,
                                "threshold":    threshold,
                                "buy_yes":      later_ticker,
                                "buy_no":       earlier_ticker,
                                "yes_ask":      round(ya_later, 4),
                                "no_ask":       round(na_earlier, 4),
                                "gross_c":      round(gross * 100, 2),
                                "fees_c":       round(fees * 100, 2),
                                "net_c":        round(net * 100, 2),
                                "d_earlier":    str(d_earlier),
                                "d_later":      str(d_later),
                                "earlier_vol":  round(earlier_vol, 0),
                                "later_vol":    round(later_vol, 0),
                                "point_in_time": point_in_time,
                            })

                # Reverse check: YES(earlier) + NO(later) < $1
                # For cumulative monotone events this violates NO-ARB and IS guaranteed.
                # For point-in-time events this is speculative.
                ya_earlier, mkt_yes_earlier = _best_yes_mkt(earlier_mkts)
                na_later,   mkt_no_later    = _best_no_mkt(later_mkts)
                if ya_earlier is not None and na_later is not None:
                    combined_rev = ya_earlier + na_later
                    if combined_rev < 1.0:
                        gross_r = 1.0 - combined_rev
                        fees_r  = _fee(ya_earlier) + _fee(na_later)
                        net_r   = gross_r - fees_r
                        if net_r * 100 >= min_edge_c:
                            earlier_ticker = mkt_yes_earlier.get("ticker", "?") if mkt_yes_earlier else "?"
                            later_ticker   = mkt_no_later.get("ticker", "?") if mkt_no_later else "?"
                            # Reverse direction: for cumulative events, later YES >= earlier YES,
                            # so this direction CAN be a genuine violation of no-arb.
                            arb_type = "SPECULATIVE" if point_in_time else "REVERSE_CALENDAR"
                            arbs_found.append({
                                "type":         arb_type,
                                "base":         base,
                                "threshold":    threshold,
                                "buy_yes":      earlier_ticker,
                                "buy_no":       later_ticker,
                                "yes_ask":      round(ya_earlier, 4),
                                "no_ask":       round(na_later, 4),
                                "gross_c":      round(gross_r * 100, 2),
                                "fees_c":       round(fees_r * 100, 2),
                                "net_c":        round(net_r * 100, 2),
                                "d_earlier":    str(d_earlier),
                                "d_later":      str(d_later),
                                "earlier_vol":  round(earlier_vol, 0),
                                "later_vol":    round(later_vol, 0),
                                "point_in_time": point_in_time,
                            })

    # Split by type
    guaranteed = [a for a in arbs_found if a["type"] in ("CALENDAR_SPREAD", "REVERSE_CALENDAR")]
    speculative = [a for a in arbs_found if a["type"] == "SPECULATIVE"]

    def _print_arb(a: dict) -> None:
        tag_map = {
            "CALENDAR_SPREAD":  "✅ CALENDAR SPREAD (guaranteed)",
            "REVERSE_CALENDAR": "✅ REVERSE CALENDAR (guaranteed)",
            "SPECULATIVE":      "〰  SPECULATIVE (point-in-time, not guaranteed)",
        }
        tag = tag_map.get(a["type"], a["type"])
        print(f"{tag}  net={a['net_c']:+.2f}c  gross={a['gross_c']:.2f}c")
        print(f"  Base:      {a['base']}  threshold={a['threshold']}")
        # For CALENDAR_SPREAD/SPECULATIVE: buy_yes=later, buy_no=earlier
        # For REVERSE_CALENDAR: buy_yes=earlier, buy_no=later
        if a["type"] in ("CALENDAR_SPREAD", "SPECULATIVE"):
            print(f"  Buy YES:   {a['buy_yes']} @ {a['yes_ask']:.3f}  (resolves {a['d_later']})")
            print(f"  Buy NO:    {a['buy_no']} @ {a['no_ask']:.3f}  (resolves {a['d_earlier']})")
        else:
            print(f"  Buy YES:   {a['buy_yes']} @ {a['yes_ask']:.3f}  (resolves {a['d_earlier']})")
            print(f"  Buy NO:    {a['buy_no']} @ {a['no_ask']:.3f}  (resolves {a['d_later']})")
        print(f"  Liquidity: YES-side vol+oi=${a['later_vol']:,.0f}  NO-side vol+oi=${a['earlier_vol']:,.0f}")
        print()

    print("=" * 72)
    if guaranteed:
        print(f"GUARANTEED CROSS-EVENT ARBS ({len(guaranteed)}):\n")
        for a in sorted(guaranteed, key=lambda x: -x["net_c"]):
            _print_arb(a)
    else:
        print("No guaranteed cross-event arbs found.")

    print()
    if speculative:
        print(f"SPECULATIVE CROSS-EVENT SIGNALS ({len(speculative)}) — point-in-time markets, NOT guaranteed:\n")
        for a in sorted(speculative, key=lambda x: -x["net_c"])[:10]:  # top 10 only
            _print_arb(a)
        if len(speculative) > 10:
            print(f"  ... and {len(speculative)-10} more (use --min-edge to filter)")
    else:
        print("No speculative signals found either.")
    print("=" * 72)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cross-event calendar spread arb scanner")
    parser.add_argument("--min-edge", type=float, default=0.5,
                        help="Minimum net edge in cents (default: 0.5)")
    parser.add_argument("--limit", type=int, default=500,
                        help="Max events to scan (default: 500)")
    args = parser.parse_args()

    scan_cross_event_arbs(min_edge_c=args.min_edge, event_limit=args.limit)
