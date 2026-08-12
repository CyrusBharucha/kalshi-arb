"""
scripts/scan_and_commit.py
==========================
Standalone arb scanner for GitHub Actions.

Zero project imports — only standard-library + websocket-client + requests.
Connects directly to the Synthesis WebSocket, tracks top-of-book for every
market, scans for YES/NO complement arbitrages, and writes found opportunities
to  data/live_arb_log.jsonl.  The GitHub Action then git-commits that file so
the Streamlit Historical Arb page can read it on the next page load.

Usage (GitHub Actions):
    pip install websocket-client requests python-dotenv
    python scripts/scan_and_commit.py

Environment variables:
    SYNTHESIS_SECRET_KEY  — required
    SCAN_DURATION         — total scan seconds (default 420 = 7 min)
    WARMUP_SECONDS        — seconds before scanning starts (default 60)
    MIN_NET_CENTS         — minimum net edge to record   (default 10)
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests as _req
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

import websocket

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scan_and_commit")

# ---------------------------------------------------------------------------
# Config (all overridable via env)
# ---------------------------------------------------------------------------
SCAN_DURATION  = int(os.environ.get("SCAN_DURATION",  "420"))   # 7 min
WARMUP_SECONDS = int(os.environ.get("WARMUP_SECONDS", "60"))    # WS warmup
MIN_NET_CENTS  = float(os.environ.get("MIN_NET_CENTS", "10"))   # 10c threshold
MIN_YES_ASK    = 0.05                                            # filter penny YES
MIN_NO_ASK     = 0.05                                            # filter penny NO
MAX_GROSS_EDGE = 0.10                                            # >10% is noise

_ROOT        = Path(__file__).resolve().parent.parent
OUTPUT_FILE  = _ROOT / "data" / "live_arb_log.jsonl"
WS_URL       = "wss://synthesis.trade/api/v1/orderbook/ws"

# ---------------------------------------------------------------------------
# Ticker date filter  (skip settled/expired markets)
# ---------------------------------------------------------------------------
_DATE_RE   = re.compile(
    r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})",
    re.IGNORECASE,
)
_MONTH_MAP = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
              "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}

def _date_past(ticker: str) -> bool:
    m = _DATE_RE.search(ticker)
    if not m:
        return False
    try:
        yr = 2000 + int(m.group(1))
        mo = _MONTH_MAP[m.group(2).upper()]
        dy = int(m.group(3))
        return datetime(yr, mo, dy, tzinfo=timezone.utc).date() < \
               datetime.now(timezone.utc).date()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fee formula (Kalshi: 7% of p*(1-p), capped at $0.035)
# ---------------------------------------------------------------------------
def _fee(p: float) -> float:
    return min(0.035, math.ceil(0.07 * p * (1.0 - p) * 100) / 100)


# ---------------------------------------------------------------------------
# In-memory top-of-book store
# ---------------------------------------------------------------------------
class TopOfBook:
    __slots__ = ("yes_ask", "no_ask", "updated_at")

    def __init__(self, yes_ask: float, no_ask: float):
        self.yes_ask    = yes_ask
        self.no_ask     = no_ask
        self.updated_at = time.time()


class OrderbookState:
    def __init__(self):
        self._lock: threading.Lock = threading.Lock()
        self._books: dict[str, TopOfBook] = {}
        self._msg_count: int = 0

    def apply(self, market_id: str, yes_ask: float, no_ask: float) -> None:
        with self._lock:
            self._books[market_id] = TopOfBook(yes_ask, no_ask)
            self._msg_count += 1

    def snapshot(self) -> dict[str, TopOfBook]:
        with self._lock:
            return dict(self._books)

    @property
    def msg_count(self) -> int:
        with self._lock:
            return self._msg_count


# ---------------------------------------------------------------------------
# Market list discovery
# ---------------------------------------------------------------------------
_SYNTHESIS_BASE = "https://synthesis.trade"
_KALSHI_MARKETS = "https://trading-api.kalshi.com/trade-api/v2/markets"
_JUNK_PREFIXES  = ("KXMVECROSSCATEGORY",)


def _fetch_synthesis_tickers(secret: str) -> list[str]:
    """Fetch all open Kalshi market tickers from Synthesis REST API."""
    headers = {"X-PROJECT-API-KEY": secret, "User-Agent": "kalshi-arb"}
    tickers: list[str] = []
    offset = 0
    logger.info("Fetching market list from Synthesis REST API…")
    while True:
        try:
            if _HAS_REQUESTS:
                r = _req.get(
                    f"{_SYNTHESIS_BASE}/api/v1/kalshi/markets?limit=250&offset={offset}",
                    headers=headers, timeout=30,
                )
                r.raise_for_status()
                batch = r.json().get("response", [])
            else:
                url = f"{_SYNTHESIS_BASE}/api/v1/kalshi/markets?limit=250&offset={offset}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    batch = json.loads(resp.read()).get("response", [])
        except Exception as exc:
            logger.warning("Synthesis fetch error at offset=%d: %s — stopping", offset, exc)
            break
        if not batch:
            break
        for event_data in batch:
            for m in event_data.get("markets", []):
                ticker = m.get("market_id")
                if ticker:
                    tickers.append(ticker)
        offset += 250
        if offset % 2500 == 0:
            logger.info("  … %d tickers so far", len(tickers))
    logger.info("Synthesis REST: %d tickers", len(tickers))
    return tickers


def _fetch_kalshi_tickers(limit: int = 2000) -> list[str]:
    """Fallback: fetch open market tickers from Kalshi public API (no auth)."""
    logger.info("Fetching market list from Kalshi public API (fallback)…")
    now   = int(time.time())
    seen: dict[str, float] = {}
    for days in (7, 30, 90, 180, 365):
        max_ts = now + days * 86400
        cursor = None
        for _ in range(3):
            try:
                url = f"{_KALSHI_MARKETS}?status=open&limit=1000&max_close_ts={max_ts}"
                if cursor:
                    url += f"&cursor={cursor}"
                req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                for m in data.get("markets", []):
                    ticker = m.get("ticker", "")
                    if ticker and not ticker.startswith(_JUNK_PREFIXES):
                        vol = float(m.get("volume_fp") or 0)
                        if ticker not in seen or seen[ticker] < vol:
                            seen[ticker] = vol
                cursor = data.get("cursor")
                if not cursor:
                    break
            except Exception as exc:
                logger.warning("Kalshi fetch error: %s", exc)
                break
    tickers = sorted(seen, key=seen.get, reverse=True)[:limit]
    logger.info("Kalshi fallback: %d tickers", len(tickers))
    return tickers


# ---------------------------------------------------------------------------
# WebSocket client (standalone, no project deps)
# ---------------------------------------------------------------------------
class Scanner:
    def __init__(self, secret: str, tickers: list[str]):
        self._secret  = secret
        self._tickers = tickers
        self._state   = OrderbookState()
        self._results: list[dict] = []
        self._seen: dict[str, float] = {}   # key -> last_detected timestamp
        self._lock    = threading.Lock()
        self._ws: websocket.WebSocketApp | None = None

    # -- WebSocket handlers ----------------------------------------------------

    def _on_open(self, ws):
        CHUNK = 1000
        for i in range(0, len(self._tickers), CHUNK):
            chunk = self._tickers[i:i + CHUNK]
            ws.send(json.dumps({"type": "subscribe", "venue": "kalshi", "markets": chunk}))
        logger.info("WS connected — subscribed to %d tickers (in %d chunks)",
                    len(self._tickers), math.ceil(len(self._tickers) / CHUNK))

    def _on_message(self, ws, raw: str):
        try:
            msg = json.loads(raw)
        except Exception:
            return
        resp = msg.get("response", {})

        if "delta" in resp:
            d = resp["delta"]
            # Nested snapshot-style delta
            if "orderbook" in d:
                self._apply_nested(d)
            else:
                # Flat delta: {market_id, yes_best_ask, no_best_ask, ...}
                mid = d.get("market_id") or d.get("ticker")
                if mid:
                    ya = float(d.get("yes_best_ask") or d.get("yes_ask") or 0)
                    na = float(d.get("no_best_ask")  or d.get("no_ask")  or 0)
                    if ya > 0 and na > 0:
                        self._state.apply(mid, ya, na)

        elif "orderbooks" in resp:
            for entry in (resp.get("orderbooks") or []):
                self._apply_nested(entry)

    def _apply_nested(self, entry: dict) -> None:
        ob = entry.get("orderbook") or entry
        mid = ob.get("market_id") or ob.get("ticker")
        if not mid:
            return
        yes_asks = ob.get("yes", {}).get("asks", {})
        no_asks  = ob.get("no",  {}).get("asks", {})
        # Best ask = lowest priced ask level
        def _best_ask(levels: dict) -> float:
            if not levels:
                return 0.0
            try:
                return min(float(k) for k in levels if float(levels.get(k, 0)) > 0)
            except Exception:
                return 0.0
        ya = _best_ask(yes_asks)
        na = _best_ask(no_asks)
        # Also try flat fields in nested format
        ya = ya or float(ob.get("yes_best_ask") or ob.get("yes_ask") or 0)
        na = na or float(ob.get("no_best_ask")  or ob.get("no_ask")  or 0)
        if ya > 0 and na > 0:
            self._state.apply(mid, ya, na)

    def _on_error(self, ws, err):
        logger.warning("WS error: %s", err)

    def _on_close(self, ws, code, msg):
        logger.info("WS closed (code=%s)", code)

    # -- Scanner loop ----------------------------------------------------------

    def scan_once(self) -> int:
        books = self._state.snapshot()
        now   = time.time()
        found = 0
        for mid, b in books.items():
            if _date_past(mid):
                continue
            ya = b.yes_ask
            na = b.no_ask
            if ya <= MIN_YES_ASK or na <= MIN_NO_ASK:
                continue
            if now - b.updated_at > 120:
                continue   # stale data
            gross = 1.0 - ya - na
            if gross <= 0 or gross > MAX_GROSS_EDGE:
                continue
            fees  = _fee(ya) + _fee(na)
            net   = gross - fees
            if net * 100 < MIN_NET_CENTS:
                continue
            key = f"YNC:{mid}"
            if now - self._seen.get(key, 0) < 3600:
                continue    # de-duplicate within session
            self._seen[key] = now
            opp = {
                "ticker":               mid,
                "strategy":             "yes_no_complement",
                "markets_involved":     mid,
                "yes_ask":              round(ya, 4),
                "no_ask":               round(na, 4),
                "gross_edge_cents":     round(gross * 100, 2),
                "fees_cents":           round(fees  * 100, 2),
                "net_edge_cents":       round(net   * 100, 2),
                "executable_contracts": None,
                "detected_at":          datetime.now(timezone.utc).isoformat(),
                "source":               "github_actions",
            }
            with self._lock:
                self._results.append(opp)
            found += 1
            logger.info("ARB  %s  gross=%.2fc  fees=%.2fc  net=%.2fc",
                        mid, gross * 100, fees * 100, net * 100)
        return found

    # -- Main run (blocks) -----------------------------------------------------

    def run(self) -> list[dict]:
        self._ws = websocket.WebSocketApp(
            WS_URL,
            header={"X-PROJECT-API-KEY": self._secret},
            on_open    = self._on_open,
            on_message = self._on_message,
            on_error   = self._on_error,
            on_close   = self._on_close,
        )

        ws_thread = threading.Thread(target=self._ws.run_forever,
                                     kwargs={"ping_interval": 30, "ping_timeout": 10},
                                     daemon=True)
        ws_thread.start()

        # Warmup: wait for data to flow
        logger.info("Warming up for %ds…", WARMUP_SECONDS)
        time.sleep(WARMUP_SECONDS)
        logger.info("Books received so far: %d (msg_count=%d)",
                    len(self._state.snapshot()), self._state.msg_count)

        # Scan loop for the remaining time
        deadline = time.time() + (SCAN_DURATION - WARMUP_SECONDS)
        n_total  = 0
        while time.time() < deadline:
            n = self.scan_once()
            if n:
                logger.info("  +%d arbs this pass (total=%d)", n, len(self._results))
                n_total += n
            time.sleep(5)

        logger.info("Scan complete — %d arbs found, %d books seen",
                    len(self._results), len(self._state.snapshot()))
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        with self._lock:
            return list(self._results)


# ---------------------------------------------------------------------------
# Write JSONL output
# ---------------------------------------------------------------------------
def write_jsonl(records: list[dict]) -> Path:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    logger.info("Wrote %d records → %s", len(records), OUTPUT_FILE)
    return OUTPUT_FILE


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    secret = os.environ.get("SYNTHESIS_SECRET_KEY", "").strip()
    if not secret:
        logger.error("SYNTHESIS_SECRET_KEY not set — aborting.")
        return 1

    # Discover markets
    tickers = _fetch_synthesis_tickers(secret)
    if not tickers:
        logger.warning("Synthesis REST returned nothing — falling back to Kalshi public API")
        tickers = _fetch_kalshi_tickers(limit=2000)
    if not tickers:
        logger.error("No markets found — cannot scan.")
        return 1

    logger.info("Starting scanner: %d tickers, warmup=%ds, duration=%ds",
                len(tickers), WARMUP_SECONDS, SCAN_DURATION)

    scanner = Scanner(secret=secret, tickers=tickers)
    records = scanner.run()

    if not records:
        logger.info("No arbs detected this run.")
        return 0

    write_jsonl(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
