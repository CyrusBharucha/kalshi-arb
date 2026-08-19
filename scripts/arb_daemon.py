"""
scripts/arb_daemon.py
======================
Headless arbitrage scanner daemon — runs the full Synthesis WebSocket +
arb detection pipeline without a Streamlit process.

When Streamlit is running, ws_bridge.py manages the WebSocket and scanning
automatically — you don't need this script. Use arb_daemon.py when you want
arb detection running in the background (e.g. overnight) without the dashboard
open. All detected arbs are persisted to dashboard/live_arbs.db so they appear
on the Historical Arb page whenever you next open Streamlit.

Usage:
    python scripts/arb_daemon.py

Requires:
    SYNTHESIS_SECRET_KEY in .env or environment

Stop with Ctrl+C.
"""
from __future__ import annotations

import logging
import math
import os
import re
import signal
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Make project root importable
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

_LOG_FILE = _ROOT / "logs" / "daemon.log"
_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("arb_daemon")


def _fee(p: float) -> float:
    return min(0.035, math.ceil(0.07 * p * (1.0 - p) * 100) / 100)


def _book_no_ask(book) -> float:
    lvls = book.no_asks(1) if book else []
    return lvls[0].price if lvls else 0.0


def _book_yes_ask(book) -> float:
    lvls = book.yes_asks(1) if book else []
    return lvls[0].price if lvls else 0.0


def _numeric_suffix(ticker: str) -> float:
    last = ticker.rsplit("-", 1)[-1]
    nums = re.findall(r"\d+\.?\d*", last)
    return float(nums[-1]) if nums else -1.0


MIN_CONTRACTS = 5
TTL_S         = 3600  # re-emit same arb after 1 hour (prevents stale-order spam)
SCAN_INTERVAL = 5    # seconds between scans

# Regex to detect date codes embedded in tickers e.g. 26AUG31, 26SEP05
_TICKER_DATE_RE = re.compile(
    r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})", re.IGNORECASE
)
_MONTH_MAP = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
              "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}

def _ticker_date_is_past(ticker: str) -> bool:
    """Return True if ticker encodes a date that is today or in the past (UTC)."""
    m = _TICKER_DATE_RE.search(ticker)
    if not m:
        return False
    try:
        yr = 2000 + int(m.group(1))
        mo = _MONTH_MAP[m.group(2).upper()]
        dy = int(m.group(3))
        mkt_date = datetime(yr, mo, dy, tzinfo=timezone.utc)
        return mkt_date.date() < datetime.now(timezone.utc).date()
    except Exception:
        return False


# Sports stat markets have notoriously stale/thin order books — skip them
_SPORTS_PREFIXES = (
    "KXLIGA", "KXLALIGA", "KXNBA", "KXNFL", "KXMLB", "KXNHL", "KXEPL",
    "KXSERIEA", "KXBUNDES", "KXMLS", "KXUCL", "KXUEFA", "KXNCAAF",
    "KXNCAAB", "KXWNBA", "KXPGA", "KXTENNIS", "KXFORMULA", "KXSOCCER",
    "KXCRICKET", "KXRUGBY", "KXGOLF", "KXUFC", "KXBOXING", "KXMMA",
)


def _walk_complement(book_yes, book_no):
    yes_lvls = [(l.price, l.quantity) for l in book_yes.yes_asks(20)
                if l.price > 0 and l.quantity > 0]
    no_lvls  = [(l.price, l.quantity) for l in book_no.no_asks(20)
                if l.price > 0 and l.quantity > 0]
    if not yes_lvls or not no_lvls:
        return None
    total = yes_cost = no_cost = 0
    yi = ni = 0
    yes_rem = yes_lvls[0][1]
    no_rem  = no_lvls[0][1]
    while yi < len(yes_lvls) and ni < len(no_lvls):
        yp  = yes_lvls[yi][0]
        np_ = no_lvls[ni][0]
        if 1.0 - yp - np_ - _fee(yp) - _fee(np_) <= 0.01:
            break
        take = min(yes_rem, no_rem)
        total    += take
        yes_cost += take * yp
        no_cost  += take * np_
        yes_rem  -= take
        no_rem   -= take
        if yes_rem == 0:
            yi += 1
            yes_rem = yes_lvls[yi][1] if yi < len(yes_lvls) else 0
        if no_rem == 0:
            ni += 1
            no_rem = no_lvls[ni][1] if ni < len(no_lvls) else 0
    if total < MIN_CONTRACTS:
        return None
    avg_yes = yes_cost / total
    avg_no  = no_cost  / total
    net_per = 1.0 - avg_yes - avg_no - _fee(avg_yes) - _fee(avg_no)
    return total, avg_yes, avg_no, net_per


def run_scanner(state, cache, event_groups: dict, ws_client=None):
    """Main arb scanner loop — runs forever."""
    # persist() is called internally by live_state.add_opportunity() — no direct import needed
    from dashboard.ws_predict import verify_ce_arb_via_api, _is_structural_ce

    _last_seen: dict = {}

    ticker_to_event: dict[str, str] = {}
    for eid, tks in event_groups.items():
        for tk in tks:
            ticker_to_event[tk] = eid

    logger.info("Scanner started (interval=%ds)", SCAN_INTERVAL)
    n_arbs = 0
    _last_heartbeat = time.time()
    _HEARTBEAT_INTERVAL = 60   # log every 60s until data flows, then settle

    while True:
        time.sleep(SCAN_INTERVAL)
        try:
            _now    = time.time()
            quotes  = state.snapshot_all()
            books   = cache.snapshot()

            # Heartbeat — prove the loop is running even when no arbs found
            if _now - _last_heartbeat >= _HEARTBEAT_INTERVAL:
                _last_heartbeat = _now
                _n_books = len(books) if books else 0
                _n_quotes = len(quotes) if quotes else 0
                _ws_stats = ws_client.stats if ws_client else {}
                logger.info(
                    "HEARTBEAT — quotes=%d  books=%d  arbs=%d  "
                    "ws_msgs=%d  ws_deltas=%d  ws_snapshots=%d  ws_reconnects=%d",
                    _n_quotes, _n_books, n_arbs,
                    _ws_stats.get("total_messages", -1),
                    _ws_stats.get("total_deltas", -1),
                    _ws_stats.get("snapshots", -1),
                    _ws_stats.get("reconnects", -1),
                )
                # If still no data after first minute, warn loudly
                if _n_quotes == 0 and _ws_stats.get("total_messages", 0) == 0:
                    logger.warning("NO WS MESSAGES RECEIVED — check SYNTHESIS_SECRET_KEY or subscription")
                elif _n_quotes == 0 and _ws_stats.get("total_messages", 0) > 0:
                    logger.warning(
                        "WS MESSAGES RECEIVED (%d) but quotes=0 — "
                        "message format mismatch or all deltas stale",
                        _ws_stats.get("total_messages", 0),
                    )
                else:
                    # Data is flowing — slow down heartbeat to every 5 min
                    _HEARTBEAT_INTERVAL = 300

            if not quotes:
                continue

            # Strategy 1: YES/NO complement (same market)
            for q in quotes.values():
                if _ticker_date_is_past(q.ticker):
                    continue
                if (q.yes_ask or 0) <= 0 or (q.no_ask or 0) <= 0:
                    continue
                if (q.yes_ask or 0) + (q.no_ask or 0) >= 1.0:
                    continue
                if 1.0 - (q.yes_ask or 0) - (q.no_ask or 0) - _fee(q.yes_ask or 0) - _fee(q.no_ask or 0) < 0.005:
                    continue
                if (q.yes_ask or 0) < 0.05 or (q.no_ask or 0) < 0.05:
                    continue
                gross = 1.0 - q.yes_ask - q.no_ask
                if gross > 0.10:
                    continue
                key = f"YNC:{q.ticker}"
                if _now - _last_seen.get(key, 0) < TTL_S:
                    logger.debug("TTL skip complement: %s", q.ticker)
                    continue
                book = books.get(q.ticker)
                if not book or getattr(book, "gap_detected", False):
                    continue
                if _now - getattr(book, "updated_at", 0) > 60:
                    continue
                res = _walk_complement(book, book)
                if not res:
                    continue
                cnt, ay, an, net = res
                _last_seen[key] = _now
                opp = {
                    "ticker":               q.ticker,
                    "strategy":             "yes_no_complement",
                    "yes_ask":              round(ay, 4),
                    "no_ask":               round(an, 4),
                    "gross_edge_cents":     round((1.0 - ay - an) * 100, 2),
                    "fees_cents":           round((_fee(ay) + _fee(an)) * 100, 2),
                    "net_edge_cents":       round(net * 100, 2),
                    "executable_contracts": cnt,
                    "detected_at_ts":       _now,
                }
                state.add_opportunity(opp)  # persist() called internally by add_opportunity
                n_arbs += 1
                logger.info("ARB [complement] %s  net=%.2fc  qty=%d  [total=%d]",
                            q.ticker, net * 100, cnt, n_arbs)

            # Strategy 2: CE arb (multi-leg, sum YES asks < 1.0)
            by_event: dict[str, list] = defaultdict(list)
            for q in quotes.values():
                if q.is_stale:
                    continue
                parts = q.ticker.rsplit("-", 1)
                if len(parts) == 2:
                    by_event[parts[0]].append(q)

            for event_prefix, legs in by_event.items():
                if len(legs) < 2 or len(legs) > 20:
                    continue
                # Skip markets whose embedded date is today or past (settled/stale orders)
                if _ticker_date_is_past(event_prefix):
                    continue
                # Skip known sports stat markets with stale/thin books
                if any(event_prefix.upper().startswith(p.upper()) for p in _SPORTS_PREFIXES):
                    continue
                legs_ok = sorted(
                    [l for l in legs if (l.yes_ask or 0) >= 0.05],
                    key=lambda l: (l.yes_ask or 0),
                )
                if len(legs_ok) < 2:
                    continue
                # Require all leg books updated within last 60s (stale = ghost orders)
                if any(_now - getattr(books.get(l.ticker), "updated_at", 0) > 60 for l in legs_ok):
                    continue
                total_yes = sum(l.yes_ask for l in legs_ok)
                if not (0.75 <= total_yes < 1.0):
                    continue
                key = f"CE:{event_prefix}"
                if _now - _last_seen.get(key, 0) < TTL_S:
                    logger.debug("TTL skip CE: %s", event_prefix)
                    continue
                # Structural CE gate: block election seats, nominations, ticket combos, etc.
                try:
                    _struct_legs = [{"ticker": l.ticker, "yes_ask_dollars": str(l.yes_ask)}
                                    for l in legs_ok]
                    struct_ok, _ = _is_structural_ce(event_prefix, _struct_legs)
                    if not struct_ok:
                        continue
                except Exception:
                    pass  # gate failure → allow through (fail-open for structural check)
                try:
                    api_ok, _ = verify_ce_arb_via_api(
                        event_prefix, [l.ticker for l in legs_ok]
                    )
                    if not api_ok:
                        continue
                except Exception:
                    continue
                gross = 1.0 - total_yes
                fees  = sum(_fee(l.yes_ask) for l in legs_ok)
                net   = gross - fees
                if net < 0.005:
                    continue
                _last_seen[key] = _now
                opp = {
                    "ticker":               f"{event_prefix} ({len(legs_ok)} legs)",
                    "strategy":             "collectively_exhaustive",
                    "legs":                 [l.ticker for l in legs_ok],
                    "yes_ask":              round(total_yes, 4),
                    "no_ask":               0.0,
                    "gross_edge_cents":     round(gross * 100, 2),
                    "fees_cents":           round(fees  * 100, 2),
                    "net_edge_cents":       round(net   * 100, 2),
                    "executable_contracts": None,
                    "detected_at_ts":       _now,
                }
                state.add_opportunity(opp)  # persist() called internally by add_opportunity
                n_arbs += 1
                logger.info("ARB [CE] %s  net=%.2fc  legs=%d  [total=%d]",
                            event_prefix, net * 100, len(legs_ok), n_arbs)

        except Exception as exc:
            logger.warning("Scanner loop error: %s", exc)


def main():
    secret = os.environ.get("SYNTHESIS_SECRET_KEY", "").strip()
    if not secret:
        logger.error("SYNTHESIS_SECRET_KEY not set. Add it to .env and retry.")
        sys.exit(1)

    logger.info("Initialising arb daemon...")

    from feeds.orderbook_l2 import L2Cache
    from feeds.synthesis_live import SynthesisWebSocketClient
    from dashboard.live_state import get_live_state
    from dashboard.market_discovery import fetch_synthesis_markets, fetch_liquid_tickers

    state = get_live_state()
    cache = L2Cache()

    tickers, event_groups = fetch_synthesis_markets()
    if not tickers:
        logger.warning("Synthesis discovery empty — falling back to Kalshi volume list")
        tickers = fetch_liquid_tickers(limit=2000, pages_per_window=20)
        event_groups = {}
    logger.info("Subscribing to %d tickers", len(tickers))

    def _on_book(book):
        try:
            state.update_from_book(book)
            state.record_message()
        except Exception as exc:
            logger.warning("Book callback error: %s", exc)

    cache.register_callback(_on_book)

    client = SynthesisWebSocketClient(api_secret=secret, cache=cache, markets=tickers)

    def _on_open(ws):
        state.set_connected(True)
        logger.info("WebSocket connected")

    def _on_close(ws, code, msg):
        state.set_connected(False)
        logger.info("WebSocket closed (code=%s)", code)

    def _on_error(ws, err):
        state.set_connected(False)
        logger.warning("WebSocket error: %s", err)

    client._on_open  = _on_open
    client._on_close = _on_close
    client._on_error = _on_error

    client.start()
    logger.info("WebSocket started")

    # Scanner runs on this main thread (blocks until Ctrl+C)
    def _handle_sigint(sig, frame):
        logger.info("Interrupted — shutting down")
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_sigint)

    try:
        run_scanner(state, cache, event_groups, ws_client=client)
    except KeyboardInterrupt:
        logger.info("Daemon stopped")


if __name__ == "__main__":
    main()
