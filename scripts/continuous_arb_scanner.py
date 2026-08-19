"""
Continuous Kalshi Arbitrage Scanner

Scans every cycle for real arbitrage opportunities and logs everything with evidence.

KEY FINDINGS (from research):
1. COMPLEMENT ARBS (YES+NO < $1 on single market) are STRUCTURALLY IMPOSSIBLE
   at rest. Kalshi's matching engine ensures YES_bid + NO_bid <= $1 - tick,
   so YES_ask + NO_ask >= $1 + tick. The orderbook API returns BIDS
   (yes_dollars = YES buy orders, no_dollars = NO buy orders), and asks are
   derived: best_YES_ask = 1 - max(NO_bid), best_NO_ask = 1 - max(YES_bid).

2. CROSS-MARKET ARBS (CE/ME on multi-outcome events) CAN exist because each
   market has its own independent order book with no cross-market matching.

3. Most apparent CE arbs are FAKE: the listed outcomes aren't exhaustive.
   "Who will be the next Pope?" lists 7 of thousands of candidates.
   Only events with catch-all markets or proven contiguous ranges are valid.

STRATEGY:
- Scan all ME events for CE arbs (buy all YES < $1)
- Strictly validate exhaustiveness before reporting
- Log near-misses to catch fleeting opportunities
- Record everything with timestamps for research
"""
import json
import math
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.elections.kalshi.com/trade-api/v2"
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
os.makedirs(LOG_DIR, exist_ok=True)

def fetch(url, retries=2):
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "kalshi-arb-scanner"}
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            if attempt < retries - 1:
                time.sleep(1)
    return None

def fee(p):
    """Kalshi fee: min(3.5c, ceil(7% * p * (1-p) * 100) / 100)"""
    if p <= 0 or p >= 1:
        return 0
    return min(0.035, math.ceil(0.07 * p * (1 - p) * 100) / 100)

def classify_exhaustiveness(ev, active):
    """Strict exhaustiveness classification.
    Returns (level, reason) where level is VALIDATED/CATCHALL/PARTIAL/UNVALIDATED."""
    subtitles = [m.get("yes_sub_title", "").lower().strip() for m in active]

    # Catch-all detection
    catchall_kw = [
        "other", "none of the above", "none of these", "someone else",
        "no other", "another person", "no one", "nobody", "no change",
        "remains", "stays", "different", "not nine", "not eight",
        "not seven", "not six", "fewer than", "less than",
    ]
    has_catchall = False
    for sub in subtitles:
        if any(kw in sub for kw in catchall_kw):
            has_catchall = True
            break

    # Range bound detection
    has_upper = any(re.search(r"(or )?above|or more|or higher|\d+\+|or over|or greater", s) for s in subtitles)
    has_lower = any(re.search(r"(or )?below|or less|or lower|or fewer|under|negative", s) for s in subtitles)

    # Contiguous numeric strikes
    tickers = [m.get("ticker", "") for m in active]
    nums = []
    for tk in tickers:
        parts = tk.rsplit("-", 1)
        if len(parts) == 2:
            found = re.findall(r"\d+\.?\d*", parts[-1])
            if found:
                nums.append(float(found[-1]))
    nums.sort()
    has_contiguous = False
    if len(nums) >= 3:
        diffs = [round(nums[i+1] - nums[i], 4) for i in range(len(nums)-1)]
        if diffs:
            mode = max(set([round(d, 2) for d in diffs]), key=lambda x: [round(d, 2) for d in diffs].count(x))
            matches = sum(1 for d in diffs if abs(round(d, 2) - mode) < 0.01)
            has_contiguous = matches >= len(diffs) * 0.7

    if has_contiguous and has_upper and has_lower:
        return "VALIDATED", "contiguous_both_bounds"
    if has_catchall and has_contiguous:
        return "VALIDATED", "contiguous_catchall"
    if has_catchall:
        return "CATCHALL", "has_catchall"
    if has_contiguous and (has_upper or has_lower):
        return "PARTIAL", "contiguous_one_bound"
    return "UNVALIDATED", "no_proof"

def ya(m):
    v = m.get("yes_ask_dollars") or m.get("yes_ask") or 0
    return float(v) if v else 0.0

def na(m):
    v = m.get("no_ask_dollars") or m.get("no_ask") or 0
    return float(v) if v else 0.0

def yb(m):
    v = m.get("yes_bid_dollars") or m.get("yes_bid") or 0
    return float(v) if v else 0.0

def mvol(m):
    return float(m.get("volume_fp", 0) or m.get("volume", 0) or 0)

def moi(m):
    return float(m.get("open_interest_fp", 0) or m.get("open_interest", 0) or 0)

def scan_once():
    """Run one full scan. Returns (arbs, near_misses, stats)."""
    t0 = time.time()
    events = []
    cursor = None
    for _ in range(30):
        url = f"{BASE}/events?status=open&with_nested_markets=true&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        data = fetch(url)
        if not data:
            break
        batch = data.get("events", [])
        if not batch:
            break
        events.extend(batch)
        cursor = data.get("cursor")
        if not cursor:
            break

    arbs = []
    near_misses = []
    stats = {"events": len(events), "me_events": 0, "validated": 0, "catchall": 0}

    for ev in events:
        mkts = ev.get("markets", [])
        if not ev.get("mutually_exclusive", False):
            continue
        active = [m for m in mkts if m.get("status") in ("active", "open")]
        if len(active) < 2:
            continue

        stats["me_events"] += 1
        et = ev.get("event_ticker", "")
        title = ev.get("title", "")
        classification, reason = classify_exhaustiveness(ev, active)

        if classification == "VALIDATED":
            stats["validated"] += 1
        elif classification == "CATCHALL":
            stats["catchall"] += 1

        tvol = sum(mvol(m) for m in active)
        toi = sum(moi(m) for m in active)

        # CE arb: sum of YES asks < $1
        priced = [(m, ya(m)) for m in active if ya(m) > 0]
        if len(priced) == len(active) and len(priced) >= 2:
            total_ya = sum(p for _, p in priced)
            if total_ya < 1.10:
                fees = sum(fee(p) for _, p in priced)
                net = (1.0 - total_ya - fees) * 100
                rec = {
                    "strategy": "CE",
                    "event": et,
                    "title": title,
                    "legs": len(priced),
                    "total_cost": round(total_ya, 4),
                    "fees_c": round(fees * 100, 2),
                    "net_c": round(net, 2),
                    "gross_c": round((1.0 - total_ya) * 100, 2),
                    "volume": round(tvol, 2),
                    "open_interest": round(toi, 2),
                    "liquid": tvol > 0 or toi > 0,
                    "classification": classification,
                    "reason": reason,
                    "outcomes": [m.get("yes_sub_title", "") for m in active],
                    "tickers": [m.get("ticker", "") for m, _ in priced],
                    "prices": {m.get("ticker", ""): round(p, 4) for m, p in priced},
                }
                if net > 0 and classification in ("VALIDATED", "CATCHALL"):
                    arbs.append(rec)
                elif net > -10 and classification in ("VALIDATED", "CATCHALL"):
                    near_misses.append(rec)

        # ME arb: sum of NO asks < N-1
        no_prices = []
        for m in active:
            n = na(m)
            if n <= 0:
                y = yb(m)
                n = 1.0 - y if y > 0 else 0
            if n > 0:
                no_prices.append((m, n))
        if len(no_prices) >= 2:
            nl = len(no_prices)
            total_na = sum(p for _, p in no_prices)
            threshold = nl - 1
            if total_na < threshold + 0.10:
                fees = sum(fee(p) for _, p in no_prices)
                net = (threshold - total_na - fees) * 100
                rec = {
                    "strategy": "ME",
                    "event": et,
                    "title": title,
                    "legs": nl,
                    "total_cost": round(total_na, 4),
                    "fees_c": round(fees * 100, 2),
                    "net_c": round(net, 2),
                    "gross_c": round((threshold - total_na) * 100, 2),
                    "volume": round(tvol, 2),
                    "open_interest": round(toi, 2),
                    "liquid": tvol > 0 or toi > 0,
                    "classification": classification,
                    "reason": reason,
                    "outcomes": [m.get("yes_sub_title", "") for m in active],
                    "tickers": [m.get("ticker", "") for m, _ in no_prices],
                }
                if net > 0 and classification in ("VALIDATED", "CATCHALL"):
                    arbs.append(rec)
                elif net > -10 and classification in ("VALIDATED", "CATCHALL"):
                    near_misses.append(rec)

    stats["elapsed"] = round(time.time() - t0, 1)
    return arbs, near_misses, stats

def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S UTC')}] Starting continuous arb scanner", flush=True)
    print(f"Logging to {LOG_DIR}/", flush=True)

    cycle = 0
    total_arbs_found = 0
    arb_log = os.path.join(LOG_DIR, "arb_log.jsonl")
    summary_log = os.path.join(LOG_DIR, "scan_summary.jsonl")

    while True:
        cycle += 1
        scan_time = datetime.now(timezone.utc)

        try:
            arbs, near_misses, stats = scan_once()
        except Exception as e:
            print(f"[{scan_time.strftime('%H:%M:%S')}] Scan error: {e}", flush=True)
            time.sleep(30)
            continue

        total_arbs_found += len(arbs)

        status = (f"[{scan_time.strftime('%H:%M:%S')}] #{cycle}: "
                  f"{stats['events']} events, {stats['me_events']} ME, "
                  f"{stats['validated']}+{stats['catchall']} exhaustive | "
                  f"{len(arbs)} arbs, {len(near_misses)} near | "
                  f"{stats['elapsed']}s")
        print(status, flush=True)

        if arbs:
            for a in arbs:
                print(f"  *** ARB [{a['strategy']}] +{a['net_c']:.1f}c  "
                      f"{a['event']}: {a['title'][:50]}  "
                      f"[{a['classification']}]", flush=True)
                a["detected_at"] = scan_time.isoformat()
                with open(arb_log, "a") as f:
                    f.write(json.dumps(a) + "\n")

        if near_misses and cycle <= 3:
            for nm in sorted(near_misses, key=lambda x: -x["net_c"])[:5]:
                print(f"  near: [{nm['strategy']}] {nm['net_c']:+.1f}c  "
                      f"{nm['event']}: {nm['title'][:50]}", flush=True)

        # Log summary
        summary = {
            "cycle": cycle,
            "time": scan_time.isoformat(),
            "events": stats["events"],
            "me_events": stats["me_events"],
            "validated": stats["validated"],
            "catchall": stats["catchall"],
            "arbs": len(arbs),
            "near_misses": len(near_misses),
            "elapsed": stats["elapsed"],
            "total_arbs_cumulative": total_arbs_found,
        }
        with open(summary_log, "a") as f:
            f.write(json.dumps(summary) + "\n")

        # Near-miss details logged every 10th cycle
        if near_misses and cycle % 10 == 0:
            nm_log = os.path.join(LOG_DIR, "near_miss_log.jsonl")
            with open(nm_log, "a") as f:
                for nm in near_misses:
                    nm["detected_at"] = scan_time.isoformat()
                    f.write(json.dumps(nm) + "\n")

        # Wait before next scan
        wait = max(5, 30 - stats["elapsed"])
        time.sleep(wait)

if __name__ == "__main__":
    main()
