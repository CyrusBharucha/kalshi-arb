"""
Classify all Kalshi event series by market type.
Each type determines CE/ME eligibility and how arb detection works.

Market types:
  binary_race          - 2 outcomes, one MUST win (R/D, Team A/B)  → CE+ME valid
  multi_race_closed    - 3+ named candidates, closed exhaustive set → CE+ME valid
  price_range_bins     - numeric price/value buckets                → CE+ME valid
  rate_decision_bins   - central bank rate move buckets             → CE+ME valid
  sports_match_winner  - 2-3 team match winner                     → CE+ME valid
  tournament_champion  - all teams in a league/tournament           → CE+ME valid
  award_nominees       - partial list of award nominees             → NOT CE
  next_role            - who fills a role (missing stays-same leg)  → NOT CE
  who_will_general     - open-ended "who will" winner               → NOT CE
  threshold_levels     - at-least-X% / count markets               → NOT ME/CE
  sports_spread_total  - spread, total, BTTS markets                → NOT ME/CE
  binary_event         - single yes/no market (no CE possible)      → NOT CE
  combo_market         - compound outcome combinations              → special
  unknown              - new, not yet classified                    → BLOCKED

Usage:
    python scripts/classify_event_series.py
"""
import os, sys, re, time, json
import urllib.request as urlreq
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2
from psycopg2.extras import execute_values

DB_URL = os.environ["DATABASE_URL"]

HOSTS = [
    "https://api.elections.kalshi.com",
    "https://trading-api.kalshi.com",
]

# ── CE/ME eligibility by market type ──────────────────────────────────────────
MARKET_TYPE_CE = {
    "binary_race":         True,
    "multi_race_closed":   True,
    "price_range_bins":    True,
    "rate_decision_bins":  True,
    "sports_match_winner": True,
    "tournament_champion": True,
    "award_nominees":      False,
    "next_role":           False,
    "who_will_general":    False,
    "threshold_levels":    False,
    "sports_spread_total": False,
    "binary_event":        False,
    "combo_market":        False,
    "unknown":             False,
}

MARKET_TYPE_ME = {
    "binary_race":         True,
    "multi_race_closed":   True,
    "price_range_bins":    True,
    "rate_decision_bins":  True,
    "sports_match_winner": True,
    "tournament_champion": True,
    "award_nominees":      True,   # ME=True but not CE
    "next_role":           True,   # ME=True but not CE
    "who_will_general":    True,   # ME=True but not CE
    "threshold_levels":    False,
    "sports_spread_total": False,
    "binary_event":        False,
    "combo_market":        False,
    "unknown":             False,
}

# ── Signal lists ───────────────────────────────────────────────────────────────
_WHO_PREFIX     = "who will"
_WHO_SIGNALS    = ("who wins", "who is leading")
_WHICH_RE       = re.compile(r'\bwhich \w+ will\b')
_WHICH_FIXED    = ("which of these", "which of the", "which season")

_AWARD_SIGNALS  = (
    "rookie of the year", "most valuable player", " mvp ",
    "cy young", "heisman", "ballon d'or", "nobel prize",
    "oscar", "academy award", "golden globe", "grammy", "emmy",
    "player of the year", "coach of the year", "best actor", "best actress",
    "best director", "best picture", "championship mvp", "finals mvp",
    "game of the year", "year award", "of the year award", "winner award",
    "film festival", "golden lion", "palme d'or", "silver lion",
)
_NEXT_ROLE_SIGNALS = (
    "next ceo", "next secretary", "next director", "next head of", "next head ",
    "next chair", "next chairman", "new ceo", "new secretary", "next coach",
    "next commissioner", "next manager", "next prime minister", "next president",
    "next speaker", "next leader", "next ambassador", "leave office next",
    "will leave", "leaves next",
)
_TOP_OPEN_SIGNALS  = ("top coding", "top chinese", "top ai ", "top model")
_RANKING_SIGNALS   = (
    "year in search", "#1 on ", "#2 on ", "#3 on ",
    "billboard runnerup", "billboard runner-up",
    "biggest returns", "biggest gains",
)
_RATE_DECISION_SIGNALS = (
    "fed decision", "rate decision", "fomc", "rate cut", "rate hike",
    "basis points", "federal funds", "bank of england", "ecb decision",
    "bank of japan", "bank of canada", "reserve bank",
)
_PRICE_RANGE_SIGNALS = (
    "price range", "price at the end", "price on", "trading at",
    "highest temperature", "high temperature", "low temperature",
    "fear & greed", "fear and greed", "margin of victory",
    "gdp growth", "unemployment rate", "cpi", "inflation",
    "real gdp", "nonfarm", "payroll",
)
_SPREAD_TOTAL_SIGNALS = (
    "spread", "over/under", "total goals", "total points", "btts",
    "both teams to score", "handicap", "asian handicap",
)
_COMBO_SIGNALS = ("combo", "exacta", "combination", "parlay", "trifecta")


def series_prefix(event_ticker: str) -> str:
    parts = event_ticker.split("-")
    if len(parts) >= 2:
        last = parts[-1]
        if re.match(r'^\d{2}[A-Z]{3}\d{2}', last) or re.match(r'^[A-Z]{1,2}\d{2}[A-Z]?$', last):
            return "-".join(parts[:-1])
    return event_ticker


def classify_event(event: dict) -> dict:
    ticker  = event.get("event_ticker", "")
    title   = (event.get("title") or "").lower()
    me      = event.get("mutually_exclusive")
    markets = event.get("markets", [])
    active  = [m for m in markets if m.get("status") in ("active", "open")]
    mlist   = active if active else markets
    count   = len(mlist)
    sfxs    = [m.get("ticker", "").rsplit("-", 1)[-1] for m in mlist]

    # Determine suffix pattern
    if sfxs and all(re.match(r'^P\d+$', s) for s in sfxs):
        sfx_pat = "P<n>"
    elif sfxs and all(re.match(r'^A\d+$', s) for s in sfxs):
        sfx_pat = "A<n>"
    elif len(sfxs) > 2 and all(re.match(r'^T\d+$', s) for s in sfxs):
        sfx_pat = "T<n>"
    elif sfxs and all(re.match(r'^\d+(\.\d+)?$', s) for s in sfxs):
        sfx_pat = "numeric"
    else:
        sfx_pat = "alpha"

    mtype, reason = _detect_type(title, me, count, sfxs, sfx_pat, ticker)

    return {
        "series_prefix": series_prefix(ticker),
        "market_type":   mtype,
        "ce_eligible":   MARKET_TYPE_CE.get(mtype, False),
        "me_eligible":   MARKET_TYPE_ME.get(mtype, False),
        "status":        "CLASSIFIED",
        "auto_reason":   reason,
        "title_example": title[:120],
        "market_count":  count,
        "me_flag":       str(me),
        "suffix_pattern": sfx_pat,
        "classified_by": "auto",
    }


def _detect_type(title: str, me, count: int, sfxs: list, sfx_pat: str, ticker: str) -> tuple:
    t = title.lower()
    tk_up = ticker.upper()

    # ── threshold / spread / total — detect first regardless of ME flag ────────
    if sfx_pat in ("P<n>", "A<n>", "T<n>"):
        return "threshold_levels", f"suffix_{sfx_pat}"
    if any(s in t for s in _SPREAD_TOTAL_SIGNALS):
        return "sports_spread_total", "spread_total_title"
    if "btts" in tk_up or "SPREAD" in tk_up or "TOTAL" in tk_up:
        return "sports_spread_total", "spread_total_ticker"

    # ── combo / exacta ─────────────────────────────────────────────────────────
    if any(s in t for s in _COMBO_SIGNALS):
        return "combo_market", "combo_title"

    # ── single-market event ────────────────────────────────────────────────────
    if count == 1:
        return "binary_event", "single_market"

    # ── award / nominees ───────────────────────────────────────────────────────
    if any(s in t for s in _AWARD_SIGNALS):
        return "award_nominees", "award_title"
    if any(s in tk_up for s in ("ROTY","MVP","OSCAR","GRAMMY","EMMY","HEISMAN","BALLON","COTY","DPOY","OPOY","OROTY","DROTY","CPOTY","CY","AWARD")):
        return "award_nominees", "award_ticker"

    # ── next role / who will leave ─────────────────────────────────────────────
    if any(s in t for s in _NEXT_ROLE_SIGNALS):
        return "next_role", "next_role_title"

    # ── open-ended "who will" / "which X will" ────────────────────────────────
    if _WHO_PREFIX in t or any(s in t for s in _WHO_SIGNALS):
        return "who_will_general", "who_will_title"
    if _WHICH_RE.search(t) or any(s in t for s in _WHICH_FIXED):
        return "who_will_general", "which_x_will_title"
    if any(s in t for s in _TOP_OPEN_SIGNALS) or any(s in t for s in _RANKING_SIGNALS):
        return "who_will_general", "top_ranking_title"

    # ── ME=False after exhausting structural blocks means multi-resolve ─────────
    if me is False:
        return "threshold_levels", "ME_False"

    # ── rate / economic decision bins ─────────────────────────────────────────
    if any(s in t for s in _RATE_DECISION_SIGNALS):
        return "rate_decision_bins", "rate_decision_title"

    # ── numeric suffix = price / value range bins ──────────────────────────────
    if sfx_pat == "numeric":
        return "price_range_bins", "numeric_suffix"

    # ── price / economic range by title ───────────────────────────────────────
    if any(s in t for s in _PRICE_RANGE_SIGNALS):
        return "price_range_bins", "price_range_title"

    # ── sports match winner (2-3 outcomes, alpha suffix) ──────────────────────
    if count <= 3 and sfx_pat == "alpha" and me is True:
        if any(w in t for w in ("vs ", " vs", "match", "game", "set ", "1st half", "first half", "regulation")):
            return "sports_match_winner", "match_title_binary"

    # ── 2-outcome binary race ─────────────────────────────────────────────────
    if count == 2 and me is True:
        return "binary_race", "binary_ME_True"

    # ── tournament champion (larger alpha sets with ME=True) ──────────────────
    if me is True and sfx_pat == "alpha" and count >= 3:
        if any(w in t for w in ("champion", "winner", "winner?", "cup", "title",
                                "election", "primary", "runoff", "race", "party")):
            return "tournament_champion", "champion_title"
        # Small closed sets (3-8 candidates in political races)
        if count <= 8:
            return "multi_race_closed", f"{count}_candidates_ME_True"

    # ── ME=True but structure unclear ─────────────────────────────────────────
    if me is True:
        return "tournament_champion", f"ME_True_{count}_legs_alpha"

    # ── fallback ───────────────────────────────────────────────────────────────
    return "unknown", "no_pattern_matched"


def fetch_all_events(host: str) -> list:
    events, cursor, page = [], None, 0
    while True:
        url = f"{host}/trade-api/v2/events?with_nested_markets=true&limit=200&status=open"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            req = urlreq.Request(url, headers={"Accept": "application/json"})
            with urlreq.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            print(f"  ERROR {host} p{page}: {e}", file=sys.stderr)
            break
        batch  = data.get("events", [])
        events.extend(batch)
        cursor = data.get("cursor")
        page  += 1
        print(f"  {host}: p{page} +{len(batch)} = {len(events)}", file=sys.stderr)
        if not batch or not cursor:
            break
        time.sleep(0.15)
    return events


def main():
    print("Fetching events...", file=sys.stderr)
    seen = {}
    for host in HOSTS:
        for ev in fetch_all_events(host):
            tk = ev.get("event_ticker", "")
            if tk and tk not in seen:
                seen[tk] = ev

    print(f"\nTotal events: {len(seen)}", file=sys.stderr)

    by_prefix = {}
    for ev in seen.values():
        rec = classify_event(ev)
        pfx = rec["series_prefix"]
        if pfx not in by_prefix:
            by_prefix[pfx] = rec

    rows = list(by_prefix.values())
    print(f"Unique series: {len(rows)}", file=sys.stderr)

    from collections import Counter
    type_counts = Counter(r["market_type"] for r in rows)
    for mtype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        ce = MARKET_TYPE_CE.get(mtype, False)
        print(f"  {mtype:<30} {cnt:>5}  CE={'Y' if ce else 'N'}", file=sys.stderr)

    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()

    execute_values(cur, """
        INSERT INTO event_series_classifications
            (series_prefix, market_type, ce_eligible, me_eligible, status,
             auto_reason, title_example, market_count, me_flag, suffix_pattern, classified_by)
        VALUES %s
        ON CONFLICT (series_prefix) DO UPDATE SET
            market_type    = CASE WHEN event_series_classifications.classified_by = 'manual'
                                  THEN event_series_classifications.market_type
                                  ELSE EXCLUDED.market_type END,
            ce_eligible    = CASE WHEN event_series_classifications.classified_by = 'manual'
                                  THEN event_series_classifications.ce_eligible
                                  ELSE EXCLUDED.ce_eligible END,
            me_eligible    = CASE WHEN event_series_classifications.classified_by = 'manual'
                                  THEN event_series_classifications.me_eligible
                                  ELSE EXCLUDED.me_eligible END,
            status         = CASE WHEN event_series_classifications.classified_by = 'manual'
                                  THEN event_series_classifications.status
                                  ELSE EXCLUDED.status END,
            auto_reason    = EXCLUDED.auto_reason,
            title_example  = EXCLUDED.title_example,
            market_count   = EXCLUDED.market_count,
            me_flag        = EXCLUDED.me_flag,
            suffix_pattern = EXCLUDED.suffix_pattern,
            last_seen_at   = NOW()
    """, [
        (r["series_prefix"], r["market_type"], r["ce_eligible"], r["me_eligible"],
         r["status"], r["auto_reason"], r["title_example"], r["market_count"],
         r["me_flag"], r["suffix_pattern"], r["classified_by"])
        for r in rows
    ])

    conn.commit()
    conn.close()
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
