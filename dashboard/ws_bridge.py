"""
dashboard/ws_bridge.py
======================
Auto-starts the Synthesis WebSocket client from within the Streamlit dashboard.

Uses @st.cache_resource so the client is created exactly ONCE per server
process, regardless of how many times the page reruns or how many browser
tabs are open.  The WebSocket runs in a daemon background thread; Streamlit
pages read its output through the thread-safe LiveState singleton.

Architecture:
    SynthesisWebSocketClient (daemon thread)
          ↓  L2Cache callback (every delta)
    LiveState singleton (RLock-protected dict)
          ↑  read by all Streamlit pages
"""

from __future__ import annotations

import logging
import os
import threading

from dashboard.ws_predict import prefetch as _ws_prefetch
from dashboard.arb_logger import log_opportunity as _log_opp
# live_arb_store.persist() is called automatically by live_state.add_opportunity()
# — no direct import needed here; removing explicit _persist_arb calls that caused
# every arb to be written to the DB twice.

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# -- Gate 0: CE series filter status (module-level, readable by dashboard pages) --
# Set to True once the series classification cache is first loaded from DB.
# Checked by p10_system.py to warn when Gate 0 is inactive.
_gate0_active: bool = False

# -- Prefetch thread guard: prevent spawning duplicate prefetch threads --------
# Each entry is a frozenset of tickers; cleared when the thread finishes.
_prefetch_active: set = set()
_prefetch_lock   = threading.Lock()


def _safe_prefetch(tickers: list) -> None:
    """Spawn a _ws_prefetch daemon thread only if one isn't already running for
    this exact set of tickers.  The guard is released when the thread exits."""
    key = frozenset(tickers)
    with _prefetch_lock:
        if key in _prefetch_active:
            return
        _prefetch_active.add(key)

    def _run():
        try:
            _ws_prefetch(tickers)
        finally:
            with _prefetch_lock:
                _prefetch_active.discard(key)

    threading.Thread(target=_run, daemon=True).start()


def ensure_ws_running():
    """
    Idempotent: ensures the Synthesis WS client is running.
    Call from dashboard/app.py at startup.
    Returns the client object or None if no API key / import error.

    Uses a module-level lock so it's safe to call on every Streamlit rerun.
    """
    return _get_or_start_client()


# -- Module-level singleton (not st.cache_resource - avoids Streamlit context
#    issues when the module is first imported during cold start) ----------------
_client      = None
_client_lock = threading.Lock()
_scanner_cycles = 0   # running total of arb-scanner outer loop iterations
_scan_count  = 0      # alias exposed to LiveState as ws_scan_count
_ws_last_arb_ts: list = [0.0]  # mutable so inner thread can update without global
_ws_last_arb_strategy: list = [""]  # strategy name of most recent arb
_ws_session_start_ts: list = [0.0]  # timestamp when WS session started
# Per-strategy arb counters (session-lifetime totals, not per 60-cycle window)
_arb_count_ync: list = [0]
_arb_count_ce:  list = [0]
_arb_count_me:  list = [0]
_arb_count_th:  list = [0]


def _get_or_start_client():
    """
    Non-blocking: returns immediately and starts the WS client in a daemon
    thread. The app renders at once; the scanner starts a few seconds later
    once market discovery completes. This eliminates the cold-start freeze
    caused by fetch_synthesis_markets() blocking for 5-30s on Streamlit Cloud.
    """
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client

        secret = os.environ.get("SYNTHESIS_SECRET_KEY", "").strip()
        if not secret:
            logger.info(
                "SYNTHESIS_SECRET_KEY not set - Synthesis live feed disabled. "
                "Dashboard will work with DB data only."
            )
            return None

        # Fire the slow startup work (market discovery + WS connect) in a
        # background thread so app.py returns immediately on cold start.
        def _deferred_start():
            global _client
            try:
                _do_start_client()
            except Exception as exc:
                logger.error("WS deferred start failed: %s", exc, exc_info=True)

        _t = threading.Thread(target=_deferred_start, daemon=True, name="ws-deferred-start")
        _t.start()
        # Return a sentinel so callers know startup is in progress
        return "starting"


def _do_start_client():
    """Blocking startup — runs in the deferred background thread."""
    global _client
    try:
        from feeds.orderbook_l2 import L2Cache
        from feeds.synthesis_live import SynthesisWebSocketClient
        from dashboard.live_state import get_live_state
        from dashboard.market_discovery import fetch_synthesis_markets, fetch_liquid_tickers

        state = get_live_state()
        cache = L2Cache()

        tickers, event_groups = fetch_synthesis_markets(force_refresh=True)
        if not tickers:
            logger.warning("Synthesis discovery returned no tickers — falling back to Kalshi list")
            tickers = fetch_liquid_tickers(limit=2000, pages_per_window=20)
            event_groups = {}
        if not tickers:
            _FALLBACK = [
                "KXBOC-26SEP-T2.25", "KXBOC-26SEP-T2.50", "KXBOC-26SEP-T2.75",
                "KXFED-26SEP-T5.25", "KXFED-26SEP-T5.50", "KXFED-26NOV-T5.25",
                "INXU-26SEP30-B5600", "INXU-26SEP30-B5650", "INXU-26SEP30-B5700",
            ]
            logger.warning("All discovery failed — using %d hardcoded tickers", len(_FALLBACK))
            tickers      = _FALLBACK
            event_groups = {}

        # -- Bridge: every L2Book update -> LiveState -----------------------
        _book_cb_errors = 0   # count errors so we don't spam logs on every frame
        def _on_book(book):
            nonlocal _book_cb_errors
            try:
                state.update_from_book(book)
                # record_message() NOT called here — _patched_message already
                # counts every WS frame (including acks/pings) to keep zombie
                # detection accurate; calling it again here double-counts book msgs
            except Exception as exc:          # never crash the WS thread
                _book_cb_errors += 1
                # Log at WARNING (visible in Streamlit Cloud) for the first error
                # and every 100th thereafter, to surface silent failures without
                # flooding the log.
                if _book_cb_errors == 1 or _book_cb_errors % 100 == 0:
                    logger.warning(
                        "Bridge callback error #%d: %s",
                        _book_cb_errors, exc, exc_info=True,
                    )

        cache.register_callback(_on_book)

        # -- Build client, patch open/close to update LiveState status -----
        client = SynthesisWebSocketClient(api_secret=secret, cache=cache, markets=tickers)

        _orig_open    = client._on_open
        _orig_close   = client._on_close
        _orig_message = client._on_message

        def _patched_open(ws):
            _orig_open(ws)
            state.set_connected(True)
            logger.info("Synthesis WS connected - LiveState set to LIVE")

        def _patched_close(ws, code, msg):
            _orig_close(ws, code, msg)
            state.set_connected(False)
            logger.info("Synthesis WS closed (code=%s)", code)

        def _patched_error(ws, error):
            logger.warning("Synthesis WS error: %s", error)
            state.set_connected(False)

        def _patched_message(ws, raw):
            # Record EVERY message as a heartbeat — prevents false-zombie
            # detection when Synthesis sends subscription acks or pings.
            state.record_message()
            _orig_message(ws, raw)

        client._on_open    = _patched_open
        client._on_close   = _patched_close
        client._on_error   = _patched_error
        client._on_message = _patched_message

        client.start()
        _client = client
        logger.info("Synthesis WebSocket client started from dashboard (ws_bridge)")

        # -- Background arb scanner: all 5 arb strategies, 1s scan interval ----
        def _arb_scanner_thread(state=state, cache=cache, event_groups=event_groups):
            import time as _time
            import re as _re
            from collections import defaultdict as _dd
            from datetime import datetime as _dt, timezone as _tz
            from dashboard.ws_predict import (
                _is_structural_ce as _structural_ce_gate,
            )

            # Month map for date-in-ticker detection
            _MONTH_MAP_WS = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                             "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
            _DATE_TICKER_RE = _re.compile(
                r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})",
                _re.IGNORECASE,
            )
            def _date_is_past(ticker: str) -> bool:
                """True if ticker encodes a date that is today or in the past (UTC)."""
                m = _DATE_TICKER_RE.search(ticker)
                if not m:
                    return False
                try:
                    yr = 2000 + int(m.group(1))
                    mo = _MONTH_MAP_WS[m.group(2).upper()]
                    dy = int(m.group(3))
                    return _dt(yr, mo, dy, tzinfo=_tz.utc).date() < _dt.now(_tz.utc).date()
                except Exception:
                    return False

            # Sports stat market prefixes — thin/stale books + complex multi-outcome
            # structures (set spreads, player stats, game lines) that produce
            # systematic false CE/ME signals. Skip all of these entirely.
            _SPORTS_PFX = (
                "KXLALIGA", "KXLIGA",
                "KXNBA", "KXNFL", "KXMLB", "KXNHL",
                "KXEPL", "KXSERIEA", "KXBUNDES", "KXMLS", "KXUCL", "KXUEFA",
                "KXNCAAF", "KXNCAAB", "KXWNBA",
                "KXPGA", "KXGOLF",
                "KXTENNIS", "KXATP", "KXWTA",   # tennis including ATP/WTA tours
                "KXFORMULA", "KXF1",
                "KXUFC", "KXBOXING", "KXMMA",
                "KXNASCAR", "KXINDYCAR",
                "KXSOCCER", "KXCRICKET", "KXRUGBY",
                "KXOLYMPIC", "KXOLYM",
                "KXATPSS",   # ATP set spread — false CE from partial set-outcome legs
                "KXTENNISSPREAD", "KXATPSSPREAD",
            )
            def _is_sports(ticker: str) -> bool:
                u = ticker.upper()
                return any(u.startswith(p) for p in _SPORTS_PFX)

            _last_seen: dict = {}   # key -> last timestamp pushed
            _TTL_S        = 300     # re-push same opportunity after 5 min

            # ── Series classification cache ────────────────────────────────────
            # Loaded from event_series_classifications table (seeded by
            # scripts/classify_event_series.py).  Maps series_prefix ->
            # (market_type, ce_eligible).  Refreshed every hour.
            # Any prefix NOT in the DB is treated as 'unknown' and BLOCKED
            # until the classifier script runs and identifies it.
            _series_ce_cache: dict = {}   # prefix -> (market_type, ce_eligible)
            _series_cache_ts: float = 0.0
            _SERIES_CACHE_TTL = 3600.0

            def _series_prefix_of(event_ticker: str) -> str:
                import re as _rsp
                parts = event_ticker.split("-")
                if len(parts) >= 2:
                    last = parts[-1]
                    if _rsp.match(r'^\d{2}[A-Z]{3}\d{2}', last) or _rsp.match(r'^[A-Z]{1,2}\d{2}[A-Z]?$', last):
                        return "-".join(parts[:-1])
                return event_ticker

            def _load_series_cache():
                nonlocal _series_cache_ts
                import dashboard.ws_bridge as _self_mod
                try:
                    _conn = _get_db_conn()
                    if _conn is None:
                        return
                    with _conn.cursor() as _c:
                        _c.execute(
                            "SELECT series_prefix, market_type, ce_eligible "
                            "FROM event_series_classifications"
                        )
                        _series_ce_cache.clear()
                        for pfx, mtype, ce_ok in _c.fetchall():
                            _series_ce_cache[pfx] = (mtype, bool(ce_ok))
                    _series_cache_ts = _time.monotonic()
                    _self_mod._gate0_active = True
                    logger.debug("Series cache loaded: %d entries", len(_series_ce_cache))
                except Exception as _e:
                    logger.debug("Failed to load series cache: %s", _e)

            def _get_series_ce_eligible(event_ticker: str, sfxs: list) -> tuple:
                """Return (market_type, ce_eligible) for a series.
                Unknown prefixes are stored as 'unknown'/False (blocked) so they
                surface in the Streamlit review page and get re-classified next
                time classify_event_series.py runs."""
                nonlocal _series_cache_ts
                if _time.monotonic() - _series_cache_ts > _SERIES_CACHE_TTL:
                    _load_series_cache()
                pfx = _series_prefix_of(event_ticker)
                if pfx in _series_ce_cache:
                    return _series_ce_cache[pfx]
                # New unseen prefix — store as unknown/blocked for review
                _series_ce_cache[pfx] = ("unknown", False)
                try:
                    _conn = _get_db_conn()
                    if _conn:
                        with _conn.cursor() as _c:
                            _c.execute("""
                                INSERT INTO event_series_classifications
                                    (series_prefix, market_type, ce_eligible,
                                     me_eligible, status, auto_reason,
                                     suffix_pattern, classified_by)
                                VALUES (%s, 'unknown', FALSE, FALSE,
                                        'UNCLASSIFIED', 'new_unseen_prefix',
                                        %s, 'auto')
                                ON CONFLICT (series_prefix) DO NOTHING
                            """, (pfx, sfxs[0] if sfxs else ""))
                        _conn.commit()
                        logger.info(
                            "New unseen series stored for review: %s", pfx)
                except Exception as _e:
                    logger.debug("Failed to store unknown series: %s", _e)
                return ("unknown", False)

            # Kalshi API event info cache:
            #   event_ticker -> (count, mutually_exclusive, title, expiry_ts)
            _kalshi_event_cache: dict = {}
            _KALSHI_EVENT_CACHE_TTL = 10800  # 3 hours

            def _kalshi_event_info(event_ticker: str) -> tuple:
                """Return (market_count, mutually_exclusive, title) for an event.
                Tries elections API first, then trading API.
                Returns (0, None, '') if both fail — callers treat 0 count as BLOCK."""
                import urllib.request as _urlreq
                import json as _json
                _now_ts = _time.monotonic()
                cached = _kalshi_event_cache.get(event_ticker)
                if cached and _now_ts < cached[3]:
                    return cached[0], cached[1], cached[2]
                _hosts = [
                    "https://api.elections.kalshi.com",
                    "https://trading-api.kalshi.com",
                ]
                for _host in _hosts:
                    try:
                        _url = f"{_host}/trade-api/v2/events/{event_ticker}?with_nested_markets=true"
                        _req = _urlreq.Request(_url, headers={"Accept": "application/json"})
                        with _urlreq.urlopen(_req, timeout=3) as _resp:
                            _ev = _json.loads(_resp.read()).get("event", {})
                        if not _ev:
                            continue
                        _markets = _ev.get("markets", [])
                        _open = [m for m in _markets if m.get("status") in ("active", "open")]
                        _count = len(_open) if _open else len(_markets)
                        _me    = _ev.get("mutually_exclusive")
                        _title = (_ev.get("title") or "").lower()
                        _kalshi_event_cache[event_ticker] = (_count, _me, _title, _now_ts + _KALSHI_EVENT_CACHE_TTL)
                        return _count, _me, _title
                    except Exception as _exc:
                        logger.debug("Kalshi event info API error (%s) for %s: %s", _host, event_ticker, _exc)
                _kalshi_event_cache[event_ticker] = (0, None, "", _now_ts + 300)
                return 0, None, ""

            def _kalshi_event_market_count(event_ticker: str) -> int:
                return _kalshi_event_info(event_ticker)[0]

            # Title-based CE guard — runs once we have the event title from the API.
            # Catches patterns that can't be detected from ticker alone: nomination
            # markets, "who will" winner markets, award/prize markets, deadline markets.
            # These patterns mirror ws_predict._is_structural_ce but apply to the title
            # string we get from the REST response (ws_bridge only passes ticker+price
            # to _is_structural_ce, so title patterns there are effectively dead code).
            # ── broad "who/which/what" patterns ──────────────────────────────────
            # Match the SUBSTRING, not a complete phrase — "who will leave" and
            # "who will acquire" are just as non-CE as "who will win".
            _WHO_PREFIX    = "who will"          # any "who will <verb>" continuation
            _WHO_SIGNALS   = ("who wins", "who is leading")   # other forms not covered by prefix
            # "which <word> will" — covers "which bank will", "which company will", etc.
            import re as _re_title
            _WHICH_RE      = _re_title.compile(r'\bwhich \w+ will\b')
            _WHICH_FIXED   = ("which of these", "which of the", "which season")

            # ── award / nomination ────────────────────────────────────────────────
            _AWARD_SIGNALS = (
                "rookie of the year", "most valuable player", " mvp ",
                "cy young", "heisman", "ballon d'or", "nobel prize",
                "oscar", "academy award", "golden globe", "grammy", "emmy",
                "player of the year", "coach of the year", "best actor",
                "best actress", "best director", "best picture",
                "championship mvp", "finals mvp", "game of the year",
                "year award", "of the year award",
            )
            _NOMINATE_SIGNALS = (
                "accepts the nomination", "wins and accepts",
                "wins the nomination",
            )

            # ── deadline / open-ended count ───────────────────────────────────────
            _DEADLINE_SIGNALS = ("before 20", "by 20")

            # ── next-role markets (missing "stays same" leg) ──────────────────────
            _NEXT_ROLE_SIGNALS = (
                "next ceo", "next secretary", "next director", "next head of",
                "next head ", "next chair", "next chairman", "new ceo",
                "new secretary", "next coach", "next commissioner",
                "next manager", "next prime minister", "next president",
                "next speaker", "next leader", "next ambassador",
                "leave office next", "will leave",
                # Sports: "Kevin Durant's Next Team", "LeBron's Next Team"
                "next team", "'s next",
            )

            # ── election/winner markets with non-exhaustive candidate sets ────────
            # "Next German federal election winner?" lists only top parties — more exist.
            # "Next Moldovan presidential election winner?" lists only named candidates.
            # Safe because US Senate "winner?" (SENATEPA, SENATESD) does NOT contain
            # "election winner" in its title — it says "Senate winner?" or "Senate race".
            _ELECTION_WINNER_SIGNALS = (
                "election winner",
                "federal election winner",
                "presidential election winner",
                "parliamentary election winner",
                "prime minister after",
            )

            # ── open-ended non-exhaustive winner markets ──────────────────────────
            # "Top Coding AI", "Top Chinese AI Company", "Top AI Model" etc.
            # These list only a subset of possible winners.
            _TOP_OPEN_SIGNALS = ("top coding", "top chinese", "top ai ", "top model")

            # ── ranking / chart position markets (non-exhaustive candidate set) ───
            _RANKING_SIGNALS = (
                "year in search",
                "#1 on ", "#2 on ", "#3 on ", "#4 on ", "#5 on ",
                "billboard runnerup", "billboard runner-up",
                "biggest returns", "biggest gains",
            )

            def _ce_title_ok(title: str) -> tuple:
                """Return (True, 'ok') if the event title is consistent with CE structure.
                Blocks titles that indicate partial candidate sets or non-exhaustive outcome
                spaces, even when Kalshi returns ME=True."""
                t = title.lower()
                # Broad "who will <anything>" — catches leave/acquire/win/be/etc.
                if _WHO_PREFIX in t:
                    return False, "who_will_partial_candidates"
                if any(s in t for s in _WHO_SIGNALS):
                    return False, "who_will_partial_candidates"
                # "which <word> will" via regex + fixed phrases
                if _WHICH_RE.search(t) or any(s in t for s in _WHICH_FIXED):
                    return False, "which_x_will_partial_set"
                if any(s in t for s in _AWARD_SIGNALS):
                    return False, "award_partial_nominees"
                if any(s in t for s in _NOMINATE_SIGNALS):
                    return False, "nomination_not_ce"
                if any(s in t for s in _DEADLINE_SIGNALS):
                    return False, "deadline_before_date_not_ce"
                if any(s in t for s in _NEXT_ROLE_SIGNALS):
                    return False, "next_role_missing_stays_same"
                if any(s in t for s in _ELECTION_WINNER_SIGNALS):
                    return False, "election_winner_non_exhaustive_candidates"
                if any(s in t for s in _TOP_OPEN_SIGNALS):
                    return False, "top_open_partial_candidates"
                if any(s in t for s in _RANKING_SIGNALS):
                    return False, "ranking_non_exhaustive_candidates"
                return True, "ok"
            _MIN_NET_CENTS = 0.02   # minimum net edge: 2c — below this, slippage + queue risk make execution impractical
            _prev_total   = 0
            _prev_ts      = 0.0
            _MIN_CONTRACTS = 2      # minimum executable depth: 2 contracts
            _last_evict_ts = 0.0
            _EVICT_INTERVAL_S = 120  # evict stale markets every 2 minutes
            _scan_count   = 0        # heartbeat counter
            _connected_since = 0.0   # timestamp when we first saw connected=True

            # Complement arb confirmation window.
            # Synthesis pushes deltas one side at a time — for ~200ms the book
            # appears crossed even when no real arb exists.  We require the same
            # Feed-artifact protection is via structural gates (10c gross cap,
            # 5c price floor, 5s book freshness) — no confirmation delay needed.

            # Build a lookup from ticker -> event_id (for group scanning)
            _ticker_to_event: dict[str, str] = {}
            for _eid, _tks in event_groups.items():
                for _tk in _tks:
                    _ticker_to_event[_tk] = _eid

            # Timestamps for periodic refreshes (separate cadence from eviction)
            _EVENT_GROUPS_REFRESH_S = 6 * 3600   # refresh market list every 6 hours
            _PG_PRUNE_INTERVAL_S    = 24 * 3600  # prune Postgres once per day
            _GC_INTERVAL_S          = 3600        # gc.collect() once per hour only
            _last_eg_refresh_ts  = _time.time()
            _last_pg_prune_ts    = _time.time()
            _last_gc_ts          = _time.time()

            def _fee(p: float) -> float:
                """Kalshi taker fee: min($0.035, ceil(0.07 * P * (1-P) * 100) / 100)"""
                import math as _math
                return min(0.035, _math.ceil(0.07 * p * (1.0 - p) * 100) / 100)

            def _book_no_ask(book) -> float:
                """Best NO ask from the L2 book (0 if none)."""
                lvls = book.no_asks(1) if book else []
                return lvls[0].price if lvls else 0.0

            def _book_yes_ask(book) -> float:
                lvls = book.yes_asks(1) if book else []
                return lvls[0].price if lvls else 0.0

            def _depth_at_price(book, side: str, price: float, tol: float = 0.01) -> int:
                """Sum of quantity at levels within tol of price on given side."""
                lvls = getattr(book, side)(20) if book else []
                return sum(int(l.quantity) for l in lvls
                           if l.price > 0 and abs(l.price - price) <= tol)

            def _walk_complement(book_yes, book_no):
                """
                Walk YES asks on book_yes and NO asks on book_no simultaneously.
                Returns (contracts, avg_yes, avg_no, net_per_contract) or None.
                Works for same-market and cross-market complement pairs.
                Falls back to deriving NO ask from YES bid levels when native
                NO-side data is absent (Synthesis typically sends YES-side deltas only).
                """
                yes_lvls = [(l.price, l.quantity) for l in book_yes.yes_asks(20)
                            if l.price > 0 and l.quantity > 0]
                no_lvls  = [(l.price, l.quantity) for l in book_no.no_asks(20)
                            if l.price > 0 and l.quantity > 0]
                # If native NO ask levels are absent, derive from YES bid complement.
                # no_ask = 1 - yes_bid: the price at which we can effectively buy NO
                # by crossing the YES bid (matching engine treats them as equivalent).
                if not no_lvls:
                    no_lvls = [(round(1.0 - l.price, 6), l.quantity)
                               for l in book_no.yes_bids(20)
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
                    take      = min(yes_rem, no_rem)
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
                if total < _MIN_CONTRACTS:
                    return None
                avg_yes = yes_cost / total
                avg_no  = no_cost  / total
                net_per = 1.0 - avg_yes - avg_no - _fee(avg_yes) - _fee(avg_no)
                return total, avg_yes, avg_no, net_per

            def _numeric_suffix(ticker: str) -> float:
                """Extract the largest numeric value from the last dash-component."""
                last = ticker.rsplit("-", 1)[-1]
                nums = _re.findall(r"\d+\.?\d*", last)
                return float(nums[-1]) if nums else -1.0

            # --- Per-gate funnel counters (accumulated over 60 cycles, then reset) ---
            # YNC (YES/NO complement) strategy gates:
            _cyc_checked          = 0  # markets entering the main filter funnel
            _cyc_blocked_min_tick = 0  # blocked: both legs at Kalshi min tick (0.01)
            _cyc_blocked_threshold = 0 # blocked: event has >5 legs (threshold/bracket)
            _cyc_blocked_floor    = 0  # blocked: price floor (yes/no ask < 0.05)
            _cyc_passed_sum       = 0  # passed yes+no < 1.0 (crossed quote)
            _cyc_passed_gross     = 0  # passed 10c gross cap
            _cyc_passed_qnet      = 0  # passed quote-level net_edge
            _cyc_blocked_ttl      = 0  # blocked: TTL dedup (same arb seen recently)
            _cyc_passed_book      = 0  # passed _walk_complement (book-level)
            # CE (collectively exhaustive) strategy gates:
            _ce_blocked_numeric   = 0  # blocked: sum not in [0.75, 1.0) or net < min
            _ce_blocked_min_tick  = 0  # blocked: >50% legs at min tick
            _ce_blocked_structural = 0 # blocked: structural CE gate
            _ce_blocked_synthesis = 0  # blocked: event not in Synthesis groups
            _ce_blocked_full_leg  = 0  # blocked: not all legs have fresh quotes
            _ce_blocked_depth     = 0  # blocked: insufficient L2 depth
            _ce_blocked_ttl       = 0  # blocked: TTL dedup
            _ce_passed            = 0  # passed all CE gates (arb emitted)
            # ME (mutually exclusive) strategy gates:
            _me_blocked_numeric   = 0  # blocked: numeric/sum/gross/net gate
            _me_blocked_min_tick  = 0  # blocked: >50% legs at min tick
            _me_blocked_synthesis = 0  # blocked: event not in Synthesis groups
            _me_blocked_full_leg  = 0  # blocked: partial leg coverage
            _me_blocked_depth     = 0  # blocked: insufficient depth
            _me_blocked_ttl       = 0  # blocked: TTL dedup
            _me_passed            = 0  # passed all ME gates (arb emitted)
            # TH (threshold order) strategy gates:
            _th_checked           = 0  # total pairs checked
            _th_blocked_monotonicity = 0  # blocked: monotonicity check (ya_i <= ya_j)
            _th_blocked_floor     = 0  # blocked: price floor (ya_i or na_j < 0.05)
            _th_blocked_gross     = 0  # blocked: gross edge > 10c (stale/crossed)
            _th_blocked_net       = 0  # blocked: net edge < min
            _th_blocked_ttl       = 0  # blocked: TTL dedup
            _th_blocked_depth     = 0  # blocked: insufficient L2 depth
            _th_passed            = 0  # passed all TH gates (arb emitted)

            while True:
                _time.sleep(1)   # 1s tick — cheap now (dirty-set scan skips unchanged markets)
                _scan_count += 1
                global _scanner_cycles
                _scanner_cycles += 1
                try:
                    # Periodic memory eviction: remove stale/closed markets.
                    # Always evict regardless of WS silence — a silent feed still
                    # accumulates stale market dicts that never free themselves.
                    _now = _time.time()
                    if _now - _last_evict_ts > _EVICT_INTERVAL_S:
                        # 1. LiveState market dict
                        _evicted = state.evict_stale_markets()
                        if _evicted:
                            logger.info("LiveState eviction: removed %d stale markets", _evicted)
                        # 2. L2Cache book dict (mirrors LiveState but separate)
                        _l2_evicted = cache.evict_stale()
                        if _l2_evicted:
                            logger.info("L2Cache eviction: removed %d stale books", _l2_evicted)
                        # 3. Evict stale _last_seen entries
                        _stale_cutoff = _now - _TTL_S
                        _stale_keys = [k for k, ts in _last_seen.items() if ts < _stale_cutoff]
                        for _sk in _stale_keys:
                            del _last_seen[_sk]
                        if _stale_keys:
                            logger.debug("_last_seen eviction: removed %d stale keys", len(_stale_keys))
                        # 4. SQLite prune: keep only last 7 days (no-op if nothing to delete)
                        try:
                            from dashboard.live_arb_store import prune_sqlite as _prune_sqlite
                            _prune_sqlite()
                        except Exception as _pe:
                            logger.debug("SQLite prune error: %s", _pe)
                        _last_evict_ts = _now

                    # 5. GC — once per hour only (stop-the-world; don't run every 2 min)
                    if _now - _last_gc_ts > _GC_INTERVAL_S:
                        import gc as _gc
                        _gc.collect()
                        _last_gc_ts = _now

                    # 6. Periodic event_groups refresh (every 6 hours).
                    # Fetches the current Synthesis market list and updates
                    # event_groups + _ticker_to_event in-place so the ME/TH
                    # scanner sees new markets that opened since startup.
                    if _now - _last_eg_refresh_ts > _EVENT_GROUPS_REFRESH_S:
                        try:
                            from dashboard.market_discovery import fetch_synthesis_markets as _fsm
                            _new_tickers, _new_eg = _fsm(force_refresh=True)
                            if _new_eg:
                                # Mutate in-place so the scanner's closure sees the update
                                event_groups.clear()
                                event_groups.update(_new_eg)
                                _ticker_to_event.clear()
                                for _eid2, _tks2 in event_groups.items():
                                    for _tk2 in _tks2:
                                        _ticker_to_event[_tk2] = _eid2
                                logger.info(
                                    "event_groups refreshed: %d events, %d tickers",
                                    len(event_groups), len(_ticker_to_event),
                                )
                        except Exception as _eg_err:
                            logger.warning("event_groups refresh failed: %s", _eg_err)
                        _last_eg_refresh_ts = _now

                    # 7. Postgres live_arbs_cloud prune (once per day, keep 30 days).
                    # Prevents the Neon table from growing forever and slowing queries.
                    if _now - _last_pg_prune_ts > _PG_PRUNE_INTERVAL_S:
                        try:
                            from dashboard.live_arb_store import _get_pg_engine as _pg_eng
                            _pg_engine = _pg_eng()
                            if _pg_engine is not None:
                                from sqlalchemy import text as _sqla_text
                                import time as _tmod
                                _pg_cutoff = _tmod.strftime(
                                    "%Y-%m-%dT%H:%M:%SZ",
                                    _tmod.gmtime(_tmod.time() - 30 * 86400),
                                )
                                with _pg_engine.begin() as _pg_conn:
                                    _pg_conn.execute(
                                        _sqla_text(
                                            "DELETE FROM live_arbs_cloud "
                                            "WHERE detected_at < CAST(:cutoff AS TIMESTAMPTZ)"
                                        ),
                                        {"cutoff": _pg_cutoff},
                                    )
                                logger.info(
                                    "Postgres live_arbs_cloud pruned (rows older than %s deleted)",
                                    _pg_cutoff,
                                )
                        except Exception as _pg_err:
                            logger.warning("Postgres prune failed: %s", _pg_err)
                        _last_pg_prune_ts = _now

                    # Heartbeat every 60 scans (~3 min at 3s interval)
                    if _scan_count % 60 == 0:
                        _hb_q = state.market_count()   # O(1) — no dict copy
                        _hb_b = len(cache)             # O(1) — uses __len__
                        _hb_stats = state.get_stats()
                        _hb_msgs  = _hb_stats.get("messages_total", 0)
                        logger.info(
                            "arb-scanner heartbeat: scan#%d quotes=%d books=%d msgs_total=%d",
                            _scan_count, _hb_q, _hb_b, _hb_msgs,
                        )
                        # Diagnose "messages flowing but 0 markets" situation:
                        # If the WS is connected and delivering messages but no
                        # market quotes have been populated after >2 min, the
                        # _on_book callback is likely failing silently or Synthesis
                        # is not sending actual orderbook deltas (only heartbeats).
                        if _hb_stats.get("connected") and _hb_msgs > 60 and _hb_q == 0:
                            logger.warning(
                                "WS DIAGNOSTIC: connected=True, %d messages received, "
                                "but 0 market quotes populated. "
                                "L2Cache has %d books. "
                                "Possible causes: (1) _on_book callback errors (check logs above), "
                                "(2) Synthesis sending only heartbeats / no book deltas, "
                                "(3) subscription not acknowledged. "
                                "book_cb_errors=%d",
                                _hb_msgs, _hb_b, _book_cb_errors,
                            )

                    # Rolling message rate
                    _stats_now = state.get_stats()
                    _total_now = _stats_now.get("messages_total", 0)
                    _now       = _time.time()
                    if _prev_total > 0 and _now > _prev_ts:
                        _rate = (_total_now - _prev_total) / (_now - _prev_ts)
                        state.update_msg_rate(max(0.0, round(_rate, 1)))
                    _prev_total = _total_now
                    _prev_ts    = _now

                    # -- Zombie watchdog: detect connected=True but dead feed --
                    # Two cases: (a) was alive but went silent >120s,
                    #            (b) connected but never sent a single message for >60s.
                    _last_msg_ts = _stats_now.get("last_message_ts", 0) or 0
                    _is_connected = _stats_now.get("connected", False)
                    if _is_connected and _connected_since == 0.0:
                        _connected_since = _now   # record when we first saw connected
                    elif not _is_connected:
                        _connected_since = 0.0    # reset on disconnect
                    _zombie_case_a = _is_connected and _last_msg_ts > 0 and (_now - _last_msg_ts) > 120
                    _zombie_case_b = _is_connected and _last_msg_ts == 0 and _connected_since > 0 and (_now - _connected_since) > 60
                    if _zombie_case_a or _zombie_case_b:
                        _silence_s = int(_now - _last_msg_ts) if _last_msg_ts > 0 else int(_now - _connected_since)
                        logger.warning(
                            "Zombie WS detected: connected=True but no message for %ds — forcing reconnect",
                            _silence_s,
                        )
                        state.set_connected(False)
                        # Close the socket — synthesis_live._run_loop handles
                        # reconnect with backoff internally.  Avoid stop()/start()
                        # because stop() clears _running then start() re-sets it,
                        # leaving the old thread alive alongside the new one (race).
                        try:
                            if _client._ws:
                                _client._ws.close()
                            logger.info("Zombie reconnect: WS socket closed, internal loop will reconnect")
                        except Exception as _ze:
                            logger.warning("Error closing zombie WS socket: %s", _ze)

                    # Event-driven dirty scan: only evaluate markets that received
                    # a WS update since the last cycle.  For ME/TH, expand each
                    # dirty ticker to include all sibling legs of its event so
                    # a one-leg update triggers a full event check.
                    _dirty_quotes = state.pop_dirty_snapshot()
                    if not _dirty_quotes:
                        continue   # nothing changed — skip entire scan cycle

                    # Expand to event siblings for ME/TH correctness.
                    _dirty_events: set = set()
                    for _dtk in _dirty_quotes:
                        _eid_d = _ticker_to_event.get(_dtk) or _dtk.rsplit("-", 1)[0]
                        _dirty_events.add(_eid_d)
                    # Include all event siblings that are in the market dict.
                    # Use snapshot_all() only when dirty set is non-empty (already true here).
                    _all_quotes = state.snapshot_all()
                    _expanded: Dict[str, Any] = dict(_dirty_quotes)
                    for _eid_d in _dirty_events:
                        for _sibling in event_groups.get(_eid_d, []):
                            if _sibling in _all_quotes and _sibling not in _expanded:
                                _expanded[_sibling] = _all_quotes[_sibling]
                    _quotes = _expanded
                    _books = cache

                    # ------------------------------------------------
                    # Strategy 1: YES/NO complement (same market)
                    #
                    # NOTE: On a single exchange, YES ask + NO ask < $1
                    # can't persist — the matching engine crosses them.
                    # Signals here are Synthesis-feed timing artifacts.
                    # We keep the scanner for research (logging how often
                    # the feed shows crossed prices and how quickly they
                    # resolve) but apply strict gates so only genuinely
                    # wide, deep, non-threshold crosses surface.
                    # ------------------------------------------------
                    for _q in _quotes.values():
                        if _date_is_past(_q.ticker) or _is_sports(_q.ticker):
                            continue
                        if _q.yes_ask <= 0 or _q.no_ask <= 0:
                            # None/zero ask — skip gracefully (no book or market not open)
                            continue
                        # Gate 1b: minimum-tick illiquidity filter (mirrors CE/ME Gate 1b).
                        # For a 2-leg YNC arb, ">50% at min tick" means BOTH legs quote
                        # at the Kalshi minimum tick (0.01 = 1c).  A sum of 0.02 would
                        # produce gross=98c — already caught by the 10c gross cap below,
                        # and also by the 5c price floor further down.  This check makes
                        # the gate explicit and symmetric with CE/ME (defense-in-depth).
                        if _q.yes_ask <= 0.01 and _q.no_ask <= 0.01:
                            _cyc_blocked_min_tick += 1
                            logger.debug(
                                "YNC blocked (illiquid): %s both legs at min tick "
                                "(yes_ask=%.2f no_ask=%.2f)",
                                _q.ticker, _q.yes_ask, _q.no_ask,
                            )
                            continue
                        # Block threshold/bracket markets (gold prices,
                        # sports totals, temperature, etc.) — these have
                        # many contracts per event and the Synthesis feed
                        # routinely shows stale crossed prices on them.
                        _eid_c = _ticker_to_event.get(_q.ticker)
                        if not _eid_c:
                            _eid_c = _q.ticker.rsplit("-", 1)[0]
                        _evt_legs_c = event_groups.get(_eid_c, [])
                        if len(_evt_legs_c) > 5:
                            # Event has many contracts — threshold/bracket market
                            _cyc_blocked_threshold += 1
                            continue
                        # (numeric-suffix gate removed — event-leg-count > 5 above already
                        # blocks threshold/bracket markets; the suffix check was incorrectly
                        # rejecting nearly all real binary markets e.g. KXBTC-26SEP-B60000)
                        # Minimum ask floor: prices below 5c signal extremely one-sided
                        # markets where resting orders are stale/erroneous.
                        if _q.yes_ask < 0.05 or _q.no_ask < 0.05:
                            _cyc_blocked_floor += 1
                            continue
                        _cyc_checked += 1
                        if _q.yes_ask + _q.no_ask >= 1.0:
                            logger.debug(
                                "YNC %s: spread not crossed (yes_ask=%.3f no_ask=%.3f sum=%.3f)",
                                _q.ticker, _q.yes_ask, _q.no_ask, _q.yes_ask + _q.no_ask,
                            )
                            continue
                        _cyc_passed_sum += 1
                        # Sanity cap: complement arb > 10c gross is almost always a stale
                        # order, not a real executable arb. Genuine complement arbs are
                        # typically 1-5c gross and vanish within seconds.
                        _gross_q = 1.0 - _q.yes_ask - _q.no_ask
                        if _gross_q > 0.10:
                            logger.debug(
                                "YNC %s: gross=%.2fc > 10c cap (likely stale order)",
                                _q.ticker, _gross_q * 100,
                            )
                            continue
                        _cyc_passed_gross += 1
                        _qnet = _gross_q - _fee(_q.yes_ask) - _fee(_q.no_ask)
                        if _qnet < _MIN_NET_CENTS:
                            logger.debug(
                                "YNC %s: quote-level net=%.4fc < min %.4fc "
                                "(gross=%.2fc fees=%.2fc)",
                                _q.ticker, _qnet * 100, _MIN_NET_CENTS * 100,
                                _gross_q * 100,
                                (_fee(_q.yes_ask) + _fee(_q.no_ask)) * 100,
                            )
                            continue
                        _cyc_passed_qnet += 1
                        _key = f"YNC:{_q.ticker}"
                        if _now - _last_seen.get(_key, 0) < _TTL_S:
                            _cyc_blocked_ttl += 1
                            continue
                        _book = _books.get(_q.ticker)
                        if not _book:
                            logger.debug("YNC %s: no L2 book in cache", _q.ticker)
                            continue
                        # Skip books with sequence gaps (data integrity issue).
                        # No time-based staleness gate on YNC — a resting order's
                        # price is valid until it's cancelled or the market settles.
                        # Price floors (5c), gross cap (10c), and depth gate (≥2)
                        # filter noise without discarding live resting books.
                        if getattr(_book, "gap_detected", False):
                            logger.debug("YNC %s: book has sequence gap — skipping", _q.ticker)
                            continue
                        _res = _walk_complement(_book, _book)
                        if not _res:
                            logger.debug(
                                "YNC %s: _walk_complement returned None "
                                "(thin book or no profitable level, quote gross=%.2fc)",
                                _q.ticker, _gross_q * 100,
                            )
                            continue
                        _cyc_passed_book += 1
                        _cnt, _ay, _an, _net = _res
                        if _net < _MIN_NET_CENTS:
                            logger.debug(
                                "YNC %s: book-walk net=%.4fc < min %.4fc "
                                "(avg_yes=%.3f avg_no=%.3f qty=%d)",
                                _q.ticker, _net * 100, _MIN_NET_CENTS * 100,
                                _ay, _an, _cnt,
                            )
                            continue
                        _gross = 1.0 - _ay - _an
                        _fees  = _fee(_ay) + _fee(_an)
                        # Confidence from executable depth at detection time.
                        # This is the only reliable signal — post-detection
                        # price checks are useless because a real arb that gets
                        # taken in 1s looks identical to a feed artifact.
                        # Depth reflects actual resting liquidity right now.
                        if _cnt >= 50:
                            _conf = "HIGH"    # deep book both sides — very likely real
                        elif _cnt >= 15:
                            _conf = "MED"     # some depth — plausible
                        else:
                            _conf = "LOW"     # thin — could be 1 stale resting order
                        _ync_opp = {
                            "ticker":               _q.ticker,
                            "strategy":             "yes_no_complement",
                            "yes_ask":              round(_ay, 4),
                            "no_ask":               round(_an, 4),
                            "gross_edge_cents":     round(_gross * 100, 2),
                            "fees_cents":           round(_fees  * 100, 2),
                            "net_edge_cents":       round(_net   * 100, 2),
                            "executable_contracts": _cnt,
                            "confidence":           _conf,
                            "detected_at_ts":       _now,
                        }
                        # Persist on first confirmed sighting.
                        # Feed timing artifacts (~200ms) are already blocked by:
                        #   - 10c gross cap (stale/crossed orders have huge gross)
                        #   - 5c price floor (near-zero prices = stale)
                        #   - 5s book freshness gate (stale L2 book rejected)
                        # A 2s confirmation window would kill real arbs that last
                        # only 1-2s — so we log immediately on first clean sighting.
                        _last_seen[_key] = _now
                        state.add_opportunity(_ync_opp)   # also persists to DB
                        _arb_count_ync[0] += 1
                        _ws_last_arb_ts[0] = _now
                        _log_opp(_ync_opp)
                        logger.info(
                            "YNC %s net=%.2fc qty=%d",
                            _q.ticker, _net * 100, _cnt,
                        )
                        _safe_prefetch([_q.ticker])

                    # YNC funnel summary — logged every 60 cycles (~1 min)
                    if _scan_count % 60 == 0:
                        logger.info(
                            "scanner: checked %d markets, %d passed yes+no<1, "
                            "%d passed gross<=10c, %d passed quote net_edge, "
                            "%d passed book walk (YNC cycle)",
                            _cyc_checked, _cyc_passed_sum, _cyc_passed_gross,
                            _cyc_passed_qnet, _cyc_passed_book,
                        )
                        state.record_scanner_funnel({
                            # YNC funnel (accumulated over 60 cycles)
                            "checked":              _cyc_checked,
                            "blocked_min_tick":     _cyc_blocked_min_tick,
                            "blocked_threshold":    _cyc_blocked_threshold,
                            "blocked_floor":        _cyc_blocked_floor,
                            "passed_sum":           _cyc_passed_sum,
                            "passed_gross":         _cyc_passed_gross,
                            "passed_qnet":          _cyc_passed_qnet,
                            "blocked_ttl":          _cyc_blocked_ttl,
                            "passed_book":          _cyc_passed_book,
                            # CE funnel
                            "ce_blocked_numeric":   _ce_blocked_numeric,
                            "ce_blocked_min_tick":  _ce_blocked_min_tick,
                            "ce_blocked_structural": _ce_blocked_structural,
                            "ce_blocked_synthesis": _ce_blocked_synthesis,
                            "ce_blocked_full_leg":  _ce_blocked_full_leg,
                            "ce_blocked_depth":     _ce_blocked_depth,
                            "ce_blocked_ttl":       _ce_blocked_ttl,
                            "ce_passed":            _ce_passed,
                            # ME funnel
                            "me_blocked_numeric":   _me_blocked_numeric,
                            "me_blocked_min_tick":  _me_blocked_min_tick,
                            "me_blocked_synthesis": _me_blocked_synthesis,
                            "me_blocked_full_leg":  _me_blocked_full_leg,
                            "me_blocked_depth":     _me_blocked_depth,
                            "me_blocked_ttl":       _me_blocked_ttl,
                            "me_passed":            _me_passed,
                            # TH funnel
                            "th_checked":           _th_checked,
                            "th_blocked_monotonicity": _th_blocked_monotonicity,
                            "th_blocked_floor":     _th_blocked_floor,
                            "th_blocked_gross":     _th_blocked_gross,
                            "th_blocked_net":       _th_blocked_net,
                            "th_blocked_ttl":       _th_blocked_ttl,
                            "th_blocked_depth":     _th_blocked_depth,
                            "th_passed":            _th_passed,
                            "cycle":                _scanner_cycles,
                            "ws_scan_count":        _scanner_cycles,
                            "ws_last_arb_ts":       _ws_last_arb_ts[0],
                            "ws_last_scan_ts":      _now,
                        })
                        # Reset all counters for the next 60-cycle window
                        _cyc_checked = 0
                        _cyc_blocked_min_tick = 0
                        _cyc_blocked_threshold = 0
                        _cyc_blocked_floor = 0
                        _cyc_passed_sum = 0
                        _cyc_passed_gross = 0
                        _cyc_passed_qnet = 0
                        _cyc_blocked_ttl = 0
                        _cyc_passed_book = 0
                        _ce_blocked_numeric = 0
                        _ce_blocked_min_tick = 0
                        _ce_blocked_structural = 0
                        _ce_blocked_synthesis = 0
                        _ce_blocked_full_leg = 0
                        _ce_blocked_depth = 0
                        _ce_blocked_ttl = 0
                        _ce_passed = 0
                        _me_blocked_numeric = 0
                        _me_blocked_min_tick = 0
                        _me_blocked_synthesis = 0
                        _me_blocked_full_leg = 0
                        _me_blocked_depth = 0
                        _me_blocked_ttl = 0
                        _me_passed = 0
                        _th_checked = 0
                        _th_blocked_monotonicity = 0
                        _th_blocked_floor = 0
                        _th_blocked_gross = 0
                        _th_blocked_net = 0
                        _th_blocked_ttl = 0
                        _th_blocked_depth = 0
                        _th_passed = 0

                    # Group live quotes by event_id for multi-leg strategies
                    _by_event: dict = _dd(list)
                    for _q2 in _quotes.values():
                        # Use Synthesis event grouping if available, else infer
                        _eid = _ticker_to_event.get(_q2.ticker)
                        if not _eid:
                            _parts = _q2.ticker.rsplit("-", 1)
                            _eid   = _parts[0] if len(_parts) == 2 else _q2.ticker
                        _by_event[_eid].append(_q2)

                    for _evt, _legs in _by_event.items():
                        if len(_legs) < 2 or len(_legs) > 50:
                            continue
                        # Skip settled/past-date markets and sports stat markets
                        if _date_is_past(_evt) or _is_sports(_evt):
                            continue

                        # --------------------------------------------
                        # Strategy 2: Collectively exhaustive
                        # Buy all YES; arb when sum(YES asks) < $1
                        #
                        # Gate order (fast local → slow REST):
                        # 1. Sum + net edge (numeric, local)
                        # 2. Structural (ticker-pattern, local)
                        # 3. Synthesis count (local)
                        # 4. Depth (local orderbook)
                        # 5. TTL dedup (local) — BEFORE any REST call to avoid hammering API
                        # 6. Kalshi API count (cached REST, fail-closed)
                        # 7. REST price verify (live REST)
                        # --------------------------------------------
                        _expected_leg_count = len(event_groups.get(_evt, []))
                        # Skip legs whose WS quote hasn't been refreshed in 60s
                        _y_valid = [l for l in _legs
                                    if l.yes_ask > 0
                                    and _now - getattr(l, "updated_at", _now) <= 300]
                        if False and len(_y_valid) >= 2:  # CE disabled: too many false positives
                            _sum_ya = sum(l.yes_ask for l in _y_valid)
                            _gross_ce = 1.0 - _sum_ya
                            _fees_ce  = sum(_fee(l.yes_ask) for l in _y_valid)
                            _net_ce   = _gross_ce - _fees_ce
                            _evt_tk = _legs[0].ticker.rsplit("-", 1)[0]
                            # Gate 0: Series classification DB check.
                            # Every known series is classified by market type.
                            # New/unknown series default to blocked (fail-closed)
                            # and surface in the Streamlit review page.
                            _sfxs_g0 = [l.ticker.rsplit("-", 1)[-1] for l in _y_valid]
                            _mtype, _ce_ok = _get_series_ce_eligible(_evt_tk, _sfxs_g0)
                            if not _ce_ok:
                                _ce_blocked_structural += 1
                                logger.debug(
                                    "CE blocked (market_type=%s): %s",
                                    _mtype, _evt_tk,
                                )
                                continue
                            # Gate 1: quick numeric filter.
                            # Lower bound: sum_ya >= 0.75 (75c minimum) means gross
                            # is at most 25c.  This blocks near-zero-sum cases
                            # (e.g. sum_ya=0.02 → gross=98c) that arise from empty
                            # or stale order books where YES asks are near-zero ticks.
                            # The 0.75 floor is intentionally stronger than the
                            # 0.10 minimum suggested as a safety net — keep it here.
                            if not (0.75 <= _sum_ya < 1.0 and _net_ce >= _MIN_NET_CENTS):
                                _ce_blocked_numeric += 1
                                continue
                            # Gate 1b: minimum-tick illiquidity filter.
                            # If more than half the valid legs quote at the Kalshi
                            # minimum tick (0.01 = 1c), the order books are too thin
                            # to represent real executable liquidity — skip.
                            # Example: 3 of 5 legs at 0.01 → likely stale/empty books.
                            _min_tick_legs = sum(1 for _l in _y_valid if _l.yes_ask <= 0.01)
                            if _min_tick_legs > len(_y_valid) // 2:
                                _ce_blocked_min_tick += 1
                                logger.debug(
                                    "CE blocked (illiquid): %s has %d/%d legs at min tick (0.01)",
                                    _evt, _min_tick_legs, len(_y_valid),
                                )
                                continue
                            # Gate 2: structural CE check (fully local)
                            _struct_legs = [
                                {"ticker": l.ticker, "yes_ask_dollars": str(l.yes_ask)}
                                for l in _y_valid
                            ]
                            _struct_ok, _struct_reason = _structural_ce_gate(_evt_tk, _struct_legs)
                            if not _struct_ok:
                                _ce_blocked_structural += 1
                                logger.debug("CE arb blocked by structural gate: %s (%s)", _evt_tk, _struct_reason)
                                continue
                            # Gate 3: Synthesis leg count — FAIL-CLOSED.
                            # If the event isn't in our Synthesis event_groups, we
                            # don't know the total leg count and CANNOT confirm the
                            # arb is real (e.g. 3 of 20 NBA MVP candidates sum < $1
                            # but we're missing 17 legs — the sum gap IS the "Other"
                            # probability, not mispricing).  Block unknown events.
                            if _expected_leg_count == 0:
                                _ce_blocked_synthesis += 1
                                logger.debug("CE blocked: event %s not in Synthesis groups", _evt)
                                continue
                            # Gate 3b: full-leg coverage required.
                            # A CE arb is only valid when ALL known legs are present with
                            # fresh quotes.  A 3-of-5 scan can appear to sum < $1 simply
                            # because the 2 missing legs are being bought by someone — those
                            # 2 legs could push the true sum above $1.  Partial-leg CE is
                            # NOT a real arb; blocking it here is the correct approach.
                            if len(_y_valid) < _expected_leg_count:
                                _ce_blocked_full_leg += 1
                                logger.debug(
                                    "CE blocked (partial legs): %s has %d/%d legs with fresh quotes",
                                    _evt, len(_y_valid), _expected_leg_count,
                                )
                                continue
                            # Gate 3c: Kalshi REST API verification (leg count + structure).
                            # Single API call returns both:
                            # (a) true market count — blocks partial-leg detections like KXMOON
                            #     (Synthesis shows 2 legs, Kalshi has 8 year-based markets).
                            # (b) mutually_exclusive flag — blocks non-CE events like:
                            #     KXMIDTERMMOV (threshold "at least X%" markets, multiple resolve YES),
                            #     KXFEATURE (multi-artist markets, multiple featured simultaneously).
                            #     mutually_exclusive=False means sum(YES)<$1 is NOT an arb.
                            # Result is cached 3 hours — only one API call per event.
                            _api_count, _api_me, _api_title = _kalshi_event_info(_evt_tk)
                            # Gate 3c-i: explicitly non-ME → definitely not CE
                            if _api_me is False:
                                _ce_blocked_structural += 1
                                logger.debug("CE blocked (ME=False): %s", _evt_tk)
                                continue
                            # Gate 3c-ii: API unreachable → fail-closed
                            if _api_count == 0:
                                _ce_blocked_full_leg += 1
                                logger.debug("CE blocked (API unreachable): %s", _evt_tk)
                                continue
                            # Gate 3c-iii: partial legs visible in WS vs total on Kalshi
                            if len(_y_valid) < _api_count:
                                _ce_blocked_full_leg += 1
                                logger.debug(
                                    "CE blocked (partial legs): %s has %d/%d",
                                    _evt_tk, len(_y_valid), _api_count,
                                )
                                continue
                            # Gate 3d: Ticker suffix pattern analysis.
                            # Threshold markets (P\d+, A\d+, T\d+, numeric) are NOT CE —
                            # multiple legs resolve YES simultaneously (buying "at least 1%"
                            # AND "at least 4%" when margin=10% means both pay out).
                            # Kalshi sometimes labels these mutually_exclusive=True incorrectly,
                            # so we cannot rely on the API field alone.
                            import re as _re_3d
                            _sfxs = [l.ticker.rsplit("-", 1)[-1] for l in _y_valid]
                            _is_pct   = all(_re_3d.match(r'^P\d+$', s) for s in _sfxs)
                            _is_cnt   = all(_re_3d.match(r'^A\d+$', s) for s in _sfxs)
                            _is_thr   = len(_sfxs) > 2 and all(_re_3d.match(r'^T\d+$', s) for s in _sfxs)
                            _is_num   = all(_re_3d.match(r'^\d+(\.\d+)?$', s) for s in _sfxs)
                            if _is_pct or _is_cnt or _is_thr or _is_num:
                                _ce_blocked_structural += 1
                                logger.debug(
                                    "CE blocked (threshold pattern): %s suffixes=%s",
                                    _evt_tk, _sfxs,
                                )
                                continue
                            # Gate 3e: Require Kalshi to explicitly confirm ME=True.
                            # When ME is None (field absent from API response) we cannot
                            # rule out a missing "other / never" outcome — the sum of
                            # named candidate YES prices is naturally <100c when there
                            # is residual probability on unlisted outcomes (e.g. "Elon
                            # stays as CEO", "no SCOTUS confirmation", "no moon landing").
                            # Only pass when Kalshi affirmatively says ME=True.
                            if _api_me is not True:
                                _ce_blocked_structural += 1
                                logger.debug(
                                    "CE blocked (ME not confirmed True, got %s): %s",
                                    _api_me, _evt_tk,
                                )
                                continue
                            # Gate 3f: Title-based CE guard
                            # Even with ME=True, some events are not exhaustive —
                            # e.g. "Who will be the next CEO?" has ME=True (at most one
                            # person becomes CEO) but the market may not cover all
                            # possible outcomes (e.g. no candidate is named at all).
                            # Block if the event title matches known non-CE patterns.
                            if _api_title:
                                _title_ok, _title_reason = _ce_title_ok(_api_title)
                                if not _title_ok:
                                    _ce_blocked_structural += 1
                                    logger.debug(
                                        "CE blocked (title pattern %s): %s title=%r",
                                        _title_reason, _evt_tk, _api_title,
                                    )
                                    continue
                            # Gate 4: depth check (local orderbook)
                            _min_d = min(
                                _depth_at_price(_books.get(l.ticker), "yes_asks", l.yes_ask)
                                for l in _y_valid
                            )
                            if _min_d < _MIN_CONTRACTS:
                                _ce_blocked_depth += 1
                                continue
                            # Gate 5: TTL dedup
                            _ce_key = f"CE:{_evt}"
                            if _now - _last_seen.get(_ce_key, 0) < _TTL_S:
                                _ce_blocked_ttl += 1
                                continue
                            # Gates 6 & 7 removed: Synthesis event_groups now the
                            # sole authority on leg count (fail-closed above).
                            # No Kalshi REST calls needed.
                            _last_seen[_ce_key] = _now
                            _ce_passed += 1
                            _ce_opp  = {
                                "ticker":               f"{_evt} ({len(_y_valid)} legs)",
                                "strategy":             "collectively_exhaustive",
                                "series_market_type":   _mtype,
                                "yes_ask":              round(_sum_ya, 4),
                                "no_ask":               0.0,
                                "gross_edge_cents":     round(_gross_ce * 100, 2),
                                "fees_cents":           round(_fees_ce  * 100, 2),
                                "net_edge_cents":       round(_net_ce   * 100, 2),
                                "executable_contracts": int(_min_d),
                                "legs":                 [l.ticker for l in _y_valid],
                                "detected_at_ts":       _now,
                            }
                            state.add_opportunity(_ce_opp)   # also persists to DB
                            _arb_count_ce[0] += 1
                            _ws_last_arb_ts[0] = _now
                            _log_opp(_ce_opp)
                            _safe_prefetch([l.ticker for l in _y_valid])

                        # --------------------------------------------
                        # Strategy 3: Mutually exclusive
                        # Buy all NO; arb when sum(NO asks) < N−1
                        # (Exactly one outcome wins → N−1 NO contracts pay)
                        #
                        # GUARD: skip threshold/cumulative events (all-numeric
                        # suffixes). In those markets multiple legs can resolve
                        # YES simultaneously, breaking the ME payout formula and
                        # creating real losses when multiple NO positions lose.
                        # --------------------------------------------
                        # Threshold detection — skip events whose leg suffixes indicate
                        # "at least X" markets (multiple NOs can lose simultaneously):
                        # (a) monotone numeric suffixes (e.g. 4.75 < 5.00 < 5.25)
                        # (b) P\d+ = "at least X%" margin markets
                        # (c) A\d+ = "at least X count" markets
                        # (d) T\d+ (>2 legs) = "at least T events" markets
                        import re as _re_me
                        _me_sfxs_raw = [l.ticker.rsplit("-", 1)[-1] for l in _legs]
                        _me_sfxs = [_numeric_suffix(l.ticker) for l in _legs]
                        _me_is_threshold = (
                            (   # monotone numeric ladder
                                all(s >= 0 for s in _me_sfxs)
                                and _me_sfxs == sorted(_me_sfxs)
                                and len(_me_sfxs) >= 3
                            )
                            or all(_re_me.match(r'^P\d+$', s) for s in _me_sfxs_raw)
                            or all(_re_me.match(r'^A\d+$', s) for s in _me_sfxs_raw)
                            or (len(_me_sfxs_raw) > 2 and all(_re_me.match(r'^T\d+$', s) for s in _me_sfxs_raw))
                        )
                        _n_valid = [] if _me_is_threshold else [
                            l for l in _legs
                            if _books.get(l.ticker)
                            and _book_no_ask(_books[l.ticker]) > 0
                            and not getattr(_books[l.ticker], "gap_detected", False)
                            and _now - getattr(_books[l.ticker], "updated_at", _now) <= 300
                        ]
                        if len(_n_valid) >= 2:
                            _N = len(_n_valid)
                            _no_asks = [_book_no_ask(_books[l.ticker]) for l in _n_valid]
                            _sum_na  = sum(_no_asks)
                            # ME gross formula: hold N NO contracts; exactly one leg
                            # resolves YES → the other (N-1) NOs each pay $1.
                            # Gross edge = (N-1)*$1 - sum(NO asks paid).
                            _gross_me = (_N - 1) - _sum_na
                            _fees_me  = sum(_fee(na) for na in _no_asks)
                            _net_me   = _gross_me - _fees_me
                            _me_evt_tk = _legs[0].ticker.rsplit("-", 1)[0]
                            # Gate 1: quick local numeric checks.
                            # sum_na >= 0.90*(N-1): gross <= 10c (stale/empty books
                            # produce near-zero NO asks, inflating gross to N-1 dollars).
                            if not (_sum_na >= 0.90 * (_N - 1) and _gross_me <= 0.10 and _net_me >= _MIN_NET_CENTS):
                                _me_blocked_numeric += 1
                                continue
                            # Gate 1b: minimum-tick illiquidity filter (mirrors CE Gate 1b).
                            # If more than half the legs show a NO ask at the Kalshi minimum
                            # tick (0.01 = 1c), the books are likely stale/empty — skip.
                            _me_min_tick_legs = sum(1 for _na in _no_asks if _na <= 0.01)
                            if _me_min_tick_legs > _N // 2:
                                _me_blocked_min_tick += 1
                                logger.debug(
                                    "ME blocked (illiquid): %s has %d/%d legs at min tick (0.01)",
                                    _evt, _me_min_tick_legs, _N,
                                )
                                continue
                            # Gate 2: Synthesis count — FAIL-CLOSED.
                            # If event not in Synthesis groups we don't know total leg count.
                            # Missing legs on ME = real money loss (one of the unseen legs wins
                            # and none of our NOs cover it). Block unknown events.
                            if _expected_leg_count == 0:
                                _me_blocked_synthesis += 1
                                logger.debug("ME blocked: event %s not in Synthesis groups", _evt)
                                continue
                            if _N < _expected_leg_count:
                                _me_blocked_full_leg += 1
                                logger.debug(
                                    "ME blocked: %s have %d/%d legs",
                                    _evt, _N, _expected_leg_count,
                                )
                                continue
                            # Gate 2c/2d: Kalshi API mutually_exclusive check.
                            # ME payout formula (N-1 NOs pay) only holds when exactly ONE
                            # leg resolves YES.  Require explicit ME=True from Kalshi:
                            # - ME=False → multiple legs can win → real losses on NOs
                            # - ME=None → unknown structure → fail-closed (suppress)
                            # - ME=True → exactly one wins → formula is valid
                            _me_api_count, _me_api_me, _me_api_title = _kalshi_event_info(_me_evt_tk)
                            if _me_api_me is not True:
                                _me_blocked_synthesis += 1
                                logger.debug(
                                    "ME blocked (ME not confirmed True, got %s): %s",
                                    _me_api_me, _me_evt_tk,
                                )
                                continue
                            # Also verify leg count — a missing leg in ME means we hold a NO
                            # on a leg that COULD win without us covering it → full loss.
                            if _me_api_count > 0 and _N < _me_api_count:
                                _me_blocked_full_leg += 1
                                logger.debug(
                                    "ME blocked (partial legs): %s has %d/%d",
                                    _me_evt_tk, _N, _me_api_count,
                                )
                                continue
                            # Gate 3: depth check (local orderbook)
                            _min_d = min(
                                _depth_at_price(_books[l.ticker], "no_asks", _no_asks[i])
                                for i, l in enumerate(_n_valid)
                            )
                            if _min_d < _MIN_CONTRACTS:
                                _me_blocked_depth += 1
                                continue
                            # Gate 4: TTL dedup
                            _me_key = f"ME:{_evt}"
                            if _now - _last_seen.get(_me_key, 0) < _TTL_S:
                                _me_blocked_ttl += 1
                                continue
                            # All gates passed — emit and persist
                            _last_seen[_me_key] = _now
                            _me_passed += 1
                            _avg_na = _sum_na / _N
                            _me_conf = "HIGH" if _min_d >= 50 else ("MED" if _min_d >= 15 else "LOW")
                            _me_opp = {
                                "ticker":               f"{_evt} ({_N} legs)",
                                "strategy":             "mutually_exclusive",
                                "yes_ask":              round(_sum_na, 4),
                                "no_ask":               round(_avg_na, 4),
                                "gross_edge_cents":     round(_gross_me * 100, 2),
                                "fees_cents":           round(_fees_me  * 100, 2),
                                "net_edge_cents":       round(_net_me   * 100, 2),
                                "executable_contracts": int(_min_d),
                                "legs":                 [l.ticker for l in _n_valid],
                                "confidence":           _me_conf,
                                "detected_at_ts":       _now,
                            }
                            state.add_opportunity(_me_opp)   # also persists to DB
                            _arb_count_me[0] += 1
                            _ws_last_arb_ts[0] = _now
                            _log_opp(_me_opp)
                            logger.info(
                                "ME %s net=%.2fc qty=%d legs=%d",
                                _evt, _net_me * 100, _min_d, _N,
                            )
                            _safe_prefetch([l.ticker for l in _n_valid])

                        # --------------------------------------------
                        # Strategy 4+5+6: Threshold / Superset / Transitivity
                        # For events where legs have sortable numeric suffixes,
                        # check ALL cross-market pairs: YES(low) + NO(high) < $1
                        # (guaranteed profit because low-threshold ⊇ high-threshold)
                        # --------------------------------------------
                        _sortable = []
                        for _l in _legs:
                            _num = _numeric_suffix(_l.ticker)
                            if _num >= 0:
                                _sortable.append((_num, _l))
                        if len(_sortable) < 2:
                            continue
                        _sortable.sort(key=lambda x: x[0])

                        # Check all pairs (i, j) where i < j in numeric order
                        # i has lower threshold = superset of j
                        for _si in range(len(_sortable)):
                            _num_i, _leg_i = _sortable[_si]
                            _book_i = _books.get(_leg_i.ticker)
                            if not _book_i:
                                continue
                            if getattr(_book_i, "gap_detected", False):
                                continue
                            if _now - getattr(_book_i, "updated_at", 0) > 300:
                                continue
                            _ya_i = _book_yes_ask(_book_i)
                            if _ya_i <= 0:
                                continue
                            for _sj in range(_si + 1, len(_sortable)):
                                _num_j, _leg_j = _sortable[_sj]
                                if _num_i == _num_j:
                                    continue
                                _book_j = _books.get(_leg_j.ticker)
                                if not _book_j:
                                    continue
                                if getattr(_book_j, "gap_detected", False):
                                    continue
                                if _now - getattr(_book_j, "updated_at", 0) > 300:
                                    continue
                                _na_j = _book_no_ask(_book_j)
                                if _na_j <= 0:
                                    continue
                                _th_checked += 1
                                # Price floor: reject near-zero prices (stale/crossed)
                                if _ya_i < 0.05 or _na_j < 0.05:
                                    _th_blocked_floor += 1
                                    continue
                                # Verify superset direction: YES(i) must be MORE expensive
                                # than YES(j), confirming leg_i covers a LARGER outcome set.
                                # "above $X" / "at least N": lower suffix → higher YES → valid.
                                # "below $X": lower suffix → lower YES → leg_i is SUBSET, not
                                # superset → the payout formula breaks in the middle range.
                                _ya_j = _book_yes_ask(_book_j)
                                if _ya_j > 0 and _ya_i <= _ya_j:
                                    _th_blocked_monotonicity += 1
                                    continue  # leg_i is not the superset — skip this pair
                                # Arb: buy YES_i (superset) + NO_j (subset)
                                # Worst-case payout $1 (when outcome > j threshold)
                                _combined = _ya_i + _na_j
                                if _combined >= 1.0:
                                    continue
                                _gross_th = 1.0 - _ya_i - _na_j
                                # Gross cap: reject if gross > 10c (stale/crossed orders)
                                if _gross_th > 0.10:
                                    _th_blocked_gross += 1
                                    continue
                                _net_th = _gross_th - _fee(_ya_i) - _fee(_na_j)
                                if _net_th < _MIN_NET_CENTS:
                                    _th_blocked_net += 1
                                    continue
                                _th_key = f"TH:{_leg_i.ticker}:{_leg_j.ticker}"
                                if _now - _last_seen.get(_th_key, 0) < _TTL_S:
                                    _th_blocked_ttl += 1
                                    continue
                                # Verify executable depth
                                _d_yes = _depth_at_price(_book_i, "yes_asks", _ya_i)
                                _d_no  = _depth_at_price(_book_j, "no_asks",  _na_j)
                                _exec  = min(_d_yes, _d_no)
                                if _exec < _MIN_CONTRACTS:
                                    _th_blocked_depth += 1
                                    continue
                                _last_seen[_th_key] = _now
                                _th_passed += 1
                                _fees_th  = _fee(_ya_i) + _fee(_na_j)
                                _th_conf  = "HIGH" if _exec >= 50 else ("MED" if _exec >= 15 else "LOW")
                                _th_opp = {
                                    "ticker":               f"{_leg_i.ticker} / {_leg_j.ticker}",
                                    "strategy":             "threshold_order",
                                    "yes_ask":              round(_ya_i, 4),
                                    "no_ask":               round(_na_j, 4),
                                    "gross_edge_cents":     round(_gross_th * 100, 2),
                                    "fees_cents":           round(_fees_th  * 100, 2),
                                    "net_edge_cents":       round(_net_th   * 100, 2),
                                    "executable_contracts": _exec,
                                    "legs":                 [_leg_i.ticker, _leg_j.ticker],
                                    "confidence":           _th_conf,
                                    "detected_at_ts":       _now,
                                }
                                state.add_opportunity(_th_opp)   # also persists to DB
                                _arb_count_th[0] += 1
                                _ws_last_arb_ts[0] = _now
                                _log_opp(_th_opp)
                                logger.info(
                                    "TH %s/%s net=%.2fc qty=%d",
                                    _leg_i.ticker, _leg_j.ticker, _net_th * 100, _exec,
                                )
                                _safe_prefetch([_leg_i.ticker, _leg_j.ticker])

                except Exception as _exc:
                    logger.warning("arb-scanner iteration error: %s", _exc, exc_info=False)

        _scanner = threading.Thread(
            target=_arb_scanner_thread, daemon=True, name="arb-scanner"
        )
        _scanner.start()
        logger.info("Background arb scanner started (1s tick, event-driven dirty scan, YNC+ME+TH active, CE disabled)")

        # -- Background L2 writer: persist full order book to l2_snapshots every 60s --
        def _l2_writer_thread(cache=cache):
            """
            Every 60 seconds, snapshot the full L2Cache and write to l2_snapshots.
            This replaces the need to run scripts/synthesis_l2_writer.py separately.
            """
            import time as _time
            import json as _json
            from datetime import datetime, timezone

            INTERVAL_S = 60

            def _write_l2(ts: datetime):
                snap = cache.snapshot()
                if not snap:
                    return
                rows = []
                ts_str = ts.isoformat()
                for market_id, book in snap.items():
                    tob = cache.top_of_book(market_id)
                    if tob is None:
                        continue
                    yes_bid = tob.get("yes_bid")
                    yes_ask = tob.get("yes_ask")
                    book_data = {
                        "yes_bids": [[lvl.price, lvl.quantity] for lvl in book.yes_bids(10)],
                        "yes_asks": [[lvl.price, lvl.quantity] for lvl in book.yes_asks(10)],
                        "no_bids":  [[lvl.price, lvl.quantity] for lvl in book.no_bids(10)],
                        "no_asks":  [[lvl.price, lvl.quantity] for lvl in book.no_asks(10)],
                        "source": "synthesis",
                    }
                    rows.append({
                        "market_id":    market_id,
                        "snapped_at":   ts_str,
                        "yes_best_bid": float(yes_bid) if yes_bid is not None else None,
                        "yes_best_ask": float(yes_ask) if yes_ask is not None else None,
                        "book_json":    _json.dumps(book_data),
                        "sequence":     book.sequence,
                    })
                if not rows:
                    return
                try:
                    import sys as _sys, os as _os
                    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
                    from database.repository import get_engine as _ge
                    from sqlalchemy import text as _text
                    engine = _ge()
                    with engine.begin() as conn:
                        conn.execute(_text("""
                            INSERT INTO l2_snapshots
                                (market_id, snapped_at, yes_best_bid, yes_best_ask,
                                 book_json, sequence)
                            SELECT
                                :market_id,
                                CAST(:snapped_at AS TIMESTAMPTZ),
                                :yes_best_bid,
                                :yes_best_ask,
                                CAST(:book_json AS jsonb),
                                :sequence
                            ON CONFLICT (market_id, snapped_at) DO NOTHING
                        """), rows)
                    logger.debug("L2 writer: wrote %d snapshots to l2_snapshots", len(rows))
                except Exception as exc:
                    logger.debug("L2 writer: DB write failed: %s", exc)

            _time.sleep(20)  # let WS warm up before first write
            while True:
                _time.sleep(INTERVAL_S)
                try:
                    _write_l2(datetime.now(timezone.utc))
                except Exception as exc:
                    logger.debug("L2 writer: unexpected error: %s", exc)

        _l2_writer = threading.Thread(
            target=_l2_writer_thread, daemon=True, name="l2-writer"
        )
        _l2_writer.start()
        logger.info("Background L2 writer started (60s interval → l2_snapshots)")

        # -- Yield/VIX writer: fetch from Yahoo Finance every 5 min → ext_market_daily --
        def _yield_vix_writer_thread():
            import time as _time
            import requests as _requests

            INTERVAL_S = 300

            def _fetch_yields_vix() -> dict:
                """Fetch live US 2Y, US 10Y, CAD 10Y yields and VIX from Yahoo Finance."""
                tickers = {"^TNX": "us10y", "^IRX": "us2y", "^VIX": "vix"}
                result: dict = {}
                try:
                    for sym, key in tickers.items():
                        url = (
                            f"https://query1.finance.yahoo.com/v8/finance/chart/"
                            f"{sym}?interval=1d&range=1d"
                        )
                        r = _requests.get(
                            url, timeout=5,
                            headers={"User-Agent": "Mozilla/5.0"},
                        )
                        if r.status_code == 200:
                            data = r.json()
                            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                            result[key] = round(float(price), 4)
                    # CAD 10Y
                    url = (
                        "https://query1.finance.yahoo.com/v8/finance/chart/"
                        "CA10YT%3DXX?interval=1d&range=1d"
                    )
                    r = _requests.get(
                        url, timeout=5,
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    if r.status_code == 200:
                        data = r.json()
                        price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                        result["cad10y"] = round(float(price), 4)
                except Exception as _exc:
                    logger.debug("_fetch_yields_vix error: %s", _exc)
                return result

            _time.sleep(30)  # let WS warm up
            while True:
                try:
                    prices = _fetch_yields_vix()
                    if prices:
                        from dashboard import data_layer as _dl
                        _dl.upsert_ext_prices(prices)
                        logger.debug("Yield/VIX writer: upserted %d records", len(prices))
                except Exception as _exc:
                    logger.debug("Yield/VIX writer error: %s", _exc)
                _time.sleep(INTERVAL_S)

        _yield_vix_writer = threading.Thread(
            target=_yield_vix_writer_thread, daemon=True, name="yield-vix-writer"
        )
        _yield_vix_writer.start()
        logger.info("Yield/VIX writer started (5min interval → ext_market_daily)")

        # -- Cross-asset live feed: poll yfinance + BOC every 5 minutes ----
        try:
            from analysis.live_feed import start_daemon as _start_cross_asset
            _start_cross_asset(interval_s=300)
            logger.info("Cross-asset live feed daemon started (5min → cross_asset_live)")
        except Exception as exc:
            logger.warning("Cross-asset live feed failed to start: %s", exc)

        return client

    except ImportError as exc:
        logger.error("WS bridge import error: %s", exc)
    except Exception as exc:
        logger.error("WS bridge startup error: %s", exc, exc_info=True)


def get_arb_counts() -> dict:
    """Return session-lifetime per-strategy arb counters (incremented on every persisted arb)."""
    return {
        "ync": _arb_count_ync[0],
        "ce":  _arb_count_ce[0],
        "me":  _arb_count_me[0],
        "th":  _arb_count_th[0],
    }


def reset_arb_counts() -> None:
    """Reset all per-strategy arb counters to zero (e.g. on session restart)."""
    _arb_count_ync[0] = 0
    _arb_count_ce[0]  = 0
    _arb_count_me[0]  = 0
    _arb_count_th[0]  = 0
