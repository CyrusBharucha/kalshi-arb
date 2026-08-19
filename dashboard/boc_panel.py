"""
dashboard/boc_panel.py
======================
Shared Bank of Canada live-data panel + policy-meeting countdown.

Used by:
- p07_cross_asset.py  (relative-value context)
- p08_canadian.py     (Canadian market monitor)

All rendering degrades gracefully: if the BOC VALET API is unreachable the
panel shows an explicit UNAVAILABLE state rather than raising.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from dashboard.styles import PANEL, PANEL2, BORDER, TEXT, TEXT2, TEXT3, GREEN, RED, AMBER, BLUE


# ---------------------------------------------------------------------------
# Bank of Canada scheduled policy interest rate announcement dates.
# Source: bankofcanada.ca "Schedule for Policy Interest Rate Announcements".
# Dates are published roughly a year ahead; update annually.
# NOTE: These dates are manually maintained — verify against bankofcanada.ca
# before relying on them for trading decisions.
# ---------------------------------------------------------------------------
BOC_MEETING_DATES: List[date] = [
    date(2026, 1, 28),   # actual
    date(2026, 3, 11),   # actual
    date(2026, 4, 16),   # actual (hold at 2.25%)
    date(2026, 6, 4),    # actual
    date(2026, 7, 30),   # actual
    date(2026, 9, 9),
    date(2026, 10, 28),
    date(2026, 12, 9),
    # 2027 schedule — estimated; BoC publishes official dates in Dec 2026. Update then.
    date(2027, 1, 20),
    date(2027, 3, 3),
    date(2027, 4, 14),
    date(2027, 6, 2),
    date(2027, 7, 14),
    date(2027, 9, 8),
    date(2027, 10, 27),
    date(2027, 12, 8),
]


def next_boc_meeting(today: Optional[date] = None) -> Optional[Tuple[date, int]]:
    """Return (next_meeting_date, days_until) or None if the schedule is exhausted.

A meeting happening *today* returns 0 days rather than being skipped.
"""
    if today is None:
        today = datetime.now(timezone.utc).date()
    upcoming = [d for d in BOC_MEETING_DATES if d >= today]
    if not upcoming:
        return None
    nxt = min(upcoming)
    return nxt, (nxt - today).days


def fetch_boc_rates() -> Dict[str, Any]:
    """Fetch live BOC rates, returning {} on any failure (never raises)."""
    try:
        from analysis.live_data import get_boc_rates
        return get_boc_rates() or {}
    except Exception as exc:  # pragma: no cover - network/import guard
        return {"error": str(exc)}


def fetch_boc_rates_with_db_fallback() -> Dict[str, Any]:
    """Like fetch_boc_rates but reads ext_market_daily when live API fails."""
    result = fetch_boc_rates()
    if result and not result.get("error"):
        return result
    # Fallback: read latest CORRA and BOC_RATE from ext_market_daily
    try:
        from analysis.market_data import get_latest_prices
        prices = get_latest_prices()
        if prices:
            out: Dict[str, Any] = {"source": "ext_market_daily (cached)", "as_of": None}
            if "CORRA" in prices:
                out["corra"] = prices["CORRA"] / 100.0
            if "BOC_RATE" in prices:
                out["policy_rate"] = prices["BOC_RATE"] / 100.0
            if "CAGB_2Y" in prices:
                out["gbond_2y"] = prices["CAGB_2Y"] / 100.0
            if "CAGB_5Y" in prices:
                out["gbond_5y"] = prices["CAGB_5Y"] / 100.0
            if "CAGB_10Y" in prices:
                out["gbond_10y"] = prices["CAGB_10Y"] / 100.0
            return out
    except Exception:
        pass
    return result  # return original (may have "error" key)


def _fmt_pct(val: Any) -> str:
    """BOC VALET returns decimals (0.0275) or percents; normalise to a % string."""
    if val is None:
        return "--"
    try:
        f = float(val)
    except (TypeError, ValueError):
        return "--"
    if abs(f) < 0.5:          # stored as a decimal fraction
        f *= 100.0
    return f"{f:.2f}%"


_FIELDS = [
    ("policy_rate", "POLICY RATE"),
    ("corra",       "CORRA"),
    ("gbond_2y",    "GOC 2Y"),
    ("gbond_5y",    "GOC 5Y"),
    ("gbond_10y",   "GOC 10Y"),
]


def build_boc_rows(rates: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Pure helper: turn a rates dict into [(label, formatted_value), ...]."""
    return [(label, _fmt_pct(rates.get(key))) for key, label in _FIELDS]


def has_any_rate(rates: Dict[str, Any]) -> bool:
    """True if at least one rate series came back with a usable value."""
    return any(rates.get(k) is not None for k, _ in _FIELDS)


def countdown_tone(days: Optional[int]) -> str:
    """Colour for the meeting countdown: imminent = amber, this week = blue."""
    if days is None:
        return TEXT3
    if days <= 2:
        return AMBER
    if days <= 7:
        return BLUE
    return GREEN


def render_boc_panel(show_countdown: bool = True) -> None:
    """Render the live BOC rate panel. Safe to call when the API is down."""
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.12em;text-transform:uppercase;"
        f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
        f"BANK OF CANADA &mdash; LIVE</div>",
        unsafe_allow_html=True,
    )

    rates = fetch_boc_rates_with_db_fallback()

    if not has_any_rate(rates):
        note = rates.get("error") or "BOC VALET API returned no observations."
        st.markdown(
            f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {AMBER};
padding:0.75rem 1rem;border-radius:3px;font-family:JetBrains Mono,monospace;
font-size:0.72rem;color:{TEXT2};'>
BOC LIVE DATA UNAVAILABLE
<div style='font-size:0.65rem;color:{TEXT3};margin-top:0.35rem;'>{str(note)[:200]}</div>
</div>""",
            unsafe_allow_html=True,
        )
        if show_countdown:
            render_meeting_countdown()
        return

    cells = "".join(
        f"""<div style='flex:1;min-width:110px;background:{PANEL2};border:1px solid {BORDER};
border-radius:3px;padding:0.6rem 0.75rem;'>
<div style='font-size:0.58rem;letter-spacing:0.1em;color:{TEXT3};
font-family:Inter,sans-serif;'>{label}</div>
<div style='font-size:1.05rem;color:{TEXT};font-family:JetBrains Mono,monospace;
margin-top:0.2rem;'>{value}</div>
</div>"""
        for label, value in build_boc_rows(rates)
    )

    as_of = rates.get("as_of") or ""
    as_of_txt = str(as_of)[:19].replace("T", " ") if as_of else "unknown"

    st.markdown(
        f"""<div style='display:flex;gap:0.5rem;flex-wrap:wrap;'>{cells}</div>
<div style='font-size:0.6rem;color:{TEXT3};font-family:JetBrains Mono,monospace;
margin:0.4rem 0 0.75rem 0;'>
SOURCE: {rates.get('source', 'Bank of Canada VALET')} &middot; AS OF {as_of_txt} UTC
</div>""",
        unsafe_allow_html=True,
    )

    if show_countdown:
        render_meeting_countdown()


def render_meeting_countdown() -> None:
    """Render the countdown to the next scheduled BOC rate announcement."""
    nxt = next_boc_meeting()

    if nxt is None:
        st.markdown(
            f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:0.6rem 1rem;
border-radius:3px;font-family:JetBrains Mono,monospace;font-size:0.7rem;color:{TEXT3};
margin-bottom:1rem;'>
BOC MEETING SCHEDULE EXHAUSTED &mdash; update BOC_MEETING_DATES in
dashboard/boc_panel.py with the next published calendar.
</div>""",
            unsafe_allow_html=True,
        )
        return

    meeting, days = nxt
    tone = countdown_tone(days)
    when = "TODAY" if days == 0 else ("TOMORROW" if days == 1 else f"IN {days} DAYS")

    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {tone};
padding:0.6rem 1rem;border-radius:3px;margin-bottom:1rem;
display:flex;align-items:baseline;gap:1rem;flex-wrap:wrap;'>
<span style='font-size:0.58rem;letter-spacing:0.1em;color:{TEXT3};
font-family:Inter,sans-serif;'>NEXT RATE ANNOUNCEMENT</span>
<span style='font-size:0.9rem;color:{TEXT};font-family:JetBrains Mono,monospace;'>
{meeting.strftime('%Y-%m-%d')}
</span>
<span style='font-size:0.75rem;color:{tone};font-family:JetBrains Mono,monospace;'>
{when}
</span>
<span style='font-size:0.58rem;color:{TEXT3};font-family:Inter,sans-serif;'>
Dates manually maintained — verify against bankofcanada.ca
</span>
</div>""",
        unsafe_allow_html=True,
    )
