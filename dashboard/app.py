# -*- coding: utf-8 -*-
"""
dashboard/app.py
================
Kalshi Arbitrage Engine - Institutional Trading Terminal v1.4

Entry point:
streamlit run dashboard/app.py

Architecture:
app.py                  -> navigation + global CSS + WS auto-start
dashboard/ws_bridge.py  -> auto-starts Synthesis WebSocket (once per process)
dashboard/styles.py     -> CSS injection
dashboard/data_layer.py -> all database queries (cached)
dashboard/live_state.py -> thread-safe live WebSocket state
dashboard/pages/pXX_*.py -> individual page modules

Changelog (v1.4):
- Fixed UnboundLocalError in p01 (TEXT3, get_live_market_summary, _p01_arb_date)
- Fixed latent UnboundLocalError in p06 (conditional style import)
- Fixed _infer_category to suppress title-cased ticker fallbacks
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Add project root to path so all local imports resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

# -- Bridge Streamlit Cloud secrets -> os.environ so load_dotenv() code works --
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:
    pass  # running locally — .env handles secrets instead

# -- Page config - MUST be first Streamlit call --------------------------------
st.set_page_config(
    page_title="Kalshi Arbitrage Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -- CSS injection -------------------------------------------------------------
from dashboard.styles import inject_css
st.markdown(inject_css(), unsafe_allow_html=True)


# -- Auto-start Synthesis WebSocket (once per server process) -----------------
# ws_bridge uses a module-level singleton so this is a no-op after first call
from dashboard.ws_bridge import ensure_ws_running
_ws_client = ensure_ws_running()

# -- Live state & health -------------------------------------------------------
from dashboard.live_state import get_live_state
from dashboard.data_layer import get_system_health

# -- Page imports --------------------------------------------------------------
from dashboard.pages import (
    p01_overview,
    p02_live_arb,
    p03_markets,
    p04_orderbook,
    p05_historical_arb,
    p06_backtest,
    p07_cross_asset,
    p08_canadian,
    p09_research,
    p10_system,
    p11_market_types,
)

# -- Navigation map ------------------------------------------------------------
_PAGES = {
    "Overview":          p01_overview,
    "Live Arbitrage":    p02_live_arb,
    "Market Explorer":   p03_markets,
    "Order Book":        p04_orderbook,
    "Arb History":       p05_historical_arb,
    "Backtest":          p06_backtest,
    "Cross-Asset":       p07_cross_asset,
    "Canadian Markets":  p08_canadian,
    "Research":          p09_research,
    "System":            p10_system,
    "Market Types":      p11_market_types,
}

# -- Sidebar -------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        "<div class='kae-brand'>Kalshi Arb</div>"
        "<div class='kae-brand-sub'>Prediction Market Arbitrage Research</div>",
        unsafe_allow_html=True,
    )

    selected = st.radio(
        "nav",
        list(_PAGES.keys()),
        index=0,
        label_visibility="collapsed",
    )

    st.markdown("<div class='kae-nav-sep'></div>", unsafe_allow_html=True)

    # -- Live status indicators ------------------------------------------------
    state      = get_live_state()
    ws_stats   = state.get_stats()
    health     = get_system_health()

    ws_connected = ws_stats.get("connected", False)
    db_connected = health.get("db_connected", False)
    # Promote to SQLite mode if SQLite file exists even if health didn't catch it.
    # Shallow-copy first so we never mutate the @st.cache_data shared object.
    health = dict(health)
    if not db_connected and not health.get("db_mode"):
        from pathlib import Path as _P
        for _sp in [_P(__file__).parent / "dashboard.db",
                    _P("dashboard/dashboard.db"), _P("dashboard.db")]:
            try:
                if _sp.resolve().exists():
                    health["db_mode"] = "sqlite"
                    break
            except Exception:
                pass
    _mps_raw = ws_stats.get("messages_per_sec") or 0
    mps = float(_mps_raw) if _mps_raw == _mps_raw else 0.0  # guard NaN (NaN != NaN)
    n_markets    = int(ws_stats.get("markets_tracked") or 0)
    now_str      = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")

    # API key present?
    has_key = bool(os.environ.get("SYNTHESIS_SECRET_KEY", "").strip())

    def _dot(ok: bool, amber: bool = False) -> str:
        cls = "dot-green" if ok else ("dot-amber" if amber else "dot-red")
        return f"<span class='kae-status-dot {cls}'></span>"

    _is_snapshot_mode = not db_connected and health.get("db_mode") == "sqlite"

    # Zombie detection: connected=True but feed is silent
    # Case A: was alive but went silent >120s
    # Case B: connected but never sent a single message in >60s (mps=0 since connect)
    _last_msg_ts = ws_stats.get("last_message_ts") or 0
    _now_app = time.time()
    _is_zombie = ws_connected and (
        (_last_msg_ts > 0 and (_now_app - _last_msg_ts) > 120)
        or (_last_msg_ts == 0 and mps == 0)
    )

    # Feed line: LIVE / ZOMBIE / CONNECTING / NO KEY
    if _is_zombie:
        feed_label = "ZOMBIE"
        feed_color = "#EF4444"
        feed_dot   = _dot(False, amber=False)
    elif ws_connected:
        feed_label = "LIVE"
        feed_color = "#22C55E"
        feed_dot   = _dot(True)
    elif has_key:
        feed_label = "CONNECTING"
        feed_color = "#F59E0B"
        feed_dot   = _dot(False, amber=True)
    else:
        feed_label = "NO KEY"
        feed_color = "#EF4444"
        feed_dot   = _dot(False)

    # Arb engine: RUNNING when WS up, READY (has DB data), OFFLINE
    if _is_zombie:
        eng_label = "RECONNECTING"
        eng_color = "#F59E0B"
        eng_dot   = _dot(False, amber=True)
    elif ws_connected:
        eng_label = "RUNNING"
        eng_color = "#22C55E"
        eng_dot   = _dot(True)
    elif _is_snapshot_mode or db_connected:
        eng_label = "READY"
        eng_color = "#22C55E"
        eng_dot   = _dot(True)
    elif has_key:
        eng_label = "STARTING"
        eng_color = "#F59E0B"
        eng_dot   = _dot(False, amber=True)
    else:
        eng_label = "OFFLINE"
        eng_color = "#64748B"
        eng_dot   = _dot(False)

    # Check live_arb_store Neon connection (separate from analytics DB)
    try:
        from dashboard.live_arb_store import _get_pg_engine as _arb_pg
        _neon_ok = _arb_pg() is not None
    except Exception:
        _neon_ok = False

    if _neon_ok:
        _db_val_color = "#22C55E"
        _db_val_label = "CLOUD"
    elif db_connected:
        _db_val_color = "#22C55E"
        _db_val_label = "CLOUD"
    else:
        _db_val_color = "#F59E0B"
        _db_val_label = "REFERENCE"

    status_html = (
        f"<div style='font-family:JetBrains Mono,monospace;'>"
        f"<div class='kae-status-row'>{feed_dot}"
        f"<span class='kae-status-label'>DATA FEED</span>"
        f"<span class='kae-status-val' style='color:{feed_color};'>{feed_label}</span></div>"
        f"<div class='kae-status-row'>{_dot(_neon_ok or db_connected, amber=not _neon_ok and not db_connected)}"
        f"<span class='kae-status-label'>DATABASE</span>"
        f"<span class='kae-status-val' style='color:{_db_val_color};'>{_db_val_label}</span></div>"
        f"<div class='kae-status-row'>{eng_dot}"
        f"<span class='kae-status-label'>ARB ENGINE</span>"
        f"<span class='kae-status-val' style='color:{eng_color};'>{eng_label}</span></div>"
        f"<div class='kae-status-row'><span class='kae-status-dot dot-gray'></span>"
        f"<span class='kae-status-label'>ET TIME</span>"
        f"<span class='kae-status-val'>{now_str}</span></div>"
        f"</div>"
    )
    st.markdown(status_html, unsafe_allow_html=True)

    # -- Live arb summary: count, best edge, last detected --------------------
    try:
        _live_opps = state.get_recent_opportunities(limit=50)
    except Exception:
        _live_opps = []
    _n_live = len(_live_opps) if _live_opps else 0

    # Best net edge across active opportunities
    _best_edge: float | None = None
    if _n_live:
        try:
            _edges = [
                float(o["net_edge_cents"])
                for o in _live_opps
                if o.get("net_edge_cents") is not None
            ]
            _best_edge = max(_edges) if _edges else None
        except Exception:
            _best_edge = None

    # Last arb timestamp — session_stats has last_arb_ts; get_stats() does not
    _sb_sess_pre = state.get_session_stats()
    _last_arb_ts = (_sb_sess_pre.get("last_arb_ts") or ws_stats.get("last_arb_ts") or 0)
    if _last_arb_ts:
        _secs_ago = int(time.time() - float(_last_arb_ts))
        if _secs_ago < 60:
            _last_arb_str = f"{_secs_ago}s ago"
        else:
            _last_arb_str = f"{_secs_ago // 60}m ago"
    else:
        _last_arb_str = "never"

    _arb_count_color = "#22C55E" if _n_live > 0 else "#64748B"
    _edge_str = f"{_best_edge:.1f}c" if _best_edge is not None else "—"
    _arb_summary_html = (
        f"<div style='font-family:JetBrains Mono,monospace;font-size:0.67rem;"
        f"background:#0F172A;border:1px solid #1E293B;border-radius:4px;"
        f"padding:6px 8px;margin-top:6px;line-height:1.8;'>"
        f"<div style='color:{_arb_count_color};font-weight:700;letter-spacing:0.05em;font-size:0.72rem;'>"
        f"SESSION ARBS &nbsp; {_n_live}</div>"
        f"<div style='color:#94A3B8;font-size:0.60rem;margin-top:2px;'>"
        f"Best edge at detection: <span style='color:#E2E8F0;'>{_edge_str}</span>"
        f"<br>Last detected: <span style='color:#E2E8F0;'>{_last_arb_str}</span>"
        f"</div></div>"
    )
    st.markdown(_arb_summary_html, unsafe_allow_html=True)

    # Feed rate + market count when live
    if ws_connected and mps > 0:
        _sb_sess = _sb_sess_pre  # already fetched above for last_arb_ts
        _n_sb_arbs = _sb_sess.get("total", 0)
        _n_sb_ce   = _sb_sess.get("ce", 0)
        _n_sb_comp = _sb_sess.get("complement", 0)
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.62rem;"
            f"color:#94A3B8;letter-spacing:0.04em;margin-top:4px;'>"
            f"&rarr; {int(mps):,} msg/s &nbsp;&middot;&nbsp; {n_markets:,} markets cached</div>",
            unsafe_allow_html=True,
        )
        if _n_sb_arbs > 0:
            _n_sb_clean = _sb_sess.get("clean", _n_sb_arbs)
            _n_sb_pre_fix = _sb_sess.get("pre_fix", 0)
            _pre_fix_note = f" &nbsp;<span style='color:#F59E0B;'>+{_n_sb_pre_fix} pre-fix</span>" if _n_sb_pre_fix > 0 else ""
            st.markdown(
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.62rem;"
                f"color:#22C55E;letter-spacing:0.04em;margin-top:2px;'>"
                f"⚡ {_n_sb_clean:,} detected this session{_pre_fix_note}</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # Auto-refresh toggle
    auto_refresh = st.toggle("Auto-refresh (10s)", value=False, key="global_auto_refresh")

    if st.button("Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # Key missing or snapshot mode — show setup hint
    _is_snapshot = not db_connected and health.get("db_mode") == "sqlite"
    if not has_key:
        st.markdown(
            f"<div style='font-size:0.6rem;color:#64748B;font-family:Inter,sans-serif;"
            f"border:1px solid #26313D;border-radius:3px;padding:0.5rem 0.6rem;margin-top:0.5rem;"
            f"line-height:1.6;'>"
            f"[i] Add <code>SYNTHESIS_SECRET_KEY=sk_gs-...</code> to Streamlit secrets"
            f" to enable the live feed.</div>",
            unsafe_allow_html=True,
        )

    st.sidebar.markdown("---")
    st.sidebar.caption("Built by [Cyrus Bharucha](mailto:cyrusbharucha7@gmail.com) · [GitHub](https://github.com/CyrusBharucha/kalshi-arb)")

# -- Render selected page -------------------------------------------------------
try:
    _PAGES[selected].render()
except Exception as _page_exc:
    import traceback as _tb
    st.error(
        f"⚠ Page error in **{selected}** — {type(_page_exc).__name__}: {_page_exc}\n\n"
        "Other pages are unaffected. If this persists, check System (p10) for WS/DB status."
    )
    with st.expander("Stack trace", expanded=False):
        st.code(_tb.format_exc(), language="python")

# -- Auto-refresh (disabled only when BOTH DB is SQLite AND WS is offline — static data) --------
_snapshot_mode = not health.get("db_connected", False) and health.get("db_mode") == "sqlite"
_ws_live_for_refresh = ws_stats.get("connected", False)
if auto_refresh and (not _snapshot_mode or _ws_live_for_refresh):
    _next_key = "_app_next_refresh"
    _now = time.time()
    if _now >= st.session_state.get(_next_key, 0):
        st.session_state[_next_key] = _now + 10
        st.rerun()
    else:
        time.sleep(min(5.0, st.session_state[_next_key] - _now))
        st.rerun()
