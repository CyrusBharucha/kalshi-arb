"""dashboard/pages/p01_overview.py -- Overview"""
from __future__ import annotations
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False
import time
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data_layer import (
    get_system_health, get_coverage_stats,
    get_live_arb_opportunities,
)
from dashboard.live_state import get_live_state
from dashboard.styles import plotly_dark_layout, GREEN, RED, AMBER, BLUE, TEXT, TEXT2, TEXT3, PANEL, BORDER


def _count_today_arbs() -> int:
    try:
        from dashboard.live_arb_store import count_today
        return count_today()
    except Exception:
        return 0


def _age_str(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _arb_card(arb: dict) -> str:
    from dashboard.styles import GREEN, AMBER, TEXT, TEXT3, PANEL, BORDER
    strat  = arb.get("strategy", "")
    ticker = arb.get("ticker", "--")
    net    = float(arb.get("net_edge_cents") or 0)
    gross  = float(arb.get("gross_edge_cents") or 0)
    fees   = float(arb.get("fees_cents") or 0)
    ya     = float(arb.get("yes_ask") or 0)
    na     = float(arb.get("no_ask") or 0)
    legs   = arb.get("legs", [])
    edge_col = GREEN if net >= 2 else AMBER
    is_ce = strat == "collectively_exhaustive"
    label1 = "SUM YES" if is_ce else "YES ASK"
    label2 = f"{len(legs)} LEGS" if is_ce else "NO ASK"
    val2   = str(len(legs)) if is_ce else f"{na:.3f}"
    det_ts = arb.get("detected_at_ts")
    age    = _age_str(time.time() - float(det_ts)) if det_ts else ""
    legs_line = ""
    if is_ce and legs:
        ls = " · ".join(l if isinstance(l, str) else l.get("ticker", str(l)) for l in legs[:5])
        if len(legs) > 5:
            ls += f" +{len(legs)-5}"
        legs_line = f"<div style='font-size:0.55rem;color:{TEXT3};font-family:JetBrains Mono,monospace;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>{ls}</div>"
    strat_label = strat.replace("_", " ").upper()
    return (
        f"<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {edge_col};"
        f"border-radius:3px;padding:0.6rem 1rem;margin-bottom:0.35rem;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:flex-start;'>"
        f"<div><div style='font-family:JetBrains Mono,monospace;font-size:0.8rem;color:{TEXT};'>{ticker}</div>"
        f"<div style='font-size:0.58rem;color:{TEXT3};font-family:Inter,sans-serif;letter-spacing:0.06em;text-transform:uppercase;margin-top:1px;'>{strat_label}{' · ' + age if age else ''}</div>"
        f"{legs_line}</div>"
        f"<div style='text-align:right;'>"
        f"<div style='font-family:JetBrains Mono,monospace;font-size:1rem;color:{edge_col};font-weight:600;'>+{net:.2f}c</div>"
        f"<div style='font-size:0.55rem;color:{TEXT3};font-family:Inter,sans-serif;'>{label1} {ya:.3f} &nbsp; {label2} {val2}</div>"
        f"<div style='font-size:0.55rem;color:{TEXT3};font-family:Inter,sans-serif;'>gross {gross:.2f}c &nbsp; fees {fees:.2f}c</div>"
        f"</div></div></div>"
    )


def render():
    state    = get_live_state()
    ws_stats = state.get_stats()
    health   = get_system_health()
    ws_on    = ws_stats.get("connected", False)
    n_mkts   = int(ws_stats.get("markets_tracked", 0) or 0)
    mps      = int(ws_stats.get("messages_per_sec", 0) or 0)
    db_ok    = health.get("db_connected", False)

    # ── Status line ──────────────────────────────────────────────────────────
    if ws_on:
        dot = f"<span style='color:{GREEN};'>●</span> {n_mkts:,} markets &nbsp;·&nbsp; {mps:,} msg/s"
    else:
        dot = f"<span style='color:#6B7280;'>○</span> scanner offline"
    db_dot = f"<span style='color:{GREEN};'>Neon</span>" if db_ok else f"<span style='color:{AMBER};'>SQLite</span>"
    st.markdown(
        f"<div style='font-size:0.65rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
        f"margin-bottom:0.85rem;'>{dot} &nbsp;·&nbsp; db: {db_dot}</div>",
        unsafe_allow_html=True,
    )

    # ── KPI row ──────────────────────────────────────────────────────────────
    sess      = state.get_session_stats()
    today_n   = _count_today_arbs()
    last_ts   = sess.get("last_arb_ts") or sess.get("last_ts")
    last_str  = _age_str(time.time() - float(last_ts)) if last_ts else "--"

    # Use all session arbs (all strategies), deduplicated by ticker (newest-first)
    _raw_opps = state.get_recent_opportunities(limit=200) or []
    _seen: set = set()
    live_arbs = []
    for a in _raw_opps:
        tk = a.get("ticker", "")
        net = float(a.get("net_edge_cents") or 0)
        if tk and tk not in _seen and 0.01 <= net <= 50:
            _seen.add(tk)
            live_arbs.append(a)
    best_edge = max((float(a.get("net_edge_cents") or 0) for a in live_arbs), default=0.0)
    sess_n    = len(live_arbs)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("LIVE MARKETS", f"{n_mkts:,}" if ws_on else "--")
    k2.metric("SESSION ARBS", sess_n if ws_on else "--")
    k3.metric("BEST EDGE", f"+{best_edge:.2f}c" if best_edge > 0 else "--")
    k4.metric("TODAY (DB)", f"{today_n:,}")
    k5.metric("LAST ARB", last_str)

    st.caption(
        "SESSION ARBS: all strategies ≥0.01c net edge detected since feed started. "
        "TODAY (DB): YNC arbs ≥2c net edge persisted to Neon cloud (anti-ghost filter applied)."
    )

    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    # ── Live arb feed ────────────────────────────────────────────────────────
    if live_arbs:
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
            f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>SESSION ARBS — TOP OPPORTUNITIES</div>",
            unsafe_allow_html=True,
        )
        for arb in sorted(live_arbs, key=lambda a: float(a.get("net_edge_cents") or 0), reverse=True)[:8]:
            st.markdown(_arb_card(arb), unsafe_allow_html=True)
        st.markdown("<hr style='margin:0.65rem 0 0.75rem 0;'>", unsafe_allow_html=True)
    elif ws_on:
        st.markdown(
            f"<div style='background:{PANEL};border:1px solid {BORDER};padding:0.75rem 1rem;"
            f"border-radius:3px;font-size:0.72rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
            f"margin-bottom:0.75rem;'>No executable arbs right now — scanner running</div>",
            unsafe_allow_html=True,
        )

    # ── Session strategy breakdown ───────────────────────────────────────────
    try:
        counts = state.arb_counts_by_strategy()
        if any(counts.values()):
            sc1, sc2, sc3, sc4, sc5 = st.columns(5)
            sc1.metric("YNC", counts.get("ync", 0))
            sc2.metric("CE",  counts.get("ce", 0))
            sc3.metric("ME",  counts.get("me", 0))
            sc4.metric("TH",  counts.get("th", 0))
            sc5.metric("SS",  counts.get("ss", 0))
            st.caption("Arbs detected this session by strategy")
            st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)
    except Exception:
        pass

    # ── 30-day sparkline ─────────────────────────────────────────────────────
    try:
        from datetime import date as _date, timedelta as _td
        _today = _date.today()
        _spine = [(_today - _td(days=29 - i)).isoformat() for i in range(30)]
        _counts: dict = {d: 0 for d in _spine}
        _loaded = False
        # Try Neon first — live_arbs_cloud is the authoritative YNC arb store
        try:
            import os, psycopg2
            _url = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
            if _url:
                _pg = psycopg2.connect(_url)
                _cur = _pg.cursor()
                _cur.execute(
                    "SELECT DATE(detected_at)::text, COUNT(*) FROM live_arbs_cloud "
                    "WHERE detected_at >= NOW()-INTERVAL '30 days' GROUP BY 1"
                )
                for r in (_cur.fetchall() or []):
                    if str(r[0])[:10] in _counts: _counts[str(r[0])[:10]] = int(r[1])
                _pg.close()
                _loaded = True
        except Exception:
            pass
        # Fall back to SQLite analytics DB
        if not _loaded:
            try:
                from dashboard.data_layer import _sqlite_conn as _sc30
                _c = _sc30()
                if _c:
                    for r in (_c.execute(
                        "SELECT SUBSTR(detected_at,1,10) d, COUNT(*) n FROM live_arbs "
                        "WHERE detected_at >= date('now','-30 days') GROUP BY d"
                    ).fetchall() or []):
                        if r[0] in _counts: _counts[r[0]] = int(r[1])
                    _c.close()
                    _loaded = True
            except Exception:
                pass
        _vals = [_counts[d] for d in _spine]
        if _loaded and any(_vals):
            _fig = go.Figure(go.Scatter(
                x=_spine, y=_vals, mode="lines",
                line={"color": GREEN, "width": 1.5},
                fill="tozeroy", fillcolor="rgba(34,197,94,0.07)",
            ))
            _fig.update_layout(**plotly_dark_layout(
                height=110,
                margin={"l": 30, "r": 10, "t": 10, "b": 30},
                xaxis={"tickformat": "%b %d", "tickfont": {"size": 8, "family": "JetBrains Mono"}, "showgrid": False},
                yaxis={"tickfont": {"size": 8, "family": "JetBrains Mono"}, "showgrid": False, "zeroline": False},
                showlegend=False,
            ))
            st.markdown(
                f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.2rem;'>30-DAY ARB RATE</div>",
                unsafe_allow_html=True,
            )
            st.plotly_chart(_fig, use_container_width=True)
            st.caption(f"{sum(_vals):,} arbs in the last 30 days")
    except Exception:
        pass

    # ── Detection activity (session, last 2h) ────────────────────────────────
    if ws_on:
        try:
            _recent = state.get_recent_opportunities(limit=200)
            _now_ts = time.time()
            _bucket_s = 300
            _n = 24
            _start = _now_ts - _n * _bucket_s
            _bc = [0] * _n
            for o in _recent:
                _ts = o.get("detected_at_ts") or 0
                if _ts >= _start:
                    _idx = int((_ts - _start) / _bucket_s)
                    if 0 <= _idx < _n:
                        _bc[_idx] += 1
            if any(_bc):
                _labels = [
                    datetime.fromtimestamp(_start + i * _bucket_s, tz=timezone.utc).strftime("%H:%M")
                    for i in range(_n)
                ]
                _fig2 = go.Figure(go.Bar(x=_labels, y=_bc, marker_color=GREEN, marker_line_width=0))
                _fig2.update_layout(**plotly_dark_layout(
                    height=140,
                    margin={"l": 30, "r": 10, "t": 10, "b": 30},
                    xaxis={"tickfont": {"size": 8, "family": "JetBrains Mono"}, "showgrid": False},
                    yaxis={"tickfont": {"size": 8, "family": "JetBrains Mono"}, "showgrid": False, "dtick": 1},
                    bargap=0.2, showlegend=False,
                ))
                st.markdown(
                    f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                    f"color:{TEXT3};font-family:Inter,sans-serif;margin:0.5rem 0 0.2rem 0;'>DETECTION ACTIVITY (2H)</div>",
                    unsafe_allow_html=True,
                )
                st.plotly_chart(_fig2, use_container_width=True)
        except Exception:
            pass
