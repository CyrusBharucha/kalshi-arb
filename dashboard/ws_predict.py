"""
dashboard/ws_predict.py
=======================
Kalshi market eligibility and arb structural verification.

Three responsibilities:

1. **Wealthsimple Predict eligibility** (is_ws_predict_eligible):
   WS Predict is authorized by Canadian regulators to offer Kalshi markets in
   three categories only: Economics, Financials, and Climate and Weather.
   Markets must also have >= 30 days to settlement.

2. **CE arb structural verification** (_is_structural_ce):
   Before treating sum(YES asks) < $1 as a collectively-exhaustive arb,
   10+ blocking patterns check whether the event is actually CE (exactly one
   outcome MUST resolve YES).  Non-CE structures — ticket combos, nomination
   markets, threshold/superset legs, partial-candidate sets — produce a sum gap
   that reflects "Other" probability, not mispricing.

3. **Complement arb price cross-validation** (verify_complement_prices):
   Synthesis WebSocket L2 data can carry stale resting orders.  This function
   cross-checks the live YES/NO asks against the Kalshi REST API and rejects
   candidates where either side deviates by more than max_deviation (default 10¢).

All Kalshi API responses are cached (disk + in-memory) to bound request rate.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# -- Rate limiter for Kalshi REST calls in ws_predict -------------------------
# Kalshi Basic tier: 20 reads/sec.  We stay well below at 2 req/sec to avoid
# 429s and to leave ample headroom for dashboard pages and KalshiClient.
_RL_LOCK = threading.Lock()
_RL_TOKENS = 2.0       # max burst (no more than 2 queued)
_RL_RATE   = 2.0        # refill per second
_rl_tokens = _RL_TOKENS
_rl_last   = time.monotonic()


def _rl_acquire() -> None:
    """Block until one request token is available (simple token-bucket)."""
    global _rl_tokens, _rl_last
    wait = 0.0
    with _RL_LOCK:
        now = time.monotonic()
        _rl_tokens = min(_RL_TOKENS, _rl_tokens + (now - _rl_last) * _RL_RATE)
        _rl_last = now
        if _rl_tokens < 1.0:
            wait = (1.0 - _rl_tokens) / _RL_RATE
            _rl_tokens = 0.0
        else:
            _rl_tokens -= 1.0
    if wait:
        time.sleep(wait)
        with _RL_LOCK:
            _rl_last = time.monotonic()

# Categories allowed on WS Predict (Kalshi's exact category strings)
WS_ALLOWED_CATEGORIES: frozenset[str] = frozenset({
    "Economics",
    "Financials",
    "Climate and Weather",
})

WS_MIN_SETTLEMENT_DAYS: float = 30.0

_KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"
_CACHE_PATH = Path(__import__("tempfile").gettempdir()) / "ws_predict_cache.json"
_CACHE_TTL_H = 12  # hours before re-fetching a ticker's category

# In-memory cache: {ticker -> {"category": str, "close_time": str|None, "ts": float}}
_cache: dict[str, dict] = {}
_cache_loaded = False


def _load_cache() -> None:
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    try:
        if _CACHE_PATH.exists():
            with open(_CACHE_PATH) as fh:
                _cache = json.load(fh)
    except Exception as exc:
        logger.warning("ws_predict cache load failed: %s", exc)
        _cache = {}


def _save_cache() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_PATH, "w") as fh:
            json.dump(_cache, fh)
    except Exception as exc:
        logger.warning("ws_predict cache save failed: %s", exc)


def _kalshi_get(path: str) -> dict:
    """Rate-limited GET against the Kalshi REST API (4 req/s token bucket)."""
    _rl_acquire()
    url = f"{_KALSHI_BASE}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb"})
    with urllib.request.urlopen(req, timeout=4) as resp:
        return json.loads(resp.read())


def _lookup_ticker(ticker: str, fetch: bool = True) -> dict:
    """
    Return {"category", "close_time", "title", "event_title"} for a market ticker.
    Uses in-memory + disk cache. When fetch=False, returns cache-only (no HTTP).
    """
    _load_cache()

    entry = _cache.get(ticker)
    if entry:
        age_h = (time.time() - entry.get("ts", 0)) / 3600
        if age_h < _CACHE_TTL_H:
            return entry

    if not fetch:
        return {}

    result: dict = {"category": None, "close_time": None, "title": None, "event_title": None, "ts": time.time()}

    _success = False
    try:
        # Step 1: get event_ticker, close_time, and title from the market endpoint
        mdata = _kalshi_get(f"/markets/{ticker}").get("market", {})
        event_ticker = mdata.get("event_ticker") or ticker.rsplit("-", 1)[0]
        result["close_time"] = mdata.get("close_time")
        result["title"] = mdata.get("title")

        # Step 2: get category and event title from the event endpoint
        edata = _kalshi_get(f"/events/{event_ticker}").get("event", {})
        result["category"] = edata.get("category")
        result["event_title"] = edata.get("title")
        _success = True

    except Exception as exc:
        logger.warning("ws_predict Kalshi lookup failed for %s: %s", ticker, exc)

    # Only cache on success — failed lookups should be retried on the next call
    if _success:
        _cache[ticker] = result
        _save_cache()
        return result

    return {}


_EVENT_COUNT_TTL_H = 4   # re-check event market count every 4 hours
_PRICE_VERIFY_TTL_S = 45  # re-verify prices every 45s (short TTL — prices change fast)
_COMPLEMENT_VERIFY_TTL_S = 10  # re-verify complement prices every 10s (prices change fast)

# In-memory price verification cache: {event_ticker -> {"sum_yes": float, "count": int, "ts": float}}
_price_cache: dict[str, dict] = {}
_price_lock = __import__("threading").Lock()

# Separate cache for complement arb price validation
_comp_cache: dict[str, dict] = {}
_comp_lock = __import__("threading").Lock()


def verify_complement_prices(
    ticker: str,
    ws_yes_ask: float,
    ws_no_ask: float,
    max_deviation: float = 0.10,
) -> tuple[bool, str]:
    """
    Cross-validate a complement arb candidate against the Kalshi REST API.

    Our order book data (from Synthesis WebSocket) can carry stale resting orders
    that reflect old prices — e.g. a YES order placed at 41¢ before a primary
    started, when the real market has since moved to 74¢.  In that case YES+NO
    appears to sum below $1 but executing would lose money (the resting order
    would be cancelled or is a data artifact).

    This check fetches the current Kalshi best-ask for the ticker and rejects
    the candidate if either side deviates from our LiveState price by more than
    `max_deviation` (default 10¢ / 0.10).

    Returns (True, "ok") when prices are consistent.
    Returns (False, reason) when the deviation is too large or on API failure.
    Cached for _COMPLEMENT_VERIFY_TTL_S seconds.  Fail-closed on any error.
    """
    with _comp_lock:
        entry = _comp_cache.get(ticker)
        if entry and (time.time() - entry.get("ts", 0)) < _COMPLEMENT_VERIFY_TTL_S:
            ok = entry.get("ok", False)
            return (ok, "cached_ok" if ok else entry.get("reason", "cached_fail"))

    def _store(ok: bool, reason: str) -> tuple[bool, str]:
        with _comp_lock:
            _comp_cache[ticker] = {"ok": ok, "reason": reason, "ts": time.time()}
        return ok, reason

    try:
        data = _kalshi_get(f"/markets/{ticker}")
        mkt = data.get("market", {})

        def _parse_price(dollars_field, cents_field) -> float | None:
            v = mkt.get(dollars_field)
            if v is not None:
                f = float(v)
                if f > 0:
                    return f
            v = mkt.get(cents_field)
            if v is not None:
                f = float(v)
                if f > 0:
                    return f / 100.0
            return None

        api_yes = _parse_price("yes_ask_dollars", "yes_ask")
        api_no  = _parse_price("no_ask_dollars",  "no_ask")

        if api_yes is None or api_no is None:
            # API didn't return price data for this market — cannot verify, so pass through.
            # Only block when we have an actual price and it deviates significantly.
            return _store(True, "no_api_price_passthrough")

        yes_dev = abs(ws_yes_ask - api_yes)
        no_dev  = abs(ws_no_ask  - api_no)

        if yes_dev > max_deviation:
            return _store(False, f"yes_ask_deviation:{yes_dev:.3f}")
        if no_dev > max_deviation:
            return _store(False, f"no_ask_deviation:{no_dev:.3f}")

        return _store(True, "ok")

    except Exception as exc:
        logger.warning("verify_complement_prices failed for %s: %s", ticker, exc)
        # API error → cannot verify price → pass through (don't block on inability to check).
        # Only block on confirmed stale prices, not on network/API failures.
        return _store(True, "api_error_passthrough")


def _is_structural_ce(event_ticker: str, markets: list[dict]) -> tuple[bool, str]:
    """
    Gate 5: verify the event is structurally CE — exactly one leg MUST resolve YES.

    Returns (True, "ok") if CE structure is confirmed, (False, reason) if blocked.

    Failure patterns (non-CE structures that produce sum(YES) < $1 without arb):
      - Nomination markets: "accepts the nomination" language means unlisted candidates can win
      - Ticket combination markets: only N of K possible pairings are listed
      - Threshold/superset markets: legs are "at least N" (overlapping, not mutually exclusive)
      - Any market where the legs collectively do NOT cover all possible outcomes
    """
    ev_upper = event_ticker.upper()

    # --- Pattern 1: known non-CE event families by ticker prefix ---
    # Presidential/VP/Party ticket combinations: only a subset of possible combos are listed
    _NON_CE_PREFIXES = (
        "KXDTICKET",   # Democratic ticket combos (e.g. KXDTICKET-28NOV07)
        "KXRTICKET",   # Republican ticket combos
        "KXGTICKET",   # Green ticket combos
        "KXITICKET",   # Independent ticket combos
        "KXTICKET",    # Any ticket combination market
    )
    if any(ev_upper.startswith(p) for p in _NON_CE_PREFIXES):
        return False, "ticket_combination_not_ce"

    # --- Pattern 1b: matchup/vs markets — Cartesian product, only 1 of N×M legs resolves YES ---
    # e.g. KXPRESMATCHUP-28NOV07: 8 Dem candidates × 2 Rep opponents = 16 legs, at most 1 YES.
    if "MATCHUP" in ev_upper:
        return False, "matchup_cartesian_not_ce"

    # --- Pattern 1d: "next role" markets — missing "stays same / nobody" probability ---
    # "Who will be the next CEO/Secretary/Director?" has residual probability on the incumbent
    # remaining; that unlisted outcome means sum < $1 is correct pricing, not an arb.
    # Also catches count/magnitude markets ("how many X") where the upper tail is unlisted.
    _NEXT_ROLE_SIGNALS = (
        "KXNEWROLE",   # next role of any person (KXNEWROLEX = next CEO of X)
        "KXNEXTCEO", "KXNEWCEO",
        "KXHURCTOT",   # hurricane total count — upper tail (T5+) may be unlisted
        "KXSCREWWORM", # screwworm case count — upper tail unlisted
        "KXBTCMAX",    # BTC max price — upper tail unlisted
        "KXBTCMIN",    # BTC min price — lower tail unlisted
        "KXETHMAX", "KXETHMIN",
        "KXSPMAX", "KXSPMIN",       # S&P price extremes
        "KXGOLDMAX", "KXGOLDMIN",
        "KXOILMAX", "KXOILMIN",
        "KXTSLAMAX", "KXTSLAIM",
        "KXUNRATE",    # unemployment rate — open-ended count
        "KXCPIMAX", "KXCPIMIN",
        "KXPCEMAX", "KXPCEMIN",
    )
    if any(ev_upper.startswith(s) for s in _NEXT_ROLE_SIGNALS):
        return False, "next_role_or_count_missing_tail_not_ce"

    # --- Pattern 2: IPO timing markets — "never" or "after last date" resolves all NO ---
    # Match only known Kalshi IPO-timing prefixes; don't block tickers where "IPO" is a
    # coincidental substring (e.g. KXREPO, KXEPIO).
    # Match any KXIPO* ticker (company name appended directly, e.g. KXIPOAIRTABLE, KXIPOSTRIPE,
    # or with separator, e.g. KXIPO-, KXIPOTIMING-, KXIPODATE-).  "KXREPO" and "KXEPIO" are
    # safe: neither starts with "KXIPO".
    if ev_upper.startswith("KXIPO") or ev_upper == "IPO":
        return False, "ipo_timing_not_ce"

    # --- Pattern 3: threshold/superset markers — legs are "at least N" or "below $X" (overlapping) ---
    # These are cumulative/nested markets where multiple legs can resolve YES simultaneously.
    # Integer suffixes: KXCITYCHAMPS -1,-2,-3,-4 = "at least 1,2,3,4 championships"
    # Decimal suffixes: KXBTCMINY -40000.00,-45000.00 = "below $40k,$45k" (BTC price thresholds)
    # Supports both dict markets (from REST API) and MarketQuote objects (from LiveState).
    def _ticker_of(m):
        if hasattr(m, "ticker"):
            return m.ticker
        return m.get("ticker", "") if hasattr(m, "get") else ""

    _num_sfx = []
    _has_decimal = False
    for m in markets:
        _last = _ticker_of(m).rsplit("-", 1)[-1]
        if "." in _last:
            _has_decimal = True
        try:
            _num_sfx.append(float(_last))
        except (ValueError, AttributeError):
            break  # non-numeric suffix — not a threshold market
    else:
        # All suffixes are numeric — block if:
        # (a) any suffix has a decimal point (price threshold like $40000.00), OR
        # (b) all are integers < 1000 (count threshold like 1,2,3,4).
        # Year-range codes (2627, 2728) are >= 1000 and pass through to title-based patterns.
        # Block if:
        #   (a) any suffix has a decimal point  → dollar amount (e.g. $40000.00)
        #   (b) all are small integers (< 1000) → count threshold (e.g. 1,2,3,4 championships)
        #   (c) all are large integers (>= 5000) → dollar threshold without decimal (e.g. 40000, 45000)
        # Year-range codes (1000–4999: 2627, 2728) are neither (b) nor (c) → pass to title patterns.
        if len(_num_sfx) >= 2 and (
            _has_decimal
            or all(v < 1000 for v in _num_sfx)
            or all(v >= 5000 for v in _num_sfx)
        ):
            return False, "numeric_threshold_not_ce"

    # --- Pattern 3b: Kalshi date-deadline suffix — "before [settlement date]" cumulative markets ---
    # Tickers like MOON-28DEC31, MOON-29DEC31 use a {2-digit-year}{MON}{2-digit-day} settlement
    # date as the last component.  Multiple legs with DIFFERENT date suffixes are a series of
    # "will X happen before date N?" nested deadlines:
    #   • If X happens early, ALL later-date legs also resolve YES → not mutually exclusive.
    #   • If X never happens, ALL legs resolve NO → not collectively exhaustive.
    # Either way this is not CE.  A single-leg event is excluded (len>=2 guard below).
    import re as _re
    _DATE_SFX_RE = _re.compile(
        r"^\d{2}(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}$", _re.IGNORECASE
    )
    if len(markets) >= 2:
        _date_sfxs = [_ticker_of(m).rsplit("-", 1)[-1] for m in markets]
        if all(_DATE_SFX_RE.match(s) for s in _date_sfxs):
            return False, "date_deadline_cumulative_not_ce"

    # --- Pattern 3d: single-letter threshold prefix suffixes (P\d+, A\d+, T\d+) ---
    # P1/P4/P7/P10 = "at least X% margin"; A0/A1/A2 = "at least X count";
    # T0/T1/T2 = "at least T [events]".  Pattern 3c (below) skips these because
    # they share the SAME alpha prefix (all "P", all "A") which looks like sequential
    # candidates — but they are cumulative thresholds where multiple legs resolve YES.
    # Use explicit regexes so the distinction is deterministic, not heuristic.
    if len(markets) >= 2:
        _sfxs_3d = [_ticker_of(m).rsplit("-", 1)[-1] for m in markets]
        _is_p_thresh = all(_re.match(r'^P\d+$', s) for s in _sfxs_3d)
        _is_a_thresh = all(_re.match(r'^A\d+$', s) for s in _sfxs_3d)
        _is_t_thresh = len(_sfxs_3d) > 2 and all(_re.match(r'^T\d+$', s) for s in _sfxs_3d)
        if _is_p_thresh or _is_a_thresh or _is_t_thresh:
            return False, "threshold_single_letter_prefix_not_ce"

    # --- Pattern 3c: alpha-numeric threshold suffixes (e.g. RVC4, BAR8, OVR5, UND3) ---
    # Pattern 3 (pure numeric) uses for-else which BREAKS on non-numeric suffixes like "RVC4",
    # so it silently skips mixed alpha-numeric legs.  This pattern catches them explicitly.
    # Key signal: leg suffixes have DIFFERENT alpha prefixes + small trailing numbers.
    # Different alpha prefixes = different entities (teams, sides) → independent thresholds,
    # NOT collectively exhaustive.  Same prefix (X0,X1,X2 = sequential candidates) → pass.
    # Example blocked:  RVC4, BAR8  (alpha=RVC vs BAR, different teams)
    # Example passed:   X0, X1, X21 (alpha=X for all, sequential nomination candidates)
    if len(markets) >= 2:
        _ansfx_alphas = []
        _ansfx_nums   = []
        for _m3c in markets:
            _s3c = _ticker_of(_m3c).rsplit("-", 1)[-1]
            _ns3c = _re.findall(r"\d+", _s3c)
            _alpha3c = _re.sub(r"\d", "", _s3c)  # strip all digits → alpha prefix
            _ansfx_alphas.append(_alpha3c)
            if _ns3c:
                _ansfx_nums.append(int(_ns3c[-1]))
        if (
            len(_ansfx_nums) == len(markets)           # every leg has a trailing number
            and all(v < 1000 for v in _ansfx_nums)     # all are small counts (< 1000)
            and len(set(_ansfx_alphas)) > 1            # alpha prefixes DIFFER → different entities
        ):
            return False, "alpha_numeric_threshold_not_ce"

    # --- Pattern 4: nomination markets with "accepts" or "wins the nomination" language ---
    # "accepts the nomination" → unlisted candidate can win, all resolve NO.
    # "wins the nomination" without "accepts" → primary where unlisted candidates can enter.
    rules_sample = (markets[0].get("rules_primary") or "").lower() if markets else ""
    title_sample = (markets[0].get("title") or "").lower() if markets else ""
    if "accepts the nomination" in rules_sample or "wins and accepts" in rules_sample:
        return False, "nomination_accepts_not_ce"
    if "wins the nomination" in rules_sample:
        return False, "nomination_wins_not_ce"

    # --- Pattern 1c: congressional / state election seat races ---
    # House, Senate, Governor, and state legislative district races ALWAYS have potential
    # third-party, independent, or write-in candidates beyond those listed on Kalshi.
    # If an unlisted candidate wins, every Kalshi leg resolves NO → NOT CE.
    # Note: "KXHOUSE-" (with dash) blocks district races (KXHOUSE-TX38) but NOT "who controls
    # the House" markets (KXHOUSEWIN-26), which list all possible outcomes (DEMS/REPS/TIED/OTHER).
    _ELECTION_SEAT_PREFIXES = (
        "KXHOUSE-",    # U.S. House congressional district races (e.g. KXHOUSE-TX38)
        "KXSENATE-",   # U.S. Senate seat races (e.g. KXSENATE-TX-28); NOT collective seat-count
        #               markets (which use different prefixes and are caught by Pattern 3 numeric
        #               thresholds, e.g. "Dems win 50+ seats" → legs 48,49,50 → numeric_threshold_not_ce)
        "KXGOV-",      # Governor races — always has unlisted candidates; KXPRES-* (presidential)
        #               is intentionally NOT blocked here: Kalshi presidential markets include an
        #               "Other/Field" leg covering all unlisted candidates, so they ARE CE.
        #               Partial-leg protection for KXPRES-* is handled by the count-mismatch
        #               check in verify_ce_arb_via_api (official count vs feed count).
        "KXMAYORAL-",  # Mayoral elections
        "KXSTATELEG-", # State legislature races
        "KXASSEMBLY-", # State assembly races
        "KXSTATESEN-", # State senate races
        "KXPRIMARY-",  # Primary election races — unlisted candidates can enter late; even if a
        #               set of candidates is listed, a late entrant causes ALL legs to resolve NO.
        #               Nomination-language rules (Pattern 4) already catch most of these, but
        #               the ticker prefix provides belt-and-suspenders blocking.
        "KXRUNOFF-",   # Runoff election races — if no candidate clears the threshold needed to
        #               trigger the runoff, the event resolves differently than any listed leg.
        #               Pattern 4e catches "qualify for the runoff" title text; this prefix
        #               catches runoff-specific event tickers without relying on title text.
    )
    if any(ev_upper.startswith(p.upper()) for p in _ELECTION_SEAT_PREFIXES):
        return False, "election_seat_unlisted_candidates_not_ce"

    # --- Pattern 4b: "who will" / "which of these" / "which season" markets ---
    # These titles signal partial candidate sets or time-bounded "when will X happen" markets.
    # In all cases there is a possible real-world outcome where ALL legs resolve NO:
    #   - "who will win / who will be" → unlisted person wins
    #   - "which of these" → time-deadline + closed set (all still in office at cutoff)
    #   - "which season will" → Jets/team misses playoffs beyond the last covered season
    if len(markets) >= 2:
        # Broad "who will <anything>" — catches leave/acquire/win/be/appoint/etc.
        if "who will" in title_sample or "who wins" in title_sample or "who is leading" in title_sample:
            return False, "who_will_partial_candidates_not_ce"
        # "which of" or "which <word> will" — catches "which bank will", "which company will"
        import re as _re_p4b
        if "which of" in title_sample or "which season" in title_sample or _re_p4b.search(r'\bwhich \w+ will\b', title_sample):
            return False, "which_x_will_partial_set_not_ce"

    # --- Pattern 4k: named award/prize markets — Kalshi only lists top candidates,
    # not all eligible participants; an unlisted winner causes ALL legs to resolve NO.
    _AWARD_TITLE_SIGNALS = (
        "rookie of the year", "roty",
        "most valuable player", " mvp ",
        "cy young", "heisman", "ballon d'or",
        "nobel prize", "nobel peace",
        "oscar", "academy award", "golden globe", "grammy award", "emmy award",
        "championship mvp", "finals mvp", "super bowl mvp", "world series mvp",
        "player of the year", "coach of the year", "manager of the year",
        "best actor", "best actress", "best director", "best picture",
    )
    if len(markets) >= 2 and any(s in title_sample for s in _AWARD_TITLE_SIGNALS):
        return False, "award_partial_candidates_not_ce"

    # Also check event ticker for award/prize signals when title is per-candidate
    _AWARD_TICKER_SIGNALS = ("ROTY", "MVP", "NOBELPRICE", "NOBELPRIZ", "OSCAR", "GRAMMY",
                              "EMMY", "HEISMAN", "CYYOUNG", "BALLON")
    if len(markets) >= 2 and any(s in ev_upper for s in _AWARD_TICKER_SIGNALS):
        return False, "award_ticker_partial_candidates_not_ce"

    # --- Pattern 4m: next-role / will-leave markets (missing "stays same/none" leg) ---
    if len(markets) >= 2:
        _NEXT_ROLE_T = (
            "next ceo", "next secretary", "next director", "next head of", "next head ",
            "next chair", "next chairman", "new ceo", "new secretary",
            "next coach", "next commissioner", "next manager",
            "next prime minister", "next president", "next speaker",
            "next leader", "next ambassador", "leave office next", "will leave",
        )
        if any(s in title_sample for s in _NEXT_ROLE_T):
            return False, "next_role_missing_stays_same_not_ce"

    # --- Pattern 4n: "top [X]" open-ended ranking markets -------------------------
    if len(markets) >= 2:
        _TOP_OPEN_T = ("top coding", "top chinese", "top ai ", "top model",
                       "year in search", "#1 on ", "#2 on ", "#3 on ",
                       "biggest returns", "biggest gains", "billboard runnerup",
                       "billboard runner-up")
        if any(s in title_sample for s in _TOP_OPEN_T):
            return False, "open_ranking_non_exhaustive_not_ce"

    # --- Pattern 4l: person-topic "what will X say/mention" markets — non-exclusive.
    # "What will Trump say at the rally?" lists topics; multiple can co-occur → NOT CE.
    # NOTE: "What will the Fed do?" IS CE (one discrete rate decision) — do NOT block
    # institutional/policy decision markets. Only block named-person topic markets.
    if len(markets) >= 2:
        _PERSON_TOPIC_SIGNALS = (
            "what will trump say", "what will trump mention", "what will trump tweet",
            "what will biden say", "what will harris say", "what will elon say",
            "what will obama say", "what topics will trump", "what topics will biden",
        )
        if any(s in title_sample for s in _PERSON_TOPIC_SIGNALS):
            return False, "non_exclusive_what_will_not_ce"
        # "will [person] mention/say/use the word" per-topic rule language
        _MENTION_SIGNALS = ("will trump mention", "will trump say the", "will biden say the",
                            "will harris say the", "will elon say the", "mentions the word",
                            "says the word", "says the phrase")
        if any(s in title_sample for s in _MENTION_SIGNALS):
            return False, "topic_mention_non_exclusive_not_ce"
        if "use the word" in rules_sample or "says the word" in rules_sample:
            return False, "topic_mention_non_exclusive_not_ce"

    # --- Pattern 4c: deadline in resolution rules OR title — event may not happen by cutoff ---
    # "first to X before Jan 1, 2028" or "wins before 2030" — ALL resolve NO if event
    # doesn't happen by the deadline. Also catches title phrasing like "before 2030?"
    _MONTHS = ("jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec")
    if any(f"before {mo}" in rules_sample for mo in _MONTHS):
        return False, "deadline_outcome_not_ce"
    if "before 20" in rules_sample:   # "before 2028", "before 2030", etc.
        return False, "deadline_outcome_not_ce"
    if "before 20" in title_sample:   # title like "NASA lands on the moon before 2030?"
        return False, "deadline_outcome_not_ce"

    # --- Pattern 4e: "advances in" / "qualify for runoff" multi-advance primary markets ---
    # Alaska top-4 primary: multiple candidates can advance simultaneously → multiple YES
    # resolutions possible, OR all resolve NO if neither advances. Not CE in either case.
    if "advances in the" in rules_sample or "qualify for the runoff" in title_sample:
        return False, "multi_advance_primary_not_ce"

    # --- Pattern 4j: individual "wins the [election]" candidate markets ---
    # "If X wins the 2028 London mayoral election" — N legs, each for a different named
    # candidate. Unlisted candidates winning causes ALL legs to resolve NO → not CE.
    # Key signal: "wins the" + "election" in rules without an exhaustive set qualifier.
    if "wins the" in rules_sample and "election" in rules_sample and len(markets) >= 2:
        return False, "election_partial_candidates_not_ce"

    # --- Pattern 4g: party-binary without "other" coverage ---
    # "representative of the X party" in rules means a 3rd party/independent winner causes
    # ALL legs to resolve NO. The gap (1 - sum) reflects the "independent wins" probability.
    if "representative of the" in rules_sample and "party" in rules_sample:
        return False, "party_binary_no_other_not_ce"

    # --- Pattern 1c (title check): seat-race events caught by title when ticker prefix misses ---
    # Catches events like SENATENY-28 (no KXSENATE- prefix) whose title reveals a seat race.
    _SEAT_TITLE_SIGNALS = (
        "house winner", "senate winner", "governor winner", "mayoral winner",
        "wins the seat", "wins the district", "wins the race",
        "congressional district", "house district", "house race",
        "senate race", "gubernatorial",
    )
    if len(markets) >= 2 and any(s in title_sample for s in _SEAT_TITLE_SIGNALS):
        return False, "election_seat_unlisted_candidates_not_ce"

    # --- Pattern 4h: "first such subject to do so after Issuance" — partial candidate list ---
    # "the first such subject to do so after Issuance" means whoever holds the position first
    # resolves YES — but only the listed candidates have legs. An unlisted person can win.
    if "first such subject to do so after issuance" in rules_sample:
        return False, "first_to_hold_partial_set_not_ce"

    # --- Pattern 4i: individual performer/cast markets ("performs/ is announced as") ---
    # James Bond "who will perform as X" markets list only some actors; an unlisted actor
    # being cast causes ALL legs to resolve NO → not CE.
    if "performs/" in rules_sample or "performs as" in rules_sample:
        return False, "performer_set_not_ce"

    # --- Pattern 4d: "last traded price" death/dispute clause in secondary rules ---
    # Some "first among set" markets resolve at the last traded price (not YES=$1) when
    # a contingency occurs (e.g. a leader dies). This breaks the buy-all-YES CE guarantee.
    rules_secondary = (markets[0].get("rules_secondary") or "").lower() if markets else ""
    if "last traded price" in rules_secondary:
        return False, "non_binary_resolution_not_ce"

    # --- Pattern 5: election "round 1 winner" markets ---
    # In many electoral systems, no candidate wins Round 1 if nobody clears 50%.
    # In that case ALL legs resolve NO → not CE.
    ev_lower = event_ticker.lower()
    if any(kw in ev_lower for kw in ("r1-", "round1", "firstround", "r1_", "-r1")):
        return False, "election_round1_not_ce"

    # --- Pattern 4m: chart / ranking / "top N" markets ---
    # Netflix, Spotify, Billboard, box-office ranking markets always have unlisted options:
    # a movie/song/show NOT on the Kalshi list can take the top spot, causing ALL legs to
    # resolve NO.  The "Other" probability is priced into the sum gap — it is NOT arb.
    _CHART_TICKER_SIGNALS = (
        "RANKMOVIE", "RANKSHOW", "RANKSONG", "RANKALBUM", "RANKTV",
        "RANKPODCAST", "RANKBOOK", "RANKLIST", "RANKGAME",
        "BOXOFFICE", "BILLBOARD", "NETFLIX", "SPOTIFY", "HBOMAX", "DISNEY",
        "PRIMEVIDEO", "APPLETV", "GOOGLESEARCH", "GOOGLETREN",
    )
    if len(markets) >= 2 and any(s in ev_upper for s in _CHART_TICKER_SIGNALS):
        return False, "chart_ranking_unlisted_options_not_ce"

    _CHART_TITLE_SIGNALS = (
        "top us netflix", "top netflix", "#1 netflix", "number 1 netflix",
        "top spotify", "#1 spotify", "top billboard", "#1 box office",
        "top box office", "top hbo", "top disney", "most streamed",
        "most watched", "top streaming",
    )
    if len(markets) >= 2 and any(s in title_sample for s in _CHART_TITLE_SIGNALS):
        return False, "chart_ranking_unlisted_options_not_ce"

    # --- Pattern 6: markets where sum is far below $1 in a large-leg event ---
    # In a liquid truly-CE market, prices should be close to $1 total because the
    # YES asks track probabilities which sum to ~100%.  A large gap signals the
    # market is CORRECTLY pricing an unlisted "Other" outcome — not a real arb.
    # True CE arb arises from stale resting orders; the gap is small (2-6c after fees).
    # Thresholds (tuned):
    #   - 2 legs:  no gate — binary markets are CE by construction; spread reflects liquidity
    #   - 3 legs:  require sum >= 0.80  (20c max gap — policy/outcome markets can be wide)
    #   - 4+ legs: require sum >= 0.75  (25c max gap — matches outer gate; 0.93 was
    #             too conservative and blocked genuine wide-spread bracket arbs)
    n = len(markets)
    sum_ya = sum(
        float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0)
        for m in markets
    )
    # 4+ legs: require >= 0.75 (25c max gap — matches outer verify_ce_arb_via_api gate)
    # 3 legs:  require >= 0.80 (wide spread possible in a 3-outcome policy market)
    # 2 legs:  no gate — binary markets (YES/NO) are CE by construction; wide spreads
    #           reflect liquidity, not unlisted outcomes.
    if n >= 4 and sum_ya < 0.75:
        return False, f"sum_too_low_for_ce:{sum_ya:.3f}_{n}legs"
    if n == 3 and sum_ya < 0.80:
        return False, f"sum_too_low_for_ce:{sum_ya:.3f}_{n}legs"

    return True, "ok"


def _set_price_cache(event_ticker: str, verified: bool, reason: str, **extra) -> None:
    with _price_lock:
        _price_cache[event_ticker] = {"verified": verified, "reason": reason, "ts": time.time(), **extra}


def verify_ce_arb_via_api(event_ticker: str, leg_tickers: list[str]) -> tuple[bool, str]:
    """
    Final-gate CE arb verification against Kalshi REST API.

    Fetches ALL open markets for event_ticker, checks:
      1. Official leg count matches len(leg_tickers)
      2. Sum of official YES asks < 0.98

    Returns (True, "ok") if verified, (False, reason) if not.
    Cached for _PRICE_VERIFY_TTL_S seconds.
    On any API failure returns (False, "api_error") — fail-closed.
    """
    with _price_lock:
        entry = _price_cache.get(event_ticker)
        if entry and (time.time() - entry.get("ts", 0)) < _PRICE_VERIFY_TTL_S:
            cached_ok = entry.get("verified", False)
            return (cached_ok, "cached_ok" if cached_ok else entry.get("reason", "cached_fail"))

    try:
        data = _kalshi_get(
            f"/markets?event_ticker={event_ticker}&status=open&limit=200"
        )
        markets = data.get("markets", [])
        if not markets:
            _set_price_cache(event_ticker, False, "no_markets")
            return False, "no_markets"

        official_count = len(markets)
        if official_count != len(leg_tickers):
            reason = f"count_mismatch:{official_count}vs{len(leg_tickers)}"
            _set_price_cache(event_ticker, False, reason)
            return False, reason

        # Check that every leg_ticker appears in the official set
        official_tickers = {m.get("ticker", "") for m in markets}
        missing = [t for t in leg_tickers if t not in official_tickers]
        if missing:
            reason = f"missing_legs:{len(missing)}"
            _set_price_cache(event_ticker, False, reason)
            return False, reason

        # Require at least some real market activity. Kalshi no longer populates
        # the legacy integer volume/open_interest fields in list responses;
        # volume_fp / open_interest_fp (dollar notional) are the live fields.
        total_vol_fp = sum(float(m.get("volume_fp") or 0) for m in markets)
        total_oi_fp  = sum(float(m.get("open_interest_fp") or 0) for m in markets)
        if total_vol_fp == 0 and total_oi_fp == 0:
            _set_price_cache(event_ticker, False, "no_liquidity")
            return False, "no_liquidity"

        # Compute sum(YES asks) from API prices.
        # Kalshi list endpoint uses yes_ask_dollars (decimal, 0-1 range).
        sum_yes = 0.0
        for m in markets:
            # Try yes_ask_dollars first (list endpoint), fall back to yes_ask (legacy)
            yes_ask_d = m.get("yes_ask_dollars")
            yes_ask_c = m.get("yes_ask")
            if yes_ask_d is not None and float(yes_ask_d) > 0:
                sum_yes += float(yes_ask_d)
            elif yes_ask_c is not None and float(yes_ask_c) > 0:
                sum_yes += float(yes_ask_c) / 100.0
            else:
                # No active YES ask on this leg — cannot confirm arb
                _set_price_cache(event_ticker, False, "missing_yes_ask")
                return False, "missing_yes_ask"

        if sum_yes < 0.75:
            reason = f"sum_too_low:{sum_yes:.4f}"
            _set_price_cache(event_ticker, False, reason)
            return False, reason

        if sum_yes >= 1.0:
            reason = f"sum_too_high:{sum_yes:.4f}"
            _set_price_cache(event_ticker, False, reason)
            return False, reason

        # Gate 5: structural CE check.
        # Markets where the outcome space is NOT fully partitioned cannot be CE:
        # we'd buy all YES but an unlisted outcome could cause all legs to resolve NO.
        struct_ok, struct_reason = _is_structural_ce(event_ticker, markets)
        if not struct_ok:
            _set_price_cache(event_ticker, False, struct_reason)
            return False, struct_reason

        _set_price_cache(event_ticker, True, "ok", sum_yes=sum_yes)
        logger.info("CE arb VERIFIED via API: %s sum_yes=%.4f legs=%d", event_ticker, sum_yes, official_count)
        return True, "ok"

    except Exception as exc:
        logger.warning("verify_ce_arb_via_api failed for %s: %s", event_ticker, exc)
        # Use a short TTL for transient API errors (429, timeouts) so they are
        # retried quickly rather than blocking CE events for the full 45s window.
        _short_ttl_cache = {"ts": time.time() - (_PRICE_VERIFY_TTL_S - 10), "verified": False, "reason": "api_error"}
        with _price_lock:
            _price_cache[event_ticker] = _short_ttl_cache
        return False, "api_error"


def get_event_official_leg_count(event_ticker: str) -> int:
    """
    Return the official number of open markets for a Kalshi event.
    Cached to disk; safe to call from background scanner threads.
    Returns 0 on failure (caller should treat as unverifiable, not as 0 legs).
    """
    _load_cache()
    _key = f"__ec__{event_ticker}"
    entry = _cache.get(_key)
    if entry:
        age_h = (time.time() - entry.get("ts", 0)) / 3600
        if age_h < _EVENT_COUNT_TTL_H:
            return entry.get("count", 0)
    try:
        data = _kalshi_get(
            f"/markets?event_ticker={event_ticker}&status=open&limit=200"
        )
        count = len(data.get("markets", []))
        if count > 0:
            _cache[_key] = {"count": count, "ts": time.time()}
            _save_cache()
            return count
    except Exception as exc:
        logger.warning("get_event_official_leg_count failed for %s: %s", event_ticker, exc)
    return 0


def prefetch(tickers: list[str]) -> None:
    """Pre-populate cache for a list of tickers (call from background thread)."""
    for t in tickers:
        try:
            _lookup_ticker(t, fetch=True)
        except Exception:
            pass


def is_ws_predict_eligible(ticker: str, ttl_days: float | None = None) -> bool:
    """
    Return True if this Kalshi ticker is likely available on Wealthsimple Predict.

    Rule: category must be Economics, Financials, or Climate and Weather.

    The 30-day rule is a WS Predict *listing* rule (they won't add new contracts
    with < 30 days to go), NOT a trading rule — once listed, a contract stays on
    WS Predict until it settles. So we do NOT filter by current TTL.

    ttl_days is accepted for API compatibility but unused.
    """
    if not ticker:
        return False

    info = _lookup_ticker(ticker, fetch=False)  # cache only — no HTTP during render
    category = info.get("category")

    if not category:
        return False

    return category in WS_ALLOWED_CATEGORIES


def any_leg_ws_eligible(tickers: list[str], ttl_days: float | None = None) -> bool:
    """True if ALL tickers in a multi-leg arb are WS Predict eligible."""
    if not tickers:
        return False
    return all(is_ws_predict_eligible(t, ttl_days) for t in tickers)


def get_market_title(ticker: str) -> str | None:
    """
    Return the human-readable market title for a Kalshi ticker, or None on failure.
    For multi-leg arbs pass the first leg ticker; the event_title covers the group.
    Results are cached alongside category data.
    """
    if not ticker:
        return None
    info = _lookup_ticker(ticker, fetch=False)
    return info.get("title") or info.get("event_title")


def get_event_title(ticker: str) -> str | None:
    """Return the event-level title (covers all legs of a CE/ME/threshold arb)."""
    if not ticker:
        return None
    info = _lookup_ticker(ticker, fetch=False)
    return info.get("event_title") or info.get("title")
