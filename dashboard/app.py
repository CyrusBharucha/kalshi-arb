# -*- coding: utf-8 -*-
"""
dashboard/app.py
================
Kalshi Arbitrage Engine - Institutional Trading Terminal v1.7

Entry point:
streamlit run dashboard/app.py

Architecture:
app.py                  -> navigation + global CSS + WS auto-start
dashboard/ws_bridge.py  -> auto-starts Synthesis WebSocket (once per process)
dashboard/styles.py     -> CSS injection
dashboard/data_layer.py -> all database queries (cached)
dashboard/live_state.py -> thread-safe live WebSocket state
dashboard/pages/pXX_*.py -> individual page modules

Changelog (v1.7):
- LaTeX rendering fixed: dollar signs in st.caption/st.markdown replaced with ¢ notation
- p11_market_types: CE-eligible types caption corrected to threshold_numeric; color map updated
- p02_live_arb: stale "CE SCANNER DISABLED" labels removed from historical-data captions
- p09_research: YNC wording standardized to "not executable"; redundant CE sentence removed
- data_layer/live_arb_store: stale "CE disabled" comments updated to reflect ws_bridge CE path
- All source comments referencing CE as disabled corrected throughout

Changelog (v1.6):
- CE scanner enabled — Gate 0 now loads event_series_classifications from Neon
- Gate 0 NameError fixed: get_db_conn import added to ws_bridge._load_series_cache()
- event_series_classifications table auto-created in Neon on first Gate 0 load
- Fallback tickers updated: NOV/DEC FOMC, OCT31 S&P500 (removed expired SEP contracts)
- All ME/TH-only labels removed across all 11 pages (CE/ME/TH strategy set)
- CE added to backtest strategy selectbox
- YNC label updated to "not executable" everywhere (exchange design, not feed bug)
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
        elif hasattr(_v, "items"):
            # Nested section e.g. [database] DATABASE_URL = "..."
            for _nk, _nv in _v.items():
                if isinstance(_nv, str):
                    os.environ.setdefault(_nk, _nv)
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

# -- Kick off Neon warmup immediately so DB is warm by first query ------------
try:
    from dashboard.data_layer import _warmup_neon_bg as _wnb
    _wnb()
except Exception:
    pass

# -- Auto-start Synthesis WebSocket (once per server process) -----------------
# ws_bridge uses a module-level singleton so this is a no-op after first call
from dashboard.ws_bridge import ensure_ws_running
_ws_client = ensure_ws_running()

# -- REST arb scanner (polls Kalshi API as fallback when WS is unreliable) ------
try:
    from dashboard.rest_scanner import start as _start_rest_scanner
    _start_rest_scanner()
except Exception as _rest_err:
    import logging as _log_mod
    _log_mod.getLogger(__name__).warning("REST scanner failed to start: %s", _rest_err)

# -- Live state & health -------------------------------------------------------
from dashboard.live_state import get_live_state
from dashboard.data_layer import get_system_health

# -- Navigation map (lazy imports — pages load only when selected) ------------
_PAGE_MODULES = {
    "Overview":          "dashboard.pages.p01_overview",
    "Live Arbitrage":    "dashboard.pages.p02_live_arb",
    "Market Explorer":   "dashboard.pages.p03_markets",
    "Order Book":        "dashboard.pages.p04_orderbook",
    "Arb History":       "dashboard.pages.p05_historical_arb",
    "Backtest":          "dashboard.pages.p06_backtest",
    "Cross-Asset":       "dashboard.pages.p07_cross_asset",
    "Canadian Markets":  "dashboard.pages.p08_canadian",
    "Research":          "dashboard.pages.p09_research",
    "System":            "dashboard.pages.p10_system",
    "Market Types":      "dashboard.pages.p11_market_types",
}

import importlib as _importlib
_page_cache: dict = {}

def _get_page(name: str):
    if name not in _page_cache:
        _page_cache[name] = _importlib.import_module(_PAGE_MODULES[name])
    return _page_cache[name]

# -- Sidebar -------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        "<div class='kae-brand'>Kalshi Arb</div>"
        "<div class='kae-brand-sub'>Prediction Market Arbitrage Research</div>",
        unsafe_allow_html=True,
    )

    selected = st.radio(
        "nav",
        list(_PAGE_MODULES.keys()),
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

    # API key present? Check os.environ first (populated by secrets bridge above),
    # then st.secrets directly, then nested secrets sections.
    _app_sk = os.environ.get("SYNTHESIS_SECRET_KEY", "").strip()
    if not _app_sk:
        try:
            _app_sk = (st.secrets.get("SYNTHESIS_SECRET_KEY", "") or "").strip()
        except Exception:
            pass
    if not _app_sk:
        try:
            for _app_ns in st.secrets.values():
                if hasattr(_app_ns, "get"):
                    _app_sk = (_app_ns.get("SYNTHESIS_SECRET_KEY", "") or "").strip()
                    if _app_sk:
                        break
        except Exception:
            pass
    has_key = bool(_app_sk)

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
    _neon_ok = False
    _neon_pending = False  # Neon configured but not yet connected (in 30s backoff)
    try:
        import dashboard.live_arb_store as _las_app
        _neon_ok = bool(getattr(_las_app, "_pg_ok", False))
        if not _neon_ok:
            # Use get_pg_engine_cached() to avoid triggering the 30s backoff on every render
            _neon_ok = _las_app.get_pg_engine_cached() is not None
        if not _neon_ok:
            # If a prior attempt failed (backoff active), Neon IS configured — just not connected yet
            _neon_pending = getattr(_las_app, "_pg_last_fail_ts", 0.0) > 0.0
            if not _neon_pending:
                # "Not yet tried" case: DATABASE_URL configured but cold-start connect hasn't run yet.
                # Check module var _pg_init_tried — if False and a DB URL exists, treat as pending.
                _not_tried = not getattr(_las_app, "_pg_init_tried", True)
                if _not_tried:
                    try:
                        _db_url_check = (
                            os.environ.get("DATABASE_URL", "").strip()
                            or st.secrets.get("DATABASE_URL", "").strip()
                            or st.secrets.get("NEON_DATABASE_URL", "").strip()
                        )
                        _neon_pending = bool(_db_url_check)
                    except Exception:
                        _neon_pending = True  # secrets not readable yet — assume configured
    except Exception:
        _neon_ok = False
    # Publish to session_state so page headers can read it without re-checking
    st.session_state["_sidebar_neon_ok"] = _neon_ok or db_connected
    # Check REST scanner status
    _rest_running = False
    _rest_scan_count = 0
    try:
        from dashboard.rest_scanner import get_status as _rest_get_status
        _rest_s = _rest_get_status()
        _rest_running = _rest_s.get("running", False)
        _rest_scan_count = _rest_s.get("scan_count", 0)
    except Exception:
        pass

    # Override ARB ENGINE label based on available scanners
    if eng_label in ("STARTING", "OFFLINE"):
        if _rest_running:
            eng_label = "SCANNING"
            eng_color = "#22C55E"
            eng_dot   = _dot(True)
        elif _neon_ok:
            eng_label = "NEON"
            eng_color = "#3B82F6"
            eng_dot   = _dot(True)
        elif _neon_pending:
            eng_label = "↻ NEON"
            eng_color = "#F59E0B"
            eng_dot   = _dot(False, amber=True)
    # Override DATA FEED based on available data sources
    if feed_label in ("CONNECTING", "NO KEY"):
        if _rest_running:
            feed_label = "REST API"
            feed_color = "#22C55E"
            feed_dot   = _dot(True)
        elif _neon_ok:
            feed_label = "CLOUD"
            feed_color = "#3B82F6"
            feed_dot   = _dot(True)
        elif _neon_pending:
            feed_label = "↻ CLOUD"
            feed_color = "#F59E0B"
            feed_dot   = _dot(False, amber=True)

    if _neon_ok:
        _db_val_color = "#22C55E"
        _db_val_label = "CLOUD"
    elif db_connected:
        _db_val_color = "#22C55E"
        _db_val_label = "CLOUD"
    elif _rest_running:
        _db_val_color = "#22C55E"
        _db_val_label = "LOCAL"
    else:
        _db_val_color = "#F59E0B"
        _db_val_label = "REFERENCE"

    status_html = (
        f"<div style='font-family:JetBrains Mono,monospace;'>"
        f"<div class='kae-status-row'>{feed_dot}"
        f"<span class='kae-status-label'>DATA FEED</span>"
        f"<span class='kae-status-val' style='color:{feed_color};'>{feed_label}</span></div>"
        f"<div class='kae-status-row'>{_dot(_neon_ok or db_connected or _rest_running, amber=not _neon_ok and not db_connected and not _rest_running)}"
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
    # Count CE/ME/TH arbs for sidebar — exclude only YNC (feed artifacts, not executable)
    _n_live_meth = 0
    if _live_opps:
        try:
            _n_live_meth = sum(
                1 for o in _live_opps
                if o.get("strategy") not in ("yes_no_complement",)
            )
        except Exception:
            _n_live_meth = _n_live

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
        elif _secs_ago < 3600:
            _last_arb_str = f"{_secs_ago // 60}m ago"
        else:
            _last_arb_str = f"{_secs_ago // 3600}h ago"
    else:
        # Fallback: pull most recent ME/TH detection from Neon (cached 300s)
        _last_ts_cache_key = "_sb_last_det_cache"
        _last_ts_cache_ts_key = "_sb_last_det_ts"
        _last_arb_str = st.session_state.get(_last_ts_cache_key, "never")
        _last_ts_cache_age = time.time() - st.session_state.get(_last_ts_cache_ts_key, 0.0)
        if _last_ts_cache_age > 300 or _last_arb_str == "never":
            try:
                from dashboard.live_arb_store import get_pg_engine_cached as _app_gpe_last
                _app_eng_last = _app_gpe_last()
                if _app_eng_last is not None:
                    from sqlalchemy import text as _app_text_last
                    with _app_eng_last.connect() as _app_c_last:
                        _app_last_row = _app_c_last.execute(_app_text_last(
                            "SELECT MAX(detected_at) FROM live_arbs_cloud "
                            "WHERE strategy_type NOT IN ('yes_no_complement')"
                        )).fetchone()
                    if _app_last_row and _app_last_row[0]:
                        _app_last_dt = _app_last_row[0]
                        if hasattr(_app_last_dt, "timestamp"):
                            _app_secs = time.time() - _app_last_dt.timestamp()
                            if _app_secs < 3600:
                                _last_arb_str = f"{int(_app_secs // 60)}m ago (Neon)"
                            elif _app_secs < 86400:
                                _last_arb_str = f"{int(_app_secs // 3600)}h ago (Neon)"
                            else:
                                _last_arb_str = _app_last_dt.strftime("%b %-d") + " (Neon)"
                            st.session_state[_last_ts_cache_key] = _last_arb_str
                            st.session_state[_last_ts_cache_ts_key] = time.time()
            except Exception:
                pass

    _arb_count_color = "#22C55E" if _n_live > 0 else "#64748B"
    _edge_str = f"{_best_edge:.1f}¢" if _best_edge is not None else "—"
    # When session is empty and Neon is connected, show cloud arb count + best edge as context
    # Cache for 120s in session_state to avoid querying Neon on every sidebar render
    _neon_cloud_line = ""
    if _n_live_meth == 0 and _neon_ok:
        _sb_cache_key = "_sb_neon_cloud_cache"
        _sb_cache_ts_key = "_sb_neon_cloud_ts"
        _sb_cache_ttl = 120
        _sb_now = time.time()
        _cached_line = st.session_state.get(_sb_cache_key, "")
        _cached_ts = st.session_state.get(_sb_cache_ts_key, 0.0)
        if _cached_line and (_sb_now - _cached_ts) < _sb_cache_ttl:
            _neon_cloud_line = _cached_line
        else:
            try:
                from dashboard.live_arb_store import get_pg_engine_cached as _app_gpe
                _app_eng = _app_gpe()
                if _app_eng is not None:
                    from sqlalchemy import text as _app_text
                    with _app_eng.connect() as _app_c:
                        _app_r = _app_c.execute(_app_text(
                            "SELECT COUNT(*), MAX(net_edge_cents), AVG(net_edge_cents) "
                            "FROM live_arbs_cloud "
                            "WHERE strategy_type NOT IN ('yes_no_complement')"
                        )).fetchone()
                    if _app_r and _app_r[0]:
                        _app_cnt = int(_app_r[0])
                        _app_best = float(_app_r[1] or 0)
                        _app_best_str = f" · best {_app_best:.1f}¢" if _app_best > 0 else ""
                        _neon_cloud_line = (
                            f"<br><span style='color:#22C55E;'>Neon: "
                            f"{_app_cnt:,} arbs on record{_app_best_str}</span>"
                        )
                        st.session_state[_sb_cache_key] = _neon_cloud_line
                        st.session_state[_sb_cache_ts_key] = _sb_now
            except Exception:
                pass
    # Sidebar box header: "SESSION DETECTIONS N" when live; "NEON CLOUD" when cloud-only
    if _n_live_meth == 0 and _neon_ok:
        _sb_header = "<span style='color:#22C55E;'>● NEON CLOUD</span>"
        _sb_sub = "Cloud arb store connected"
    elif _n_live_meth > 0:
        _sb_header = f"SESSION DETECTIONS &nbsp; {_n_live_meth}"
        _sb_sub = "Executable arbs this session"
    else:
        _sb_header = f"SESSION DETECTIONS &nbsp; {_n_live}"
        _sb_sub = "Start scanner to detect arbs"
    _arb_summary_html = (
        f"<div style='font-family:JetBrains Mono,monospace;font-size:0.67rem;"
        f"background:#0F172A;border:1px solid #1E293B;border-radius:4px;"
        f"padding:6px 8px;margin-top:6px;line-height:1.8;'>"
        f"<div style='color:{_arb_count_color};font-weight:700;letter-spacing:0.05em;font-size:0.72rem;'>"
        f"{_sb_header}</div>"
        f"<div style='color:#94A3B8;font-size:0.60rem;margin-top:2px;'>"
        f"{_sb_sub}"
        f"<br>Best edge: <span style='color:#E2E8F0;'>{_edge_str}</span>"
        f"<br>Last detected: <span style='color:#E2E8F0;'>{_last_arb_str}</span>"
        f"{_neon_cloud_line}"
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
                f"{_n_sb_clean:,} detected this session{_pre_fix_note}</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # Auto-refresh toggle (default ON so the dashboard stays live)
    auto_refresh = st.toggle("AUTO-REFRESH (10s)", value=True, key="global_auto_refresh")

    if st.button("Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # Key missing or snapshot mode — show setup hint
    _is_snapshot = not db_connected and health.get("db_mode") == "sqlite"
    if not has_key and not _neon_ok:
        st.markdown(
            f"<div style='font-size:0.6rem;color:#64748B;font-family:Inter,sans-serif;"
            f"border:1px solid #26313D;border-radius:3px;padding:0.5rem 0.6rem;margin-top:0.5rem;"
            f"line-height:1.6;'>"
            f"[i] Add <code>SYNTHESIS_SECRET_KEY=sk_gs-...</code> to Streamlit secrets"
            f" to enable the live feed</div>",
            unsafe_allow_html=True,
        )
    elif not has_key and _neon_ok:
        st.markdown(
            f"<div style='font-size:0.6rem;color:#22C55E;font-family:Inter,sans-serif;"
            f"border:1px solid #22C55E33;border-radius:3px;padding:0.5rem 0.6rem;margin-top:0.5rem;"
            f"line-height:1.6;'>"
            f"● Neon cloud connected — historical arbs available · add SYNTHESIS_SECRET_KEY to enable live feed</div>",
            unsafe_allow_html=True,
        )

    st.sidebar.markdown("---")
    st.sidebar.caption("Built by [Cyrus Bharucha](https://www.linkedin.com/in/cyrus-bharucha) · [GitHub](https://github.com/CyrusBharucha/kalshi-arb)")

# -- Render selected page -------------------------------------------------------
try:
    _get_page(selected).render()
except Exception as _page_exc:
    import traceback as _tb
    st.error(
        f"Page error in **{selected}** — {type(_page_exc).__name__}: {_page_exc}\n\n"
        "Other pages unaffected. Check System (p10) for WS/DB status."
    )
    with st.expander("STACK TRACE", expanded=False):
        st.code(_tb.format_exc(), language="python")

# -- Auto-refresh: always enabled when auto_refresh is on (so connecting status resolves live) --------
_snapshot_mode = not health.get("db_connected", False) and health.get("db_mode") == "sqlite" and not _neon_ok
_ws_live_for_refresh = ws_stats.get("connected", False)
if auto_refresh:
    _next_key = "_app_next_refresh"
    _now = time.time()
    if _now >= st.session_state.get(_next_key, 0):
        st.session_state[_next_key] = _now + 5  # 5s refresh — fast enough to catch WS connect
        st.rerun()
    # No sleep branch — blocking a worker thread for up to 5s per render is wasteful.
    # The next user interaction or p02's own timer will trigger the next rerun.
