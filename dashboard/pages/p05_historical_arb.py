"""
dashboard/pages/p05_historical_arb.py
======================================
Historical Arbitrage page — shows arbs detected by the live scanner AND
historical research candidates sourced from OHLC candlestick scans.

Data sources:
  live_arb_store — dual-write: SQLite locally, Neon PostgreSQL on Streamlit Cloud.
  Arbs are persisted autonomously 24/7 by the ws_bridge scanner (1s interval).

  targeted_ohlc — historical candlestick scan results. These represent backtested
  price patterns and are NOT live executable arbitrages. Edges may be inflated
  relative to real market conditions.
"""
from __future__ import annotations
import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta

import pandas as pd

_EST = timezone(timedelta(hours=-5))   # EST (no DST — use fixed offset for display)
def _to_est(ts_series: pd.Series) -> pd.Series:
    """Convert a UTC timestamp Series to ET strings (EDT in summer, EST in winter)."""
    return (
        pd.to_datetime(ts_series, utc=True, errors="coerce")
        .dt.tz_convert("America/New_York")
        .dt.strftime("%Y-%m-%d %H:%M ET")
    )
import plotly.graph_objects as go
import streamlit as st

from dashboard.data_layer import get_live_arb_history
from dashboard.styles import (
    plotly_dark_layout, GREEN, AMBER, BLUE, CYAN,
    TEXT, TEXT2, TEXT3, PANEL, BORDER,
)

_KALSHI_MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
_KALSHI_EVENTS_URL  = "https://api.elections.kalshi.com/trade-api/v2/events"

_DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _title_to_slug(title: str) -> str:
    """Convert a title to a URL slug (lowercase, hyphens, no special chars)."""
    import re as _re2
    s = title.lower().strip()
    s = _re2.sub(r"[^a-z0-9\s-]", "", s)
    s = _re2.sub(r"\s+", "-", s)
    s = _re2.sub(r"-+", "-", s).strip("-")
    return s


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_market_info(ticker: str) -> dict:
    try:
        url = f"{_KALSHI_MARKETS_URL}/{ticker}"
        req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            m = json.loads(resp.read()).get("market", {})
            title = m.get("title") or ""
            series = (m.get("series_ticker") or "").lower()
            event_ticker = (m.get("event_ticker") or "").lower()
            slug = m.get("slug") or _title_to_slug(title)
            if series and event_ticker and slug:
                kalshi_url = f"https://kalshi.com/markets/{series}/{event_ticker}/{slug}"
            elif series and slug:
                kalshi_url = f"https://kalshi.com/markets/{series}/{slug}"
            elif series:
                kalshi_url = f"https://kalshi.com/markets/{series}"
            else:
                kalshi_url = ""
            return {"title": title, "close_time": m.get("close_time") or "", "kalshi_url": kalshi_url}
    except Exception:
        return {"title": "", "close_time": "", "kalshi_url": ""}


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_event_info(event_ticker: str) -> dict:
    try:
        url = f"{_KALSHI_EVENTS_URL}/{event_ticker}?with_nested_markets=true"
        req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            ev = json.loads(resp.read()).get("event", {})
            title = ev.get("title") or ""
            series = (ev.get("series_ticker") or event_ticker).lower()
            slug = ev.get("slug") or _title_to_slug(title)
            kalshi_url = f"https://kalshi.com/markets/{series}/{slug}" if slug else f"https://kalshi.com/markets/{series}"
            return {
                "title":      title,
                "close_time": ev.get("expected_expiration_time") or "",
                "kalshi_url": kalshi_url,
            }
    except Exception:
        return {"title": "", "close_time": "", "kalshi_url": ""}


def _resolve_info(ticker: str, strategy: str) -> dict:
    if not ticker:
        return {"title": "", "close_time": ""}
    if strategy == "collectively_exhaustive":
        clean = re.sub(r"\s*\(\d+ legs\)\s*$", "", ticker).strip()
        return _fetch_event_info(clean)
    return _fetch_market_info(ticker)


def _days_to_close(close_time_str: str) -> float | None:
    if not close_time_str:
        return None
    try:
        ct = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        delta = (ct - datetime.now(timezone.utc)).total_seconds()
        return max(delta / 86400.0, 1.0 / 24)
    except Exception:
        return None


def _action_label(strategy: str, ticker: str, title: str = "") -> str:
    m = re.search(r"\((\d+) legs\)", ticker or "")
    n = int(m.group(1)) if m else ""
    _tn = f" — {title}" if title else ""
    if strategy == "yes_no_complement":
        return f"Feed artifact — YES+NO same market (not executable){_tn}"
    elif strategy == "collectively_exhaustive":
        return f"Buy YES on all {n} candidates{_tn}" if n else f"Buy YES on all candidates{_tn}"
    elif strategy == "mutually_exclusive":
        return f"Buy NO on all {n} candidates{_tn}" if n else f"Buy NO on all candidates{_tn}"
    elif strategy == "threshold_order":
        return f"Buy YES (lower threshold) + NO (higher threshold){_tn}"
    elif strategy == "superset":
        return f"Buy YES (superset) + NO (subset){_tn}"
    return "—"


def render():
    st.markdown("""
<span style='font-size:1rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;'>
HISTORICAL ARBITRAGE
</span>
""", unsafe_allow_html=True)
    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    # --- Compact status line ---
    try:
        from dashboard.live_state import get_live_state as _gls
        _ws = _gls()
        _ws_stats = _ws.get_stats()
        _ws_connected = _ws_stats.get("connected", False)
        _sess = _ws.get_session_stats()
        _mkts = _ws_stats.get("markets_tracked", 0)
        _sess_total = _sess.get("total", 0)
    except Exception:
        _ws_connected = False
        _mkts = 0
        _sess_total = 0

    try:
        import dashboard.live_arb_store as _las_p05
        _pg_live = (
            bool(getattr(_las_p05, "_pg_ok", False))
            or (getattr(_las_p05, "_pg_engine", None) is not None)
        )
        if not _pg_live:
            _pg_live = _las_p05.get_pg_engine_cached() is not None
    except Exception:
        _pg_live = False
    # Fallback: sidebar already confirmed Neon via _get_pg_engine()
    if not _pg_live:
        _pg_live = bool(st.session_state.get("_sidebar_neon_ok", False))

    import os as _os_p05
    _sk_p05 = _os_p05.environ.get("SYNTHESIS_SECRET_KEY", "").strip()
    if not _sk_p05:
        try:
            _sk_p05 = (st.secrets.get("SYNTHESIS_SECRET_KEY", "") or "").strip()
        except Exception:
            pass
    if not _sk_p05:
        try:
            for _ns_p05 in st.secrets.values():
                if hasattr(_ns_p05, "get"):
                    _sk_p05 = (_ns_p05.get("SYNTHESIS_SECRET_KEY", "") or "").strip()
                    if _sk_p05:
                        break
        except Exception:
            pass
    _has_key_p05 = bool(_sk_p05)
    if _ws_connected:
        _scanner_dot = f"<span style='color:#22C55E;'>●</span> {int(_mkts):,} markets"
    elif _pg_live:
        _scanner_dot = f"<span style='color:#22C55E;'>●</span> cloud feed (Neon)"
    elif _has_key_p05:
        _scanner_dot = f"<span style='color:#F59E0B;'>◔</span> scanner starting…"
    else:
        _scanner_dot = f"<span style='color:#6B7280;'>○</span> scanner offline"
    _db_dot = f"<span style='color:#22C55E;'>Neon</span>" if _pg_live else f"<span style='color:{AMBER};'>&#8635;&nbsp;Neon</span>"
    st.markdown(
        f"<div style='font-size:0.65rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
        f"margin-bottom:0.75rem;'>{_scanner_dot} &nbsp;·&nbsp; db: {_db_dot}"
        f"{'&nbsp;·&nbsp; session: ' + str(_sess_total) + ' detections' if _sess_total > 0 else ''}</div>",
        unsafe_allow_html=True,
    )

    # --- Filters ---
    _STRAT_DISPLAY = {
        "All": "All strategies",
        "yes_no_complement": "YES/NO Complement",
        "collectively_exhaustive": "Collectively Exhaustive",
        "mutually_exclusive": "Mutually Exclusive",
        "threshold_order": "Threshold Order",
        "superset": "Superset",
    }
    _STRAT_KEYS = list(_STRAT_DISPLAY.keys())

    # min_edge must be known before the DB load so we show it first
    f1, f2, f3, f4 = st.columns(4)
    with f2:
        min_edge = st.number_input("MIN NET EDGE (c)", value=0.5, step=0.5, format="%.1f")
    with f3:
        _sort_options = ["NET EDGE ↓", "NET EDGE ↑", "GROSS EDGE ↓", "TIME ↓", "TIME ↑"]
        _sort_choice = st.selectbox("SORT BY", _sort_options, index=0)

    # Date range filter
    _today_dt   = pd.Timestamp.now(tz="America/New_York").date()
    _default_from = _today_dt - pd.Timedelta(days=30)
    with f4:
        _date_range = st.date_input(
            "DATE RANGE",
            value=(_default_from, _today_dt),
            max_value=_today_dt,
        )
    # Unpack safely — user may have selected only one date while picking
    if isinstance(_date_range, (list, tuple)) and len(_date_range) == 2:
        _date_from, _date_to = _date_range
    else:
        _date_from, _date_to = _default_from, _today_dt

    # --- Load arbs from DB (Neon Postgres or SQLite fallback) — no strategy filter so counts are available ---
    df = pd.DataFrame()
    _db_err: str | None = None
    try:
        df = get_live_arb_history(
            days_back=30,
            strategy=None,
            min_net_edge_cents=min_edge,
        )
    except Exception as _e:
        _db_err = str(_e)

    if df is None:
        df = pd.DataFrame()

    # --- Detect OHLC-sourced (historical research) rows ---
    _ohlc_mask = pd.Series([False] * len(df), index=df.index)
    if not df.empty and "source" in df.columns:
        _ohlc_mask = df["source"].astype(str).str.lower() == "targeted_ohlc"
    _has_ohlc = bool(_ohlc_mask.any())
    _n_ohlc = int(_ohlc_mask.sum()) if _has_ohlc else 0
    _n_live = len(df) - _n_ohlc

    # Show OHLC disclaimer banner if any OHLC-sourced rows are present
    if _has_ohlc:
        st.info(
            f"**Historical Research Candidates ({_n_ohlc:,} signals)**  \n"
            "These results come from a historical candlestick scan (`targeted_ohlc` source). "
            "They represent backtested price patterns, **NOT live executable arbitrages**. "
            "Edges shown may be inflated vs real market conditions and cannot be assumed "
            "to be reproducible in a live order book."
        )

    # Compute per-strategy counts BEFORE showing the dropdown
    _counts: dict = (
        df["strategy_type"].value_counts().to_dict()
        if not df.empty and "strategy_type" in df.columns
        else {}
    )

    with f1:
        _strat_choice = st.selectbox(
            "STRATEGY TYPE",
            _STRAT_KEYS,
            format_func=lambda k: (
                f"{_STRAT_DISPLAY[k]} ({len(df)})"
                if k == "All"
                else f"{_STRAT_DISPLAY[k]} ({_counts.get(k, 0)})"
            ),
        )
        strategy = _strat_choice

    # Apply strategy filter in-memory
    if strategy != "All":
        df = df[df["strategy_type"] == strategy] if not df.empty and "strategy_type" in df.columns else df

    # Apply date range filter in-memory
    if not df.empty and "detected_at" in df.columns:
        _det_ts = pd.to_datetime(df["detected_at"], utc=True, errors="coerce").dt.tz_convert("America/New_York")
        _date_mask = (
            (_det_ts.dt.date >= _date_from) &
            (_det_ts.dt.date <= _date_to)
        )
        df = df[_date_mask]

    if _db_err:
        st.caption(f"DB unavailable ({_db_err}) — verify DATABASE_URL secret is set in Streamlit Cloud.")


    if df.empty:
        # --- Neon cloud hint when date range has no local rows ---
        _neon_cloud_hint = ""
        try:
            import dashboard.live_arb_store as _las_p05e
            from sqlalchemy import text as _p05e_text
            _eng_p05e = getattr(_las_p05e, "_pg_engine", None) or _las_p05e.get_pg_engine_cached()
            if _eng_p05e is not None:
                with _eng_p05e.connect() as _c_p05e:
                    _row_p05e = _c_p05e.execute(_p05e_text(
                        "SELECT COUNT(*), MIN(detected_at)::date, MAX(detected_at)::date "
                        "FROM live_arbs_cloud WHERE strategy_type != 'collectively_exhaustive'"
                    )).fetchone()
                if _row_p05e and _row_p05e[0]:
                    _cnt_p05e = int(_row_p05e[0])
                    _min_dt = str(_row_p05e[1])[:10] if _row_p05e[1] else "--"
                    _max_dt = str(_row_p05e[2])[:10] if _row_p05e[2] else "--"
                    _neon_cloud_hint = (
                        f"<div style='margin-bottom:0.85rem;padding:0.5rem 0.85rem;"
                        f"background:rgba(34,197,94,0.06);border:1px solid #22C55E44;"
                        f"border-left:3px solid #22C55E;border-radius:3px;"
                        f"font-size:0.7rem;font-family:JetBrains Mono,monospace;color:{TEXT2};line-height:1.7;'>"
                        f"<span style='color:#22C55E;font-size:0.6rem;letter-spacing:0.1em;"
                        f"text-transform:uppercase;'>NEON CLOUD — {_cnt_p05e} ME/TH ARBS ON RECORD</span><br>"
                        f"Range: {_min_dt} → {_max_dt}. "
                        f"<span style='color:{TEXT3};'>Widen the DATE RANGE filter above to see them.</span>"
                        f"</div>"
                    )
        except Exception:
            pass

        # --- Inline scanner status for empty state ---
        _scanner_html_badge = ""
        try:
            from dashboard.live_state import get_live_state as _gls_es
            _ws_es = _gls_es()
            _stats_es = _ws_es.get_stats()
            if _stats_es.get("connected", False):
                _mkts_es = _stats_es.get("markets_tracked", 0)
                _rate_es = _stats_es.get("messages_per_sec", 0)
                _scanner_html_badge = (
                    f"<div style='margin-bottom:0.9rem;padding:0.5rem 0.85rem;"
                    f"background:rgba(34,197,94,0.08);border:1px solid #22C55E;"
                    f"border-radius:3px;font-size:0.7rem;font-family:JetBrains Mono,monospace;"
                    f"color:{TEXT2};line-height:1.7;'>"
                    f"<span style='color:#22C55E;font-size:0.62rem;letter-spacing:0.1em;"
                    f"text-transform:uppercase;'>● SCANNER CONNECTED</span><br>"
                    f"MARKETS TRACKED: <b style='color:{TEXT};'>{int(_mkts_es):,}</b>"
                    f"&nbsp;&nbsp;MSG/S: <b style='color:{TEXT};'>{float(_rate_es):.1f}</b><br>"
                    f"<span style='color:{TEXT3};font-size:0.65rem;'>"
                    f"ME/TH arbs appear here within minutes of detection. YNC detections are feed artifacts."
                    f"</span></div>"
                )
            else:
                _scanner_html_badge = (
                    f"<div style='margin-bottom:0.9rem;padding:0.5rem 0.85rem;"
                    f"background:rgba(245,158,11,0.07);border:1px solid {AMBER};"
                    f"border-radius:3px;font-size:0.7rem;font-family:JetBrains Mono,monospace;"
                    f"color:{TEXT2};line-height:1.7;'>"
                    f"<span style='color:{AMBER};font-size:0.62rem;letter-spacing:0.1em;"
                    f"text-transform:uppercase;'>○ SCANNER NOT CONNECTED</span><br>"
                    f"<span style='color:{TEXT3};font-size:0.65rem;'>"
                    f"Start the scanner to begin detecting arbs. See step 1 below."
                    f"</span></div>"
                )
        except Exception:
            pass

        st.markdown(
            f"<div style='background:{PANEL};border:1px solid {BORDER};"
            f"border-left:4px solid {BLUE};border-radius:4px;"
            f"padding:1.1rem 1.3rem 1rem 1.3rem;margin-top:0.25rem;'>"
            f"{_neon_cloud_hint}"
            f"{_scanner_html_badge}"
            f"<div style='font-size:0.72rem;color:{TEXT2};font-family:Inter,sans-serif;"
            f"line-height:1.75;'>"
            f"<div style='font-size:0.62rem;letter-spacing:0.1em;text-transform:uppercase;"
            f"color:{TEXT3};margin-bottom:0.6rem;'>NO DETECTIONS IN SELECTED DATE RANGE</div>"
            f"The scanner writes detections to the database as they are found. "
            f"With the scanner running, ME/TH arbs appear here within minutes of detection. "
            f"YNC detections are feed artifacts (not actionable).<br><br>"
            f"<b style='color:{TEXT};font-size:0.68rem;letter-spacing:0.05em;'>TO START SEEING DETECTIONS HERE:</b><br>"
            f"<span style='color:{CYAN};'>1.</span>&nbsp; Start the WebSocket scanner: "
            f"<code style='background:rgba(255,255,255,0.06);padding:0.1rem 0.35rem;"
            f"border-radius:2px;font-size:0.68rem;'>python ws_bridge.py</code> "
            f"(or the Streamlit Cloud background process)<br>"
            f"<span style='color:{CYAN};'>2.</span>&nbsp; The scanner checks all markets every second "
            f"and writes any detection (ME/TH arbs + YNC feed artifacts) to this database automatically.<br>"
            f"<span style='color:{CYAN};'>3.</span>&nbsp; Detections persist across restarts — once saved "
            f"they always appear here.<br><br>"
            f"<span style='color:{TEXT3};font-size:0.67rem;'>"
            f"ME (mutually exclusive) and TH (threshold order) arbs are detected and persisted automatically. YNC records are feed artifacts (live YES+NO always ≥ $1.00 — not executable). CE scanning is currently disabled.</span>"
            f"</div></div>",
            unsafe_allow_html=True,
        )
        # Still show session queue arbs if the scanner has found any this session
        try:
            from dashboard.live_state import get_live_state as _gls5
            _state5 = _gls5()
            _sess5 = _state5.get_recent_opportunities(limit=50)
            if _sess5:
                st.markdown("<hr>", unsafe_allow_html=True)
                st.markdown(
                    f"<div style='font-size:0.62rem;letter-spacing:0.1em;text-transform:uppercase;"
                    f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
                    f"LIVE DETECTIONS ({len(_sess5)} found this session — saving to database...)</div>",
                    unsafe_allow_html=True,
                )
                _rows5 = []
                for _o5 in _sess5:
                    _ts5 = _o5.get("detected_at_ts")
                    _t5 = (
                        pd.Timestamp.fromtimestamp(_ts5, tz="UTC").tz_convert("America/New_York").strftime("%H:%M ET")
                        if _ts5 else "--"
                    )
                    _rows5.append({
                        "TIME":     _t5,
                        "TICKER":   _o5.get("ticker", "--"),
                        "STRATEGY": str(_o5.get("strategy", "--")).upper().replace("_", " "),
                        "GROSS":    f"{float(_o5.get('gross_edge_cents', 0)):.2f}c",
                        "NET EDGE": f"+{float(_o5.get('net_edge_cents', 0)):.2f}c",
                        "QTY":      str(int(_o5.get("executable_contracts", 0) or 0)),
                    })
                st.dataframe(pd.DataFrame(_rows5), use_container_width=True,
                             height=min(400, 35 * len(_rows5) + 45), hide_index=True)
        except Exception as _e5:
            st.caption(f"⚠️ Could not display session detections: {type(_e5).__name__}")
        return

    n = len(df)

    if n == 0:
        st.info(
            "No detections or historical research candidates found in the selected filters. "
            "Try widening the date range or removing strategy/edge filters."
        )
        return

    # --- KPI row ---
    edges = pd.to_numeric(df.get("net_edge_cents", pd.Series(dtype=float)), errors="coerce").dropna()
    _today_str = pd.Timestamp.now(tz="America/New_York").strftime("%Y-%m-%d")
    _det_est = pd.to_datetime(df["detected_at"], utc=True, errors="coerce").dt.tz_convert("America/New_York") if "detected_at" in df.columns else None
    n_today = int((_det_est.dt.strftime("%Y-%m-%d") == _today_str).sum()) if _det_est is not None else 0

    # Capital and profit totals
    _qty_series = pd.to_numeric(df.get("executable_contracts", df.get("qty", pd.Series(dtype=float))), errors="coerce").fillna(0)
    _ya_series  = pd.to_numeric(df.get("yes_ask", pd.Series(dtype=float)), errors="coerce").fillna(0)
    _na_series  = pd.to_numeric(df.get("no_ask",  pd.Series(dtype=float)), errors="coerce").fillna(0)
    _net_series = pd.to_numeric(df.get("net_edge_cents", pd.Series(dtype=float)), errors="coerce").fillna(0)
    _total_capital = ((_ya_series + _na_series) * _qty_series).sum()
    _total_profit  = ((_net_series / 100.0) * _qty_series).sum()

    _avg_edge_cents = edges.mean() if len(edges) > 0 else float("nan")
    _best_edge = edges.max() if len(edges) > 0 else float("nan")
    _now_et = pd.Timestamp.now(tz="America/New_York")
    _hours_elapsed = max(1/60, (_now_et - _now_et.normalize()).total_seconds() / 3600)
    _arbs_per_hour = n_today / _hours_elapsed

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("TODAY", f"{n_today:,}", help="Detections today (all strategies — ME/TH arbs + YNC feed artifacts; use strategy filter to isolate ME/TH)")
    k2.metric("IN RANGE", f"{n:,}", help="Detections in selected date range (all strategies — ME/TH arbs + YNC feed artifacts; use strategy filter to isolate ME/TH)")
    k3.metric("BEST EDGE", f"{_best_edge:.2f}c" if len(edges) > 0 else "--",
              help="Best net edge in view (use strategy filter to exclude YNC feed artifacts)")
    k4.metric("AVG EDGE", f"{_avg_edge_cents:.2f}c" if len(edges) > 0 else "--",
              help="Mean net edge in view (use strategy filter to exclude YNC feed artifacts)")
    k5.metric("RATE", f"{_arbs_per_hour:.1f}/hr" if n_today > 0 else "--",
              help="Detections today divided by hours elapsed. Use strategy filter to isolate ME/TH actionable arbs (excludes YNC feed artifacts).")

    # --- Analytics expander (charts + breakdowns, collapsed by default) ---
    def _section_header_inner(t):
        st.markdown(
            f"<div style='font-size:0.62rem;font-weight:700;letter-spacing:0.1em;"
            f"text-transform:uppercase;color:{AMBER};margin:0.6rem 0 0.2rem 0;'>{t}</div>",
            unsafe_allow_html=True,
        )
    with st.expander("📊 Analytics", expanded=False):
        _section_header_inner("BY STRATEGY")
        _strat_breakdown_loaded = False
        try:
            _strat_rows_db = []
            if not df.empty and "strategy_type" in df.columns:
                for _st_key, _st_grp in df.groupby("strategy_type"):
                    _st_count = len(_st_grp)
                    _st_net_s = pd.to_numeric(_st_grp.get("net_edge_cents", pd.Series(dtype=float)), errors="coerce")
                    _st_gross_s = pd.to_numeric(_st_grp.get("gross_edge_cents", pd.Series(dtype=float)), errors="coerce")
                    _avg_n = _st_net_s.mean() if len(_st_net_s.dropna()) > 0 else float("nan")
                    _avg_g = _st_gross_s.mean() if len(_st_gross_s.dropna()) > 0 else float("nan")
                    _best_n = _st_net_s.max() if len(_st_net_s.dropna()) > 0 else float("nan")
                    _strat_rows_db.append({
                        "Strategy": str(_st_key),
                        "Count": _st_count,
                        "Avg Net (¢)": f"{_avg_n:.2f}" if pd.notna(_avg_n) else "--",
                        "Avg Gross (¢)": f"{_avg_g:.2f}" if pd.notna(_avg_g) else "--",
                        "Best (¢)": f"{_best_n:.2f}" if pd.notna(_best_n) else "--",
                        "_avg_net_num": float(_avg_n) if pd.notna(_avg_n) else 0.0,
                        "_count": _st_count,
                    })
                _strat_breakdown_loaded = bool(_strat_rows_db)

            if _strat_breakdown_loaded and _strat_rows_db:
                _strat_grp_df = (
                    pd.DataFrame(_strat_rows_db)
                    .sort_values("_count", ascending=False)
                    .reset_index(drop=True)
                )
                _display_strat_df = _strat_grp_df.drop(columns=["_avg_net_num", "_count"])

                def _color_count(val):
                    try:
                        v = int(val)
                    except Exception:
                        return ""
                    if v > 10:
                        return "color:#22C55E;font-weight:600;"
                    elif v >= 1:
                        return "color:#F59E0B;font-weight:600;"
                    return "color:#6B7280;"

                _styled_strat = _display_strat_df.style.applymap(_color_count, subset=["Count"])
                st.dataframe(_styled_strat, use_container_width=True, hide_index=True)

                _chart_labels = _strat_grp_df["Strategy"].tolist()
                _chart_vals = _strat_grp_df["_avg_net_num"].tolist()
                _STRAT_COLORS_PERF = {
                    "yes_no_complement": "#3B82F6", "collectively_exhaustive": "#22C55E",
                    "mutually_exclusive": "#F97316", "threshold_order": "#A855F7", "superset": "#8B5CF6",
                }
                _STRAT_SHORT_P = {
                    "yes_no_complement": "YNC", "collectively_exhaustive": "CE",
                    "mutually_exclusive": "ME", "threshold_order": "TH", "superset": "SS",
                }
                if _chart_labels:
                    _perf_fig = go.Figure(go.Bar(
                        x=[_STRAT_SHORT_P.get(s, s[:4].upper()) for s in _chart_labels],
                        y=_chart_vals,
                        marker_color=[_STRAT_COLORS_PERF.get(s, "#64748B") for s in _chart_labels],
                        marker_line_width=0,
                        text=[f"{v:.2f}c" for v in _chart_vals],
                        textposition="outside", cliponaxis=False,
                    ))
                    _perf_fig.update_layout(**plotly_dark_layout(
                        title={"text": "AVG NET EDGE BY STRATEGY (¢) — YNC = feed artifact", "font": {"size": 10, "color": TEXT3}},
                        height=200, showlegend=False,
                        margin={"l": 30, "r": 30, "t": 35, "b": 30},
                        xaxis_title="", yaxis_title="Avg Net (¢)",
                    ))
                    st.plotly_chart(_perf_fig, use_container_width=True)
        except Exception:
            pass

        # TOP MARKETS
        try:
            _mkt_col_a = next((c for c in ("market_id_1", "market_ticker", "ticker") if c in df.columns), None)
            if _mkt_col_a and not df.empty:
                _mkt_counts = df[_mkt_col_a].dropna().astype(str).value_counts().head(10)
                if not _mkt_counts.empty:
                    _section_header_inner("TOP MARKETS")
                    _mkt_fig = go.Figure(go.Bar(
                        x=_mkt_counts.values.tolist(), y=_mkt_counts.index.tolist(),
                        orientation="h", marker_color="#26a69a", marker_line_width=0,
                    ))
                    _mkt_fig.update_layout(**plotly_dark_layout(
                        height=200, showlegend=False,
                        margin={"l": 160, "r": 40, "t": 10, "b": 30},
                        xaxis_title="Arb Count (all strategies; use filter to exclude YNC feed artifacts)", yaxis={"automargin": True},
                    ))
                    st.plotly_chart(_mkt_fig, use_container_width=True)
        except Exception:
            pass

        # Hour-of-day + heatmap
        if "detected_at" in df.columns:
            try:
                _ts_raw = pd.to_datetime(df["detected_at"], errors="coerce")
                if _ts_raw.dt.tz is None:
                    _ts = _ts_raw.dt.tz_localize("UTC").dt.tz_convert("America/New_York")
                else:
                    _ts = _ts_raw.dt.tz_convert("America/New_York")
                _hour_of_day = _ts.dropna().dt.hour
                _by_hour = _hour_of_day.value_counts().reindex(range(24), fill_value=0).reset_index()
                _by_hour.columns = ["hour", "count"]
                _by_hour = _by_hour.sort_values("hour")
                _section_header_inner("DETECTIONS BY HOUR (ET)")
                _fig = go.Figure(go.Bar(
                    x=_by_hour["hour"], y=_by_hour["count"],
                    marker_color="#22C55E", marker_line_width=0,
                ))
                _fig.update_layout(**plotly_dark_layout(
                    height=180, xaxis_title="Hour of Day (ET)", yaxis_title="Count",
                    xaxis={"tickmode": "linear", "dtick": 1},
                    margin={"l": 40, "r": 20, "t": 15, "b": 40},
                ))
                st.plotly_chart(_fig, use_container_width=True)

                if len(df) >= 10:
                    _ts_hm = _ts.dropna()
                    _hm_df = pd.DataFrame({"hour": _ts_hm.dt.hour, "dow": _ts_hm.dt.dayofweek}).dropna()
                    _DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                    _hm_pivot = (
                        _hm_df.groupby(["dow", "hour"]).size().unstack(fill_value=0)
                        .reindex(index=range(7), columns=range(24), fill_value=0)
                    )
                    _hm_fig = go.Figure(go.Heatmap(
                        z=_hm_pivot.values.tolist(), x=list(range(24)), y=_DAY_NAMES,
                        colorscale="Greens", showscale=True,
                    ))
                    _hm_fig.update_layout(**plotly_dark_layout(
                        title={"text": "HEATMAP (Hour × Day)", "font": {"size": 10, "color": TEXT3}},
                        height=200,
                        xaxis={"title": "Hour (ET)", "tickmode": "linear", "dtick": 1},
                        yaxis={"automargin": True},
                        margin={"l": 50, "r": 20, "t": 30, "b": 40},
                    ))
                    st.plotly_chart(_hm_fig, use_container_width=True)
            except Exception:
                pass

    # --- Opportunity log ---
    _opp_hdr_l, _opp_hdr_r = st.columns([3, 1])
    with _opp_hdr_l:
        _section_header("LOGGED OPPORTUNITIES")
    with _opp_hdr_r:
        if len(df) > 0:
            st.download_button(
                label="⬇️ CSV",
                data=df.to_csv(index=False),
                file_name="kalshi_arb_history.csv",
                mime="text/csv",
                key="p05_dl_arb_history",
            )
    disp = df.copy()
    if "detected_at" in disp.columns:
        disp["TIME"] = _to_est(disp["detected_at"])

    # Pre-warm Kalshi API title cache concurrently, then build a lookup dict so the
    # display loop below does O(1) dict lookups instead of per-row function calls.
    _info_cache: dict = {}
    try:
        from concurrent.futures import ThreadPoolExecutor as _TPE5
        _ticker_list5 = disp.get("ticker", pd.Series()).tolist()
        _strat_list5  = disp.get("strategy_type", pd.Series()).tolist()
        _unique_pairs5: list = []
        _seen5: set = set()
        for _tk5, _st5 in zip(_ticker_list5, _strat_list5):
            _key5 = (_tk5, _st5)
            if _key5 not in _seen5:
                _seen5.add(_key5)
                _unique_pairs5.append(_key5)
        if _unique_pairs5:
            with _TPE5(max_workers=min(8, len(_unique_pairs5))) as _pool5:
                _results5 = list(_pool5.map(lambda p: _resolve_info(p[0], p[1]), _unique_pairs5))
            _info_cache = {p: r for p, r in zip(_unique_pairs5, _results5)}
    except Exception:
        pass

    _market_col, _action_col = [], []
    _capital_col, _pnl_col, _ann_col = [], [], []

    for _, row in disp.iterrows():
        tk    = row.get("ticker", "")
        strat = row.get("strategy_type", "")
        qty   = float(row.get("qty") or row.get("executable_contracts") or 0)
        ya    = float(row.get("yes_ask") or 0)
        na    = float(row.get("no_ask") or 0)
        net   = float(row.get("net_edge_cents") or 0)

        # Use pre-built dict; fall back to calling _resolve_info only if cache missed
        info = _info_cache.get((tk, strat)) or _resolve_info(tk, strat)
        _title = info["title"] or re.sub(r"\s*\(\d+ legs\)\s*$", "", str(tk)).strip()
        _market_col.append(_title)
        _action_col.append(_action_label(strat, tk, info["title"]))

        capital = (ya + na) * qty
        pnl     = (net / 100.0) * qty

        _capital_col.append(f"${capital:.2f}" if capital > 0 else "--")
        _pnl_col.append(f"${pnl:.4f}" if pnl > 0 else "--")

        # Holding period = time from detection to market close (not time remaining from now).
        # This is correct for historical arbs where close_time is in the past.
        days = None
        _det_raw = row.get("detected_at")
        if info["close_time"] and _det_raw is not None:
            try:
                _ct = datetime.fromisoformat(info["close_time"].replace("Z", "+00:00"))
                _da = pd.Timestamp(_det_raw, tz="UTC").to_pydatetime()
                if _da.tzinfo is None:
                    _da = _da.replace(tzinfo=timezone.utc)
                _hold = (_ct - _da).total_seconds() / 86400.0
                if _hold > 0:
                    days = max(_hold, 1.0 / 24)
            except Exception:
                days = None
        if capital > 0 and pnl > 0 and days:
            roi = pnl / capital
            apr = roi / (days / 365.25) * 100
            _ann_col.append(f"{apr:,.0f}%")
        else:
            _ann_col.append("--")

    disp["MARKET"]     = _market_col
    disp["ACTION"]     = _action_col
    disp["CAPITAL"]    = _capital_col
    disp["EST. PROFIT"] = _pnl_col
    disp["ANN RETURN"] = _ann_col

    for col in ["gross_edge_cents", "fees_cents", "net_edge_cents"]:
        if col in disp.columns:
            disp[col] = pd.to_numeric(disp[col], errors="coerce").apply(
                lambda v: f"{v:.2f}c" if pd.notna(v) else "--"
            )

    # Prefer executable_contracts over qty to avoid duplicate "QTY" columns
    _qty_col = "executable_contracts" if "executable_contracts" in disp.columns else "qty"
    _excl = {"qty", "executable_contracts"} - {_qty_col}
    show = [c for c in [
        "TIME", "MARKET", "ACTION", "ticker", "strategy_type",
        "classification", "confidence",
        "gross_edge_cents", "fees_cents", "net_edge_cents",
        _qty_col, "CAPITAL", "EST. PROFIT", "ANN RETURN",
    ] if c in disp.columns and c not in _excl]
    rename = {
        "ticker": "TICKER", "strategy_type": "STRATEGY",
        "classification": "CLASS", "confidence": "CONF",
        "gross_edge_cents": "GROSS", "fees_cents": "FEES",
        "net_edge_cents": "NET EDGE", "qty": "QTY", "executable_contracts": "QTY",
        "detected_at": "DETECTED_AT_UTC",
    }
    # --- Apply sort ---
    _disp_out = disp[show].rename(columns=rename)
    _sort_col_map = {
        "NET EDGE ↓":   ("NET EDGE",   False),
        "NET EDGE ↑":   ("NET EDGE",   True),
        "GROSS EDGE ↓": ("GROSS",      False),
        "TIME ↓":       ("TIME",       False),
        "TIME ↑":       ("TIME",       True),
    }
    _sc, _asc = _sort_col_map.get(_sort_choice, ("NET EDGE", False))
    if _sc in _disp_out.columns:
        # NET EDGE / GROSS columns are formatted strings like "1.50c"; strip for sort
        if _sc in ("NET EDGE", "GROSS"):
            _sort_key = pd.to_numeric(
                _disp_out[_sc].astype(str).str.replace("c", "", regex=False).str.replace("--", "nan", regex=False),
                errors="coerce",
            )
            _disp_out = (
                _disp_out
                .assign(_sort_key=_sort_key.values)
                .sort_values("_sort_key", ascending=_asc, na_position="last")
                .drop(columns=["_sort_key"])
            )
        else:
            _disp_out = _disp_out.sort_values(_sc, ascending=_asc, na_position="last")
    if "net_edge_cents" in df.columns and (pd.to_numeric(df["net_edge_cents"], errors="coerce") > 50).any():
        st.caption("Some records show edge >50c — pre-fix scanner data from before the Gate 1b stale-book fix. CE scanner is currently disabled.")

    # --- Total executed P&L metric ---
    _exec_col = next((c for c in df.columns if c in ("executed", "is_executed")), None)
    if _exec_col:
        _exec_mask = df[_exec_col].astype(str).str.lower().isin({"1", "true", "yes"})
        _exec_net = pd.to_numeric(df.loc[_exec_mask, "net_edge_cents"] if "net_edge_cents" in df.columns else pd.Series(dtype=float), errors="coerce").fillna(0)
        _exec_pnl_cents = float(_exec_net.sum())
        st.metric("TOTAL EXECUTED P&L", f"{_exec_pnl_cents:+.2f}c",
                  help="Sum of net_edge_cents for rows where executed=True")

    # --- Classification color coding ---
    _CLASS_COLORS = {
        "A": "background-color:#14532d;color:#86efac;",   # green
        "B": "background-color:#78350f;color:#fcd34d;",   # amber
        "C": "background-color:#450a0a;color:#fca5a5;",   # red/dim
        "D": "background-color:#1c1c1e;color:#6b7280;",   # gray
    }

    def _style_class_row(row: pd.Series):
        cls = str(row.get("CLASS", "")).strip().upper()
        style = _CLASS_COLORS.get(cls, "")
        return [style] * len(row)

    if not _disp_out.empty and "CLASS" in _disp_out.columns:
        _styled = _disp_out.style.apply(_style_class_row, axis=1)
        st.dataframe(_styled, use_container_width=True, height=500, hide_index=True)
    else:
        st.dataframe(_disp_out, use_container_width=True, height=500, hide_index=True)

    # --- Summary Stats expander ---
    if len(df) > 0:
        with st.expander("📊 Summary Stats", expanded=False):
            _ss_edges = pd.to_numeric(df.get("net_edge_cents", pd.Series(dtype=float)), errors="coerce").dropna()
            _ss_gross = pd.to_numeric(df.get("gross_edge_cents", pd.Series(dtype=float)), errors="coerce").dropna()
            _ss_total = len(df)
            _ss_avg_net = _ss_edges.mean() if len(_ss_edges) > 0 else float("nan")
            _ss_med_gross = _ss_gross.median() if len(_ss_gross) > 0 else float("nan")
            # Best single arb
            _ss_best_ticker = "--"
            _ss_best_edge = float("nan")
            if len(_ss_edges) > 0:
                _ss_best_idx = _ss_edges.idxmax()
                _ss_best_edge = _ss_edges[_ss_best_idx]
                _tk_col = next((c for c in ("ticker", "market_id_1") if c in df.columns), None)
                if _tk_col:
                    _ss_best_ticker = str(df.loc[_ss_best_idx, _tk_col])[:40]
            # Most active hour
            _ss_most_active_hour = "--"
            if "detected_at" in df.columns:
                try:
                    _ss_ts = pd.to_datetime(df["detected_at"], utc=True, errors="coerce").dt.tz_convert("America/New_York")
                    _ss_hour_counts = _ss_ts.dt.hour.value_counts()
                    if not _ss_hour_counts.empty:
                        _ss_top_hr = int(_ss_hour_counts.idxmax())
                        _ss_most_active_hour = f"{_ss_top_hr:02d}:00 ET ({_ss_hour_counts.iloc[0]} detections)"
                except Exception:
                    pass
            _ssc1, _ssc2, _ssc3 = st.columns(3)
            _ssc1.metric(
                "Total Records" if not _has_ohlc else "Total (Records + Historical Signals)",
                f"{_ss_total:,}",
            )
            _ssc2.metric("Avg Net Edge", f"{_ss_avg_net:.2f}c" if not pd.isna(_ss_avg_net) else "--")
            _ssc3.metric("Median Gross Edge", f"{_ss_med_gross:.2f}c" if not pd.isna(_ss_med_gross) else "--")
            _ssc4, _ssc5 = st.columns(2)
            _ssc4.metric("Best Single Arb", f"{_ss_best_edge:.2f}c" if not pd.isna(_ss_best_edge) else "--",
                         help=_ss_best_ticker)
            _ssc5.metric("Most Active Hour", _ss_most_active_hour)
            if not pd.isna(_ss_best_edge):
                st.caption(f"Best detection ticker: {_ss_best_ticker} — use strategy filter to exclude YNC feed artifacts and isolate ME/TH arbs")

    # --- Classification upgrade path note (shown when Class C or D arbs are present) ---
    if not _disp_out.empty and "CLASS" in _disp_out.columns:
        _has_c_or_d = _disp_out["CLASS"].astype(str).str.strip().str.upper().isin({"C", "D"}).any()
        if _has_c_or_d:
            st.info("""
📈 **Classification Upgrade Path**
- Class C → B: Confirmed on next snapshot with consistent pricing
- Class B → A: Verified against live L2 orderbook with sufficient depth
- Class D: Fee-killed — gross edge too small to survive Kalshi's taker fee
""")

    # --- Arb detail drilldown ---
    _STRAT_BADGE_COLORS = {
        "yes_no_complement":      ("#14532d", "#86efac", "YNC"),
        "collectively_exhaustive": ("#1e3a5f", "#93c5fd", "CE"),
        "mutually_exclusive":      ("#4a1d96", "#c4b5fd", "ME"),
        "threshold_order":         ("#78350f", "#fcd34d", "TH"),
        "superset":                ("#1c1c1e", "#a1a1aa", "SS"),
    }
    _detail_options = df["id"].tolist() if "id" in df.columns else df.index.tolist()
    if _detail_options:
        _sel = st.selectbox(
            "Select detection to inspect",
            options=_detail_options,
            key="p05_detail_select",
        )
        # Lookup raw row from original df
        if "id" in df.columns:
            _row_mask = df["id"] == _sel
        else:
            _row_mask = df.index == _sel
        if _row_mask.any():
            _row = df[_row_mask].iloc[0]
            _strat_raw = str(_row.get("strategy_type", "") or "")
            _bg, _fg, _abbr = _STRAT_BADGE_COLORS.get(
                _strat_raw,
                ("#2a2a2a", "#a1a1aa", _strat_raw.upper()[:4] or "?")
            )
            _gross_raw = pd.to_numeric(_row.get("gross_edge_cents"), errors="coerce")
            _net_raw   = pd.to_numeric(_row.get("net_edge_cents"),   errors="coerce")
            _fee_paid  = (_gross_raw - _net_raw) if (pd.notna(_gross_raw) and pd.notna(_net_raw)) else float("nan")
            _det_raw   = _row.get("detected_at")
            _ticker_dd = str(_row.get("ticker") or _row.get("market_id_1") or "")
            try:
                _det_dt = pd.Timestamp(_det_raw, tz="UTC").tz_convert("America/New_York")
                _det_human = _det_dt.strftime("%Y-%m-%d %H:%M:%S ET")
                _det_age_s = (datetime.now(timezone.utc) - _det_dt.to_pydatetime().astimezone(timezone.utc)).total_seconds()
                if _det_age_s < 60:
                    _det_ago = f"{int(_det_age_s)}s ago"
                elif _det_age_s < 3600:
                    _det_ago = f"{int(_det_age_s // 60)}m ago"
                elif _det_age_s < 86400:
                    _det_ago = f"{int(_det_age_s // 3600)}h ago"
                else:
                    _det_ago = f"{int(_det_age_s // 86400)}d ago"
                _det_display = f"{_det_human} ({_det_ago})"
            except Exception:
                _det_human = str(_det_raw) if _det_raw is not None else "--"
                _det_display = _det_human

            with st.container():
                # Header: strategy badge + ticker
                st.markdown(
                    f"<div style='margin:0.75rem 0 0.5rem 0;display:flex;align-items:center;gap:0.75rem;'>"
                    f"<span style='background:{_bg};color:{_fg};padding:0.2rem 0.65rem;"
                    f"border-radius:999px;font-size:0.72rem;font-weight:700;"
                    f"letter-spacing:0.08em;font-family:JetBrains Mono,monospace;'>{_abbr}</span>"
                    f"<span style='font-family:JetBrains Mono,monospace;font-size:0.95rem;"
                    f"color:{TEXT};font-weight:600;letter-spacing:0.04em;'>{_ticker_dd or '—'}</span>"
                    f"<span style='font-size:0.68rem;color:{TEXT3};'>{_strat_raw.replace('_', ' ').title()}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                # 3-col metric row: Gross | Net | Fee Paid
                _dc1, _dc2, _dc3 = st.columns(3)
                _dc1.metric(
                    "GROSS EDGE",
                    f"{_gross_raw:.2f}c" if pd.notna(_gross_raw) else "—",
                )
                _dc2.metric(
                    "NET EDGE",
                    f"{_net_raw:.2f}c" if pd.notna(_net_raw) else "—",
                )
                _dc3.metric(
                    "FEE PAID",
                    f"{_fee_paid:.2f}c" if pd.notna(_fee_paid) else "—",
                    help="gross_edge − net_edge",
                )
                # Timestamp
                st.markdown(
                    f"<div style='font-size:0.68rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
                    f"margin:0.25rem 0 0.5rem 0;'>🕐 {_det_display}</div>",
                    unsafe_allow_html=True,
                )

                # CE legs section
                _is_ce_dd = _strat_raw == "collectively_exhaustive"
                if _is_ce_dd:
                    _legs_dd = []
                    # Try to get legs from a 'legs' or 'metadata' column
                    _legs_raw_dd = _row.get("legs") or _row.get("metadata")
                    if _legs_raw_dd:
                        try:
                            if isinstance(_legs_raw_dd, str):
                                _legs_parsed = json.loads(_legs_raw_dd)
                            else:
                                _legs_parsed = _legs_raw_dd
                            if isinstance(_legs_parsed, list):
                                _legs_dd = [
                                    l if isinstance(l, str) else l.get("ticker", str(l))
                                    for l in _legs_parsed
                                ]
                            elif isinstance(_legs_parsed, dict):
                                _legs_dd = _legs_parsed.get("legs", [])
                        except Exception:
                            pass
                    # Fallback: extract from ticker "(N legs)" pattern
                    if not _legs_dd and _ticker_dd:
                        _leg_match = re.search(r"\((\d+) legs\)", _ticker_dd)
                        if _leg_match:
                            _legs_dd = [f"leg_{i+1}" for i in range(int(_leg_match.group(1)))]
                    if _legs_dd:
                        st.markdown(
                            f"<div style='background:rgba(30,58,95,0.35);border:1px solid #1e3a5f;"
                            f"border-radius:3px;padding:0.5rem 0.85rem;margin-bottom:0.5rem;'>"
                            f"<div style='font-size:0.58rem;letter-spacing:0.1em;text-transform:uppercase;"
                            f"color:#93c5fd;margin-bottom:0.3rem;'>CE LEGS ({len(_legs_dd)})</div>"
                            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.68rem;"
                            f"color:{TEXT};line-height:1.8;'>"
                            + " &nbsp;·&nbsp; ".join(str(l) for l in _legs_dd)
                            + "</div></div>",
                            unsafe_allow_html=True,
                        )

                # Similar arbs (same ticker ±24h) — search live arb store only
                _similar_count = 0
                _ticker_freq   = 0
                if _ticker_dd:
                    try:
                        from dashboard.live_arb_store import query as _lar_query
                        _all_live = _lar_query(days_back=365, min_net_edge_cents=0)
                        _ticker_freq = sum(1 for _a in _all_live if _a.get("ticker") == _ticker_dd)
                        if _det_raw:
                            import time as _tmod
                            try:
                                _det_ts = pd.to_datetime(_det_raw, utc=True).timestamp()
                            except Exception:
                                _det_ts = None
                            if _det_ts:
                                _similar_count = sum(
                                    1 for _a in _all_live
                                    if _a.get("ticker") == _ticker_dd
                                    and _a.get("id") != _sel
                                    and abs((pd.to_datetime(_a.get("detected_at", ""), utc=True).timestamp() - _det_ts)) <= 86400
                                )
                    except Exception:
                        pass

                _dd_sim_col, _dd_freq_col = st.columns(2)
                _dd_sim_col.metric(
                    "SIMILAR DETECTIONS (±24h, same ticker)",
                    f"{_similar_count:,}" if _similar_count is not None else "—",
                    help="Detections with same ticker within 24h (all strategies — ME/TH arbs + YNC feed artifacts)",
                )
                _dd_freq_col.metric(
                    "TICKER FREQUENCY (all-time)",
                    f"{_ticker_freq:,}" if _ticker_freq is not None else "—",
                    help="Total times this ticker has appeared in detection history (all strategies — ME/TH arbs + YNC feed artifacts)",
                )

                # Copy arb details
                _copy_lines = [
                    f"Ticker:     {_ticker_dd}",
                    f"Strategy:   {_strat_raw.replace('_', ' ').upper()}",
                    f"Detected:   {_det_display}",
                    f"Gross Edge: {f'{_gross_raw:.2f}c' if pd.notna(_gross_raw) else '—'}",
                    f"Net Edge:   {f'{_net_raw:.2f}c' if pd.notna(_net_raw) else '—'}",
                    f"Fee Paid:   {f'{_fee_paid:.2f}c' if pd.notna(_fee_paid) else '—'}",
                ]
                _cls_dd = str(_row.get("classification") or "")
                if _cls_dd:
                    _copy_lines.append(f"Class:      {_cls_dd}")
                _qty_dd = _row.get("executable_contracts") or _row.get("qty")
                if _qty_dd is not None:
                    try:
                        _copy_lines.append(f"Qty:        {int(float(_qty_dd))}")
                    except Exception:
                        pass
                if _ticker_freq:
                    _copy_lines.append(f"Freq (DB):  {_ticker_freq} total · {_similar_count} within ±24h")
                st.markdown(
                    f"<div style='font-size:0.6rem;letter-spacing:0.08em;text-transform:uppercase;"
                    f"color:{TEXT3};margin:0.5rem 0 2px 0;'>Copy detection details</div>",
                    unsafe_allow_html=True,
                )
                st.code("\n".join(_copy_lines), language=None)
                # Copy ticker text_input (read-only display for easy copy-paste to Kalshi)
                if _ticker_dd:
                    st.text_input(
                        "Copy ticker",
                        value=_ticker_dd,
                        key=f"p05_copy_ticker_{_sel}",
                        help="Select all and copy to paste this ticker directly into Kalshi",
                        disabled=True,
                    )
                    _dd_info = _resolve_info(_ticker_dd, _strat_raw)
                    _dd_url = _dd_info.get("kalshi_url") or ""
                    if not _dd_url:
                        _series = re.sub(r"\s*\(\d+ legs\)\s*$", "", _ticker_dd).strip().lower().rsplit("-", 1)[0]
                        _dd_url = f"https://kalshi.com/markets/{_series}"
                    st.markdown(f"[🔗 View on Kalshi]({_dd_url})")

                # Similar markets — other live arbs from the same event prefix
                try:
                    _event_prefix = _ticker_dd.rsplit("-", 1)[0] if "-" in _ticker_dd else _ticker_dd
                    _sim_market_rows = []
                    try:
                        from dashboard.live_arb_store import query as _lar_query_sim
                        _all_live_sim = _lar_query_sim(days_back=365, min_net_edge_cents=0)
                        for _smr in _all_live_sim:
                            _smr_tk = str(_smr.get("ticker") or "")
                            if _smr_tk.startswith(_event_prefix) and _smr.get("id") != _sel:
                                _sim_market_rows.append({
                                    "TICKER": _smr_tk,
                                    "STRATEGY": str(_smr.get("strategy_type") or "--").replace("_", " ").upper(),
                                    "NET EDGE (c)": f"{float(_smr.get('net_edge_cents', 0)):.2f}",
                                    "DETECTED": str(_smr.get("detected_at") or "--")[:19],
                                })
                        _sim_market_rows = sorted(
                            _sim_market_rows,
                            key=lambda x: float(x["NET EDGE (c)"].replace("--", "0")),
                            reverse=True,
                        )[:10]
                    except Exception:
                        pass
                    if _sim_market_rows:
                        st.markdown(
                            f"<div style='font-size:0.6rem;letter-spacing:0.08em;text-transform:uppercase;"
                            f"color:{TEXT3};margin:0.75rem 0 4px 0;'>Similar markets (same event: {_event_prefix})</div>",
                            unsafe_allow_html=True,
                        )
                        st.dataframe(
                            pd.DataFrame(_sim_market_rows),
                            use_container_width=True,
                            hide_index=True,
                        )
                except Exception:
                    pass

    # --- TOP ARBS Leaderboard ---
    try:
        _top_df = df.copy() if not df.empty else pd.DataFrame()
        if not _top_df.empty and "classification" in _top_df.columns and "net_edge_cents" in _top_df.columns:
            _cls_a = _top_df[_top_df["classification"].astype(str).str.strip().str.upper() == "A"]
            _cls_b = _top_df[_top_df["classification"].astype(str).str.strip().str.upper() == "B"]
            if not _cls_a.empty:
                _lead_src = _cls_a
                _lead_label = "Class A"
            elif not _cls_b.empty:
                _lead_src = _cls_b
                _lead_label = "Class B"
            else:
                _lead_src = pd.DataFrame()
                _lead_label = ""

            # Strategy filter multiselect above the leaderboard
            _all_strat_labels = sorted({
                str(r.get("strategy_type") or r.get("STRATEGY") or "").replace("_", " ").title()
                for _, r in _lead_src.iterrows()
            } - {""})
            _lead_strat_filter = st.multiselect(
                "Strategy filter",
                options=_all_strat_labels,
                default=_all_strat_labels,
                key="p05_lead_strat_filter",
                help="Filter leaderboard by strategy type",
            )
            if _lead_strat_filter and len(_lead_strat_filter) < len(_all_strat_labels):
                _lead_src = _lead_src[
                    _lead_src.get("strategy_type", pd.Series(dtype=str))
                    .fillna("")
                    .str.replace("_", " ")
                    .str.title()
                    .isin(_lead_strat_filter)
                ]

            _section_header("TOP DETECTIONS LEADERBOARD (use filter above to exclude YNC feed artifacts)")
            if not _lead_src.empty:
                _lead_src = _lead_src.copy()
                _lead_src["_net_num"] = pd.to_numeric(_lead_src["net_edge_cents"], errors="coerce")
                _top10 = (
                    _lead_src.sort_values("_net_num", ascending=False)
                    .head(10)
                    .reset_index(drop=True)
                )
                _now_utc = datetime.now(timezone.utc)
                _lead_rows = []
                for _ri, _rrow in _top10.iterrows():
                    _ticker = str(_rrow.get("ticker") or _rrow.get("TICKER") or "--")
                    _strat  = str(_rrow.get("strategy_type") or "--").replace("_", " ").upper()
                    _net_v  = pd.to_numeric(_rrow.get("net_edge_cents"), errors="coerce")
                    _det    = _rrow.get("detected_at") or "--"
                    try:
                        _det_str = (
                            pd.Timestamp(_det, tz="UTC")
                            .tz_convert("America/New_York")
                            .strftime("%Y-%m-%d %H:%M ET")
                        )
                    except Exception:
                        _det_str = str(_det)[:19]
                    # Age column
                    try:
                        _det_ts = pd.Timestamp(_det, tz="UTC")
                        _age_sec = (_now_utc - _det_ts.to_pydatetime()).total_seconds()
                        if _age_sec < 3600:
                            _age_str = f"{int(_age_sec // 60)}m ago"
                        elif _age_sec < 86400:
                            _age_str = f"{int(_age_sec // 3600)}h ago"
                        else:
                            _age_str = f"{int(_age_sec // 86400)}d ago"
                    except Exception:
                        _age_str = "--"
                    # Rank label
                    _rank_label = f"🏆 #1" if _ri == 0 else f"#{_ri + 1}"
                    _net_display = f"{_net_v:.2f}" if pd.notna(_net_v) else "--"
                    _row_source = str(_rrow.get("source") or "").lower()
                    _source_label = "OHLC Scan" if _row_source == "targeted_ohlc" else "Live Scanner"
                    _lead_rows.append({
                        "RANK": _rank_label,
                        "TICKER": _ticker,
                        "STRATEGY": _strat,
                        "NET EDGE (c)": _net_display,
                        "SOURCE": _source_label,
                        "AGE": _age_str,
                        "DETECTED": _det_str,
                    })
                st.caption(f"Top 10 by net edge — {_lead_label} only")
                _lead_df = pd.DataFrame(_lead_rows)

                # Color the NET EDGE (c) column
                def _color_net_edge(val: str):
                    try:
                        v = float(str(val).replace("c", "").strip())
                    except Exception:
                        return ""
                    if v > 5:
                        return "color:#22C55E;font-weight:600;"
                    elif v >= 2:
                        return "color:#F59E0B;font-weight:600;"
                    else:
                        return "color:#EF4444;"

                _styled_lead = _lead_df.style.applymap(_color_net_edge, subset=["NET EDGE (c)"])
                st.dataframe(
                    _styled_lead,
                    use_container_width=True,
                    height=min(400, 45 * len(_lead_rows) + 45),
                    hide_index=True,
                )

                # Show ticker in st.code for easy copy
                if _lead_rows:
                    _top_ticker = _lead_rows[0]["TICKER"]
                    st.markdown(
                        f"<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.06em;"
                        f"text-transform:uppercase;margin-top:0.3rem;margin-bottom:2px;'>Top ticker (select to copy)</div>",
                        unsafe_allow_html=True,
                    )
                    st.code(_top_ticker, language=None)

                # Median time between top arbs
                try:
                    _top_det_times = []
                    for _rrow2 in _lead_rows:
                        try:
                            _top_det_times.append(pd.Timestamp(_rrow2["DETECTED"], tz="America/New_York"))
                        except Exception:
                            pass
                    if len(_top_det_times) >= 3:
                        _top_det_times_sorted = sorted(_top_det_times)
                        _gaps = [
                            (_top_det_times_sorted[i+1] - _top_det_times_sorted[i]).total_seconds()
                            for i in range(len(_top_det_times_sorted) - 1)
                        ]
                        _med_gap_sec = sorted(_gaps)[len(_gaps) // 2]
                        if _med_gap_sec < 3600:
                            _med_gap_str = f"{int(_med_gap_sec // 60)}m"
                        elif _med_gap_sec < 86400:
                            _med_gap_str = f"{_med_gap_sec / 3600:.1f}h"
                        else:
                            _med_gap_str = f"{_med_gap_sec / 86400:.1f}d"
                        st.caption(f"Median time between top records: {_med_gap_str}")
                except Exception:
                    pass
            else:
                st.info("No records in dataset yet (use strategy filter to isolate ME/TH arbs)")
    except Exception:
        pass

    # --- Download CSV ---
    _KEY_EXPORT_COLS = [
        "id", "detected_at", "strategy_type", "classification",
        "gross_edge_cents", "net_edge_cents",
        "market_id_1", "market_id_2",
        "yes_ask_1", "no_ask_1", "yes_ask_2", "no_ask_2",
        "executed", "confidence",
    ]
    _export_df = df[[c for c in _KEY_EXPORT_COLS if c in df.columns]].copy()
    _csv_bytes = _export_df.to_csv(index=False).encode("utf-8")
    _csv_filename = f"kalshi_arbs_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    _dl_label_suffix = (
        f"{_n_live:,} live detections + {_n_ohlc:,} historical signals"
        if _has_ohlc and _n_live > 0
        else (f"{_n_ohlc:,} historical research candidates" if _has_ohlc else f"{len(_export_df):,} detections (ME/TH arbs + YNC feed artifacts)")
    )
    st.download_button(
        label=f"⬇ Download CSV — {_dl_label_suffix} ({_date_from.strftime('%b %d')} – {_date_to.strftime('%b %d, %Y')})",
        data=_csv_bytes,
        file_name=_csv_filename,
        mime="text/csv",
        help=(
            f"Exports ALL {len(_export_df):,} filtered rows (not just the current page view). "
            f"Key columns: id, detected_at, strategy_type, classification, gross/net edge, "
            f"market_id_1/2, yes/no ask prices, executed, confidence."
        ),
    )

    # --- Auto-refresh when WS live ---
    try:
        from dashboard.live_state import get_live_state as _gls_ar
        import time as _t_p05
        if _gls_ar().get_stats().get("connected", False):
            _now_p05 = _t_p05.time()
            if st.session_state.get("_p05_next_refresh", 0) <= _now_p05:
                st.session_state["_p05_next_refresh"] = _now_p05 + 10
                st.rerun()
    except Exception:
        pass


def _section_header(title: str):
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.12em;text-transform:uppercase;"
        f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>{title}</div>",
        unsafe_allow_html=True,
    )


def _unavailable(err: str):
    """Show an inline data-unavailable notice with an optional error hint."""
    _msg = (err or "").strip().replace("\n", " ")
    if len(_msg) > 200:
        _msg = _msg[:200] + "…"
    st.markdown(
        f"<div style='color:{TEXT3};font-size:0.72rem;font-family:JetBrains Mono,monospace;"
        f"padding:0.5rem 0;'>"
        f"Data unavailable{f' — {_msg}' if _msg else '.'}</div>",
        unsafe_allow_html=True,
    )
