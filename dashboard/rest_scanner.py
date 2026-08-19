"""
dashboard/rest_scanner.py
=========================
REST-based arb scanner — polls Kalshi public API as a fallback when the
Synthesis WebSocket is unreliable.  Runs alongside the WS scanner; both
push to the same LiveState arb queue.

Scans for:
  CE — sum(YES asks) < $1 across all legs of an event
  ME — sum(NO asks)  < N-1 across all legs
  TH — monotonicity violations in threshold markets

No auth required — uses the public Kalshi trading API.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_KALSHI_EVENTS_URL = "https://api.elections.kalshi.com/trade-api/v2/events"
_KALSHI_MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"

_POLL_INTERVAL_S = 30
_MIN_NET_CENTS = 0.02
_MIN_LEGS = 2
_MAX_LEGS = 25

# Series known to be CE-eligible (threshold/numeric markets)
_CE_SERIES = {
    "KXBOC", "KXFEDDECISION", "INXU", "NASDAQ100U", "KXBTC",
    "KXWTI", "CPICORE", "CPI", "UNEMP", "NFP",
}

_SPORTS_PFX = (
    "KXLALIGA", "KXLIGA", "KXNBA", "KXNFL", "KXMLB", "KXNHL",
    "KXEPL", "KXSERIEA", "KXBUNDES", "KXMLS", "KXUCL", "KXUEFA",
    "KXNCAAF", "KXNCAAB", "KXWNBA", "KXPGA", "KXGOLF",
    "KXTENNIS", "KXATP", "KXWTA", "KXFORMULA", "KXF1",
    "KXUFC", "KXBOXING", "KXMMA", "KXNASCAR", "KXINDYCAR",
    "KXSOCCER", "KXCRICKET", "KXRUGBY", "KXOLYMPIC", "KXOLYM",
    "KXATPSS", "KXTENNISSPREAD", "KXATPSSPREAD",
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
    u = ticker.upper()
    return any(u.startswith(p) for p in _SPORTS_PFX)


def _ya_dollars(m: dict) -> float:
    """Extract YES ask as a float from Kalshi API market dict."""
    v = m.get("yes_ask_dollars") or m.get("yes_ask") or "0"
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _na_dollars(m: dict) -> float:
    """Extract NO ask as a float from Kalshi API market dict."""
    v = m.get("no_ask_dollars") or m.get("no_ask") or "0"
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _yb_dollars(m: dict) -> float:
    """Extract YES bid as a float from Kalshi API market dict."""
    v = m.get("yes_bid_dollars") or m.get("yes_bid") or "0"
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fetch_events_page(cursor: str = "", limit: int = 100) -> dict:
    url = f"{_KALSHI_EVENTS_URL}?status=open&limit={limit}&with_nested_markets=true"
    if cursor:
        url += f"&cursor={cursor}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "kalshi-arb-rest-scanner",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as he:
            if he.code in (503, 429) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    return {"events": []}


def _make_opp(strategy: str, event_ticker: str, tickers: list,
              prices: dict, n_legs: int, gross: float, fees: float, net: float) -> dict:
    return {
        "strategy": strategy,
        "event_ticker": event_ticker,
        "tickers": tickers,
        "prices": prices,
        "n_legs": n_legs,
        "gross_edge_cents": gross,
        "total_fees_cents": fees,
        "net_edge_cents": net,
        "source": "rest_api",
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "detected_at_ts": time.time(),
    }


def _numeric_suffix(ticker: str) -> float:
    last = ticker.rsplit("-", 1)[-1]
    nums = re.findall(r"\d+\.?\d*", last)
    return float(nums[-1]) if nums else -1.0


def _is_maintenance() -> bool:
    """Kalshi maintenance window: 3-6 AM ET daily."""
    try:
        from datetime import timezone as _tz
        from zoneinfo import ZoneInfo
        et = datetime.now(ZoneInfo("America/New_York"))
        return 3 <= et.hour < 6
    except Exception:
        return False


def _fetch_json(url: str, retries: int = 3) -> Optional[dict]:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "kalshi-arb-rest-scanner",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as he:
            if he.code in (503, 429) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            return None
    return None


def _scan_event(event: dict, markets: List[dict]) -> List[Dict[str, Any]]:
    """Scan a single event's markets for CE/ME/TH arbs."""
    arbs: List[Dict[str, Any]] = []
    event_ticker = event.get("event_ticker", "")
    me = event.get("mutually_exclusive", False)
    series = _series_prefix(event_ticker)

    open_markets = [
        m for m in markets
        if m.get("status") in ("active", "open")
        and _ya_dollars(m) > 0
    ]
    if len(open_markets) < _MIN_LEGS or len(open_markets) > _MAX_LEGS:
        return arbs

    # Count total active legs (including unpriced) to validate CE completeness
    all_active = [m for m in markets if m.get("status") in ("active", "open")]

    # -- CE scan: sum(YES asks) < $1 --
    # Only valid if ALL active legs have YES asks (otherwise sum is artificially low)
    if (me or series in _CE_SERIES) and len(open_markets) >= 2:
        if len(open_markets) == len(all_active):  # all legs priced
            yes_asks = [(m.get("ticker", ""), _ya_dollars(m)) for m in open_markets]
            total_ya = sum(ya for _, ya in yes_asks)
            if total_ya < 1.0:
                gross = 1.0 - total_ya
                fees = sum(_fee(ya) for _, ya in yes_asks)
                net = gross - fees
                if net >= _MIN_NET_CENTS:
                    arbs.append(_make_opp("collectively_exhaustive", event_ticker,
                        [t for t, _ in yes_asks],
                        {t: round(ya * 100, 1) for t, ya in yes_asks},
                        len(yes_asks), round(gross * 100, 2),
                        round(fees * 100, 2), round(net * 100, 2)))

    # -- ME scan: sum(NO asks) < N-1 --
    if me and len(open_markets) >= 2:
        no_asks = []
        for m in open_markets:
            na_val = _na_dollars(m)
            if na_val <= 0:
                yb = _yb_dollars(m)
                na_val = 1.0 - yb if yb > 0 else 1.0
            if na_val > 0:
                no_asks.append((m.get("ticker", ""), na_val))
        if len(no_asks) >= 2:
            n = len(no_asks)
            total_na = sum(na for _, na in no_asks)
            if total_na < n - 1:
                gross = (n - 1) - total_na
                fees = sum(_fee(na) for _, na in no_asks)
                net = gross - fees
                if net >= _MIN_NET_CENTS:
                    arbs.append(_make_opp("mutually_exclusive", event_ticker,
                        [t for t, _ in no_asks],
                        {t: round(na * 100, 1) for t, na in no_asks},
                        n, round(gross * 100, 2),
                        round(fees * 100, 2), round(net * 100, 2)))

    # -- TH scan: threshold monotonicity violations --
    if len(open_markets) >= 2:
        with_suffix = [(ns, m.get("ticker", ""), _ya_dollars(m))
                       for m in open_markets
                       if (ns := _numeric_suffix(m.get("ticker", ""))) >= 0]
        if len(with_suffix) >= 2:
            with_suffix.sort(key=lambda x: x[0])
            for i in range(len(with_suffix)):
                for j in range(i + 1, len(with_suffix)):
                    _, tk_i, ya_i = with_suffix[i]
                    _, tk_j, ya_j = with_suffix[j]
                    if ya_i <= ya_j:
                        continue
                    na_j_val = 0.0
                    for m in open_markets:
                        if m.get("ticker") == tk_j:
                            na_j_val = _na_dollars(m)
                            if na_j_val <= 0:
                                yb = _yb_dollars(m)
                                na_j_val = 1.0 - yb if yb > 0 else 1.0
                            break
                    cost = ya_i + na_j_val
                    if cost >= 1.0:
                        continue
                    gross = 1.0 - cost
                    fees_th = _fee(ya_i) + _fee(na_j_val)
                    net = gross - fees_th
                    if net >= _MIN_NET_CENTS:
                        arbs.append(_make_opp("threshold_order", event_ticker,
                            [tk_i, tk_j],
                            {tk_i: round(ya_i * 100, 1), tk_j: round(na_j_val * 100, 1)},
                            2, round(gross * 100, 2),
                            round(fees_th * 100, 2), round(net * 100, 2)))

    return arbs


def scan_once() -> List[Dict[str, Any]]:
    """
    Two-pronged scan:
    1. Targeted: fetch CE-eligible series via series_ticker param (fast, precise)
    2. General: paginate all events for ME/TH opportunities
    Skips during Kalshi's 3-6 AM ET maintenance window.
    """
    if _is_maintenance():
        logger.debug("REST scanner: skipping — Kalshi maintenance window (3-6 AM ET)")
        return []

    arbs: List[Dict[str, Any]] = []
    events_checked = 0

    # Prong 1: targeted CE series scan (fast — one API call per series)
    for series in _CE_SERIES:
        data = _fetch_json(
            f"{_KALSHI_EVENTS_URL}?status=open&series_ticker={series}"
            f"&limit=10&with_nested_markets=true"
        )
        if not data:
            continue
        for e in data.get("events", []):
            mkts = e.get("markets", [])
            if len(mkts) < 2:
                continue
            events_checked += 1
            try:
                arbs.extend(_scan_event(e, mkts))
            except Exception as exc:
                logger.debug("REST scanner CE error on %s: %s", e.get("event_ticker", ""), exc)
        time.sleep(0.3)

    # Prong 2: general scan for ME/TH (paginate first 250 events)
    cursor = ""
    for page in range(10):
        data = _fetch_json(
            f"{_KALSHI_EVENTS_URL}?status=open&limit=25&with_nested_markets=true"
            + (f"&cursor={cursor}" if cursor else "")
        )
        if not data:
            break
        events = data.get("events", [])
        if not events:
            break
        for e in events:
            et = e.get("event_ticker", "")
            if not et or _is_sports(et):
                continue
            mkts = e.get("markets", [])
            if len(mkts) < 2:
                continue
            events_checked += 1
            try:
                arbs.extend(_scan_event(e, mkts))
            except Exception as exc:
                logger.debug("REST scanner error on %s: %s", et, exc)
        cursor = data.get("cursor", "")
        if not cursor:
            break
        time.sleep(0.5)

    logger.info("REST scanner: checked %d events, found %d arb(s)", events_checked, len(arbs))
    return arbs


# -- Background thread ---------------------------------------------------------

_thread: Optional[threading.Thread] = None
_running = threading.Event()
_scan_count = 0
_last_scan_ts = 0.0
_last_arbs: List[Dict[str, Any]] = []


def _scanner_loop():
    global _scan_count, _last_scan_ts, _last_arbs
    logger.info("REST arb scanner started (polling every %ds)", _POLL_INTERVAL_S)
    while _running.is_set():
        try:
            arbs = scan_once()
            _scan_count += 1
            _last_scan_ts = time.time()
            _last_arbs = arbs

            if arbs:
                logger.info("REST scanner found %d arb(s)!", len(arbs))
                try:
                    from dashboard.live_state import get_live_state
                    state = get_live_state()
                    for opp in arbs:
                        state.add_opportunity(opp)
                except Exception as e:
                    logger.warning("REST scanner: failed to push arbs to LiveState: %s", e)
            else:
                if _scan_count % 10 == 0:
                    logger.info("REST scanner: scan #%d complete, no arbs found", _scan_count)

        except Exception as exc:
            logger.warning("REST scanner loop error: %s", exc)

        _running.wait(timeout=_POLL_INTERVAL_S)


def start():
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _running.set()
    _thread = threading.Thread(target=_scanner_loop, daemon=True, name="rest-arb-scanner")
    _thread.start()


def stop():
    _running.clear()


def get_status() -> Dict[str, Any]:
    return {
        "running": _thread is not None and _thread.is_alive(),
        "scan_count": _scan_count,
        "last_scan_ts": _last_scan_ts,
        "last_arbs_count": len(_last_arbs),
        "poll_interval_s": _POLL_INTERVAL_S,
    }
