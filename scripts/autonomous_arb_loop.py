"""
Autonomous Kalshi Arb Scanner
Continuously monitors ALL exhaustive ME events for CE arbs.
Logs every scan with evidence. Designed to run indefinitely.

Current state of the market (as of research):
- 11 strictly exhaustive events (8 GDP year, 2 cabinet picks, 1 catch-all)
- Best near-miss: KXNEXTSTATE-29 at -3.5c after fees (2.5c gross, 6c fees)
- 0 real arbs found across 14,000+ scans
- Complement arbs (YES+NO < $1) are structurally impossible
- Cross-market CE arbs can exist but fees (2-7c/leg) kill thin edges
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

ARB_LOG = os.path.join(LOG_DIR, "arb_log.jsonl")
SCAN_LOG = os.path.join(LOG_DIR, "scan_summary.jsonl")
NEAR_LOG = os.path.join(LOG_DIR, "near_miss_log.jsonl")
PRICE_LOG = os.path.join(LOG_DIR, "price_snapshots.jsonl")

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)

def fetch(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json", "User-Agent": "kalshi-arb-scanner"
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    return None

def fee(p):
    if p <= 0 or p >= 1: return 0
    return min(0.035, math.ceil(0.07 * p * (1 - p) * 100) / 100)

def is_truly_exhaustive(ev, active):
    """STRICT exhaustiveness. Returns (bool, reason)."""
    subs_lower = [m.get("yes_sub_title", "").lower().strip() for m in active]
    tickers = [m.get("ticker", "") for m in active]

    has_upper = any(re.search(r"or above|or more|or higher|or greater|\d+\.\d*\+|and above", s) for s in subs_lower)
    has_lower = any(re.search(r"or below|or less|or lower|or fewer|and below|0\.0% or below|negative|below 0", s) for s in subs_lower)

    range_markets = []
    for s in subs_lower:
        m = re.search(r"(\d+\.?\d*)\s*(%\s*)?(to|-|–)\s*(\d+\.?\d*)", s)
        if m:
            range_markets.append((float(m.group(1)), float(m.group(4))))

    if len(range_markets) >= 2 and has_upper and has_lower:
        range_markets.sort()
        contiguous = True
        for i in range(len(range_markets) - 1):
            gap = range_markets[i + 1][0] - range_markets[i][1]
            step = range_markets[0][1] - range_markets[0][0]
            if gap > step * 0.5 + 0.01:
                contiguous = False
                break
        if contiguous:
            return True, f"contiguous_ranges ({len(range_markets)} ranges + both bounds)"

    genuine_catchall_patterns = [
        r"^other$", r"^someone else$", r"^none of the above$",
        r"^none of these$", r"^another person$", r"^no other person$",
        r"^different person$", r"^anyone else$",
    ]
    has_genuine_catchall = any(
        any(re.match(pat, s) for pat in genuine_catchall_patterns)
        for s in subs_lower
    )
    if has_genuine_catchall:
        return True, "genuine_catchall"

    nums = []
    for tk in tickers:
        last = tk.rsplit("-", 1)[-1] if "-" in tk else ""
        found = re.findall(r"\d+\.?\d*", last)
        if found:
            nums.append(float(found[-1]))

    if len(nums) >= 4 and has_upper and has_lower:
        nums.sort()
        diffs = [round(nums[i+1] - nums[i], 4) for i in range(len(nums)-1)]
        if diffs:
            mode = max(set([round(d, 2) for d in diffs]), key=lambda x: [round(d, 2) for d in diffs].count(x))
            consistent = sum(1 for d in diffs if abs(round(d, 2) - mode) < 0.01)
            if consistent >= len(diffs) * 0.6:
                return True, f"contiguous_strikes ({len(nums)} strikes, step={mode})"

    return False, "not_exhaustive"


def scan_once():
    t0 = time.time()
    events = []
    cursor = None
    for _ in range(30):
        url = f"{BASE}/events?status=open&with_nested_markets=true&limit=200"
        if cursor: url += f"&cursor={cursor}"
        data = fetch(url)
        if not data: break
        batch = data.get("events", [])
        if not batch: break
        events.extend(batch)
        cursor = data.get("cursor")
        if not cursor: break
        time.sleep(0.2)

    arbs = []
    near_misses = []
    price_snaps = []
    stats = {"events": len(events), "me": 0, "exhaustive": 0}

    for ev in events:
        if not ev.get("mutually_exclusive"): continue
        active = [m for m in ev.get("markets", []) if m.get("status") in ("active", "open")]
        if len(active) < 2: continue
        stats["me"] += 1

        et = ev.get("event_ticker", "")
        title = ev.get("title", "")
        is_exh, reason = is_truly_exhaustive(ev, active)
        if not is_exh: continue
        stats["exhaustive"] += 1

        prices = []
        for m in active:
            ya = float(m.get("yes_ask_dollars") or 0)
            prices.append({
                "ticker": m.get("ticker", ""),
                "sub": m.get("yes_sub_title", ""),
                "ya": ya,
                "yb": float(m.get("yes_bid_dollars") or 0),
                "vol": float(m.get("volume_fp", 0) or 0),
                "oi": float(m.get("open_interest_fp", 0) or 0),
            })

        all_priced = all(p["ya"] > 0 for p in prices)
        if not all_priced: continue

        total = sum(p["ya"] for p in prices)
        fees = sum(fee(p["ya"]) for p in prices)
        net = (1.0 - total - fees) * 100
        tvol = sum(p["vol"] for p in prices)
        toi = sum(p["oi"] for p in prices)

        # Record price snapshot for time-series
        price_snaps.append({
            "event": et, "total_ya": round(total, 4),
            "net_c": round(net, 2), "gross_c": round((1 - total) * 100, 2),
        })

        rec = {
            "event": et, "title": title, "legs": len(active),
            "total_cost": round(total, 4),
            "fees_c": round(fees * 100, 2),
            "net_c": round(net, 2),
            "gross_c": round((1 - total) * 100, 2),
            "reason": reason,
            "volume": round(tvol, 2), "oi": round(toi, 2),
            "prices": {p["ticker"]: {"sub": p["sub"], "ya": round(p["ya"], 4)} for p in prices},
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }

        if net > 0:
            arbs.append(rec)
        elif net > -10:
            near_misses.append(rec)

    stats["elapsed"] = round(time.time() - t0, 1)
    return arbs, near_misses, price_snaps, stats


def main():
    log("Autonomous Arb Scanner starting")
    cycle = 0
    total_arbs = 0

    while True:
        cycle += 1
        try:
            arbs, near_misses, prices, stats = scan_once()
        except Exception as e:
            log(f"Error: {e}")
            time.sleep(15)
            continue

        total_arbs += len(arbs)
        log(f"#{cycle}: {stats['events']}ev {stats['me']}ME {stats['exhaustive']}exh | "
            f"{len(arbs)}arb {len(near_misses)}near | {stats['elapsed']:.0f}s | total={total_arbs}")

        if arbs:
            for a in sorted(arbs, key=lambda x: -x["net_c"]):
                log(f"  *** ARB +{a['net_c']:.2f}c  {a['event']}: {a['title'][:50]}")
                log(f"      sum=${a['total_cost']} fees={a['fees_c']}c [{a['reason']}]")
                for tk, info in sorted(a["prices"].items(), key=lambda x: -x[1]["ya"])[:6]:
                    log(f"      {tk}: ${info['ya']:.4f} ({info['sub']})")
                with open(ARB_LOG, "a") as f:
                    f.write(json.dumps(a) + "\n")

        if near_misses and (cycle <= 3 or cycle % 10 == 0):
            for nm in sorted(near_misses, key=lambda x: -x["net_c"])[:3]:
                log(f"  near {nm['net_c']:+.1f}c  {nm['event']}: {nm['title'][:45]}")

        # Log scan
        with open(SCAN_LOG, "a") as f:
            f.write(json.dumps({
                "cycle": cycle, "time": datetime.now(timezone.utc).isoformat(),
                **stats, "arbs": len(arbs), "near": len(near_misses),
                "total_arbs": total_arbs,
            }) + "\n")

        # Log prices every cycle for time-series
        with open(PRICE_LOG, "a") as f:
            for p in prices:
                p["cycle"] = cycle
                p["time"] = datetime.now(timezone.utc).isoformat()
                f.write(json.dumps(p) + "\n")

        # Log near misses periodically
        if near_misses and cycle % 10 == 0:
            with open(NEAR_LOG, "a") as f:
                for nm in near_misses:
                    f.write(json.dumps(nm) + "\n")

        wait = max(5, 30 - stats["elapsed"])
        time.sleep(wait)


if __name__ == "__main__":
    main()
