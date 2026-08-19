"""
dashboard/rest_scanner.py
=========================
High-frequency REST arb scanner — polls Kalshi public API every ~1 second
for CE-eligible hot series and every ~30 seconds for a broader ME/TH sweep.

Scans for:
  CE — sum(YES asks) < $1 across all legs of an event
  ME — sum(NO asks)  < N-1 across all legs
  TH — monotonicity violations in threshold markets

All detected arbs are double-verified: on first detection the event is
re-fetched immediately and the arb must still exist to be reported.

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

_FAST_INTERVAL_S = 3.0
_BROAD_INTERVAL_S = 30.0
_MIN_NET_CENTS = 0.02
_MIN_LEGS = 2
_MAX_LEGS = 200
_SERIES_PER_TICK = 3
_CE_EVENTS_PER_TICK = 6

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


def _nb(m: dict) -> float:
    v = m.get("no_bid_dollars") or m.get("no_bid") or "0"
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _vol(m: dict) -> float:
    return float(m.get("volume_fp") or 0)


def _oi(m: dict) -> float:
    return float(m.get("open_interest_fp") or 0)


def _depth(m: dict) -> float:
    return float(m.get("yes_ask_size_fp") or 0) + float(m.get("no_ask_size_fp") or 0)


def _event_liquidity(markets: list) -> float:
    return sum(_vol(m) + _oi(m) for m in markets)


def _is_maintenance() -> bool:
    try:
        from zoneinfo import ZoneInfo
        et = datetime.now(ZoneInfo("America/New_York"))
        return 3 <= et.hour < 6
    except Exception:
        return False


def _fetch_json(url: str, retries: int = 2, timeout: int = 10) -> Optional[dict]:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "kalshi-arb-rest-scanner",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as he:
            if he.code == 429:
                retry_after = float(he.headers.get("Retry-After", "2"))
                time.sleep(retry_after)
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


def _fetch_event_fresh(event_ticker: str) -> Optional[dict]:
    data = _fetch_json(
        f"{_KALSHI_BASE}/events/{event_ticker}?with_nested_markets=true",
        retries=1, timeout=8,
    )
    if data and "event" in data:
        return data["event"]
    return data


def _numeric_suffix(ticker: str) -> float:
    last = ticker.rsplit("-", 1)[-1]
    nums = re.findall(r"\d+\.?\d*", last)
    return float(nums[-1]) if nums else -1.0


_CATCH_ALL_KEYWORDS = (
    "other", "field", "none of", "no one", "nobody",
    "someone else", "another", "any other",
)


def _is_truly_ce(event: dict, markets: list) -> bool:
    """
    Determine if an ME event is truly collectively exhaustive —
    i.e. the listed legs cover ALL possible outcomes, so sum(YES) must = $1.

    True when:
    - Markets have contiguous floor_strike/cap_strike ranges (threshold buckets
      spanning the full outcome space, like BTC price in $100 increments)
    - OR a catch-all leg exists ("Other", "Field", etc.)
    - OR the series is in _CE_SERIES (known threshold series)

    False for:
    - Candidate-based markets ("Who will be Pope?") where unlisted outcomes
      can win and sum < $1 is expected, not an arb.
    - Discrete count markets ("How many X?") where floor==cap on each leg
      and gaps exist (e.g. legs for 2,3,4,5 miss 0,1,6+).
    """
    series = _series_prefix(event.get("event_ticker", ""))
    if series in _CE_SERIES:
        return True

    strikes = []
    for m in markets:
        fs = m.get("floor_strike")
        cs = m.get("cap_strike")
        if fs is not None and cs is not None:
            try:
                strikes.append((float(fs), float(cs)))
            except (TypeError, ValueError):
                pass

    if strikes:
        # Contiguous range check: sort by floor, verify each cap == next floor
        # Discrete counts (floor==cap on every leg) are NOT contiguous ranges
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


def _make_opp(strategy: str, event_ticker: str, tickers: list,
              prices: dict, n_legs: int, gross: float, fees: float,
              net: float, liquidity: float = 0.0,
              max_contracts: int = 0) -> dict:
    return {
        "strategy": strategy,
        "event_ticker": event_ticker,
        "tickers": tickers,
        "prices": prices,
        "n_legs": n_legs,
        "gross_edge_cents": gross,
        "total_fees_cents": fees,
        "net_edge_cents": net,
        "liquidity": liquidity,
        "max_contracts": max_contracts,
        "source": "rest_api",
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "detected_at_ts": time.time(),
    }


# ---------------------------------------------------------------------------
# Arb state tracker — detects OPEN / CLOSE transitions
# ---------------------------------------------------------------------------

class _ArbTracker:
    """Tracks open arb states by (event_ticker, strategy) key."""

    def __init__(self):
        self._open: Dict[Tuple[str, str], dict] = {}
        self._history: List[dict] = []
        self._lock = threading.Lock()

    def reconcile(self, detected: List[dict]) -> Tuple[List[dict], List[dict]]:
        """
        Compare detected arbs against known open state.
        Returns (newly_opened, newly_closed).
        """
        now = time.time()
        seen_keys: Set[Tuple[str, str]] = set()
        opened: List[dict] = []
        closed: List[dict] = []

        with self._lock:
            for opp in detected:
                key = (opp["event_ticker"], opp["strategy"])
                seen_keys.add(key)
                if key not in self._open:
                    opp["opened_at"] = now
                    opp["opened_at_iso"] = datetime.now(timezone.utc).isoformat()
                    self._open[key] = opp
                    opened.append(opp)
                else:
                    self._open[key].update({
                        "net_edge_cents": opp["net_edge_cents"],
                        "gross_edge_cents": opp["gross_edge_cents"],
                        "prices": opp["prices"],
                        "last_seen": now,
                    })

            stale_keys = [k for k in self._open if k not in seen_keys]
            for k in stale_keys:
                entry = self._open.pop(k)
                entry["closed_at"] = now
                entry["closed_at_iso"] = datetime.now(timezone.utc).isoformat()
                entry["duration_s"] = round(now - entry.get("opened_at", now), 1)
                closed.append(entry)
                self._history.append(entry)

        return opened, closed

    def get_open(self) -> List[dict]:
        with self._lock:
            return list(self._open.values())

    def get_history(self) -> List[dict]:
        with self._lock:
            return list(self._history)

    @property
    def open_count(self) -> int:
        return len(self._open)


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------

def _scan_event(event: dict, markets: List[dict]) -> List[Dict[str, Any]]:
    """Scan a single event's markets for CE/ME/TH arbs with strict filtering."""
    arbs: List[Dict[str, Any]] = []
    event_ticker = event.get("event_ticker", "")
    me = event.get("mutually_exclusive", False)
    series = _series_prefix(event_ticker)

    all_active = [m for m in markets if m.get("status") in ("active", "open")]
    if len(all_active) < _MIN_LEGS or len(all_active) > _MAX_LEGS:
        return arbs

    priced = [m for m in all_active if _ya(m) > 0]

    liq = _event_liquidity(all_active)

    # -- CE: sum(YES asks) < $1 --
    # Only valid when the event truly covers ALL outcomes
    if (me or series in _CE_SERIES) and len(priced) >= 2 and _is_truly_ce(event, all_active):
        if len(priced) == len(all_active):
            yes_asks = [(m.get("ticker", ""), _ya(m)) for m in priced]
            total_ya = sum(ya for _, ya in yes_asks)
            if total_ya < 1.0:
                gross = 1.0 - total_ya
                fees = sum(_fee(ya) for _, ya in yes_asks)
                net = gross - fees
                if net >= _MIN_NET_CENTS:
                    min_depth = min(
                        (float(m.get("yes_ask_size_fp") or 0) for m in priced),
                        default=0,
                    )
                    arbs.append(_make_opp(
                        "collectively_exhaustive", event_ticker,
                        [t for t, _ in yes_asks],
                        {t: round(ya * 100, 1) for t, ya in yes_asks},
                        len(yes_asks), round(gross * 100, 2),
                        round(fees * 100, 2), round(net * 100, 2),
                        liquidity=liq,
                        max_contracts=int(min_depth),
                    ))

    # -- ME: sum(NO asks) < N-1 --
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
                if net >= _MIN_NET_CENTS:
                    arbs.append(_make_opp(
                        "mutually_exclusive", event_ticker,
                        [t for t, _ in no_asks],
                        {t: round(na * 100, 1) for t, na in no_asks},
                        n, round(gross * 100, 2),
                        round(fees * 100, 2), round(net * 100, 2),
                        liquidity=liq,
                    ))

    # -- TH: threshold monotonicity violations --
    if len(priced) >= 2:
        with_suffix = [
            (ns, m.get("ticker", ""), _ya(m), m)
            for m in priced
            if (ns := _numeric_suffix(m.get("ticker", ""))) >= 0
        ]
        if len(with_suffix) >= 2:
            with_suffix.sort(key=lambda x: x[0])
            for i in range(len(with_suffix)):
                for j in range(i + 1, len(with_suffix)):
                    _, tk_i, ya_i, m_i = with_suffix[i]
                    _, tk_j, ya_j, m_j = with_suffix[j]
                    if ya_i <= ya_j:
                        continue
                    na_j_val = _na(m_j)
                    if na_j_val <= 0:
                        yb_j = _yb(m_j)
                        na_j_val = 1.0 - yb_j if yb_j > 0 else 1.0
                    cost = ya_i + na_j_val
                    if cost >= 1.0:
                        continue
                    gross = 1.0 - cost
                    fees_th = _fee(ya_i) + _fee(na_j_val)
                    net = gross - fees_th
                    if net >= _MIN_NET_CENTS:
                        arbs.append(_make_opp(
                            "threshold_order", event_ticker,
                            [tk_i, tk_j],
                            {tk_i: round(ya_i * 100, 1),
                             tk_j: round(na_j_val * 100, 1)},
                            2, round(gross * 100, 2),
                            round(fees_th * 100, 2), round(net * 100, 2),
                            liquidity=_vol(m_i) + _vol(m_j) + _oi(m_i) + _oi(m_j),
                        ))

    return arbs


def _verify_arb(opp: dict) -> Optional[dict]:
    """
    Double-tap verification: re-fetch the event and re-scan.
    Returns the arb dict if confirmed, None if gone.
    """
    et = opp["event_ticker"]
    event = _fetch_event_fresh(et)
    if not event:
        return None
    mkts = event.get("markets", [])
    if not mkts:
        return None
    re_arbs = _scan_event(event, mkts)
    for a in re_arbs:
        if a["strategy"] == opp["strategy"]:
            a["verified"] = True
            a["verify_ts"] = time.time()
            return a
    return None


# ---------------------------------------------------------------------------
# Public scan_once — compatible with old interface
# ---------------------------------------------------------------------------

_tracker = _ArbTracker()
_last_broad_scan = 0.0
_hot_events_cache: Dict[str, float] = {}
_series_index = 0
_ce_event_index = 0
_known_ce_events: Dict[str, dict] = {}


def _discover_ce_events(series_subset: List[str]) -> int:
    """Scan a subset of CE series, cache discovered event tickers. Returns events found."""
    found = 0

    def _fetch_series(series: str):
        return _fetch_json(
            f"{_KALSHI_BASE}/events?status=open&series_ticker={series}"
            f"&limit=10&with_nested_markets=true",
            timeout=8,
        )

    with ThreadPoolExecutor(max_workers=min(6, len(series_subset) or 1)) as pool:
        results = pool.map(_fetch_series, series_subset)
        for data in results:
            if not data:
                continue
            for e in data.get("events", []):
                et = e.get("event_ticker", "")
                mkts = e.get("markets", [])
                if len(mkts) >= _MIN_LEGS and _is_truly_ce(e, mkts):
                    _known_ce_events[et] = {"last_discovery": time.time()}
                    found += 1
    return found


def scan_once(broad: bool = False) -> List[Dict[str, Any]]:
    """
    Single scan cycle.

    Fast mode (~1s): re-check cached CE event tickers by direct fetch
    + rotate through a few CE series to discover new events.

    Broad mode (~30s): full paginated sweep of all open events for
    ME/TH opportunities + full series discovery.

    Detected arbs are double-verified before reporting.
    """
    global _series_index, _ce_event_index

    if _is_maintenance():
        return []

    raw_arbs: List[Dict[str, Any]] = []
    events_checked = 0
    series_list = sorted(_CE_SERIES)

    if broad:
        # Full series discovery
        _discover_ce_events(series_list)
    else:
        # Only discover new CE events every 5th fast tick or if cache is empty
        if not _known_ce_events or _scan_count % 5 == 0:
            end = min(_series_index + _SERIES_PER_TICK, len(series_list))
            subset = series_list[_series_index:end]
            _series_index = end if end < len(series_list) else 0
            _discover_ce_events(subset)

    # --- Fast: re-check a rotating subset of known CE events ---
    all_ce = list(_known_ce_events.keys())
    if broad:
        ce_tickers = all_ce
    else:
        end = min(_ce_event_index + _CE_EVENTS_PER_TICK, len(all_ce))
        ce_tickers = all_ce[_ce_event_index:end]
        _ce_event_index = end if end < len(all_ce) else 0
    stale_ce = []

    def _check_ce_event(et: str):
        event = _fetch_event_fresh(et)
        if not event:
            return et, None, []
        mkts = event.get("markets", [])
        active = [m for m in mkts if m.get("status") in ("active", "open")]
        if not active:
            return et, None, []
        try:
            return et, event, _scan_event(event, mkts)
        except Exception:
            return et, event, []

    with ThreadPoolExecutor(max_workers=min(8, len(ce_tickers) or 1)) as pool:
        futures = {pool.submit(_check_ce_event, et): et for et in ce_tickers}
        for fut in as_completed(futures):
            et, event, found = fut.result()
            if event is None:
                stale_ce.append(et)
            else:
                events_checked += 1
                raw_arbs.extend(found)

    for et in stale_ce:
        _known_ce_events.pop(et, None)

    # --- Broad ME/TH sweep (only when requested) ---
    if broad:
        cursor = ""
        for page in range(5):
            data = _fetch_json(
                f"{_KALSHI_BASE}/events?status=open&limit=50"
                f"&with_nested_markets=true"
                + (f"&cursor={cursor}" if cursor else ""),
                timeout=12,
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
                if len(mkts) < _MIN_LEGS:
                    continue
                events_checked += 1
                try:
                    found = _scan_event(e, mkts)
                    raw_arbs.extend(found)
                    if found:
                        _hot_events_cache[et] = time.time()
                except Exception as exc:
                    logger.debug("Broad scan error on %s: %s", et, exc)
            cursor = data.get("cursor", "")
            if not cursor:
                break
            time.sleep(0.1)

        # Re-check recently-hot non-CE events
        stale = []
        for et, ts in list(_hot_events_cache.items()):
            if time.time() - ts > 300:
                stale.append(et)
                continue
            event = _fetch_event_fresh(et)
            if event:
                mkts = event.get("markets", [])
                if mkts:
                    events_checked += 1
                    try:
                        raw_arbs.extend(_scan_event(event, mkts))
                    except Exception:
                        pass
        for et in stale:
            _hot_events_cache.pop(et, None)

    # --- Double-verify all detected arbs ---
    verified: List[Dict[str, Any]] = []
    for opp in raw_arbs:
        confirmed = _verify_arb(opp)
        if confirmed:
            verified.append(confirmed)
        else:
            logger.info("Arb REJECTED by verification: %s %s net=%.2fc",
                        opp["strategy"], opp["event_ticker"],
                        opp["net_edge_cents"])

    # --- Reconcile with tracker ---
    opened, closed = _tracker.reconcile(verified)

    if opened:
        logger.info("ARB OPENED: %d new — %s", len(opened),
                     ", ".join(f'{a["strategy"]}@{a["event_ticker"]}' for a in opened))
    if closed:
        logger.info("ARB CLOSED: %d — %s", len(closed),
                     ", ".join(
                         f'{a["strategy"]}@{a["event_ticker"]} '
                         f'(lived {a.get("duration_s", 0):.0f}s)'
                         for a in closed
                     ))

    return verified


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

_thread: Optional[threading.Thread] = None
_running = threading.Event()
_stop_event = threading.Event()
_scan_count = 0
_fast_count = 0
_broad_count = 0
_last_scan_ts = 0.0
_last_arbs: List[Dict[str, Any]] = []


def _scanner_loop():
    global _scan_count, _fast_count, _broad_count
    global _last_scan_ts, _last_arbs, _last_broad_scan

    logger.info("REST arb scanner started — fast=%.1fs, broad=%.1fs",
                _FAST_INTERVAL_S, _BROAD_INTERVAL_S)

    while _running.is_set():
        try:
            now = time.time()
            do_broad = (now - _last_broad_scan) >= _BROAD_INTERVAL_S

            arbs = scan_once(broad=do_broad)

            _scan_count += 1
            _last_scan_ts = now
            _last_arbs = arbs

            if do_broad:
                _last_broad_scan = now
                _broad_count += 1
            else:
                _fast_count += 1

            if arbs:
                logger.info("REST scanner found %d verified arb(s)!", len(arbs))
                try:
                    from dashboard.live_state import get_live_state
                    state = get_live_state()
                    for opp in arbs:
                        state.add_opportunity(opp)
                except Exception as e:
                    logger.warning("Failed to push arbs to LiveState: %s", e)
                try:
                    from dashboard.live_arb_store import persist as _persist_arb
                    for opp in arbs:
                        _persist_arb({
                            "strategy": opp["strategy"],
                            "ticker": (
                                f'{opp["event_ticker"]} ({opp["n_legs"]} legs)'
                                if opp["strategy"] == "collectively_exhaustive"
                                else opp["tickers"][0] if opp["tickers"] else opp["event_ticker"]
                            ),
                            "legs": opp.get("tickers"),
                            "yes_ask": sum(v for v in opp.get("prices", {}).values()) / 100.0 if opp["strategy"] == "collectively_exhaustive" else (list(opp.get("prices", {}).values()) or [0])[0] / 100.0,
                            "no_ask": None,
                            "gross_edge_cents": opp["gross_edge_cents"],
                            "fees_cents": opp.get("total_fees_cents", 0),
                            "net_edge_cents": opp["net_edge_cents"],
                            "executable_contracts": opp.get("max_contracts", 0),
                            "confidence": "HIGH" if opp.get("verified") else "MED",
                            "detected_at_ts": opp.get("detected_at_ts"),
                        })
                except Exception as e:
                    logger.warning("Failed to persist arbs: %s", e)
                try:
                    from pathlib import Path as _ScanPath
                    _jsonl_path = _ScanPath(__file__).resolve().parent.parent / "data" / "live_arb_log.jsonl"
                    _jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(_jsonl_path, "a", encoding="utf-8") as _jf:
                        for opp in arbs:
                            _jf.write(json.dumps({
                                "strategy": opp["strategy"],
                                "event_ticker": opp["event_ticker"],
                                "tickers": opp.get("tickers", []),
                                "n_legs": opp.get("n_legs", 0),
                                "gross_edge_cents": opp["gross_edge_cents"],
                                "fees_cents": opp.get("total_fees_cents", 0),
                                "net_edge_cents": opp["net_edge_cents"],
                                "liquidity": opp.get("liquidity", 0),
                                "verified": opp.get("verified", False),
                                "detected_at": opp.get("detected_at", datetime.now(timezone.utc).isoformat()),
                                "source": "streamlit_rest_scanner",
                            }) + "\n")
                except Exception as e:
                    logger.warning("Failed to write JSONL: %s", e)
            else:
                if _scan_count % 60 == 0:
                    logger.info("Scan #%d: no arbs (fast=%d, broad=%d, open=%d)",
                                _scan_count, _fast_count, _broad_count,
                                _tracker.open_count)

        except Exception as exc:
            logger.warning("Scanner loop error: %s", exc)

        _stop_event.wait(timeout=_FAST_INTERVAL_S)


def start():
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _running.set()
    _thread = threading.Thread(target=_scanner_loop, daemon=True,
                               name="rest-arb-scanner")
    _thread.start()


def stop():
    _running.clear()
    _stop_event.set()


def get_status() -> Dict[str, Any]:
    return {
        "running": _thread is not None and _thread.is_alive(),
        "scan_count": _scan_count,
        "fast_scans": _fast_count,
        "broad_scans": _broad_count,
        "last_scan_ts": _last_scan_ts,
        "last_arbs_count": len(_last_arbs),
        "open_arbs": _tracker.open_count,
        "arb_history_count": len(_tracker.get_history()),
        "fast_interval_s": _FAST_INTERVAL_S,
        "broad_interval_s": _BROAD_INTERVAL_S,
        "hot_events_cached": len(_hot_events_cache),
    }


def get_open_arbs() -> List[dict]:
    return _tracker.get_open()


def get_arb_history() -> List[dict]:
    return _tracker.get_history()
