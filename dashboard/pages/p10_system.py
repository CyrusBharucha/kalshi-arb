"""
dashboard/pages/p10_system.py — System Monitoring

Sections:
1. Synthesis WebSocket panel (status, msg/sec, stale books, seq gaps)
2. PostgreSQL panel (connection, latency, row counts)
3. Data coverage panel (L2, Canadian, relationships)
4. Arb engine status + process commands
5. Database tables (sizes, chart)
6. Data quality flags (crossed books, impossible prices, etc.)
7. Performance benchmarks snapshot (in-process timing)
8. Ingestion log (last 50 runs)
"""
from __future__ import annotations
from datetime import datetime, timezone
import time
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data_layer import (
    get_system_health, get_coverage_stats, get_table_sizes,
    get_data_quality_stats, get_ingestion_log, purge_prefixarbs,
    get_live_arbs_cloud_count, get_ext_market_daily_stats,
)
from dashboard.live_state import get_live_state
from dashboard.styles import plotly_dark_layout, GREEN, RED, AMBER, BLUE, TEXT, TEXT2, TEXT3, PANEL, BORDER, PANEL2


def render():
    st.markdown("""
<span style='font-size:1rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;'>
SYSTEM MONITOR
</span>
""", unsafe_allow_html=True)
    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    auto_refresh = st.checkbox(
        "AUTO REFRESH (10s)", value=False, key="system_auto_refresh",
        help="Re-runs this page every 10 seconds with fresh DB and WebSocket stats.",
    )

    # --- Live data -----
    state    = get_live_state()
    ws_stats = state.get_stats()
    health   = get_system_health()
    coverage = get_coverage_stats()
    _is_sqlite = not health.get("db_connected", False) and health.get("db_mode") == "sqlite"

    # Compute quiet books count (age > 300s) — resting books are valid longer
    _stale_threshold_s = 300
    try:
        _all_quotes = state.snapshot_all()
        _stale_books_n = sum(
            1 for q in (_all_quotes.values() if _all_quotes else [])
            if getattr(q, "age_seconds", 0) > _stale_threshold_s
        )
    except Exception:
        _stale_books_n = ws_stats.get("stale_books", 0)

    # (no banner — system page shows live WS stats when available, DB stats otherwise)

    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    # Git version: show HEAD hash from disk so user can detect stale server
    _git_hash = "--"
    _git_dirty = ""
    try:
        import subprocess as _sp
        _git_hash = _sp.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=__file__[:__file__.rfind("dashboard")],
            stderr=_sp.DEVNULL, timeout=2,
        ).decode().strip()
        _git_status = _sp.check_output(
            ["git", "status", "--porcelain"],
            cwd=__file__[:__file__.rfind("dashboard")],
            stderr=_sp.DEVNULL, timeout=2,
        ).decode().strip()
        if _git_status:
            _git_dirty = " (uncommitted changes)"
    except Exception:
        pass

    _gh_link = (
        f"<a href='https://github.com/CyrusBharucha/kalshi-arb/commit/{_git_hash}' "
        f"target='_blank' style='color:#94A3B8;text-decoration:none;'>"
        f"git:{_git_hash}</a>"
    ) if _git_hash != "--" else "--"
    st.markdown(
        f"<div style='font-family:JetBrains Mono,monospace;font-size:0.65rem;color:{TEXT3};margin-bottom:0.75rem;'>"
        f"Last refresh: {now} &nbsp;·&nbsp; "
        f"Code on disk: {_gh_link}{_git_dirty} "
        f"<span style='font-size:0.58rem;color:{TEXT3};'>"
        f"· <a href='https://github.com/CyrusBharucha/kalshi-arb/commits/master' "
        f"target='_blank' style='color:{TEXT3};'>history</a> "
        f"· if hash doesn't match last push, reboot app from share.streamlit.io</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # --- Three status panels -----
    col1, col2, col3 = st.columns(3)

    with col1:
        _section_header("LIVE DATA FEED")
        connected = ws_stats.get("connected", False)
        last_ts   = ws_stats.get("last_message_ts")
        last_age  = ws_stats.get("last_message_age_s")
        last_str  = (
            datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%H:%M:%S")
            if last_ts else "--"
        )
        rows = [
            ("STATUS",          (("● LIVE" if connected else "● DISCONNECTED"), GREEN if connected else RED)),
            ("MESSAGES / SEC",  (f"{ws_stats.get('messages_per_sec', 0):.1f}", TEXT)),
            ("TOTAL MESSAGES",  (f"{ws_stats.get('messages_total', 0):,}", TEXT)),
            ("MARKETS TRACKED", (f"{ws_stats.get('markets_tracked', 0):,}", TEXT)),
            ("RECONNECTS",      (str(ws_stats.get("reconnects", 0)), AMBER if ws_stats.get("reconnects", 0) > 0 else TEXT)),
            ("SEQ ERRORS",      (str(ws_stats.get("sequence_errors", 0) or ws_stats.get("seq_gaps_total", 0)), RED if (ws_stats.get("sequence_errors", 0) or ws_stats.get("seq_gaps_total", 0)) > 0 else TEXT)),  # WS sequence number gaps — zero is normal
            ("QUIET BOOKS >5m", (str(_stale_books_n), AMBER if _stale_books_n > 100 else TEXT)),  # books with no update in >5 min; high counts normal for low-liquidity markets
            ("DROPPED MSGS",    (str(ws_stats.get("dropped_messages", 0)), RED if ws_stats.get("dropped_messages", 0) > 0 else TEXT)),
            ("LAST MESSAGE",    (last_str, AMBER if last_age and last_age > 10 else TEXT)),
            ("CONN LATENCY",    (f"{ws_stats.get('latency_ms', 0) or 0:.1f} ms", TEXT)),
            ("LAST MSG AGE",    (f"{int(last_age)}s ago" if last_age is not None else "--", AMBER if last_age and last_age > 10 else TEXT)),
        ]
        _stat_panel(rows)

        # --- WS Connection State Badge -----
        try:
            _ws_connect_ts = ws_stats.get("connect_ts") or ws_stats.get("connection_time")
            _ws_uptime_s = None
            if _ws_connect_ts:
                import time as _t_uptime
                _ws_uptime_s = int(_t_uptime.time() - float(_ws_connect_ts))
        except Exception:
            _ws_uptime_s = None

        if connected:
            _ws_badge_text = "🟢 CONNECTED"
            _ws_badge_col = GREEN
        elif ws_stats.get("reconnects", 0) > 0:
            _ws_badge_text = "🟡 CONNECTING"
            _ws_badge_col = AMBER
        else:
            _ws_badge_text = "🔴 DISCONNECTED"
            _ws_badge_col = RED
        st.markdown(
            f"<div style='font-size:0.9rem;font-weight:700;color:{_ws_badge_col};"
            f"font-family:JetBrains Mono,monospace;margin:0.5rem 0 0.25rem 0;'>"
            f"{_ws_badge_text}</div>",
            unsafe_allow_html=True,
        )

        # Show reconnect attempt count if non-zero
        _reconn_n = ws_stats.get("reconnects", 0)
        if _reconn_n > 0:
            st.markdown(
                f"<div style='font-size:0.6rem;color:{AMBER};font-family:JetBrains Mono,monospace;'>"
                f"Reconnect attempts: {_reconn_n}</div>",
                unsafe_allow_html=True,
            )

        # --- WS bridge importability + live badge -----
        st.markdown("<br>", unsafe_allow_html=True)
        _section_header("WS FEED HEALTH")
        try:
            import importlib as _il_ws
            _ws_bridge_mod = _il_ws.util.find_spec("dashboard.ws_bridge")
            if _ws_bridge_mod is None:
                _ws_bridge_mod = _il_ws.util.find_spec("ws_bridge")
            if _ws_bridge_mod is None:
                _ws_badge_html = (
                    f"<div style='display:inline-block;padding:3px 10px;border-radius:3px;"
                    f"background:#374151;font-family:JetBrains Mono,monospace;font-size:0.72rem;"
                    f"color:#9CA3AF;letter-spacing:0.08em;'>N/A — ws_bridge not importable</div>"
                )
            elif connected:
                _ws_badge_html = (
                    f"<div style='display:inline-block;padding:3px 10px;border-radius:3px;"
                    f"background:#14532d;font-family:JetBrains Mono,monospace;font-size:0.72rem;"
                    f"color:{GREEN};letter-spacing:0.08em;'>● LIVE</div>"
                )
            else:
                _ws_badge_html = (
                    f"<div style='display:inline-block;padding:3px 10px;border-radius:3px;"
                    f"background:#450a0a;font-family:JetBrains Mono,monospace;font-size:0.72rem;"
                    f"color:{RED};letter-spacing:0.08em;'>● OFFLINE</div>"
                )
        except Exception:
            _ws_badge_html = (
                f"<div style='display:inline-block;padding:3px 10px;border-radius:3px;"
                f"background:#374151;font-family:JetBrains Mono,monospace;font-size:0.72rem;"
                f"color:#9CA3AF;letter-spacing:0.08em;'>N/A</div>"
            )
        st.markdown(_ws_badge_html, unsafe_allow_html=True)

        # --- WS message rate from LiveState -----
        try:
            _live_mps = state.get_stats().get("messages_per_sec", None)
            if _live_mps is not None:
                st.metric(
                    "WS MSG/SEC",
                    f"{float(_live_mps):.1f}",
                    help="Messages per second from LiveState (rolling average)",
                )
        except Exception:
            pass

        # Last message in ET
        try:
            from zoneinfo import ZoneInfo as _ZI_ws
            _ET_ws = _ZI_ws("America/New_York")
        except Exception:
            _ET_ws = None

        _last_ts_ws = ws_stats.get("last_message_ts")
        _last_age_ws = ws_stats.get("last_message_age_s")
        if _last_ts_ws:
            try:
                _last_dt = datetime.fromtimestamp(float(_last_ts_ws), tz=timezone.utc)
                _last_str_et = (_last_dt.astimezone(_ET_ws).strftime("%H:%M:%S ET")
                                if _ET_ws else _last_dt.strftime("%H:%M:%S UTC"))
            except Exception:
                _last_str_et = "--"
        else:
            _last_str_et = "--"

        # Messages in last minute (approx from rate × 60 or from dedicated counter)
        _msgs_last_min = ws_stats.get("messages_last_minute") or ws_stats.get("messages_last_60s")
        if _msgs_last_min is None:
            _mps_now = ws_stats.get("messages_per_sec", 0) or 0
            _msgs_last_min = int(_mps_now * 60)

        _uptime_str_ws = "--"
        if connected and _ws_uptime_s is not None:
            _u_h = _ws_uptime_s // 3600
            _u_m = (_ws_uptime_s % 3600) // 60
            _u_s = _ws_uptime_s % 60
            _uptime_str_ws = f"{_u_h}h {_u_m}m {_u_s}s" if _u_h else (f"{_u_m}m {_u_s}s" if _u_m else f"{_u_s}s")

        # Gate 0: CE series DB check — True once cache has loaded from DB
        try:
            import dashboard.ws_bridge as _wsb
            _g0_active = bool(getattr(_wsb, "_gate0_active", False))
            _g0_label = "ACTIVE" if _g0_active else "INACTIVE (loading…)"
            _g0_col = GREEN if _g0_active else AMBER
        except Exception:
            _g0_active = False
            _g0_label, _g0_col = "UNKNOWN", AMBER

        _ws_health_rows = [
            ("MESSAGES RECEIVED", (f"{ws_stats.get('messages_total', 0):,}", TEXT)),
            ("MSGS LAST MINUTE",  (f"{_msgs_last_min:,}", TEXT)),
            ("CONNECTION UPTIME", (_uptime_str_ws, GREEN if connected else TEXT3)),
            ("LAST MSG (ET)",     (_last_str_et, AMBER if _last_age_ws and _last_age_ws > 10 else TEXT)),
            ("GATE 0 (SERIES FILTER)", (_g0_label, _g0_col)),
        ]
        _ws_health_html = f"<div style='background:{PANEL};border:1px solid {BORDER};border-radius:3px;padding:0.4rem 0;'>"
        for _lbl, (_val, _col) in _ws_health_rows:
            _ws_health_html += (
                f"<div style='display:flex;justify-content:space-between;align-items:center;"
                f"padding:5px 0.8rem;border-bottom:1px solid {BORDER};'>"
                f"<span style='font-size:0.6rem;letter-spacing:0.08em;text-transform:uppercase;"
                f"color:{TEXT3};font-family:Inter,sans-serif;'>{_lbl}</span>"
                f"<span style='font-family:JetBrains Mono,monospace;font-size:0.73rem;color:{_col};'>{_val}</span></div>"
            )
        _ws_health_html += "</div>"
        st.markdown(_ws_health_html, unsafe_allow_html=True)

        # --- WS disconnected >60s alert -----
        if not connected and _last_age_ws is not None and _last_age_ws > 60:
            st.error("🔴 WebSocket disconnected for >60s — data may be stale")

        # --- Gate 0 alert -----
        if not _g0_active:
            st.warning(
                "⚠️ **Gate 0 (series filter) is loading** — the series classification cache "
                "has not yet been fetched from the DB. It activates automatically within ~30s "
                "of the WebSocket scanner starting.",
                icon=None,
            )

        # --- Message rate sparkline -----
        st.markdown("<br>", unsafe_allow_html=True)
        _section_header("WS MESSAGE RATE (msg/s)")
        try:
            _cur_rate = ws_stats.get("messages_per_sec", 0) or 0
            if "p10_msg_history" not in st.session_state:
                st.session_state["p10_msg_history"] = []
            else:
                _hist = st.session_state["p10_msg_history"]
                _hist.append(_cur_rate)
                if len(_hist) > 30:
                    _hist = _hist[-30:]
                st.session_state["p10_msg_history"] = _hist
                if len(_hist) >= 2:
                    _spark_color = GREEN if _cur_rate > 1000 else (AMBER if _cur_rate >= 100 else RED)
                    _fig_spark = go.Figure(go.Scatter(
                        y=_hist, mode="lines",
                        line={"color": _spark_color, "width": 1.5},
                        name="msg/s",
                    ))
                    _fig_spark.update_layout(
                        height=90,
                        margin={"l": 30, "r": 5, "t": 5, "b": 20},
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        xaxis={"visible": True, "showticklabels": False, "showgrid": False},
                        yaxis={"visible": True, "tickfont": {"size": 7}, "showgrid": False,
                               "color": TEXT3},
                        showlegend=False,
                    )
                    st.plotly_chart(_fig_spark, use_container_width=True, config={"displayModeBar": False})
        except Exception:
            pass

        # --- Force Reconnect button -----
        if st.button("🔌 Force Reconnect", key="p10_reconnect"):
            st.session_state["p10_reconnect_requested"] = True
            st.warning(
                "Reconnect requested — the WebSocket bridge will reconnect on its next health check cycle (~30s)"
            )
        st.caption(
            "The bridge auto-reconnects every 30s if disconnected; this button simply logs the request "
            "so it appears in the next health check cycle."
        )

    with col2:
        _section_header("POSTGRESQL DATABASE")
        db_ok     = health.get("db_connected", False)
        db_sqlite = health.get("db_mode") == "sqlite"
        db_lat    = health.get("db_latency_ms")
        last_snap = health.get("latest_snapshot_ts")

        # Also check live_arb_store Neon (may be connected even if analytics DB isn't)
        _neon_ok_p10 = False
        _neon_pending_p10 = False  # configured but in 30s retry backoff
        try:
            import dashboard.live_arb_store as _las_p10
            _neon_ok_p10 = (
                bool(getattr(_las_p10, "_pg_ok", False))
                or (getattr(_las_p10, "_pg_engine", None) is not None)
            )
            if not _neon_ok_p10:
                _neon_ok_p10 = _las_p10.get_pg_engine_cached() is not None
            if not _neon_ok_p10:
                _neon_pending_p10 = getattr(_las_p10, "_pg_last_fail_ts", 0.0) > 0.0
        except Exception:
            pass
        # Fallback: sidebar already connected and stored result in session_state
        if not _neon_ok_p10:
            _neon_ok_p10 = bool(st.session_state.get("_sidebar_neon_ok", False))
        _effective_db_ok = db_ok or _neon_ok_p10

        _pg_markets = int(health.get("markets_total", 0) or 0)
        if db_ok:
            _pg_status_str = "CONNECTED (empty)" if _pg_markets == 0 else "CONNECTED"
            _pg_status_col = GREEN if _pg_markets > 0 else AMBER
        elif _neon_ok_p10:
            _pg_status_str = "NEON CLOUD ✓"
            _pg_status_col = GREEN
        elif _neon_pending_p10:
            _pg_status_str = "↻ CONNECTING"
            _pg_status_col = AMBER
        elif db_sqlite:
            _pg_status_str = "PG UNAVAILABLE → SQLite"
            _pg_status_col = AMBER
        else:
            _pg_status_str = "OFFLINE"
            _pg_status_col = RED
        rows2 = [
            ("STATUS",          (_pg_status_str, _pg_status_col)),
            ("CONN LAT (cold)", (f"{db_lat:.1f} ms" if db_lat else "--", TEXT)),  # TCP + Neon cold-start; 500–2000 ms is normal on Streamlit Cloud
            ("DATABASE SIZE",   (health.get("db_size_mb", "--") or "--", TEXT)),
            ("MARKETS",         (f"{_pg_markets:,}", TEXT)),
            ("EVENTS",          (f"{int(health.get('events_total', 0) or 0):,}", TEXT)),
            ("TRADES",          (f"{int(health.get('trades_total', 0) or 0):,}", TEXT)),
            ("RELATIONSHIPS",   (f"{int(health.get('relationships_total', 0) or 0):,}", TEXT)),
            ("HISTORICAL DETECTIONS", (f"{int(health.get('arb_opportunities_open', 0) or 0):,}", TEXT)),
            ("LATEST L2 SNAPSHOT (UTC)", (str(last_snap)[:19] if last_snap else "--", TEXT)),
        ]
        _stat_panel(rows2)

    with col3:
        _section_header("DATA COVERAGE")
        l2_days  = int(coverage.get("l2_coverage_days", 0))
        l2_ticks = int(coverage.get("markets_with_l2", 0))
        _l2_cov_str = (
            f"{l2_days} days" if l2_days > 0
            else ("1 session" if l2_ticks > 0 else "0 days")
        )
        _l2_cov_color = GREEN if l2_days > 0 else (AMBER if l2_ticks > 0 else RED)
        rows3 = [
            ("L2 SNAPSHOTS",      (f"{int(health.get('l2_snapshots_total', 0) or 0):,}", TEXT)),
            ("L2 COVERAGE",       (_l2_cov_str, _l2_cov_color)),
            ("L2 EARLIEST",       (str(coverage.get("l2_earliest_ts") or "--")[:10], TEXT)),
            ("MARKETS WITH L2",   (f"{int(coverage.get('markets_with_l2') or 0):,}", TEXT)),
            ("LIQUID MARKETS",    (f"{int(coverage.get('markets_liquid') or 0):,}", TEXT)),
            ("CANADIAN MARKETS",  (f"{int(coverage.get('canadian_markets') or 0):,}", TEXT)),
            ("WITH RELATIONSHIPS",(f"{int(coverage.get('markets_with_relationships') or 0):,}", TEXT)),
            ("WITH DETECT",        (f"{int(coverage.get('markets_with_arb') or 0):,}", TEXT)),
        ]
        _stat_panel(rows3)

    # --- WS disconnected + no-key notice -----
    _synthesis_key_set = bool(__import__("os").environ.get("SYNTHESIS_SECRET_KEY", "").strip())
    if not connected and not _synthesis_key_set:
        st.warning(
            "**Live feed is offline** — `SYNTHESIS_SECRET_KEY` is not set, so the WebSocket scanner "
            "cannot start. The dashboard is showing database data only. "
            "See ⚙️ **STREAMLIT CLOUD SETUP — Required Secrets** at the bottom of this page for instructions.",
            icon="⚠️",
        )

    # --- Engine section -----
    st.markdown("<hr>", unsafe_allow_html=True)
    e1, e2 = st.columns(2)

    with e1:
        _section_header("ARB ENGINE STATUS")
        if connected:
            _scanner_label = "RUNNING"
            _scanner_color = GREEN
        elif _is_sqlite:
            _scanner_label = "READY"
            _scanner_color = GREEN
        elif _neon_ok_p10:
            _scanner_label = "NEON CLOUD"
            _scanner_color = BLUE
        else:
            _scanner_label = "STOPPED"
            _scanner_color = AMBER
        # Session arb stats (non-decaying session counters)
        _sess_stats = state.get_session_stats()
        _n_session = _sess_stats.get("total", 0)
        _n_ce = _sess_stats.get("ce", 0)
        _n_comp = _sess_stats.get("complement", 0)
        _best_edge = _sess_stats.get("best_edge", 0)

        # Last arb detected — prefer session timestamp, fall back to DB record
        _last_arb_ts = _sess_stats.get("last_arb_ts") or _sess_stats.get("last_ts")
        if _last_arb_ts:
            try:
                _last_arb_age_s = (datetime.now(timezone.utc) - datetime.fromtimestamp(
                    _last_arb_ts, tz=timezone.utc
                )).total_seconds()
                if _last_arb_age_s < 60:
                    _last_arb_str = f"{int(_last_arb_age_s)}s ago"
                elif _last_arb_age_s < 3600:
                    _last_arb_str = f"{int(_last_arb_age_s / 60)}m ago"
                else:
                    _last_arb_str = f"{int(_last_arb_age_s / 3600)}h ago"
                _last_arb_color = GREEN if _last_arb_age_s < 300 else (AMBER if _last_arb_age_s < 1800 else TEXT)
            except Exception:
                _last_arb_str, _last_arb_color = "--", TEXT
        else:
            # Try DB as fallback
            try:
                _db_last_arb = health.get("latest_arb_ts")
                if _db_last_arb:
                    _db_dt = pd.to_datetime(_db_last_arb, utc=True, errors="coerce")
                    if _db_dt is not pd.NaT and _db_dt == _db_dt:
                        _db_age_s = (datetime.now(timezone.utc) - _db_dt.to_pydatetime()).total_seconds()
                        if _db_age_s < 60:
                            _last_arb_str = f"{int(_db_age_s)}s ago"
                        elif _db_age_s < 3600:
                            _last_arb_str = f"{int(_db_age_s / 60)}m ago"
                        else:
                            _last_arb_str = f"{int(_db_age_s / 3600)}h ago"
                        _last_arb_color = AMBER
                    else:
                        _last_arb_str, _last_arb_color = "--", TEXT
                else:
                    _last_arb_str, _last_arb_color = "No detections this session", TEXT
            except Exception:
                _last_arb_str, _last_arb_color = "--", TEXT
        # Final fallback: Neon live_arbs_cloud most recent ME/TH arb
        if _last_arb_str in ("--", "No detections this session"):
            try:
                import dashboard.live_arb_store as _las_p10_last
                from sqlalchemy import text as _p10_last_text
                _p10_last_eng = getattr(_las_p10_last, "_pg_engine", None) or _las_p10_last.get_pg_engine_cached()
                if _p10_last_eng is not None:
                    with _p10_last_eng.connect() as _p10_last_c:
                        _p10_last_row = _p10_last_c.execute(_p10_last_text(
                            "SELECT MAX(detected_at) FROM live_arbs_cloud "
                            "WHERE strategy_type NOT IN ('yes_no_complement','collectively_exhaustive')"
                        )).fetchone()
                    if _p10_last_row and _p10_last_row[0]:
                        _p10_last_dt = _p10_last_row[0]
                        if hasattr(_p10_last_dt, "timestamp"):
                            _p10_age_s = (datetime.now(timezone.utc) - _p10_last_dt.replace(tzinfo=timezone.utc)).total_seconds()
                            if _p10_age_s < 3600:
                                _last_arb_str = f"{int(_p10_age_s / 60)}m ago (Neon)"
                            elif _p10_age_s < 86400:
                                _last_arb_str = f"{int(_p10_age_s / 3600)}h ago (Neon)"
                            else:
                                _last_arb_str = _p10_last_dt.strftime("%b %-d") + " (Neon)"
                            _last_arb_color = AMBER
            except Exception:
                pass

        _funnel = state.get_scanner_funnel()
        if _funnel:
            # YNC (YES/NO complement) funnel
            _f_checked  = _funnel.get("checked", 0)
            _f_blk_mt   = _funnel.get("blocked_min_tick", 0)
            _f_blk_thr  = _funnel.get("blocked_threshold", 0)
            _f_blk_flr  = _funnel.get("blocked_floor", 0)
            _f_p_sum    = _funnel.get("passed_sum", 0)
            _f_p_gross  = _funnel.get("passed_gross", 0)
            _f_p_qnet   = _funnel.get("passed_qnet", 0)
            _f_blk_ttl  = _funnel.get("blocked_ttl", 0)
            _f_p_book   = _funnel.get("passed_book", 0)
            _f_blk_sum  = max(0, _f_checked - _f_p_sum)
            _f_blk_grs  = max(0, _f_p_sum - _f_p_gross)
            _f_blk_qnet = max(0, _f_p_gross - _f_p_qnet)
            _ync_line = (
                f"YNC: {_f_checked} checked"
                f" | -{_f_blk_mt} min-tick | -{_f_blk_thr} threshold | -{_f_blk_flr} floor"
                f" | -{_f_blk_sum} sum&ge;1 | -{_f_blk_grs} gross&gt;10c | -{_f_blk_qnet} net&lt;2c"
                f" | -{_f_blk_ttl} TTL | {_f_p_book} passed"
            )
            # CE funnel
            _ce_p = _funnel.get("ce_passed", 0)
            _ce_line = (
                f"CE: -{_funnel.get('ce_blocked_numeric', 0)} numeric"
                f" | -{_funnel.get('ce_blocked_min_tick', 0)} min-tick"
                f" | -{_funnel.get('ce_blocked_structural', 0)} structural"
                f" | -{_funnel.get('ce_blocked_synthesis', 0)} no-synth-group"
                f" | -{_funnel.get('ce_blocked_full_leg', 0)} partial-leg"
                f" | -{_funnel.get('ce_blocked_depth', 0)} depth"
                f" | -{_funnel.get('ce_blocked_ttl', 0)} TTL"
                f" | {_ce_p} passed"
            )
            # ME funnel
            _me_p = _funnel.get("me_passed", 0)
            _me_line = (
                f"ME: -{_funnel.get('me_blocked_numeric', 0)} numeric"
                f" | -{_funnel.get('me_blocked_min_tick', 0)} min-tick"
                f" | -{_funnel.get('me_blocked_synthesis', 0)} no-synth-group"
                f" | -{_funnel.get('me_blocked_full_leg', 0)} partial-leg"
                f" | -{_funnel.get('me_blocked_depth', 0)} depth"
                f" | -{_funnel.get('me_blocked_ttl', 0)} TTL"
                f" | {_me_p} passed"
            )
            # TH funnel
            _th_p = _funnel.get("th_passed", 0)
            _th_line = (
                f"TH: {_funnel.get('th_checked', 0)} checked"
                f" | -{_funnel.get('th_blocked_floor', 0)} floor"
                f" | -{_funnel.get('th_blocked_monotonicity', 0)} monotonicity"
                f" | -{_funnel.get('th_blocked_gross', 0)} gross&gt;10c"
                f" | -{_funnel.get('th_blocked_net', 0)} net&lt;2c"
                f" | -{_funnel.get('th_blocked_ttl', 0)} TTL"
                f" | -{_funnel.get('th_blocked_depth', 0)} depth"
                f" | {_th_p} passed"
            )
            _funnel_str = f"{_ync_line}<br>{_me_line}<br>{_th_line}<br><span style='color:#888;'>CE: disabled (scanner off)</span> &nbsp;(60-cycle window)"
        else:
            _funnel_str = "--"

        # -- Scanner efficiency (passed / (passed + blocked_ttl) across all strategy types) --
        if _funnel:
            _eff_passed = (
                _funnel.get("passed_book", 0)
                + _funnel.get("ce_passed", 0)
                + _funnel.get("me_passed", 0)
                + _funnel.get("th_passed", 0)
            )
            _eff_blocked = (
                _funnel.get("blocked_ttl", 0)
                + _funnel.get("ce_blocked_ttl", 0)
                + _funnel.get("me_blocked_ttl", 0)
                + _funnel.get("th_blocked_ttl", 0)
            )
            _eff_denom = _eff_passed + _eff_blocked
            _eff_pct = (_eff_passed / _eff_denom * 100) if _eff_denom > 0 else 0.0
            _eff_str = f"{_eff_pct:.1f}% of scanned &rarr; detection"
            _eff_color = GREEN if _eff_pct > 0 else TEXT3
        else:
            _eff_str = "--"
            _eff_color = TEXT3

        _n_clean = _sess_stats.get("clean", _sess_stats.get("total", 0))
        rows_e = [
            ("SCANNER",              (_scanner_label, _scanner_color)),
            ("STRATEGY",             ("YNC + ME + TH active; CE disabled", TEXT)),
            ("SESSION DETECTIONS",   (str(_n_session), GREEN if _n_session > 0 else TEXT)),
            ("CLEAN DETECTIONS",     (str(_n_clean), GREEN if _n_clean > 0 else TEXT)),
            ("BEST SESSION EDGE",    (f"+{_best_edge:.2f}c" if _best_edge > 0 else "--", GREEN if _best_edge > 0 else TEXT)),
            ("LAST DETECTION",       (_last_arb_str, _last_arb_color)),
            ("SCANNER FUNNEL",       (_funnel_str, TEXT2)),
            ("EFFICIENCY",           (_eff_str, _eff_color)),
        ]
        _stat_panel(rows_e)

        # -- Per-scanner efficiency breakdown table --
        st.markdown("<br>", unsafe_allow_html=True)
        # -- Live quote sample: verify no_ask is populated --
        try:
            _sample_quotes = sorted(
                [q for q in state.snapshot_all().values()
                 if q.yes_ask > 0.05 and q.no_ask > 0.05 and q.yes_ask < 0.95],
                key=lambda q: q.yes_ask + q.no_ask
            )[:8]
            if _sample_quotes:
                st.markdown("<br>", unsafe_allow_html=True)
                _section_header("LIVE QUOTE SAMPLE (sorted by sum)")
                _sq_rows = []
                for _sq in _sample_quotes:
                    _sum = _sq.yes_ask + _sq.no_ask
                    _sum_col = GREEN if _sum < 1.0 else (AMBER if _sum < 1.01 else TEXT3)
                    _derived_no = round(1.0 - _sq.yes_bid, 4) if _sq.yes_bid > 0 else 1.0
                    _no_gap = round(_sq.no_ask - _derived_no, 4)
                    _no_src = "native" if abs(_no_gap) > 0.001 else "derived"
                    _sq_rows.append({
                        "TICKER": _sq.ticker[:28],
                        "YES ASK": f"{_sq.yes_ask:.3f}",
                        "YES BID": f"{_sq.yes_bid:.3f}",
                        "NO ASK": f"{_sq.no_ask:.3f}",
                        "IMPL NO": f"{_derived_no:.3f}",
                        "NO SRC": _no_src,
                        "SUM": f"{_sum:.3f}",
                    })
                import pandas as _pd_sq
                st.dataframe(_pd_sq.DataFrame(_sq_rows), use_container_width=True, hide_index=True)
                _crossed = [q for q in _sample_quotes if q.yes_ask + q.no_ask < 1.0]
                if _crossed:
                    st.caption(f"{len(_crossed)} markets with YES_ask + NO_ask < $1 (feed/rounding artifacts — Kalshi YES/NO always sum ≥ $1.00 structurally)")
                else:
                    st.caption("All sampled markets: YES_ask + NO_ask >= $1 — no crossed quotes in sample")
        except Exception:
            pass

        _section_header("SCANNER EFFICIENCY BREAKDOWN")
        if _funnel:
            # Derive per-scanner scans-run and arbs-found
            _ync_scans = _f_checked
            _ync_found = _funnel.get("passed_book", 0)
            _ce_found  = _funnel.get("ce_passed", 0)
            _ce_scans  = (
                _ce_found
                + _funnel.get("ce_blocked_numeric", 0)
                + _funnel.get("ce_blocked_min_tick", 0)
                + _funnel.get("ce_blocked_structural", 0)
                + _funnel.get("ce_blocked_synthesis", 0)
                + _funnel.get("ce_blocked_full_leg", 0)
                + _funnel.get("ce_blocked_depth", 0)
                + _funnel.get("ce_blocked_ttl", 0)
            )
            _me_found  = _funnel.get("me_passed", 0)
            _me_scans  = (
                _me_found
                + _funnel.get("me_blocked_numeric", 0)
                + _funnel.get("me_blocked_min_tick", 0)
                + _funnel.get("me_blocked_synthesis", 0)
                + _funnel.get("me_blocked_full_leg", 0)
                + _funnel.get("me_blocked_depth", 0)
                + _funnel.get("me_blocked_ttl", 0)
            )
            _th_scans  = _funnel.get("th_checked", 0)
            _th_found  = _funnel.get("th_passed", 0)

            def _hit_pct(found, scans):
                return f"{found / scans * 100:.2f}%" if scans > 0 else "0.00%"

            _eff_table = pd.DataFrame([
                {"Scanner": "YNC (YES/NO Complement) — feed artifacts", "Scans Run": _ync_scans, "Detections": _ync_found, "Hit Rate %": _hit_pct(_ync_found, _ync_scans)},
                {"Scanner": "CE (Collectively Exhaustive) — disabled", "Scans Run": _ce_scans, "Detections": _ce_found, "Hit Rate %": _hit_pct(_ce_found, _ce_scans)},
                {"Scanner": "ME (Mutually Exclusive)", "Scans Run": _me_scans, "Detections": _me_found, "Hit Rate %": _hit_pct(_me_found, _me_scans)},
                {"Scanner": "TH (Threshold Order)", "Scans Run": _th_scans, "Detections": _th_found, "Hit Rate %": _hit_pct(_th_found, _th_scans)},
            ])
            st.dataframe(_eff_table, use_container_width=True, hide_index=True)

            # -- Scanner latency (time between last WS message and last arb check) --
            try:
                _ws_last_msg_ts = ws_stats.get("last_message_ts", 0) or 0
                _ws_last_scan_ts_lat = _funnel.get("ws_last_scan_ts", 0) or 0
                if _ws_last_msg_ts > 0 and _ws_last_scan_ts_lat > 0:
                    _scan_latency_s = abs(_ws_last_scan_ts_lat - _ws_last_msg_ts)
                    _scan_lat_str = f"{_scan_latency_s*1000:.0f} ms" if _scan_latency_s < 1 else f"{_scan_latency_s:.2f}s"
                    st.caption(f"Scanner latency (last WS msg → last arb check): {_scan_lat_str}")
            except Exception:
                pass

            # -- Scanner Status: green if WS connected (scan cycle is running), red if stale --
            st.markdown("<br>", unsafe_allow_html=True)
            _section_header("SCANNER STATUS")
            _now_ts = time.time()
            _ws_last_scan_ts = _funnel.get("ws_last_scan_ts", 0) or 0
            _scanners_list = ["YNC", "ME", "TH"]
            _status_html = f"<div style='background:{PANEL};border:1px solid {BORDER};border-radius:3px;padding:0.4rem 0;'>"
            for _sc_name in _scanners_list:
                # Use WS last scan ts as proxy; individual per-scanner timestamps not yet tracked
                _sc_age = _now_ts - _ws_last_scan_ts if _ws_last_scan_ts > 0 else None
                if not connected:
                    _sc_color = RED
                    _sc_label = "OFFLINE"
                    _sc_age_str = "no WS"
                elif _sc_age is not None and _sc_age <= 60:
                    _sc_color = GREEN
                    _sc_label = "OK"
                    _sc_age_str = f"{int(_sc_age)}s ago"
                elif _sc_age is not None:
                    _sc_color = AMBER
                    _sc_label = "STALE"
                    _sc_age_str = f"{int(_sc_age)}s ago"
                else:
                    _sc_color = AMBER
                    _sc_label = "WAITING"
                    _sc_age_str = "no scan yet"
                _status_html += (
                    f"<div style='display:flex;justify-content:space-between;align-items:center;"
                    f"padding:5px 0.8rem;border-bottom:1px solid {BORDER};'>"
                    f"<span style='font-size:0.6rem;letter-spacing:0.08em;text-transform:uppercase;"
                    f"color:{TEXT3};font-family:Inter,sans-serif;'>{_sc_name}</span>"
                    f"<span style='font-family:JetBrains Mono,monospace;font-size:0.73rem;color:{_sc_color};'>"
                    f"● {_sc_label} &nbsp; <span style='color:{TEXT3};font-size:0.6rem;'>{_sc_age_str}</span></span></div>"
                )
            _status_html += "</div>"
            st.markdown(_status_html, unsafe_allow_html=True)
            st.caption("Status: green = scanner active (last scan ≤60s ago), amber = stale, red = WS disconnected. Per-scanner timestamps share the WS cycle clock.")
        else:
            st.caption("Per-scanner breakdown available when WS connected and first 60-cycle window completes.")

        # -- Scanner health telemetry metrics --
        _sm1, _sm2 = st.columns(2)
        with _sm1:
            try:
                _ws_scan_n = _funnel.get("ws_scan_count", 0) if _funnel else 0
                st.metric("TOTAL SCANS", f"{_ws_scan_n:,}")
            except Exception:
                st.metric("TOTAL SCANS", "--")
        with _sm2:
            try:
                _ws_arb_ts = _funnel.get("ws_last_arb_ts", 0) if _funnel else 0
                if _ws_arb_ts:
                    import time as _t_arb
                    _arb_age = _t_arb.time() - _ws_arb_ts
                    if _arb_age < 60:
                        _arb_age_str = f"{int(_arb_age)}s ago"
                    elif _arb_age < 3600:
                        _arb_age_str = f"{int(_arb_age / 60)}m ago"
                    else:
                        _arb_age_str = f"{int(_arb_age / 3600)}h ago"
                else:
                    _arb_age_str = "never"
                st.metric("LAST DETECTION", _arb_age_str, help="Last scanner detection (any strategy incl. YNC feed artifacts)")
            except Exception:
                st.metric("LAST DETECTION", "--")

        # -- Resource monitoring metrics --
        _rm1, _rm2, _rm3 = st.columns(3)
        with _rm1:
            try:
                import psutil as _psutil
                import os as _os_pid
                _rss_mb = _psutil.Process(_os_pid.getpid()).memory_info().rss / 1024 / 1024
                st.metric("MEMORY (RSS)", f"{_rss_mb:.1f} MB")
                # --- Rolling 30-point memory history ---
                _mem_hist = st.session_state.get("mem_history", [])
                _mem_hist.append(_rss_mb)
                if len(_mem_hist) > 30:
                    _mem_hist = _mem_hist[-30:]
                st.session_state["mem_history"] = _mem_hist
                # Sparkline
                if len(_mem_hist) >= 2:
                    import pandas as _pd_mem
                    st.caption("Memory trend (last 30 readings)")
                    st.line_chart(_pd_mem.DataFrame({"RSS MB": _mem_hist}), height=80)
                # Trend indicator
                _mem_trend_label = "➡️ Stable"
                if len(_mem_hist) >= 5:
                    _last5_mem = _mem_hist[-5:]
                    if all(_last5_mem[i] < _last5_mem[i + 1] for i in range(4)):
                        _mem_trend_label = "📈 Growing"
                    elif all(_last5_mem[i] > _last5_mem[i + 1] for i in range(4)):
                        _mem_trend_label = "📉 Shrinking"
                st.caption(f"Memory trend: {_mem_trend_label}")
                # High-memory alert
                if _rss_mb > 500:
                    st.error("⚠️ High memory usage — possible leak, consider restarting")
                # Growing-trend warning
                if len(_mem_hist) >= 5:
                    _last5_chk = _mem_hist[-5:]
                    if all(_last5_chk[i] < _last5_chk[i + 1] for i in range(4)):
                        st.warning("⚠️ Memory trend increasing — monitor for leak")
            except Exception:
                st.metric("MEMORY (RSS)", "--")
        with _rm2:
            try:
                _opps = state.get_opportunities()
                _queue_depth = len(_opps) if _opps is not None else 0
                st.metric(
                    "QUEUE DEPTH",
                    str(_queue_depth),
                    delta=f"{_queue_depth} live" if _queue_depth > 0 else None,
                )
            except Exception:
                _queue_depth = 0
                st.metric("QUEUE DEPTH", "--")
            # --- Queue depth sparkline -----
            try:
                _qdh = st.session_state.get("queue_depth_history", [])
                _qdh.append(_queue_depth)
                if len(_qdh) > 30:
                    _qdh = _qdh[-30:]
                st.session_state["queue_depth_history"] = _qdh
                if len(_qdh) >= 2:
                    import pandas as _pd_qdh
                    st.caption("Queue Depth (last 30 readings)")
                    st.line_chart(_pd_qdh.DataFrame({"Queue Depth": _qdh}), height=100)
                _peak_qd = max(_qdh) if _qdh else 0
                st.metric("Peak Queue Depth", str(_peak_qd))
                if _peak_qd > 1000:
                    st.error("⚠️ Queue backlog")
                elif _peak_qd > 500:
                    st.warning("Queue elevated")
                else:
                    st.success("Queue healthy")
            except Exception:
                pass
            try:
                _prev_queue = st.session_state.get("p10_prev_queue")
                _drain_rate = (_prev_queue - _queue_depth) if _prev_queue is not None else 0
                st.session_state["p10_prev_queue"] = _queue_depth
                if _prev_queue is None:
                    st.metric("QUEUE TREND", "--")
                else:
                    st.metric(
                        "QUEUE TREND",
                        f"{abs(_drain_rate):+.0f}/cycle",
                        delta=f"{_drain_rate:+.0f}",
                    )
            except Exception:
                st.metric("QUEUE TREND", "--")
        with _rm3:
            try:
                import time as _t_rate
                if "p10_session_start" not in st.session_state:
                    st.session_state["p10_session_start"] = _t_rate.time()
                _uptime_h = (_t_rate.time() - st.session_state["p10_session_start"]) / 3600
                if _uptime_h > 0 and _n_session > 0:
                    _arbs_per_hr = _n_session / _uptime_h
                    st.metric(
                        "DETECTION RATE",
                        f"{_arbs_per_hr:.1f}/hr",
                        delta=f"{_n_session} this session",
                        help="Scanner detections per hour (all strategies — ME/TH arbs + YNC feed artifacts; CE disabled).",
                    )
                else:
                    st.metric("DETECTION RATE", "--")
            except Exception:
                st.metric("DETECTION RATE", "--")

        # -- GC Objects and Threads ---
        _gc1, _gc2 = st.columns(2)
        with _gc1:
            try:
                import gc as _gc_mod
                st.metric("GC OBJECTS", f"{len(_gc_mod.get_objects()):,}")
            except Exception:
                st.metric("GC OBJECTS", "--")
        with _gc2:
            try:
                import threading as _threading_mod
                st.metric("THREADS", str(_threading_mod.active_count()))
            except Exception:
                st.metric("THREADS", "--")

        # -- Session uptime counter --
        st.session_state.setdefault("p10_boot_ts", time.time())
        _elapsed = time.time() - st.session_state["p10_boot_ts"]
        _up_h = int(_elapsed // 3600)
        _up_m = int((_elapsed % 3600) // 60)
        _up_s = int(_elapsed % 60)
        _uptime_str = f"{_up_h}h {_up_m}m {_up_s}s"
        st.metric("SESSION UPTIME", _uptime_str)
        st.caption("Session uptime resets on app restart or page refresh")

        if _eff_str != "--":
            st.caption(
                "EFFICIENCY = detections passed / (passed + TTL-blocked) across all strategy types. "
                "Expected to be very low — most scan cycles find no actionable mispricing."
            )
        if _funnel_str == "--":
            st.caption("SCANNER FUNNEL shows '--' until the first 60-cycle window completes (~60 seconds after the scanner starts).")

        with st.expander("🧹 DATA MAINTENANCE", expanded=False):
            # --- SQLite file info + VACUUM ---
            import os as _os_maint
            from dashboard.data_layer import _SQLITE_PATH as _sq_path
            _sq_path_live = _sq_path.parent / "live_arbs.db"
            _sq_files = {
                "dashboard.db": _sq_path,
                "live_arbs.db": _sq_path_live,
            }
            _size_rows = []
            for _sq_label, _sq_fp in _sq_files.items():
                try:
                    if _sq_fp.exists():
                        _sz_mb = _sq_fp.stat().st_size / 1_048_576
                        _size_rows.append(f"**{_sq_label}**: {_sz_mb:.2f} MB  (`{_sq_fp}`)")
                    else:
                        _size_rows.append(f"**{_sq_label}**: not found")
                except Exception as _e:
                    _size_rows.append(f"**{_sq_label}**: error reading size — {_e}")
            st.markdown("**SQLite file sizes**")
            for _r in _size_rows:
                st.markdown(_r)

            st.markdown("---")
            st.markdown(
                "**VACUUM** reclaims disk space after deletions by defragmenting the SQLite file. "
                "It is safe to run at any time — it does not delete any data."
            )
            _vac_col1, _vac_col2 = st.columns(2)
            with _vac_col1:
                if st.button("VACUUM dashboard.db", key="vacuum_dashboard_db"):
                    try:
                        import sqlite3 as _sq3_vac
                        if _sq_path.exists():
                            _vc = _sq3_vac.connect(str(_sq_path), check_same_thread=False)
                            _vc.execute("VACUUM")
                            _vc.close()
                            _new_sz = _sq_path.stat().st_size / 1_048_576
                            st.success(f"VACUUM complete. dashboard.db is now {_new_sz:.2f} MB.")
                        else:
                            st.warning("dashboard.db not found.")
                    except Exception as _ve:
                        st.error(f"VACUUM failed: {_ve}")
            with _vac_col2:
                if st.button("VACUUM live_arbs.db", key="vacuum_live_arbs_db"):
                    try:
                        import sqlite3 as _sq3_vac2
                        if _sq_path_live.exists():
                            _vc2 = _sq3_vac2.connect(str(_sq_path_live), check_same_thread=False)
                            _vc2.execute("VACUUM")
                            _vc2.close()
                            _new_sz2 = _sq_path_live.stat().st_size / 1_048_576
                            st.success(f"VACUUM complete. live_arbs.db is now {_new_sz2:.2f} MB.")
                        else:
                            st.warning("live_arbs.db not found.")
                    except Exception as _ve2:
                        st.error(f"VACUUM failed: {_ve2}")

            st.markdown("---")
            st.caption(
                "Pre-fix ghost arbs (net_edge_cents > 50c) were written by an earlier version "
                "of the arb scanner before a bug fix capped edge values correctly. "
                "These rows inflate totals in live_arbs_cloud (Neon) and live_arbs (SQLite). "
                "Use the buttons below to inspect and optionally remove them."
            )
            if st.button("Run dry run", key="purge_dryrun"):
                _res = purge_prefixarbs(dry_run=True)
                _msg = f"Would delete {_res.get('would_delete', 0):,} ghost arb row(s) (net_edge_cents > 50c)."
                if _res.get("pg_error"):
                    _msg += f"  PG error: {_res['pg_error']}"
                if _res.get("sqlite_error"):
                    _msg += f"  SQLite error: {_res['sqlite_error']}"
                st.info(_msg)
            st.warning("⚠️ The button below permanently deletes ghost arb rows from both databases.")
            if st.button("Purge ghost arbs from DB", key="purge_execute"):
                _res2 = purge_prefixarbs(dry_run=False)
                _msg2 = f"Deleted {_res2.get('deleted', 0):,} ghost arb row(s) (net_edge_cents > 50c)."
                if _res2.get("pg_error"):
                    _msg2 += f"  PG error: {_res2['pg_error']}"
                if _res2.get("sqlite_error"):
                    _msg2 += f"  SQLite error: {_res2['sqlite_error']}"
                st.success(_msg2)

    with e2:
        if _is_sqlite:
            _section_header("ARCHITECTURE")
            _ws_connected_now = ws_stats.get("connected", False)
            _ws_line = (
                f"Synthesis WS &rarr; LiveState (LIVE &mdash; {ws_stats.get('markets_tracked', 0):,} markets)"
                if _ws_connected_now else
                "Synthesis WS &rarr; LiveState (offline)"
            )
            _neon_line = "Arb Scanner &rarr; live_arbs_cloud (Neon)" if _ws_connected_now else "Arb Scanner (waiting for WS)"
            st.markdown(
                f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:0.75rem 1rem;
border-radius:3px;font-family:JetBrains Mono,monospace;font-size:0.7rem;
color:{TEXT2};line-height:1.9;'>
<span style='color:{TEXT3};'>// Live data flow</span><br>
{_ws_line}<br>
&nbsp;&nbsp;&rarr; {_neon_line}<br><br>
<span style='color:{TEXT3};'>// Reference DB (historical)</span><br>
{int(health.get('arb_opportunities_open', 0) or 0):,} historical records &middot; {int(health.get('markets_total', 0) or 0):,} markets &middot; {int(health.get('relationships_total', 0) or 0):,} rels<br><br>
<span style='color:{TEXT3};'>// Live arbs persisted to Neon PostgreSQL (live_arbs_cloud)</span>
</div>""",
                unsafe_allow_html=True,
            )
        else:
            _section_header("TECHNICAL ARCHITECTURE")
            st.markdown(
                f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:0.75rem 1rem;
border-radius:3px;font-family:JetBrains Mono,monospace;font-size:0.72rem;
color:{TEXT2};line-height:1.8;'>
<span style='color:{TEXT3};'>// Data feed</span><br>
Synthesis WebSocket &rarr; LiveState singleton (thread-safe, ~3,400 msg/s)<br><br>
<span style='color:{TEXT3};'>// Arbitrage detection</span><br>
YNC: buy YES+NO when sum &lt; 1.00 — scanner runs; live YNC sum &ge; 1.00 always (structural), so detections are feed artifacts only<br>
CE: buy all YES legs of an event when sum &lt; 1.00 (collectively exhaustive set) — <span style='color:{AMBER};'>currently disabled</span><br>
ME: buy all NO legs of an event when sum &lt; N−1 (mutually exclusive set)<br>
TH: buy YES(superset) + NO(subset) when threshold legs are mispriced<br>
<span style='color:{AMBER};'>Kalshi YES/NO are complements; YNC sum=1+spread &ge; 1.0 always. CE is the viable path once re-enabled.</span><br>
Dual-write: SQLite (local) + Neon PostgreSQL (cloud)<br><br>
<span style='color:{TEXT3};'>// Dashboard</span><br>
Streamlit Cloud &mdash; 11 pages &mdash; auto-refresh 10s
</div>""",
                unsafe_allow_html=True,
            )

    # --- Database row counts (Neon primary / SQLite fallback) -----
    st.markdown("<hr>", unsafe_allow_html=True)
    _section_header("DATABASE STATUS")
    # Try Neon first (primary on Streamlit Cloud)
    _p10_db_shown = False
    try:
        import dashboard.live_arb_store as _las_p10_rc
        from sqlalchemy import text as _p10_rc_text
        _p10_rc_eng = (
            getattr(_las_p10_rc, "_pg_engine", None)
            or _las_p10_rc.get_pg_engine_cached()
        )
        if _p10_rc_eng is not None:
            _p10_neon_tables = [
                ("live_arbs_cloud", "Neon arb records (cloud-persisted)"),
                ("event_series_classifications", "Gate 0 market type classifications"),
            ]
            _p10_neon_rows = []
            with _p10_rc_eng.connect() as _p10_rc_c:
                for _tbl, _desc in _p10_neon_tables:
                    try:
                        _r = _p10_rc_c.execute(_p10_rc_text(f"SELECT COUNT(*) FROM {_tbl}")).fetchone()
                        if _r:
                            _p10_neon_rows.append({"TABLE": _tbl, "ROW COUNT": int(_r[0] or 0), "DESCRIPTION": _desc})
                    except Exception:
                        pass
            if _p10_neon_rows:
                _p10_neon_df = pd.DataFrame(_p10_neon_rows)
                _p10_neon_df["ROW COUNT"] = _p10_neon_df["ROW COUNT"].apply(lambda v: f"{int(v):,}")
                st.markdown(
                    f"<div style='font-size:0.6rem;letter-spacing:0.08em;text-transform:uppercase;"
                    f"color:#22C55E;margin-bottom:0.3rem;'>● NEON POSTGRESQL TABLES</div>",
                    unsafe_allow_html=True,
                )
                st.dataframe(_p10_neon_df, use_container_width=True, hide_index=True)
                _p10_db_shown = True
    except Exception:
        pass
    # SQLite fallback
    if not _p10_db_shown:
        try:
            import sqlite3 as _sq3_rc
            from dashboard.data_layer import _SQLITE_PATH as _rc_path
            if _rc_path.exists():
                _rc_conn = _sq3_rc.connect(str(_rc_path), check_same_thread=False)
                _rc_tables = [
                    r[0] for r in _rc_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                ]
                _rc_rows = []
                for _t in _rc_tables:
                    try:
                        _cnt = _rc_conn.execute(f"SELECT COUNT(*) FROM \"{_t}\"").fetchone()[0]
                        _rc_rows.append({"TABLE": _t, "ROW COUNT": _cnt})
                    except Exception:
                        pass
                _rc_conn.close()
                if _rc_rows:
                    _rc_df = (
                        pd.DataFrame(_rc_rows)
                        .sort_values("ROW COUNT", ascending=False)
                        .head(10)
                        .reset_index(drop=True)
                    )
                    _rc_df["ROW COUNT"] = _rc_df["ROW COUNT"].apply(lambda v: f"{int(v):,}")
                    st.dataframe(_rc_df, use_container_width=True, hide_index=True)
                else:
                    st.info("No tables found in SQLite database.")
            else:
                st.info("No local database on Streamlit Cloud — Neon PostgreSQL is the primary store.")
        except Exception:
            st.info("Database unavailable")

    # --- Neon PostgreSQL status panel -----
    _neon_count = get_live_arbs_cloud_count()
    # Cache may have warmed with 0 before Neon connected — bypass with direct query
    if _neon_count == 0:
        try:
            import dashboard.live_arb_store as _las_p10_cnt
            from sqlalchemy import text as _p10_cnt_text
            _p10_cnt_eng = (
                getattr(_las_p10_cnt, "_pg_engine", None)
                or _las_p10_cnt.get_pg_engine_cached()
            )
            if _p10_cnt_eng is not None:
                with _p10_cnt_eng.connect() as _p10_cnt_c:
                    _p10_cnt_r = _p10_cnt_c.execute(_p10_cnt_text("SELECT COUNT(*) FROM live_arbs_cloud")).fetchone()
                if _p10_cnt_r and _p10_cnt_r[0]:
                    _neon_count = int(_p10_cnt_r[0])
        except Exception:
            pass
    if _neon_count >= 0:
        _nc1, _nc2, _nc3, _nc4 = st.columns(4)
        _nc1.metric("NEON PG ROWS", f"{_neon_count:,}")
        # Pull best/avg edge from Neon for richer display
        try:
            import dashboard.live_arb_store as _las_p10b
            from sqlalchemy import text as _p10_text
            _p10_eng = getattr(_las_p10b, "_pg_engine", None) or _las_p10b.get_pg_engine_cached()
            if _p10_eng is not None:
                with _p10_eng.connect() as _p10_c:
                    _p10_r = _p10_c.execute(_p10_text(
                        "SELECT MAX(net_edge_cents), AVG(net_edge_cents), "
                        "COUNT(*) FILTER (WHERE strategy_type='mutually_exclusive'), "
                        "COUNT(*) FILTER (WHERE strategy_type='threshold_order') "
                        "FROM live_arbs_cloud WHERE strategy_type != 'collectively_exhaustive'"
                    )).fetchone()
                if _p10_r and _p10_r[0] is not None:
                    _nc2.metric("BEST EDGE", f"{float(_p10_r[0]):.2f}c")
                    _nc3.metric("AVG EDGE", f"{float(_p10_r[1]):.2f}c")
                    _nc4.metric("ME / TH", f"{int(_p10_r[2])} / {int(_p10_r[3])}")
        except Exception:
            pass
    else:
        st.error("Neon PostgreSQL unavailable")

    # --- DB Connectivity Test button -----
    st.markdown("<hr>", unsafe_allow_html=True)
    _section_header("DB CONNECTIVITY TEST")
    if st.button("🔌 Test DB Connections", key="p10_test_db_connections"):
        import sqlite3 as _sq3_test
        import time as _t_test
        from dashboard.data_layer import _SQLITE_PATH as _sqlite_test_path

        _sqlite_ok = False
        _sqlite_ms = None
        _sqlite_err = None
        try:
            _t0_sq = _t_test.perf_counter()
            _sq_c = _sq3_test.connect(str(_sqlite_test_path), check_same_thread=False)
            _sq_c.execute("SELECT 1").fetchone()
            _sq_c.close()
            _sqlite_ms = (_t_test.perf_counter() - _t0_sq) * 1000
            _sqlite_ok = True
        except Exception as _sq_e:
            _sqlite_err = str(_sq_e)

        _pg_ok = False
        _pg_ms = None
        _pg_count = None
        _pg_err = None
        try:
            import sys as _sys_ct, os as _os_ct
            _sys_ct.path.insert(0, _os_ct.path.dirname(_os_ct.path.dirname(_os_ct.path.abspath(__file__))))
            from database.repository import get_engine as _get_engine_ct
            from sqlalchemy import text as _text_ct
            _t0_pg = _t_test.perf_counter()
            _eng_ct = _get_engine_ct()
            with _eng_ct.connect() as _conn_ct:
                try:
                    _pg_count = int(_conn_ct.execute(_text_ct("SELECT COUNT(*) FROM live_arbs_cloud")).scalar() or 0)
                except Exception:
                    _pg_count = int(_conn_ct.execute(_text_ct("SELECT COUNT(*) FROM arbs")).scalar() or 0)
            _pg_ms = (_t_test.perf_counter() - _t0_pg) * 1000
            _pg_ok = True
        except Exception as _pg_e:
            _pg_err = str(_pg_e)

        st.session_state["p10_db_test_ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        st.session_state["p10_db_test_results"] = {
            "sqlite_ok": _sqlite_ok, "sqlite_ms": _sqlite_ms, "sqlite_err": _sqlite_err,
            "pg_ok": _pg_ok, "pg_ms": _pg_ms, "pg_count": _pg_count, "pg_err": _pg_err,
        }
        st.rerun()

    _db_test_results = st.session_state.get("p10_db_test_results")
    _db_test_ts = st.session_state.get("p10_db_test_ts")
    if _db_test_results:
        _dbt_c1, _dbt_c2 = st.columns(2)
        with _dbt_c1:
            _sq_ok = _db_test_results["sqlite_ok"]
            _sq_ms = _db_test_results["sqlite_ms"]
            _sq_err = _db_test_results["sqlite_err"]
            if _sq_ok:
                st.success(f"✅ SQLite — {_sq_ms:.1f} ms")
            else:
                st.error(f"❌ SQLite — {_sq_err}")
        with _dbt_c2:
            _pg_ok2 = _db_test_results["pg_ok"]
            _pg_ms2 = _db_test_results["pg_ms"]
            _pg_count2 = _db_test_results["pg_count"]
            _pg_err2 = _db_test_results["pg_err"]
            if _pg_ok2:
                st.success(f"✅ Neon PG — {_pg_ms2:.1f} ms · {_pg_count2:,} rows")
            else:
                st.error(f"❌ Neon PG — {_pg_err2}")
        if _db_test_ts:
            st.caption(f"Last tested: {_db_test_ts}")
        if not _db_test_results["sqlite_ok"] and not _db_test_results["pg_ok"]:
            st.error("⚠️ All DB connections failed — system running in memory-only mode")
        elif not _db_test_results["sqlite_ok"] or not _db_test_results["pg_ok"]:
            st.warning("⚠️ Partial DB connectivity — some persistence features degraded")

    # --- Table sizes -----
    st.markdown("<hr>", unsafe_allow_html=True)
    _section_header("STORAGE USAGE")

    _TABLE_DESCRIPTIONS = {
        "live_arbs_cloud":      "Live detections — ME/TH arbs + YNC feed artifacts (Neon)",
        "kalshi_markets":       "Market metadata",
        "kalshi_events":        "Event metadata",
        "kalshi_trades":        "Trade history",
        "kalshi_relationships": "Market relationships (CE/ME/etc.)",
        "kalshi_arb_opportunities": "Historical detections (ME/TH arbs + YNC feed artifacts)",
        "l2_snapshots":         "Order book snapshots",
        "ingestion_log":        "Pipeline run history",
        "ext_market_daily":     "External price data (yfinance/BOC)",
    }

    table_df = get_table_sizes()
    if not table_df.empty:
        display = table_df.copy()
        if "row_count" in display.columns:
            display["row_count"] = display["row_count"].apply(
                lambda v: f"{int(v):,}" if pd.notna(v) else "--"
            )
        if "table_name" in display.columns:
            display["description"] = display["table_name"].map(
                lambda t: _TABLE_DESCRIPTIONS.get(t, "")
            )
        show_tbl_cols = [c for c in ["table_name", "description", "row_count", "total_size"] if c in display.columns]
        st.dataframe(
            display[show_tbl_cols].rename(columns={
                "table_name": "TABLE",
                "description": "DESCRIPTION",
                "row_count": "ROWS",
                "total_size": "SIZE",
            }),
            use_container_width=True,
            height=350,
            hide_index=True,
        )

        # Size chart (skip when SQLite fallback returns all-zero size_bytes)
        top_tables = table_df.nlargest(10, "size_bytes") if "size_bytes" in table_df.columns else pd.DataFrame()
        if not top_tables.empty and top_tables["size_bytes"].sum() > 0:
            fig = go.Figure(go.Bar(
                y=top_tables["table_name"],
                x=top_tables["size_bytes"] / 1e6,
                orientation="h",
                marker_color=BLUE,
                marker_line_width=0,
            ))
            fig.update_layout(
                **plotly_dark_layout(
                title={"text": "TABLE SIZES (MB)", "font": {"size": 10, "color": TEXT3}},
                height=280,
                xaxis_title="MB", yaxis_title="",
                yaxis={"autorange": "reversed", "tickfont": {"size": 9}},
            ))
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.markdown(
            f"<div style='color:{TEXT3};font-size:0.75rem;font-family:JetBrains Mono,monospace;"
            f"background:{PANEL};border:1px solid {BORDER};padding:0.75rem;border-radius:3px;'>"
            f"No storage data available — pg_stat_user_tables returned empty. "
            f"This clears on reconnect; table sizes appear once the analytics engine is fully warmed up.</div>",
            unsafe_allow_html=True,
        )

    # --- Data quality section -----
    st.markdown("<hr>", unsafe_allow_html=True)
    _section_header("DATA QUALITY")

    dq = get_data_quality_stats()
    dq_err = dq.get("error")

    if dq_err:
        st.markdown(
            f"<div style='color:{TEXT3};font-size:0.72rem;font-family:JetBrains Mono,monospace;"
            f"background:{PANEL};border:1px solid {BORDER};padding:0.6rem;border-radius:3px;'>"
            f"Data quality stats unavailable — {dq_err}</div>",
            unsafe_allow_html=True,
        )
    else:
        dq1, dq2 = st.columns(2)

        with dq1:
            # Build as one HTML block to avoid stray </div> leaks across separate st.markdown calls
            def _dq_flag_html(n: int, label: str, warn_at: int = 1) -> str:
                color = RED if n >= warn_at else GREEN
                icon  = "X" if n >= warn_at else "OK"
                return (
                    f"<div style='display:flex;justify-content:space-between;align-items:center;"
                    f"padding:5px 0.8rem;border-bottom:1px solid {BORDER};'>"
                    f"<span style='font-size:0.6rem;letter-spacing:0.08em;text-transform:uppercase;"
                    f"color:{TEXT3};font-family:Inter,sans-serif;'>{label}</span>"
                    f"<span style='font-family:JetBrains Mono,monospace;font-size:0.73rem;color:{color};'>"
                    f"{icon}  {n:,}</span></div>"
                )
            _dq1_html = (
                f"<div style='background:{PANEL};border:1px solid {BORDER};border-radius:3px;padding:0.4rem 0;'>"
                + _dq_flag_html(dq.get("crossed_books", 0),        "CROSSED BOOKS")
                + _dq_flag_html(dq.get("impossible_prices", 0),    "IMPOSSIBLE PRICES")
                + _dq_flag_html(dq.get("duplicate_trades", 0),     "DUPLICATE TRADES")
                + _dq_flag_html(dq.get("orphan_relationships", 0), "ORPHAN RELATIONSHIPS")
                + "</div>"
            )
            st.markdown(_dq1_html, unsafe_allow_html=True)

        with dq2:
            rows_24h = dq.get("ingestion_rows_24h", 0)
            runs_24h = dq.get("ingestion_runs_24h", 0)
            _sqlite_alltime = dq.get("ingestion_sqlite_alltime", False)
            color_rows = GREEN if rows_24h > 0 else AMBER
            _ing_label = "INGESTION HEALTH (ALL TIME)" if _sqlite_alltime else "INGESTION HEALTH (LAST 24H)"
            st.markdown(
                f"""<div style='background:{PANEL};border:1px solid {BORDER};border-radius:3px;
padding:0.75rem 1rem;'>
<div style='font-size:0.6rem;letter-spacing:0.1em;color:{TEXT3};
text-transform:uppercase;font-family:Inter,sans-serif;'>
{_ing_label}
</div>
<div style='font-family:JetBrains Mono,monospace;font-size:1.1rem;
color:{color_rows};margin-top:6px;'>
{rows_24h:,} rows
</div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.7rem;
color:{TEXT3};margin-top:2px;'>
{runs_24h:,} ingestion runs
</div>
</div>""",
                unsafe_allow_html=True,
            )

    # --- Performance snapshot -----
    st.markdown("<hr>", unsafe_allow_html=True)
    _section_header("PERFORMANCE BENCHMARKS")
    st.markdown(
        f"<div style='font-size:0.7rem;color:{TEXT2};margin-bottom:0.5rem;'>"
        "Times three key data-layer queries (live detections, historical detection summary, "
        "and 7-day rolling trend). Cached results return ~0 ms after the first call; "
        "cold PostgreSQL connections may show 500&ndash;2000 ms due to Neon wake-up latency."
        "</div>",
        unsafe_allow_html=True,
    )
    if st.button("RUN BENCHMARKS", help="Execute timing benchmarks for key DB queries (cached results return ~0ms)"):
        _render_performance_panel()
    else:
        st.caption("Click RUN BENCHMARKS to time key database queries.")

    # --- Ingestion job summary -----
    st.markdown("<hr>", unsafe_allow_html=True)
    _section_header("INGESTION JOB SUMMARY")
    try:
        from dashboard.data_layer import _sqlite_conn as _gsc_ingest
        _ic = _gsc_ingest()
        if _ic:
            _job_rows = _ic.execute("""
SELECT job_type,
COUNT(*) AS runs,
SUM(CAST(rows_inserted AS INTEGER)) AS total_rows,
SUM(CAST(rows_skipped AS INTEGER)) AS total_skipped,
SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS successes
FROM ingestion_log
GROUP BY job_type
ORDER BY total_rows DESC
""").fetchall()
            _ic.close()
            if _job_rows:
                _jdf = pd.DataFrame(
                    _job_rows,
                    columns=["job_type", "runs", "total_rows", "total_skipped", "successes"],
                )
                _jdf["total_rows"]    = pd.to_numeric(_jdf["total_rows"],    errors="coerce").fillna(0).astype(int)
                _jdf["total_skipped"] = pd.to_numeric(_jdf["total_skipped"], errors="coerce").fillna(0).astype(int)
                _jdf["runs"]          = pd.to_numeric(_jdf["runs"],          errors="coerce").fillna(0).astype(int)
                _jdf["successes"]     = pd.to_numeric(_jdf["successes"],     errors="coerce").fillna(0).astype(int)
                _jdf["success_rate"]  = (_jdf["successes"] / _jdf["runs"].clip(lower=1) * 100).round(1)

                _js1, _js2 = st.columns(2)
                with _js1:
                    # Bar chart: rows inserted per job type
                    _fig_jb = go.Figure(go.Bar(
                        x=_jdf["total_rows"],
                        y=_jdf["job_type"],
                        orientation="h",
                        marker_color=[GREEN if v > 0 else TEXT3 for v in _jdf["total_rows"]],
                        marker_line_width=0,
                        text=_jdf["total_rows"].apply(lambda v: f"{int(v):,}"),
                        textposition="outside",
                        textfont={"size": 8, "family": "JetBrains Mono"},
                    ))
                    _fig_jb.update_layout(**plotly_dark_layout(
                        title={"text": "TOTAL ROWS INSERTED BY JOB TYPE", "font": {"size": 10, "color": TEXT3}},
                        height=max(200, len(_jdf) * 35 + 60),
                        xaxis_title="Rows",
                        yaxis={"autorange": "reversed", "tickfont": {"size": 9}},
                        margin={"l": 10, "r": 80, "t": 30, "b": 30},
                    ))
                    st.plotly_chart(_fig_jb, use_container_width=True)

                with _js2:
                    # Summary table
                    _jdisp = _jdf.copy()
                    _jdisp["total_rows"]    = _jdisp["total_rows"].apply(lambda v: f"{v:,}")
                    _jdisp["total_skipped"] = _jdisp["total_skipped"].apply(lambda v: f"{v:,}")
                    _jdisp["success_rate"]  = _jdisp["success_rate"].apply(lambda v: f"{v:.0f}%")
                    st.dataframe(
                        _jdisp[["job_type", "runs", "total_rows", "success_rate"]].rename(columns={
                            "job_type": "JOB", "runs": "RUNS",
                            "total_rows": "ROWS INSERTED", "success_rate": "SUCCESS",
                        }),
                        use_container_width=True,
                        height=min(250, len(_jdf) * 35 + 45),
                        hide_index=True,
                    )
                    _total_rows_all = sum(_jdf["total_rows"])
                    st.caption(
                        f"Total: {_total_rows_all:,} rows across {sum(_jdf['runs']):,} runs "
                        f"({len(_jdf)} job types). A job showing 0 rows inserted means its data was already up to date."
                    )
    except Exception as _jingest_e:
        st.markdown(
            f"<div style='color:{TEXT3};font-size:0.72rem;font-family:JetBrains Mono,monospace;"
            f"background:{PANEL};border:1px solid {BORDER};padding:0.75rem;border-radius:3px;'>"
            f"Ingestion summary unavailable — ingestion_log table not yet populated. "
            f"Connect the data pipeline to start ingesting historical data."
            f"</div>",
            unsafe_allow_html=True,
        )

    # --- Ingestion log -----
    log_df, log_err = get_ingestion_log(limit=50)
    if log_df.empty:
        st.markdown("<hr>", unsafe_allow_html=True)
        _section_header("INGESTION LOG (LAST 50)")
        if _is_sqlite:
            st.markdown(
                f"<div style='color:{TEXT3};font-size:0.72rem;font-family:JetBrains Mono,monospace;"
                f"background:{PANEL};border:1px solid {BORDER};padding:0.75rem;border-radius:3px;'>"
                f"Ingestion log will populate once the data pipeline is connected and running.</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div style='color:{TEXT3};font-size:0.72rem;font-family:JetBrains Mono,monospace;"
                f"background:{PANEL};border:1px solid {BORDER};padding:0.75rem;border-radius:3px;'>"
                f"No ingestion log entries found.</div>",
                unsafe_allow_html=True,
            )
    if not log_df.empty:
        st.markdown("<hr>", unsafe_allow_html=True)
        _section_header("INGESTION LOG (LAST 50)")
        display_log = log_df.copy()
        if "run_ts" in display_log.columns:
            display_log["run_ts"] = pd.to_datetime(display_log["run_ts"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(
            display_log.rename(columns={
                "job_type": "JOB", "target": "TARGET",
                "rows_inserted": "INSERTED", "rows_skipped": "SKIPPED",
                "status": "STATUS", "error_message": "ERROR", "run_ts": "TIMESTAMP",
            }),
            use_container_width=True,
            height=280,
            hide_index=True,
        )

    # --- External market data status -----
    st.markdown("<hr>", unsafe_allow_html=True)
    _section_header("EXTERNAL MARKET DATA (yfinance + BOC VALET)")
    _ext_stats = get_ext_market_daily_stats()
    if _ext_stats.get("n_assets", 0) > 0:
        _ea1, _ea2, _ea3, _ea4 = st.columns(4)
        _ea1.metric("ASSETS LOADED", str(_ext_stats["n_assets"]))
        _ea2.metric("TOTAL ROWS", f"{_ext_stats['n_rows']:,}")
        _ea3.metric("DATE RANGE", f"{_ext_stats['earliest']} → {_ext_stats['latest']}")
        _ea4.metric("LAST FETCH", _ext_stats["last_fetch"][:16] if _ext_stats.get("last_fetch") else "--")
    else:
        st.markdown(
            f"<div style='color:{AMBER};font-size:0.72rem;font-family:JetBrains Mono,monospace;"
            f"background:{PANEL};border:1px solid {BORDER};padding:0.75rem;border-radius:3px;'>"
            f"No external market data loaded yet. Connect the cross-asset data pipeline to populate this section."
            f"</div>",
            unsafe_allow_html=True,
        )


    # --- Error Log expander -----
    st.markdown("<hr>", unsafe_allow_html=True)
    with st.expander("🔴 Error Log (last 50)", expanded=False):
        _error_log = st.session_state.get("_error_log", [])

        # Severity filter
        _sev_filter = st.selectbox(
            "Filter by severity",
            ["All", "ERROR", "WARNING", "INFO"],
            key="p10_error_log_severity",
            label_visibility="visible",
        )

        if not _error_log:
            st.success("No errors logged this session ✅")
        else:
            # Build rows with naive severity inference from message text
            def _infer_severity(msg: str) -> str:
                _m = str(msg).upper()
                if any(k in _m for k in ("ERROR", "EXCEPTION", "TRACEBACK", "FAILED", "CRITICAL")):
                    return "ERROR"
                if any(k in _m for k in ("WARNING", "WARN", "DEPRECATED", "CAUTION")):
                    return "WARNING"
                return "INFO"

            _log_rows_all = [
                {
                    "TIMESTAMP": str(ts),
                    "SEVERITY": _infer_severity(msg),
                    "ERROR": msg,
                }
                for ts, msg in reversed(_error_log[-50:])
            ]

            # Apply filter
            if _sev_filter != "All":
                _log_rows_filtered = [r for r in _log_rows_all if r["SEVERITY"] == _sev_filter]
            else:
                _log_rows_filtered = _log_rows_all

            if _log_rows_filtered:
                _log_df_display = pd.DataFrame(_log_rows_filtered)
                st.dataframe(_log_df_display, use_container_width=True, hide_index=True)
                st.download_button(
                    label="⬇ Download log as CSV",
                    data=_log_df_display.to_csv(index=False).encode(),
                    file_name=f"kalshi_error_log_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    key="p10_download_error_log",
                )
            else:
                st.info(f"No {_sev_filter} entries found in the error log.")

        st.caption("Errors are logged here during WebSocket operation. Empty = no errors this session.")

    # --- Streamlit Cloud setup expander -----
    st.markdown("<hr>", unsafe_allow_html=True)
    with st.expander("⚙️ STREAMLIT CLOUD SETUP — Required Secrets", expanded=False):
        st.markdown(
            f"""<div style='font-family:JetBrains Mono,monospace;font-size:0.75rem;
color:{TEXT2};line-height:1.8;'>
To enable the live arb scanner on Streamlit Cloud, set these secrets in<br>
<strong>Settings → Secrets</strong>:<br><br>
<span style='color:{GREEN};'>SYNTHESIS_SECRET_KEY</span> = <span style='color:{AMBER};'>"your-synthesis-api-key"</span><br>
<span style='color:{GREEN};'>DATABASE_URL</span> = <span style='color:{AMBER};'>"postgresql://user:pass@host/dbname"</span><br><br>
<span style='color:{TEXT3};'>Without <strong>SYNTHESIS_SECRET_KEY</strong>: the WS scanner won't start (dashboard shows
DB data only, no live feed).</span><br><br>
<span style='color:{TEXT3};'>Without <strong>DATABASE_URL</strong>: arbs are stored in SQLite (local, not persisted across
restarts on Streamlit Cloud's ephemeral filesystem).</span>
</div>""",
            unsafe_allow_html=True,
        )

    # --- Page-level auto-refresh (10s, live WS only) -----
    # Use timestamp throttle — never sleep() in Streamlit's main thread.
    import time as _t10
    _now10 = _t10.time()
    if auto_refresh and ws_stats.get("connected", False):
        if st.session_state.get("_p10_next_refresh", 0) <= _now10:
            st.session_state["_p10_next_refresh"] = _now10 + 10
            st.rerun()
    elif auto_refresh:
        st.caption("Auto-refresh paused — WebSocket offline. Refresh manually.")


def _render_performance_panel():
    """Show in-process timing benchmarks for key dashboard queries."""
    import time
    from dashboard.data_layer import (
        get_live_arb_opportunities, get_historical_arb_stats,
        get_arb_rolling_7d,
    )

    benchmarks = []
    for label, fn, kwargs in [
        ("Live detections (all strategies)",   get_live_arb_opportunities, {}),
        ("Historical detection summary",      get_historical_arb_stats,   {}),
        ("Rolling 7-day trend",      get_arb_rolling_7d,         {}),
    ]:
        t0 = time.perf_counter()
        try:
            fn(**kwargs)
            ms = (time.perf_counter() - t0) * 1000
            benchmarks.append({"QUERY": label, "LATENCY": f"{ms:.1f} ms", "STATUS": "OK", "_ms": ms})
        except Exception as exc:
            benchmarks.append({"QUERY": label, "LATENCY": "--", "STATUS": "ERROR", "_ms": 0})

    if benchmarks:
        p1, p2, p3 = st.columns(3)
        for col, row in zip([p1, p2, p3], benchmarks):
            color = GREEN if row["_ms"] < 100 else (AMBER if row["_ms"] < 500 else RED)
            col.metric(row["QUERY"][:24], row["LATENCY"], delta=row["STATUS"] if row["STATUS"] != "OK" else None)

    st.markdown(
        f"<div style='font-size:0.6rem;color:{TEXT3};font-family:JetBrains Mono,monospace;margin-top:0.25rem;'>"
        f"Latency measured from Streamlit process. "
        f"(cached — connection latency includes TCP handshake + Neon cold-start; hot connections are faster). "
        f"Cached queries return ~0ms after first call.</div>",
        unsafe_allow_html=True,
    )


def _section_header(title: str):
    from dashboard.styles import TEXT3
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.12em;text-transform:uppercase;"
        f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>{title}</div>",
        unsafe_allow_html=True,
    )


def _stat_panel(rows: list):
    from dashboard.styles import PANEL, BORDER, TEXT3
    html = f"<div style='background:{PANEL};border:1px solid {BORDER};border-radius:3px;padding:0.4rem 0;'>"
    for label, (value, color) in rows:
        html += f"""
<div style='display:flex;justify-content:space-between;align-items:center;
padding:5px 0.8rem;border-bottom:1px solid {BORDER};'>
<span style='font-size:0.6rem;letter-spacing:0.08em;text-transform:uppercase;
color:{TEXT3};font-family:Inter,sans-serif;'>{label}</span>
<span style='font-family:JetBrains Mono,monospace;font-size:0.73rem;
color:{color};'>{value}</span>
</div>"""
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

