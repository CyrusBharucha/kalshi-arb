"""dashboard/pages/p01_overview.py -- Overview page"""
from __future__ import annotations
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False
import os
import time
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data_layer import (
    get_system_health, get_coverage_stats,
    get_live_arb_opportunities, get_arb_30day_counts,
)
from dashboard.live_state import get_live_state
from dashboard.styles import plotly_dark_layout, GREEN, RED, AMBER, BLUE, TEXT, TEXT2, TEXT3, PANEL, BORDER


@st.cache_data(ttl=120, max_entries=1)
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
    # app.py bridges st.secrets → os.environ at startup, so os.environ is
    # the reliable source. Also try st.secrets directly as belt-and-suspenders.
    _sk = os.environ.get("SYNTHESIS_SECRET_KEY", "").strip()
    if not _sk:
        try:
            _sk = (st.secrets.get("SYNTHESIS_SECRET_KEY", "") or "").strip()
        except Exception:
            pass
    if not _sk:
        # Try nested secrets (e.g. [kalshi] section)
        try:
            for _sec_ns in st.secrets.values():
                if hasattr(_sec_ns, "get"):
                    _sk = (_sec_ns.get("SYNTHESIS_SECRET_KEY", "") or "").strip()
                    if _sk:
                        break
        except Exception:
            pass
    has_key = bool(_sk)
    # ── Neon check FIRST so scanner dot can reflect cloud status ────────────
    _neon_ok_p01 = False
    try:
        import dashboard.live_arb_store as _las_p01
        _neon_ok_p01 = (
            bool(getattr(_las_p01, "_pg_ok", False))
            or (getattr(_las_p01, "_pg_engine", None) is not None)
        )
        if not _neon_ok_p01:
            _neon_ok_p01 = _las_p01.get_pg_engine_cached() is not None
    except Exception:
        _neon_ok_p01 = False
    # sidebar_neon_ok set by app.py BEFORE page body — check outside try so it always runs
    if not _neon_ok_p01:
        _neon_ok_p01 = bool(st.session_state.get("_sidebar_neon_ok", False))
    if not _neon_ok_p01:
        try:
            from dashboard.data_layer import get_live_arbs_cloud_stats as _glas_p01
            _neon_ok_p01 = (_glas_p01().get("total_count", 0) or 0) > 0
        except Exception:
            pass
    # ── Scanner / feed status dot ────────────────────────────────────────────
    if ws_on:
        dot = f"<span style='color:{GREEN};'>●</span> {n_mkts:,} markets &nbsp;·&nbsp; {mps:,} msg/s"
    elif _neon_ok_p01:
        dot = f"<span style='color:{GREEN};'>●</span> cloud feed (Neon)"
    elif has_key:
        dot = f"<span style='color:{AMBER};'>◔</span> scanner starting…"
    else:
        dot = f"<span style='color:#6B7280;'>○</span> scanner offline"
    # This dashboard always runs with Neon — never show "SQLite".
    # If Neon isn't confirmed yet (cold start), show "↻ Neon" (connecting).
    if _neon_ok_p01 or db_ok:
        db_dot = f"<span style='color:{GREEN};'>Neon</span>"
    else:
        db_dot = f"<span style='color:{AMBER};'>&#8635;&nbsp;Neon</span>"
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
    # When no session data, try to pull last detection from Neon cloud
    if last_str == "--":
        try:
            import dashboard.live_arb_store as _las_last
            from sqlalchemy import text as _last_text
            _last_eng = getattr(_las_last, "_pg_engine", None) or _las_last.get_pg_engine_cached()
            if _last_eng is not None:
                with _last_eng.connect() as _last_c:
                    _last_row = _last_c.execute(_last_text(
                        "SELECT MAX(detected_at) FROM live_arbs_cloud "
                        "WHERE strategy_type != 'collectively_exhaustive'"
                    )).fetchone()
                if _last_row and _last_row[0]:
                    _last_dt = _last_row[0]
                    if hasattr(_last_dt, "timestamp"):
                        _secs_old = time.time() - _last_dt.timestamp()
                        if _secs_old > 7 * 86400:
                            # Show actual date for old arbs (>7 days)
                            last_str = _last_dt.strftime("%b %-d") + " (Neon)"
                        else:
                            last_str = _age_str(_secs_old) + " (Neon)"
        except Exception:
            pass

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

    # Neon aggregate stats for KPI row
    _neon_total_p01 = 0
    _neon_mkt_count_p01 = 0
    _neon_max_edge_p01 = 0.0
    _neon_7d_p01 = 0
    try:
        from dashboard.data_layer import get_live_arbs_cloud_stats as _p01_cls
        _p01_cls_result = _p01_cls()
        _neon_total_p01 = _p01_cls_result.get("total_count", 0) or 0
        _neon_mkt_count_p01 = _p01_cls_result.get("distinct_tickers", 0) or 0
        _neon_max_edge_p01 = float(_p01_cls_result.get("max_net_edge_cents", 0) or 0)
    except Exception:
        pass
    # Last-7-days count from Neon (when scanner offline — cloud has no daily arbs)
    if not ws_on and _neon_ok_p01:
        try:
            import dashboard.live_arb_store as _las_p01_7d
            from sqlalchemy import text as _p01_7d_text
            _p01_7d_eng = getattr(_las_p01_7d, "_pg_engine", None) or _las_p01_7d.get_pg_engine_cached()
            if _p01_7d_eng is not None:
                with _p01_7d_eng.connect() as _p01_7d_c:
                    _p01_7d_row = _p01_7d_c.execute(_p01_7d_text(
                        "SELECT COUNT(*) FROM live_arbs_cloud "
                        "WHERE detected_at >= NOW() - INTERVAL '7 days' "
                        "AND strategy_type NOT IN ('yes_no_complement','collectively_exhaustive') "
                        "AND net_edge_cents > 0"
                    )).fetchone()
                _neon_7d_p01 = int(_p01_7d_row[0]) if _p01_7d_row else 0
        except Exception:
            pass
    # LIVE MARKETS: use WS count when live, else distinct tickers in Neon (better than "STARTING")
    if ws_on:
        _mkt_display = f"{n_mkts:,}"
    elif _neon_ok_p01 and _neon_mkt_count_p01 > 0:
        _mkt_display = f"{_neon_mkt_count_p01:,} (DB)"
    elif has_key:
        _mkt_display = "STARTING"
    else:
        _mkt_display = "OFFLINE"
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("LIVE MARKETS", _mkt_display)
    # SESSION ARBS: live count when WS on; Neon total when offline (today_n always 0 on cloud)
    if ws_on:
        k2.metric("SESSION ARBS", sess_n)
    elif _neon_total_p01 > 0:
        k2.metric("NEON TOTAL", f"{_neon_total_p01:,}", help="All-time arbs in Neon cloud store (ME/TH strategies, net edge > 0)")
    else:
        k2.metric("SESSION ARBS", today_n)
    _best_edge_display = (
        f"+{best_edge:.2f}¢" if best_edge > 0
        else (f"{_neon_max_edge_p01:.2f}¢ (Neon)" if _neon_max_edge_p01 > 0 else "--")
    )
    k3.metric("BEST EDGE", _best_edge_display)
    # TODAY (DB): show actual today count; when 0 show last-7d from Neon as delta
    if today_n > 0:
        k4.metric("TODAY (DB)", f"{today_n:,}")
    elif _neon_7d_p01 > 0:
        k4.metric("LAST 7D (NEON)", f"{_neon_7d_p01:,}", help="Arbs in Neon cloud from the past 7 days (ME/TH only, net edge > 0)")
    elif _neon_total_p01 > 0:
        k4.metric("TODAY (DB)", "0", delta=f"{_neon_total_p01:,} all-time in Neon", delta_color="off")
    else:
        k4.metric("TODAY (DB)", "0")
    k5.metric("LAST DETECTION", last_str)

    st.caption(
        "LIVE MARKETS: WebSocket-tracked markets when online; (DB) = distinct tickers from Neon cloud records when offline. "
        "SESSION ARBS: scanner detections ≥0.01c net edge since feed started (ME/TH arbs are actionable; YNC detections are feed artifacts — not executable). "
        "TODAY (DB): detections ≥2c net edge persisted to Neon cloud today (all strategies; YNC records are feed artifacts — ME/TH are actionable; anti-ghost filter applied)."
    )

    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    # ── Live arb feed ────────────────────────────────────────────────────────
    if live_arbs:
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
            f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>SESSION ARBS — TOP OPPORTUNITIES (ME/TH actionable · YNC = feed artifact)</div>",
            unsafe_allow_html=True,
        )
        for arb in sorted(live_arbs, key=lambda a: float(a.get("net_edge_cents") or 0), reverse=True)[:8]:
            st.markdown(_arb_card(arb), unsafe_allow_html=True)
        st.markdown("<hr style='margin:0.65rem 0 0.75rem 0;'>", unsafe_allow_html=True)
    elif ws_on:
        st.markdown(
            f"<div style='background:{PANEL};border:1px solid {BORDER};padding:0.75rem 1rem;"
            f"border-radius:3px;font-size:0.72rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
            f"margin-bottom:0.75rem;'>No ME/TH arbs right now — scanner running (YNC feed artifacts excluded)</div>",
            unsafe_allow_html=True,
        )
    elif _neon_ok_p01:
        # Scanner offline + Neon connected: show recent cloud arbs as page body
        try:
            import dashboard.live_arb_store as _las_p01b
            from sqlalchemy import text as _p01b_text
            _p01b_eng = getattr(_las_p01b, "_pg_engine", None) or _las_p01b.get_pg_engine_cached()
            if _p01b_eng is not None:
                with _p01b_eng.connect() as _p01b_c:
                    _p01b_rows = _p01b_c.execute(_p01b_text(
                        "SELECT ticker, strategy_type, net_edge_cents, detected_at "
                        "FROM live_arbs_cloud "
                        "WHERE strategy_type NOT IN ('yes_no_complement','collectively_exhaustive') "
                        "AND net_edge_cents > 0 "
                        "ORDER BY detected_at DESC LIMIT 8"
                    )).fetchall()
                if _p01b_rows:
                    st.markdown(
                        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                        f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
                        f"<span style='color:{GREEN};'>●</span> NEON CLOUD — RECENT ARBS</div>",
                        unsafe_allow_html=True,
                    )
                    for _row in _p01b_rows:
                        _tk = str(_row[0])
                        _st = str(_row[1]).upper().replace("_", " ")
                        _ne = float(_row[2])
                        _dt = str(_row[3])[:16] if _row[3] else "--"
                        st.markdown(
                            f"<div style='background:{PANEL};border:1px solid {BORDER};"
                            f"border-left:3px solid {GREEN};border-radius:3px;"
                            f"padding:0.4rem 0.8rem;margin-bottom:0.3rem;"
                            f"font-family:JetBrains Mono,monospace;font-size:0.7rem;"
                            f"display:flex;justify-content:space-between;align-items:center;'>"
                            f"<span style='color:#E2E8F0;'>{_tk}</span>"
                            f"<span style='color:{TEXT3};font-size:0.62rem;'>{_st}</span>"
                            f"<span style='color:{GREEN};font-weight:600;'>+{_ne:.2f}¢</span>"
                            f"<span style='color:{TEXT3};font-size:0.6rem;'>{_dt}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                    st.markdown("<hr style='margin:0.65rem 0 0.75rem 0;'>", unsafe_allow_html=True)
        except Exception:
            pass

    # ── Session strategy breakdown ───────────────────────────────────────────
    try:
        counts = state.arb_counts_by_strategy()
        if any(counts.values()):
            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("YNC DETECT", counts.get("ync", 0))
            sc2.metric("ME ARBS", counts.get("me", 0))
            sc3.metric("TH ARBS", counts.get("th", 0))
            st.caption("ME/TH arbs + YNC feed artifacts detected this session (CE disabled)")
            st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)
    except Exception:
        pass

    # ── 30-day sparkline ─────────────────────────────────────────────────────
    try:
        from datetime import date as _date, timedelta as _td
        _today = _date.today()
        _spine = [(_today - _td(days=29 - i)).isoformat() for i in range(30)]
        _counts = get_arb_30day_counts()  # cached 900s — no Neon hit on every render
        _vals = [_counts.get(d, 0) for d in _spine]
        _loaded = any(v > 0 for v in _vals)
        if _loaded:
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
                f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.2rem;'>30-DAY DETECTION RATE (ALL STRATEGIES)</div>",
                unsafe_allow_html=True,
            )
            st.plotly_chart(_fig, use_container_width=True)
            st.caption(f"{sum(_vals):,} scanner detections in the last 30 days (includes YNC feed artifacts; ME/TH are actionable)")
        else:
            # No chart data yet — show Neon summary instead
            try:
                import dashboard.live_arb_store as _las_chart
                from sqlalchemy import text as _p01_text
                # Prefer cached engine; fall back to init call
                _eng = getattr(_las_chart, "_pg_engine", None) or _las_chart.get_pg_engine_cached()
                if _eng is not None:
                    with _eng.connect() as _c:
                        _row = _c.execute(_p01_text(
                            "SELECT COUNT(*), MAX(net_edge_cents), AVG(net_edge_cents) "
                            "FROM live_arbs_cloud WHERE strategy_type != 'collectively_exhaustive'"
                        )).fetchone()
                    if _row and _row[0]:
                        st.markdown(
                            f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                            f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>NEON CLOUD — ALL-TIME ARB LOG</div>",
                            unsafe_allow_html=True,
                        )
                        _nc1, _nc2, _nc3 = st.columns(3)
                        _nc1.metric("TOTAL LOGGED", int(_row[0]))
                        _nc2.metric("BEST EDGE", f"{float(_row[1]):.2f}c")
                        _nc3.metric("AVG EDGE", f"{float(_row[2]):.2f}c")
            except Exception:
                pass
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
                st.caption("5-min buckets — all strategies (includes YNC feed artifacts; ME/TH are actionable)")
        except Exception:
            pass

