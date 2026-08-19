"""
scripts/rest_scan_and_commit.py
================================
Standalone CE/ME/TH arb scanner for GitHub Actions.

Zero project imports — uses only stdlib + the public Kalshi REST API (no auth).
Discovers events, scans for collectively-exhaustive, mutually-exclusive, and
threshold-monotonicity arbs, double-verifies, and appends results to
data/live_arb_log.jsonl.  The GitHub Action commits that file so arb history
persists across Streamlit Cloud reboots.

Runs in ~30-60 seconds (no WebSocket warmup).

Usage (GitHub Actions):
    python scripts/rest_scan_and_commit.py

Environment variables (all optional):
    MIN_NET_CENTS  — minimum net edge to record (default 0.5)
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rest_scan_and_commit")

_KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = _ROOT / "data" / "live_arb_log.jsonl"

MIN_NET_CENTS = float(os.environ.get("MIN_NET_CENTS", "0.5"))
_MIN_LEGS = 2
_MAX_LEGS = 200

_CE_SERIES = {
    "KXBOC", "KXFEDDECISION", "INXU", "NASDAQ100U", "KXBTC",
    "KXWTI", "CPICORE", "CPI", "UNEMP", "NFP", "KXETH",
    "KXGOLD", "KXSILVER", "KXSP500", "GDP",
}

_SPORTS_PFX = (
    "KXLALIGA", "KXLIGA", "KXNBA", "KXNFL", "KXMLB", "KXNHL",
    "KXEPL", "KXSERIEA", "KXBUNDES", "KXMLS", "KXUCL", "KXUEFA",
    "KXNCAAF", "KXNCAAB", "KXWNBA", "KXPGA", "KXGOLF",
    "KXTENNIS", "KXATP", "KXWTA", "KXFORMULA", "KXF1",
    "KXUFC", "KXBOXING", "KXMMA", "KXNASCAR", "KXINDYCAR",
    "KXSOCCER", "KXCRICKET", "KXRUGBY", "KXOLYMPIC", "KXOLYM",
)

_CATCH_ALL_KEYWORDS = (
    "other", "field", "none of", "no one", "nobody",
    "someone else", "another", "any other",
)


def _fee(p: float) -> float:
    return min(0.035, math.ceil(0.07 * p * (1.0 - p) * 100) / 100)


def _series_prefix(event_ticker: str) -> str:
    parts = event_ticker.split("-")
    if len(parts) >= 2:
        last = parts[-1]
        if re.match(r'^\d{2}[A-Z]{3}', last):
            return "-".join(parts[:-1])
    return event_ticker


def _is_sports(ticker: str) -> bool:
    return any(ticker.upper().startswith(p) for p in _SPORTS_PFX)


def _ya(m: dict) -> float:
    v = m.get("yes_ask_dollars") or m.get("yes_ask") or "0"
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _na(m: dict) -> float:
    v = m.get("no_ask_dollars") or m.get("no_ask") or "0"
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _yb(m: dict) -> float:
    v = m.get("yes_bid_dollars") or m.get("yes_bid") or "0"
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _vol(m: dict) -> float:
    return float(m.get("volume_fp") or 0)


def _oi(m: dict) -> float:
    return float(m.get("open_interest_fp") or 0)


def _numeric_suffix(ticker: str) -> float:
    last = ticker.rsplit("-", 1)[-1]
    nums = re.findall(r"\d+\.?\d*", last)
    return float(nums[-1]) if nums else -1.0


def _fetch_json(url: str, retries: int = 2, timeout: int = 10) -> dict | None:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "kalshi-arb-gha",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as he:
            if he.code == 429:
                time.sleep(float(he.headers.get("Retry-After", "2")))
                continue
            if he.code in (503, 502, 500) and attempt < retries - 1:
                time.sleep(1)
                continue
            return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(0.5)
                continue
            return None
    return None


def _is_truly_ce(event: dict, markets: list) -> bool:
    series = _series_prefix(event.get("event_ticker", ""))
    if series in _CE_SERIES:
        return True

    strikes = []
    for m in markets:
        fs, cs = m.get("floor_strike"), m.get("cap_strike")
        if fs is not None and cs is not None:
            try:
                strikes.append((float(fs), float(cs)))
            except (TypeError, ValueError):
                pass

    if strikes:
        all_discrete = all(f == c for f, c in strikes)
        if not all_discrete and len(strikes) >= 2:
            strikes.sort()
            contiguous = all(
                abs(strikes[i][1] - strikes[i + 1][0]) < 0.01
                for i in range(len(strikes) - 1)
            )
            if contiguous:
                return True

    for m in markets:
        text = (
            (m.get("title") or "") + " " +
            (m.get("yes_sub_title") or "") + " " +
            (m.get("no_sub_title") or "")
        ).lower()
        if any(kw in text for kw in _CATCH_ALL_KEYWORDS):
            return True

    return False


def _scan_event(event: dict, markets: list) -> list[dict]:
    arbs: list[dict] = []
    event_ticker = event.get("event_ticker", "")
    me = event.get("mutually_exclusive", False)
    series = _series_prefix(event_ticker)

    all_active = [m for m in markets if m.get("status") in ("active", "open")]
    if len(all_active) < _MIN_LEGS or len(all_active) > _MAX_LEGS:
        return arbs

    priced = [m for m in all_active if _ya(m) > 0]
    liq = sum(_vol(m) + _oi(m) for m in all_active)

    # CE: sum(YES asks) < $1
    if (me or series in _CE_SERIES) and len(priced) >= 2 and _is_truly_ce(event, all_active):
        if len(priced) == len(all_active):
            yes_asks = [(m.get("ticker", ""), _ya(m)) for m in priced]
            total_ya = sum(ya for _, ya in yes_asks)
            if total_ya < 1.0:
                gross = 1.0 - total_ya
                fees = sum(_fee(ya) for _, ya in yes_asks)
                net = gross - fees
                if net * 100 >= MIN_NET_CENTS:
                    arbs.append({
                        "strategy": "collectively_exhaustive",
                        "event_ticker": event_ticker,
                        "tickers": [t for t, _ in yes_asks],
                        "n_legs": len(yes_asks),
                        "gross_edge_cents": round(gross * 100, 2),
                        "fees_cents": round(fees * 100, 2),
                        "net_edge_cents": round(net * 100, 2),
                        "liquidity": liq,
                    })

    # ME: sum(NO asks) < N-1
    if me and len(priced) >= 2:
        no_asks = []
        for m in priced:
            na_val = _na(m)
            if na_val <= 0:
                yb_val = _yb(m)
                na_val = 1.0 - yb_val if yb_val > 0 else 1.0
            if na_val > 0:
                no_asks.append((m.get("ticker", ""), na_val))
        if len(no_asks) >= 2:
            n = len(no_asks)
            total_na = sum(na for _, na in no_asks)
            if total_na < n - 1:
                gross = (n - 1) - total_na
                fees = sum(_fee(na) for _, na in no_asks)
                net = gross - fees
                if net * 100 >= MIN_NET_CENTS:
                    arbs.append({
                        "strategy": "mutually_exclusive",
                        "event_ticker": event_ticker,
                        "tickers": [t for t, _ in no_asks],
                        "n_legs": n,
                        "gross_edge_cents": round(gross * 100, 2),
                        "fees_cents": round(fees * 100, 2),
                        "net_edge_cents": round(net * 100, 2),
                        "liquidity": liq,
                    })

    # TH: threshold monotonicity violations
    if len(priced) >= 2:
        with_suffix = []
        for m in priced:
            ns = _numeric_suffix(m.get("ticker", ""))
            if ns >= 0:
                with_suffix.append((ns, m.get("ticker", ""), _ya(m), m))
        if len(with_suffix) >= 2:
            with_suffix.sort(key=lambda x: x[0])
            for i in range(len(with_suffix)):
                for j in range(i + 1, len(with_suffix)):
                    _, tk_i, ya_i, m_i = with_suffix[i]
                    _, tk_j, ya_j, m_j = with_suffix[j]
                    if ya_i <= ya_j:
                        continue
                    na_j = _na(m_j)
                    if na_j <= 0:
                        yb_j = _yb(m_j)
                        na_j = 1.0 - yb_j if yb_j > 0 else 1.0
                    cost = ya_i + na_j
                    if cost >= 1.0:
                        continue
                    gross = 1.0 - cost
                    fees = _fee(ya_i) + _fee(na_j)
                    net = gross - fees
                    if net * 100 >= MIN_NET_CENTS:
                        arbs.append({
                            "strategy": "threshold_order",
                            "event_ticker": event_ticker,
                            "tickers": [tk_i, tk_j],
                            "n_legs": 2,
                            "gross_edge_cents": round(gross * 100, 2),
                            "fees_cents": round(fees * 100, 2),
                            "net_edge_cents": round(net * 100, 2),
                            "liquidity": _vol(m_i) + _vol(m_j) + _oi(m_i) + _oi(m_j),
                        })

    return arbs


def _verify_arb(opp: dict) -> dict | None:
    et = opp["event_ticker"]
    data = _fetch_json(f"{_KALSHI_BASE}/events/{et}?with_nested_markets=true", retries=1, timeout=8)
    if not data:
        return None
    event = data.get("event", data)
    mkts = event.get("markets", [])
    if not mkts:
        return None
    re_arbs = _scan_event(event, mkts)
    for a in re_arbs:
        if a["strategy"] == opp["strategy"]:
            a["verified"] = True
            return a
    return None


def _discover_events() -> list[dict]:
    """Fetch all open events with nested markets from Kalshi public API."""
    events = []
    cursor = None
    for _ in range(20):
        url = f"{_KALSHI_BASE}/events?status=open&with_nested_markets=true&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        data = _fetch_json(url, retries=2, timeout=15)
        if not data:
            break
        batch = data.get("events", [])
        if not batch:
            break
        events.extend(batch)
        cursor = data.get("cursor")
        if not cursor:
            break
        logger.info("  … %d events fetched", len(events))
    return events


def _load_existing() -> set[str]:
    """Load existing arb keys to deduplicate."""
    keys: set[str] = set()
    if not OUTPUT_FILE.exists():
        return keys
    try:
        for line in OUTPUT_FILE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts = rec.get("detected_at", "")[:16]
                key = f"{rec.get('strategy','')}:{rec.get('event_ticker','')}:{ts}"
                keys.add(key)
            except Exception:
                pass
    except Exception:
        pass
    return keys


def main() -> int:
    logger.info("REST arb scanner starting (CE/ME/TH via public Kalshi API)")
    t0 = time.time()

    events = _discover_events()
    logger.info("Discovered %d open events in %.1fs", len(events), time.time() - t0)

    if not events:
        logger.error("No events found — cannot scan.")
        return 1

    existing_keys = _load_existing()
    logger.info("Existing arb log has %d entries", len(existing_keys))

    raw_arbs: list[dict] = []
    for ev in events:
        mkts = ev.get("markets", [])
        if not mkts:
            continue
        et = ev.get("event_ticker", "")
        if _is_sports(et):
            continue
        found = _scan_event(ev, mkts)
        raw_arbs.extend(found)

    logger.info("Raw scan found %d potential arbs in %.1fs", len(raw_arbs), time.time() - t0)

    verified: list[dict] = []
    for opp in raw_arbs:
        ts = datetime.now(timezone.utc).isoformat()[:16]
        key = f"{opp['strategy']}:{opp['event_ticker']}:{ts}"
        if key in existing_keys:
            continue
        confirmed = _verify_arb(opp)
        if confirmed:
            confirmed["detected_at"] = datetime.now(timezone.utc).isoformat()
            confirmed["source"] = "github_actions_rest"
            verified.append(confirmed)
            logger.info("VERIFIED  %s  %s  net=%.2fc",
                        confirmed["strategy"], confirmed["event_ticker"],
                        confirmed["net_edge_cents"])

    logger.info("Verified %d arbs in %.1fs total", len(verified), time.time() - t0)

    if not verified:
        logger.info("No new arbs detected this run.")
        return 0

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        for rec in verified:
            f.write(json.dumps(rec) + "\n")
    logger.info("Wrote %d records → %s", len(verified), OUTPUT_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
