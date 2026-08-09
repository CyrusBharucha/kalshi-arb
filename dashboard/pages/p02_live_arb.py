"""
dashboard/pages/p02_live_arb.py
================================
Live Arbitrage Opportunities page.

Displays all currently open arbitrage opportunities from arbitrage_opportunities
(status='open') with their classification, edge, quantity, and lifecycle status.

Opportunity Classifications (from lifecycle.py):
A = EXECUTABLE      -- depth confirmed, qty available
B = PENDING DEPTH   -- top-of-book only, depth unverified
C = RELATIVE VALUE  -- theoretical (no live book confirmation)
D = NEG AFTER FEES  -- gross edge exists but fee-negative

Opportunity Lifecycle Statuses:
OPEN        -- currently active, edge present
STALE       -- last update > 30s ago (possible stale book)
DISAPPEARED -- edge gone, lifecycle manager closed it
SETTLED     -- market settled, opportunity resolved

Falls back to live WebSocket complement-arb scan when database is unavailable.
"""
from __future__ import annotations
import html as _html
import json
import urllib.request
from datetime import datetime, timezone, timedelta
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_SPORTS_PFX = (
    "KXLIGA", "KXLALIGA", "KXNBA", "KXNFL", "KXMLB", "KXNHL", "KXEPL",
    "KXSERIEA", "KXBUNDES", "KXMLS", "KXUCL", "KXUEFA", "KXNCAAF",
    "KXNCAAB", "KXWNBA", "KXPGA", "KXTENNIS", "KXFORMULA", "KXSOCCER",
    "KXCRICKET", "KXRUGBY", "KXGOLF", "KXUFC", "KXBOXING", "KXMMA",
)

def _is_sports_ticker(t: str) -> bool:
    u = t.upper()
    return any(u.startswith(p) for p in _SPORTS_PFX)

from dashboard.data_layer import (
    get_live_arb_opportunities, get_opportunity_detail,
    get_recently_closed_opps,
)
from dashboard.styles import plotly_dark_layout, GREEN, RED, AMBER, BLUE, CYAN, TEXT, TEXT2, TEXT3, PANEL, BORDER, PANEL2

_KALSHI_EVENTS_URL = "https://api.elections.kalshi.com/trade-api/v2/events"


def _title_to_slug_p02(title: str) -> str:
    import re as _re_slug
    s = title.lower().strip()
    s = _re_slug.sub(r"[^a-z0-9\s-]", "", s)
    s = _re_slug.sub(r"\s+", "-", s)
    return _re_slug.sub(r"-+", "-", s).strip("-")


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_event_info_p02(event_ticker: str) -> dict:
    """Fetch title + correct Kalshi URL for an event."""
    try:
        url = f"{_KALSHI_EVENTS_URL}/{event_ticker}?with_nested_markets=true"
        req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            ev = json.loads(resp.read()).get("event", {})
        title = ev.get("title") or ""
        series = (ev.get("series_ticker") or event_ticker).lower()
        slug = ev.get("slug") or _title_to_slug_p02(title)
        kalshi_url = (f"https://kalshi.com/markets/{series}/{slug}" if slug
                      else f"https://kalshi.com/markets/{series}")
        return {"title": title, "kalshi_url": kalshi_url}
    except Exception:
        return {"title": "", "kalshi_url": ""}


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_event_title(event_ticker: str) -> str:
    """Fetch and cache the human-readable event title from Kalshi public API."""
    return _fetch_event_info_p02(event_ticker).get("title") or ""


def _kalshi_event_url(event_ticker: str) -> str:
    """Return the correct Kalshi URL for an event, falling back to series page."""
    info = _fetch_event_info_p02(event_ticker)
    if info.get("kalshi_url"):
        return info["kalshi_url"]
    # Fallback: strip event suffix to get series ticker (e.g. KXFOO-MT02 → kxfoo)
    series = event_ticker.upper().rsplit("-", 1)[0].lower()
    return f"https://kalshi.com/markets/{series}"


_STRATEGIES = [
    "All", "mutually_exclusive", "superset", "threshold_order", "collectively_exhaustive",
    "yes_no_complement",
]

_STATUS_COLORS = {
    "A": (GREEN, "EXECUTABLE"),
    "B": (AMBER, "PENDING DEPTH"),
    "C": (BLUE,  "RELATIVE VALUE"),
    "D": (RED,   "NEG AFTER FEES"),
}

# Age thresholds for stale/warning indicators
_STALE_AGE_S  = 30    # seconds before showing STALE warning
_ALERT_AGE_S  = 120   # seconds before showing AGE ALERT


def render():
    st.markdown("""
<div style='margin-bottom:0.5rem;'>
<span style='font-size:1rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;'>
LIVE ARBITRAGE
</span>
<span style='font-size:0.68rem;color:#64748B;letter-spacing:0.04em;margin-left:1rem;'>
Real-time executable opportunities
</span>
</div>
""", unsafe_allow_html=True)


    # -- Detect SQLite mode early for controls --
    from dashboard.data_layer import get_system_health as _gh_early
    _h_early = _gh_early()
    _is_sqlite_early = not _h_early.get("db_connected", False) and _h_early.get("db_mode") == "sqlite"

    # -- Auto-refresh controls --
    rc1, rc2, rc3 = st.columns([1, 1, 4])
    with rc1:
        auto_refresh = st.checkbox("AUTO REFRESH", value=True, key="live_arb_auto_refresh")
    with rc2:
        refresh_interval = st.selectbox(
            "INTERVAL", [10, 15, 30, 60], index=1,
            format_func=lambda s: f"{s}s",
            key="live_arb_interval",
            label_visibility="collapsed",
        )
    with rc3:
        from datetime import datetime as _dt
        now_utc = _dt.now(timezone.utc).strftime("%H:%M:%S UTC")
        st.markdown(
            f"<div style='font-size:0.62rem;font-family:JetBrains Mono,monospace;"
            f"color:{TEXT3};padding-top:0.6rem;'>last refresh: {now_utc}</div>",
            unsafe_allow_html=True,
        )

    # -- Filters --
    fc1, fc2, fc3, fc4, fc5 = st.columns([2, 1.5, 1.5, 1, 1.5])

    with fc1:
        strategy = st.selectbox("STRATEGY", _STRATEGIES, label_visibility="visible")
        strategy_arg = None if strategy == "All" else strategy

    with fc2:
        min_edge = st.number_input(
            "MIN NET EDGE (c)", value=1.0, step=0.5, format="%.1f",
            help="Minimum net edge in cents after fees"
        )

    with fc3:
        if not _is_sqlite_early:
            min_qty = st.number_input(
                "MIN EXEC QTY", value=1, step=1, min_value=1
            )
        else:
            min_qty = 1

    with fc4:
        canadian = st.checkbox("CAN ONLY", value=False)

    with fc5:
        _sort_options = ["Net Edge", "Qty"] if _is_sqlite_early else ["Net Edge", "Age", "Qty", "Max P&L"]
        sort_by = st.selectbox("SORT BY", _sort_options)

    st.markdown("<hr style='margin:0.4rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    # -- Load data --
    from dashboard.data_layer import get_live_market_summary
    from dashboard.live_state import get_live_state as _get_live_state
    _health = _h_early  # reuse early health check (already cached in same TTL window)
    _is_sqlite = _is_sqlite_early

    # Check WebSocket connection
    _ws_state  = _get_live_state()
    _ws_stats  = _ws_state.get_stats()
    _ws_live   = _ws_stats.get("connected", False)

    # Apply sidebar min-edge filter on top of the main filter row
    try:
        _sidebar_min_edge = float(st.session_state.get("p02_min_edge_show", 2))
    except Exception:
        _sidebar_min_edge = 0.0
    _effective_min_edge = max(float(min_edge), _sidebar_min_edge)

    # When WebSocket is live, show live arb cards FIRST, collapse historical behind expander
    if _ws_live:
        _render_live_fallback(min_edge_cents=_effective_min_edge, strategy_filter=strategy_arg)
        st.markdown("<hr>", unsafe_allow_html=True)
        with st.expander(
            "Open opportunities (database — arbitrage_opportunities table, status='open')",
            expanded=False,
        ):
            st.caption(
                "Source: arbitrage_opportunities DB table (status='open' rows). "
                "These are persisted detections from the scanner — NOT the live in-memory ws_bridge queue shown above. "
                "Rows here survive session restarts; the live cards above are lost on page reload."
            )
            _hist_df, _hist_err = get_live_arb_opportunities(
                strategy=strategy_arg,
                min_net_edge_cents=min_edge,
                min_qty=int(min_qty),
                canadian_only=canadian,
            )
            if not _hist_df.empty:
                _p02_arb_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if "detected_at" in _hist_df.columns:
                    try:
                        _p02_arb_date = str(pd.to_datetime(_hist_df["detected_at"], utc=True, errors="coerce").min())[:10]
                    except Exception:
                        pass
                st.markdown(
                    f"<div style='font-size:0.65rem;color:{TEXT3};"
                    f"font-family:JetBrains Mono,monospace;padding:0.4rem 0;line-height:1.6;'>"
                    f"{len(_hist_df):,} open opportunities persisted since {_p02_arb_date}.</div>",
                    unsafe_allow_html=True,
                )
                _hd = _hist_df.copy()
                if "detected_at" in _hd.columns:
                    _hd["TIME"] = pd.to_datetime(_hd["detected_at"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
                if "strategy_type" in _hd.columns:
                    _hd["STRATEGY"] = _hd["strategy_type"].str.upper()
                if "markets_involved" in _hd.columns:
                    _hd["CONTRACT"] = _hd["markets_involved"].apply(_fmt_markets)
                if "classification" in _hd.columns:
                    _hd["CLASS"] = _hd["classification"]
                if "gross_edge_cents" in _hd.columns:
                    _hd["GROSS"] = _hd["gross_edge_cents"].apply(lambda v: f"{float(v):.2f}c" if pd.notna(v) else "--")
                if "fees_cents" in _hd.columns:
                    _hd["FEES"] = _hd["fees_cents"].apply(lambda v: f"{float(v):.2f}c" if pd.notna(v) else "--")
                if "net_edge_cents" in _hd.columns:
                    _hd["NET EDGE"] = _hd["net_edge_cents"].apply(lambda v: f"+{float(v):.2f}c" if pd.notna(v) else "--")
                _hcols = [c for c in ["TIME", "STRATEGY", "CLASS", "CONTRACT", "GROSS", "FEES", "NET EDGE"] if c in _hd.columns]
                st.dataframe(_hd[_hcols], use_container_width=True, height=300, hide_index=True)
            else:
                st.info("No historical opportunities in the database.")
        _render_recently_closed()
        _render_session_arb_history()
        _render_today_arb_log()
        st.markdown("<hr>", unsafe_allow_html=True)
        with st.expander("📼 RECENT ARBS (Last 10)", expanded=False):
            _render_recent_arbs_store()
        if auto_refresh and (not _is_sqlite or _ws_live):
            import time as _time
            _interval = int(refresh_interval)
            _next_key = "_p02_next_refresh"
            _now = _time.time()
            if _now >= st.session_state.get(_next_key, 0):
                st.session_state[_next_key] = _now + _interval
                st.rerun()
        return

    # Always show live arb cards — _render_live_fallback handles both live and non-live states
    _render_live_fallback(min_edge_cents=_effective_min_edge, strategy_filter=strategy_arg)
    st.markdown("<hr>", unsafe_allow_html=True)
    with st.expander("Open opportunities (database — arbitrage_opportunities table, status='open')", expanded=False):
        st.caption(
            "Source: arbitrage_opportunities DB table (status='open' rows). "
            "These are persisted scanner detections — not the live in-memory ws_bridge queue. "
            "Rows survive session restarts; the live cards above are lost on page reload."
        )
        _hist_df, _hist_err = get_live_arb_opportunities(
            strategy=strategy_arg,
            min_net_edge_cents=min_edge,
            min_qty=int(min_qty),
            canadian_only=canadian,
        )
        if not _hist_df.empty:
            _p02b_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if "detected_at" in _hist_df.columns:
                try:
                    _p02b_date = str(pd.to_datetime(_hist_df["detected_at"], utc=True, errors="coerce").min())[:10]
                except Exception:
                    pass
            st.markdown(
                f"<div style='font-size:0.65rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
                f"padding:0.4rem 0;line-height:1.6;'>{len(_hist_df):,} open opportunities persisted since {_p02b_date}.</div>",
                unsafe_allow_html=True,
            )
            _hd = _hist_df.copy()
            if "detected_at" in _hd.columns:
                _hd["TIME"] = pd.to_datetime(_hd["detected_at"], utc=True, errors="coerce").dt.strftime("%m/%d %H:%M")
            if "strategy_type" in _hd.columns:
                _hd["STRATEGY"] = _hd["strategy_type"].str.upper()
            if "classification" in _hd.columns:
                _hd["CLASS"] = _hd["classification"]
            if "gross_edge_cents" in _hd.columns:
                _hd["GROSS"] = _hd["gross_edge_cents"].apply(lambda v: f"{float(v):.2f}c" if pd.notna(v) else "--")
            if "fees_cents" in _hd.columns:
                _hd["FEES"] = _hd["fees_cents"].apply(lambda v: f"{float(v):.2f}c" if pd.notna(v) else "--")
            if "net_edge_cents" in _hd.columns:
                _hd["NET EDGE"] = _hd["net_edge_cents"].apply(lambda v: f"+{float(v):.2f}c" if pd.notna(v) else "--")
            _hcols = [c for c in ["TIME","STRATEGY","CLASS","GROSS","FEES","NET EDGE"] if c in _hd.columns]
            st.dataframe(_hd[_hcols], use_container_width=True, height=250, hide_index=True)
        else:
            st.info("No historical opportunities in the database.")
    _render_today_arb_log()
    st.markdown("<hr>", unsafe_allow_html=True)
    with st.expander("📼 RECENT ARBS (Last 10)", expanded=False):
        _render_recent_arbs_store()

    # -- Execution Log --
    st.markdown("<hr>", unsafe_allow_html=True)
    _render_execution_log()

    if auto_refresh and (not _is_sqlite or _ws_live):
        import time as _time
        _interval = int(refresh_interval)
        _next_key = "_p02_next_refresh"
        _now = _time.time()
        if _now >= st.session_state.get(_next_key, 0):
            st.session_state[_next_key] = _now + _interval
            st.rerun()


def _render_recent_arbs_store():
    """Show the 10 most recent arbs from the arbs DB table (SQLite or PostgreSQL fallback)."""
    import time as _time_ras
    try:
        try:
            from zoneinfo import ZoneInfo as _ZI_ras
            _ET_ras = _ZI_ras("America/New_York")
        except Exception:
            _ET_ras = None

        from dashboard.data_layer import get_system_health as _gh_ras
        _h_ras = _gh_ras()
        _sqlite_ras = not _h_ras.get("db_connected", False) and _h_ras.get("db_mode") == "sqlite"

        _rows_ras = []
        _total_count = 0

        if _sqlite_ras:
            # SQLite path
            from dashboard.data_layer import _sqlite_conn as _gsc_ras
            _conn_ras = _gsc_ras()
            if _conn_ras:
                try:
                    _cnt_row = _conn_ras.execute("SELECT COUNT(*) FROM arbs").fetchone()
                    _total_count = int(_cnt_row[0]) if _cnt_row else 0
                    _raw_ras = _conn_ras.execute(
                        "SELECT ticker, strategy, net_edge_cents, gross_edge_cents, ts "
                        "FROM arbs ORDER BY ts DESC LIMIT 10"
                    ).fetchall()
                    _rows_ras = _raw_ras
                except Exception:
                    _rows_ras = []
                finally:
                    _conn_ras.close()
        else:
            # PostgreSQL path
            try:
                from dashboard.data_layer import get_db_connection as _get_db_ras
                _conn_pg = _get_db_ras()
                if _conn_pg:
                    with _conn_pg.cursor() as _cur_ras:
                        _cur_ras.execute("SELECT COUNT(*) FROM arbs")
                        _cnt_pg = _cur_ras.fetchone()
                        _total_count = int(_cnt_pg[0]) if _cnt_pg else 0
                        _cur_ras.execute(
                            "SELECT ticker, strategy, net_edge_cents, gross_edge_cents, ts "
                            "FROM arbs ORDER BY ts DESC LIMIT 10"
                        )
                        _rows_ras = _cur_ras.fetchall()
                    _conn_pg.close()
            except Exception:
                _rows_ras = []

        if not _rows_ras:
            st.info("No arbs logged yet — arbs appear here once detected and saved")
            st.caption("0 arbs logged in DB")
            return

        _now_ras_epoch = _time_ras.time()
        _disp_rows = []
        for _r in _rows_ras:
            _ticker_r, _strat_r, _net_r, _gross_r, _ts_r = _r
            # Format ts as ET
            _ts_str = "--"
            if _ts_r is not None:
                try:
                    from datetime import datetime as _dt_ras
                    if isinstance(_ts_r, (int, float)):
                        _dt_utc = _dt_ras.fromtimestamp(float(_ts_r), tz=__import__("datetime").timezone.utc)
                    else:
                        _dt_utc = pd.to_datetime(_ts_r, utc=True).to_pydatetime()
                    _ts_str = (_dt_utc.astimezone(_ET_ras).strftime("%m/%d %H:%M:%S ET")
                               if _ET_ras else _dt_utc.strftime("%m/%d %H:%M:%S UTC"))
                except Exception:
                    _ts_str = str(_ts_r)[:19]
            # Compute age string
            _age_str_r = "--"
            if _ts_r is not None:
                try:
                    if isinstance(_ts_r, (int, float)):
                        _ts_epoch_r = float(_ts_r)
                    else:
                        _ts_epoch_r = pd.to_datetime(_ts_r, utc=True).timestamp()
                    _age_s_r = _now_ras_epoch - _ts_epoch_r
                    if _age_s_r < 3600:
                        _age_str_r = f"{int(_age_s_r // 60)}m ago"
                    else:
                        _age_str_r = f"{int(_age_s_r // 3600)}h {int((_age_s_r % 3600) // 60)}m ago"
                except Exception:
                    pass
            _disp_rows.append({
                "TICKER":    str(_ticker_r or "--")[:35],
                "STRATEGY":  str(_strat_r or "--").upper().replace("_", " "),
                "NET EDGE":  f"+{float(_net_r):.2f}c" if _net_r is not None else "--",
                "GROSS EDGE": f"{float(_gross_r):.2f}c" if _gross_r is not None else "--",
                "AGE":       _age_str_r,
                "TIME (ET)": _ts_str,
            })

        _disp_df = pd.DataFrame(_disp_rows)
        st.markdown(
            "<div style='font-size:0.6rem;letter-spacing:0.12em;text-transform:uppercase;"
            f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
            "RECENT ARBS (LAST 10) — FROM DB</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(_disp_df, use_container_width=True, height=min(385, 35 * len(_disp_df) + 45), hide_index=True)
        st.caption(f"{_total_count} arbs logged in DB")
    except Exception:
        st.info("No arbs logged yet — arbs appear here once detected and saved")


def _render_today_arb_log():
    """Show all arbs persisted to disk today (survives page refresh / session restart)."""
    try:
        from dashboard.arb_logger import load_today_log as _load_log
        from datetime import datetime as _dt_l, timezone as _tz_l
        _rows_log = _load_log()
    except Exception:
        return
    if not _rows_log:
        return

    st.markdown("<hr>", unsafe_allow_html=True)
    with st.expander(
        f"💾 TODAY'S CONFIRMED ARB LOG — {len(_rows_log)} detections (persistent across restarts)",
        expanded=False,
    ):
        st.caption(
            "Source: flat-file JSONL on disk (/tmp/kalshi_arb_log_YYYYMMDD.jsonl), written by the ws_bridge scanner. "
            "Distinct from both the live in-memory session queue (lost on reload) and the DB table above. "
            "Every detection that passed all filters appears here and persists across page reloads and session restarts."
        )
        _log_disp = []
        for _r in reversed(_rows_log):
            _ts = _r.get("detected_at_ts")
            _t_str = (
                _dt_l.fromtimestamp(_ts, tz=_tz_l.utc).strftime("%H:%M:%S UTC")
                if _ts else "--"
            )
            _strat_l = _r.get("strategy", "--")
            _ya = float(_r.get("yes_ask", 0))
            _na = float(_r.get("no_ask", 0))
            _legs = _r.get("legs", [])
            _exec_l = _r.get("executable_contracts") or 0
            _net_l  = float(_r.get("net_edge_cents", 0))
            _n_legs_l = len(_legs) if _legs else ""
            if _strat_l == "yes_no_complement":
                _act_l = "Buy YES + NO (same market)"
            elif _strat_l == "collectively_exhaustive":
                _act_l = f"Buy YES on all {_n_legs_l} legs" if _n_legs_l else "Buy YES on all legs"
            elif _strat_l == "mutually_exclusive":
                _act_l = f"Buy NO on all {_n_legs_l} legs" if _n_legs_l else "Buy NO on all legs"
            elif _strat_l == "threshold_order":
                _act_l = "Buy YES (low) + NO (high)"
            else:
                _act_l = "--"
            _capital_l = (_ya + _na) * _exec_l if _exec_l > 0 else 0.0
            _pnl_l     = (_net_l / 100.0) * _exec_l if _exec_l > 0 else 0.0
            _log_disp.append({
                "TIME":      _t_str,
                "TICKER":    _r.get("ticker", "--"),
                "ACTION":    _act_l,
                "GROSS":     f"{float(_r.get('gross_edge_cents', 0)):.2f}c",
                "NET EDGE":  f"+{_net_l:.2f}c",
                "EXEC QTY":  f"{int(_exec_l):,}" if _exec_l else "--",
                "CAPITAL":   f"${_capital_l:.2f}" if _capital_l > 0 else "--",
                "EXP P&L":   f"${_pnl_l:.4f}" if _pnl_l > 0 else "--",
            })
        _log_df = pd.DataFrame(_log_disp)
        _log_show = [c for c in ["TIME","TICKER","ACTION","GROSS","NET EDGE","EXEC QTY","CAPITAL","EXP P&L"] if c in _log_df.columns]
        st.dataframe(_log_df[_log_show], use_container_width=True, height=min(400, 35 * len(_log_disp) + 45), hide_index=True)
        st.download_button(
            "⬇ Download CSV",
            data=_log_df[_log_show].to_csv(index=False).encode(),
            file_name=f"kalshi_arb_log_{_dt_l.now(_tz_l.utc).strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key="dl_today_log",
        )


def _render_recently_closed():
    """Show opportunities that disappeared or settled in the last hour."""
    # In SQLite snapshot mode all rows are 'historical' — skip this section
    from dashboard.data_layer import get_system_health as _gh
    _h = _gh()
    _sqlite = not _h.get("db_connected", False) and _h.get("db_mode") == "sqlite"
    if _sqlite:
        return

    closed_df, _ = get_recently_closed_opps(hours=1)
    if closed_df.empty:
        return

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.12em;text-transform:uppercase;"
        f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
        f"RECENTLY CLOSED OPPORTUNITIES (LAST 1H)</div>",
        unsafe_allow_html=True,
    )

    disp = closed_df.copy()
    if "detected_at" in disp.columns:
        disp["DETECTED"] = pd.to_datetime(disp["detected_at"], utc=True, errors="coerce").dt.strftime("%H:%M:%S")
    if "strategy_type" in disp.columns:
        disp["STRATEGY"] = disp["strategy_type"].str.upper()
    if "markets_involved" in disp.columns:
        disp["CONTRACT"] = disp["markets_involved"].apply(_fmt_markets)
    if "net_edge_cents" in disp.columns:
        disp["EDGE"] = disp["net_edge_cents"].apply(lambda v: f"+{float(v):.2f}c" if pd.notna(v) else "--")
    if "duration_seconds" in disp.columns:
        disp["DURATION"] = disp["duration_seconds"].apply(
            lambda s: f"{int(s)}s" if pd.notna(s) and s < 3600 else (f"{int(s//3600)}h{int((s%3600)//60)}m" if pd.notna(s) else "--")
        )
    if "status" in disp.columns:
        status_colors = {"expired": RED, "settled": BLUE, "closed": AMBER}
        disp["STATUS"] = disp["status"].str.upper()

    show = [c for c in ["DETECTED", "STRATEGY", "STATUS", "CONTRACT", "EDGE", "DURATION"] if c in disp.columns]
    st.dataframe(disp[show], use_container_width=True, height=min(220, 35 * len(closed_df) + 45), hide_index=True)


_SESSION_STRATEGY_LABELS = {
    "yes_no_complement":    "YES/NO COMPLEMENT",
    "collectively_exhaustive": "COLLECTIVELY EXHAUSTIVE",
    "mutually_exclusive":   "MUTUALLY EXCLUSIVE",
    "threshold_order":      "THRESHOLD ORDER",
    "superset":             "SUPERSET",
}

def _render_session_arb_history():
    """Show arbs detected by the background scanner this session (from the arb queue)."""
    from dashboard.live_state import get_live_state as _gls2
    from datetime import datetime as _dt2, timezone as _tz2
    try:
        from zoneinfo import ZoneInfo as _ZI
        _ET = _ZI("America/New_York")
    except Exception:
        _ET = None  # fall back to UTC display if zoneinfo unavailable

    _state2 = _gls2()
    _session_arbs_raw = [a for a in _state2.get_recent_opportunities(limit=200)
                         if not _is_sports_ticker(str(a.get("ticker", "")))]
    _sess_total = _state2.get_session_stats().get("total", 0)
    if not _session_arbs_raw:
        # Still show a "0 arbs this session" note so user knows scanner is active
        st.caption(f"📡 Scanner active — 0 arbs detected this session (scanning every 1s)")
        return

    # Split clean vs. high-edge (pre-fix suspect): net_edge_cents > 50c
    _PRE_FIX_THRESHOLD = 50.0
    _session_arbs_all  = [a for a in _session_arbs_raw if float(a.get("net_edge_cents", 0)) <= _PRE_FIX_THRESHOLD]
    _high_edge_arbs    = [a for a in _session_arbs_raw if float(a.get("net_edge_cents", 0)) >  _PRE_FIX_THRESHOLD]

    # Deduplicate by ticker — the scanner re-detects every 5 min (Gate 5 TTL reset).
    # Keep only the most recent detection per unique event so each arb shows once.
    _seen_tickers: set = set()
    _session_arbs = []
    for _a in _session_arbs_all:  # queue is newest-first
        _tk = _a.get("ticker", "")
        if _tk not in _seen_tickers:
            _seen_tickers.add(_tk)
            _session_arbs.append(_a)

    _showing_n   = len(_session_arbs)
    _n_high      = len(_high_edge_arbs)
    _n_redetects = len(_session_arbs_all) - _showing_n  # suppressed re-detections

    st.markdown("<hr>", unsafe_allow_html=True)
    with st.expander(
        f"📡 SESSION ARB HISTORY — {_showing_n} unique arb{'s' if _showing_n != 1 else ''} this session"
        + (f" ({_n_redetects} re-detections hidden)" if _n_redetects else "")
        + (f" ({_n_high} high-edge hidden)" if _n_high else ""),
        expanded=False,
    ):
        st.markdown(
            f"<div style='font-size:0.65rem;color:{TEXT3};margin-bottom:0.4rem;'>"
            f"Unique arbs detected this session (1s scan, YES/NO complement). "
            f"Same event re-detected every 5 min as Gate 5 TTL resets — showing latest detection per event only. "
            f"Entries with net_edge &gt; 50c are hidden below — they may be pre-fix stale-book CE detections.</div>",
            unsafe_allow_html=True,
        )
        # Pre-warm title cache in parallel before looping (avoids N × 2s sequential HTTP)
        try:
            from concurrent.futures import ThreadPoolExecutor as _TPE_sh
            _sh_tickers = list(dict.fromkeys(
                (str(_o.get("ticker", "")).split(" (")[0] if not _o.get("legs")
                 else str(_o.get("ticker", "")).split(" (")[0]).rsplit("-", 1)[0]
                for _o in _session_arbs
            ))
            if _sh_tickers:
                with _TPE_sh(max_workers=min(8, len(_sh_tickers))) as _pool_sh:
                    list(_pool_sh.map(_fetch_event_title, _sh_tickers))
        except Exception:
            pass

        _rows = []
        for _opp in _session_arbs:
            _ts = _opp.get("detected_at_ts")
            if _ts:
                _dt_utc = _dt2.fromtimestamp(_ts, tz=_tz2.utc)
                if _ET:
                    _dt_et = _dt_utc.astimezone(_ET)
                    _time_str = _dt_et.strftime("%H:%M:%S ET")
                else:
                    _time_str = _dt_utc.strftime("%H:%M:%S UTC")
            else:
                _time_str = "--"

            _strat_raw = _opp.get("strategy", "--")
            _strat_h = _SESSION_STRATEGY_LABELS.get(_strat_raw, str(_strat_raw).upper().replace("_", " "))
            _is_ce_h = _strat_raw == "collectively_exhaustive"
            _is_me_h = _strat_raw == "mutually_exclusive"
            _legs_h  = _opp.get("legs", [])
            _ya_h  = float(_opp.get("yes_ask", 0))
            _na_h  = float(_opp.get("no_ask", 0))
            _exec_c = _opp.get("executable_contracts") or 0
            _net_h  = float(_opp.get("net_edge_cents", 0))
            _tk_h_raw = _opp.get("ticker", "--")
            _tk_h = (_tk_h_raw[:22] + "…") if len(str(_tk_h_raw)) > 23 else _tk_h_raw

            # Fetch human-readable event/market title (cached after first call)
            _event_tk_h = str(_tk_h_raw).split(" (")[0]
            if not _legs_h:
                _event_tk_h = _event_tk_h.rsplit("-", 1)[0]
            _title_h = _fetch_event_title(_event_tk_h) or ""

            # Action description with event name
            _n_legs_h = len(_legs_h) if _legs_h else ""
            _tn_h = f" — {_title_h}" if _title_h else ""
            if _strat_raw == "yes_no_complement":
                _action_h = f"Buy YES + NO on same market{_tn_h}"
            elif _is_ce_h:
                _action_h = f"Buy YES on all {_n_legs_h} candidates{_tn_h}" if _n_legs_h else f"Buy YES on all candidates{_tn_h}"
            elif _is_me_h:
                _action_h = f"Buy NO on all {_n_legs_h} candidates{_tn_h}" if _n_legs_h else f"Buy NO on all candidates{_tn_h}"
            elif _strat_raw == "threshold_order":
                _action_h = f"Buy YES (low) + NO (high){_tn_h}"
            else:
                _action_h = "--"

            # Legs column: show individual leg tickers for multi-leg arbs
            if _legs_h and (_is_ce_h or _is_me_h or _strat_raw == "threshold_order"):
                _legs_disp = " | ".join(str(l)[:25] for l in _legs_h[:4])
                if len(_legs_h) > 4:
                    _legs_disp += f" … +{len(_legs_h)-4} more"
            else:
                _legs_disp = "--"

            # Capital and P&L
            _capital_h = (_ya_h + _na_h) * _exec_c if _exec_c > 0 else 0.0
            _pnl_h     = (_net_h / 100.0) * _exec_c if _exec_c > 0 else 0.0

            _profit_100_h = _net_h  # net_edge_cents * 100 contracts / 100 = net_edge_cents dollars
            _rows.append({
                "TIME":      _time_str,
                "STRATEGY":  _strat_h,
                "TICKER":    _tk_h,
                "MARKET":    _title_h or "--",
                "ACTION":    _action_h,
                "LEGS":      _legs_disp,
                "GROSS EDGE": f"{float(_opp.get('gross_edge_cents', 0)):.2f}c",
                "FEES":       f"{float(_opp.get('fees_cents', 0)):.2f}c",
                "NET EDGE":   f"+{_net_h:.2f}c",
                "CONTRACTS":  f"{int(_exec_c):,}" if _exec_c else "--",
                "CAPITAL":    f"${_capital_h:.2f}" if _capital_h > 0 else "--",
                "EXP P&L":    f"${_pnl_h:.2f}" if _pnl_h > 0 else "--",
                "profit_100_contracts": f"${_profit_100_h:.2f}",
            })
        _hist_df = pd.DataFrame(_rows)
        _show_cols = [c for c in ["TIME","STRATEGY","TICKER","MARKET","ACTION","LEGS","GROSS EDGE","FEES","NET EDGE","CONTRACTS","CAPITAL","EXP P&L","profit_100_contracts"] if c in _hist_df.columns]
        st.dataframe(_hist_df[_show_cols], use_container_width=True, height=min(400, 35 * len(_rows) + 45), hide_index=True)
        from datetime import datetime as _dt_sess, timezone as _tz_sess
        st.download_button(
            "⬇ Download CSV",
            data=_hist_df[_show_cols].to_csv(index=False).encode(),
            file_name=f"kalshi_session_arbs_{_dt_sess.now(_tz_sess.utc).strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            key="dl_session_arbs",
        )

        # -- High-edge entries (suspected pre-fix stale-book CE arbs) --
        if _high_edge_arbs:
            with st.expander(
                f"Show high-edge entries (pre-fix) — {_n_high} entries with net_edge > 50c",
                expanded=False,
            ):
                st.markdown(
                    f"<div style='font-size:0.63rem;color:{AMBER};margin-bottom:0.5rem;'>"
                    f"⚠️ pre-fix? &nbsp; These {_n_high} entr{'y' if _n_high==1 else 'ies'} have net_edge &gt; 50c. "
                    f"High-edge CE arbs detected before the Gate 1b stale-book fix was deployed "
                    f"may have been re-detected from stale order books. Review before acting.</div>",
                    unsafe_allow_html=True,
                )
                _hi_rows = []
                for _opp in _high_edge_arbs:
                    _ts = _opp.get("detected_at_ts")
                    if _ts:
                        _dt_utc = _dt2.fromtimestamp(_ts, tz=_tz2.utc)
                        _time_str = _dt_utc.astimezone(_ET).strftime("%H:%M:%S ET") if _ET else _dt_utc.strftime("%H:%M:%S UTC")
                    else:
                        _time_str = "--"
                    _strat_raw = _opp.get("strategy", "--")
                    _strat_h = _SESSION_STRATEGY_LABELS.get(_strat_raw, str(_strat_raw).upper().replace("_", " "))
                    _net_h  = float(_opp.get("net_edge_cents", 0))
                    _exec_c = _opp.get("executable_contracts") or 0
                    _hi_rows.append({
                        "TIME":     _time_str,
                        "STRATEGY": _strat_h,
                        "TICKER":   str(_opp.get("ticker", "--"))[:25],
                        "GROSS EDGE": f"{float(_opp.get('gross_edge_cents', 0)):.2f}c",
                        "NET EDGE": f"+{_net_h:.2f}c",
                        "CONTRACTS": f"{int(_exec_c):,}" if _exec_c else "--",
                        "FLAG":     "⚠️ pre-fix?",
                    })
                _hi_df = pd.DataFrame(_hi_rows)
                _hi_cols = [c for c in ["TIME","STRATEGY","TICKER","GROSS EDGE","NET EDGE","CONTRACTS","FLAG"] if c in _hi_df.columns]
                st.dataframe(_hi_df[_hi_cols], use_container_width=True, height=min(300, 35 * len(_hi_rows) + 45), hide_index=True)


def _no_opps_banner(n_markets: int = 0, mps: float = 0.0, min_edge_cents: float = 0.0, last_scan_str: str = "--"):
    from dashboard.styles import PANEL, BORDER, TEXT3
    _markets_str = f"{n_markets:,} markets" if n_markets > 0 else "markets"
    _mps_str = f"{mps:.1f} msg/s" if mps > 0 else "--"
    _edge_str = f"≥ {min_edge_cents:.1f}¢" if min_edge_cents > 0 else "after fees"
    _scan_line = f"Last scan: {last_scan_str}" if last_scan_str and last_scan_str != "--" else "Scanner running"
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};
padding:2rem;border-radius:3px;text-align:center;'>
<div style='font-family:JetBrains Mono,monospace;font-size:0.85rem;
color:{TEXT3};letter-spacing:0.06em;'>
NO LIVE ARBS DETECTED — SCANNER IS RUNNING
</div>
<div style='font-size:0.7rem;color:{TEXT3};margin-top:0.5rem;'>
No fee-adjusted edge {_edge_str} found right now. Adjust MIN NET EDGE above to widen the search.
</div>
<div style='font-size:0.65rem;color:{TEXT3};margin-top:0.4rem;
font-family:JetBrains Mono,monospace;letter-spacing:0.04em;'>
{_scan_line} &nbsp;&middot;&nbsp; Scanning {_markets_str} &nbsp;&middot;&nbsp; Feed: {_mps_str} &nbsp;&middot;&nbsp; Cycle: 1s
</div>
</div>""",
        unsafe_allow_html=True,
    )


def _fmt_live_status(age_s) -> str:
    """Return a display-friendly lifecycle status label based on opportunity age."""
    if not pd.notna(age_s):
        return "UNKNOWN"
    s = float(age_s)
    if s > _ALERT_AGE_S:
        return f"⚠ STALE ({int(s)}s)"
    if s > _STALE_AGE_S:
        return f"◔ AGING ({int(s)}s)"
    return f"● LIVE ({int(s)}s)"


def _fmt_markets(val) -> str:
    if not val:
        return "--"
    # Parse JSON array
    try:
        mkts = json.loads(val) if isinstance(val, str) else val
        if isinstance(mkts, list):
            return " | ".join(str(m)[:30] for m in mkts[:2])
    except Exception:
        pass
    # Parse PostgreSQL array notation: {TICKER1,TICKER2,...}
    s = str(val).strip()
    if s.startswith("{") and s.endswith("}"):
        parts = [p.strip() for p in s[1:-1].split(",") if p.strip()]
        return " | ".join(p[:30] for p in parts[:2])
    return s[:50]


def _fmt_class(c) -> str:
    """Map classification code to display label."""
    _MAP = {
        "A": "EXECUTABLE",
        "B": "PENDING DEPTH",
        "C": "RELATIVE VALUE",
        "D": "NEG AFTER FEES",
    }
    s = str(c).upper() if c else ""
    return _MAP.get(s, s) if s else "--"


def _opp_label(df: pd.DataFrame, oid: str) -> str:
    row = df[df["opportunity_id"].astype(str) == oid]
    if row.empty:
        return oid
    strategy = row["strategy_type"].iloc[0] if "strategy_type" in row else ""
    edge = row["net_edge_cents"].iloc[0] if "net_edge_cents" in row else 0
    # Add date and first ticker for SQLite snapshot mode
    date_str = ""
    if "detected_at" in row.columns:
        try:
            dt = pd.to_datetime(row["detected_at"].iloc[0], utc=True, errors="coerce")
            date_str = f" [{dt.strftime('%Y-%m-%d')}]" if pd.notna(dt) else ""
        except Exception:
            pass
    ticker_str = ""
    if "markets_involved" in row.columns:
        raw = row["markets_involved"].iloc[0]
        first = _fmt_markets(raw).split(" | ")[0][:20] if raw else ""
        ticker_str = f" · {first}" if first else ""
    edge_f = float(edge) if pd.notna(edge) else 0.0
    return f"{strategy.upper()} | +{edge_f:.2f}c{date_str}{ticker_str}"


def _render_opportunity_detail(opp_id: str, df: pd.DataFrame):
    """Render detailed two-leg view of one opportunity."""
    row = df[df["opportunity_id"].astype(str) == opp_id].iloc[0]

    net_edge  = row.get("net_edge_cents", 0)
    class_val = row.get("classification", "B")
    color, label = _STATUS_COLORS.get(str(class_val), (TEXT3, "UNKNOWN"))

    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};
border-radius:3px;padding:1rem 1.5rem;margin-bottom:1rem;'>
<div style='font-size:0.6rem;letter-spacing:0.1em;color:{TEXT3};font-family:Inter,sans-serif;'>
{label}
</div>
<div style='font-size:1.6rem;font-family:JetBrains Mono,monospace;
color:{color};font-weight:400;letter-spacing:-0.02em;'>
+{float(net_edge):.2f}c / CONTRACT
</div>
</div>""",
        unsafe_allow_html=True,
    )

    # P&L breakdown table
    gross  = row.get("gross_edge_cents", 0)
    fees   = row.get("fees_cents", 0)
    net    = row.get("net_edge_cents", 0)
    qty    = row.get("qty", 0)
    maxpnl = row.get("max_net_profit", 0)

    # Estimate MAX P&L from net_edge × qty when max_net_profit is NULL (snapshot mode)
    if pd.notna(maxpnl) and float(maxpnl) != 0:
        maxpnl_display = f"${float(maxpnl):.4f}"
    elif pd.notna(net) and pd.notna(qty) and qty != 0:
        _est = (float(net) / 100) * float(qty)
        maxpnl_display = f"~${_est:.4f}" if _est > 0 else "--"
    else:
        maxpnl_display = "--"

    cols = st.columns(5)
    cols[0].metric("GROSS EDGE",  f"{float(gross):.2f}c")
    cols[1].metric("FEES",        f"{float(fees):.2f}c")
    cols[2].metric("NET EDGE",    f"+{float(net):.2f}c")
    cols[3].metric("MAX QTY",     f"{int(float(qty)):,}" if pd.notna(qty) and qty != 0 else "--")
    cols[4].metric("MAX P&L",     maxpnl_display)

    # Markets involved — handle both JSON array and PostgreSQL array {T1,T2} notation
    markets_raw = row.get("markets_involved", "")
    try:
        markets = json.loads(markets_raw) if isinstance(markets_raw, str) else markets_raw
    except Exception:
        # Try PostgreSQL array notation: {TICKER1,TICKER2,...}
        s = str(markets_raw).strip()
        if s.startswith("{") and s.endswith("}"):
            markets = [t.strip() for t in s[1:-1].split(",") if t.strip()]
        else:
            markets = [s] if s else []

    if isinstance(markets, list) and len(markets) >= 2:
        st.markdown("<br>", unsafe_allow_html=True)
        leg1_col, sep_col, leg2_col = st.columns([5, 1, 5])
        with leg1_col:
            _leg_card("LEG 1 - BUY YES", str(markets[0]))
        with sep_col:
            st.markdown(
                f"<div style='text-align:center;padding-top:2rem;color:{TEXT3};font-size:1.2rem;'>+</div>",
                unsafe_allow_html=True
            )
        with leg2_col:
            _leg_card("LEG 2 - BUY NO / YES", str(markets[1]))

    strategy = row.get("strategy_type", "")
    if strategy:
        st.markdown("<br>", unsafe_allow_html=True)
        _relationship_panel(strategy)


def _leg_card(label: str, ticker: str):
    from dashboard.styles import PANEL, PANEL2, BORDER, TEXT, TEXT3
    st.markdown(
        f"""<div style='background:{PANEL2};border:1px solid {BORDER};
border-radius:3px;padding:0.75rem 1rem;'>
<div style='font-size:0.6rem;letter-spacing:0.1em;color:{TEXT3};
text-transform:uppercase;font-family:Inter,sans-serif;'>
{label}
</div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.8rem;
color:{TEXT};margin-top:4px;word-break:break-all;'>
{ticker}
</div>
</div>""",
        unsafe_allow_html=True,
    )


def _render_arb_inspect(arbs: list):
    """Selectbox + expander: pick a live arb to see full detail and execution guide."""
    labels = [
        f"{i}: {a.get('ticker','?')} (+{float(a.get('net_edge_cents', 0)):.2f}c)"
        for i, a in enumerate(arbs)
    ]
    sel = st.selectbox(
        "Select arb to inspect", labels, index=None,
        placeholder="Choose an opportunity…", key="live_arb_inspect_sel",
    )
    if sel is None:
        return
    idx = int(sel.split(":")[0])
    arb = arbs[idx]
    with st.expander("ARB DETAIL", expanded=True):
        ticker  = arb.get("ticker", "--")
        strat   = arb.get("strategy", "--")
        legs    = arb.get("legs", [])
        det_ts  = arb.get("detected_at_ts")
        det_str = (
            datetime.fromtimestamp(det_ts, tz=timezone.utc).strftime("%H:%M:%S UTC")
            if det_ts else "--"
        )
        gross = float(arb.get("gross_edge_cents", 0))
        fees  = float(arb.get("fees_cents", 0))
        net   = float(arb.get("net_edge_cents", 0))
        ya    = float(arb.get("yes_ask", 0))
        na    = float(arb.get("no_ask", 0))
        qty   = int(arb.get("executable_contracts") or 0)
        exp_pnl = (net / 100.0) * qty if qty > 0 else 0.0

        # Fetch human-readable event/market title
        _insp_evt_tk = str(ticker).split(" (")[0]
        if not legs:
            _insp_evt_tk = _insp_evt_tk.rsplit("-", 1)[0]
        _insp_title = _fetch_event_title(_insp_evt_tk) or ""
        if _insp_title:
            st.markdown(
                f"<div style='font-size:1.05rem;font-weight:700;color:#e2e8f0;"
                f"margin-bottom:0.15rem;'>{_insp_title}</div>"
                f"<div style='font-size:0.65rem;color:#64748b;font-family:JetBrains Mono,monospace;"
                f"margin-bottom:0.5rem;'>{ticker}</div>",
                unsafe_allow_html=True,
            )

        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f"**Ticker:** `{ticker}`")
        c2.markdown(f"**Strategy:** `{strat.replace('_', ' ').upper()}`")
        c3.markdown(f"**Detected:** {det_str}")
        c4.markdown(f"**Qty available:** {qty:,}" if qty else "**Qty available:** --")

        ec1, ec2, ec3 = st.columns(3)
        ec1.metric("Gross Edge", f"{gross:.2f}c")
        ec2.metric("Fees", f"{fees:.2f}c")
        ec3.metric("Net Edge", f"+{net:.2f}c")

        capital = (ya + na) * qty if qty > 0 else 0.0
        if qty > 0:
            st.metric("Expected P&L", f"${exp_pnl:.4f}  ({qty:,} contracts × {net:.2f}c)")

        # Kalshi market URL
        _kalshi_base = "https://kalshi.com/markets"
        _market_ticker = (legs[0] if legs else ticker).rsplit("-", 1)[0] if legs else ticker.rsplit("-", 1)[0]
        _kalshi_url = f"{_kalshi_base}/{_market_ticker.lower()}"

        st.markdown("---\n**HOW TO EXECUTE**")
        # Summary line with capital + expected profit
        _summary = (
            f"**Target:** {qty:,} contracts &nbsp;·&nbsp; "
            f"**Capital required:** ${capital:.2f} &nbsp;·&nbsp; "
            f"**Expected profit:** ${exp_pnl:.4f}  \n"
            f"**Market link:** [{ticker}]({_kalshi_url})  \n\n"
        ) if qty > 0 else f"**Market link:** [{ticker}]({_kalshi_url})  \n\n"

        if strat == "yes_no_complement":
            _ync_name = f"**{_insp_title}**" if _insp_title else f"`{ticker}`"
            guide = (
                _summary +
                f"Buy YES + NO on the same market ({_ync_name}):  \n"
                f"1. Buy YES on `{ticker}` at **{ya:.4f}**  \n"
                f"2. Buy NO on `{ticker}` at **{na:.4f}** (simultaneously)  \n"
                f"Combined cost: **{ya+na:.4f}** — guaranteed profit: **{1-(ya+na):.4f}** per contract "
                f"({net:.2f}c net after fees)."
            )
        elif strat == "collectively_exhaustive":
            _leg_list = legs or [ticker]
            _ce_what = f"**{_insp_title}** candidates" if _insp_title else f"all {len(_leg_list)} candidates"
            leg_str = "  \n".join(f"  {i+1}. Buy YES on `{l}`" for i, l in enumerate(_leg_list))
            guide = (
                _summary +
                f"Buy YES on ALL {len(_leg_list)} {_ce_what} simultaneously:  \n{leg_str}  \n"
                f"Exactly one candidate resolves YES ($1 payout). "
                f"Profit: **{net:.2f}c** per contract set."
            )
        elif strat == "mutually_exclusive":
            _leg_list = legs or [ticker]
            _me_what = f"**{_insp_title}** candidates" if _insp_title else f"all {len(_leg_list)} candidates"
            leg_str = "  \n".join(f"  {i+1}. Buy NO on `{l}`" for i, l in enumerate(_leg_list))
            guide = (
                _summary +
                f"Buy NO on ALL {len(_leg_list)} {_me_what} simultaneously:  \n{leg_str}  \n"
                f"Exactly one resolves YES — the remaining {len(_leg_list)-1} NO contracts pay $1 each. "
                f"Profit: **{net:.2f}c** per contract set."
            )
        elif strat == "threshold_order":
            l0 = legs[0] if legs else ticker
            l1 = legs[1] if len(legs) > 1 else ticker
            guide = (
                _summary +
                f"1. Buy YES on `{l0}` (lower threshold) at **{ya:.4f}**  \n"
                f"2. Buy NO on `{l1}` (higher threshold) at **{na:.4f}** (simultaneously)  \n"
                f"Spread profit: **{net:.2f}c** per contract."
            )
        else:
            guide = _summary + f"Strategy `{strat}`: review leg prices above and place trades accordingly."
        st.markdown(guide)


def _render_live_fallback(min_edge_cents: float = 0.0, strategy_filter: str | None = None):
    """Show live WS-based complement arb scan with detailed per-opportunity cards."""
    import sqlite3 as _sq3
    from pathlib import Path as _Path
    from dashboard.data_layer import get_live_market_summary
    from dashboard.live_state import get_live_state as _gls

    summary = get_live_market_summary()
    all_complement = summary.get("complement_only", [])
    all_ce         = summary.get("ce_arbs", [])
    all_other      = summary.get("other_arbs", [])
    n_markets = summary.get("markets", 0)
    connected = summary.get("connected", False)

    # Combine and apply filters
    all_arbs = [a for a in all_complement + all_ce + all_other
                if not _is_sports_ticker(str(a.get("ticker", "")))]
    arbs = all_arbs
    if min_edge_cents > 0:
        arbs = [a for a in arbs if a.get("net_edge_cents", 0) >= min_edge_cents]
    if strategy_filter:
        arbs = [a for a in arbs if a.get("strategy", "") == strategy_filter]

    # Sort all arbs by net_edge_cents descending so best opportunities appear first
    arbs = sorted(arbs, key=lambda a: a.get("net_edge_cents", 0), reverse=True)

    complement_arbs = [a for a in arbs if a.get("strategy") == "yes_no_complement"]
    ce_arbs         = [a for a in arbs if a.get("strategy") == "collectively_exhaustive"]
    other_arbs      = [a for a in arbs if a.get("strategy") in ("mutually_exclusive", "threshold_order", "superset")]

    # KPIs
    _live_st = _gls()
    _kpi_stats = _live_st.get_stats()
    _mps = _kpi_stats.get("messages_per_sec", 0.0)
    _last_arb_ts = _live_st.get_session_stats().get("last_arb_ts")
    if _last_arb_ts:
        try:
            _last_det_str = datetime.fromtimestamp(_last_arb_ts, tz=timezone.utc).strftime("%H:%M:%S UTC")
        except Exception:
            _last_det_str = "--"
    else:
        _last_det_str = "--"
    k1, k2, k3, k4, k5, k6, k7, k8 = st.columns(8)
    k1.metric("MARKETS LIVE", f"{n_markets:,}")
    k2.metric("COMPLEMENT", str(len(complement_arbs)))
    k3.metric("CE + ME + TH", str(len(ce_arbs) + len(other_arbs)),
              help="Collectively Exhaustive + Mutually Exclusive + Threshold Order arbs")
    if arbs:
        best = max(a["net_edge_cents"] for a in arbs)
        k4.metric("BEST NET EDGE", f"+{best:.2f}c")
    else:
        k4.metric("BEST NET EDGE", "--")
    k5.metric("FEED", "● LIVE" if connected else "◔ CONNECTING")
    k6.metric("MSG/S", f"{_mps:.1f}")
    k7.metric("SCAN INTERVAL", "1s")
    k8.metric("LAST DETECTION", _last_det_str)

    st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)

    # -- Per-strategy session alert banner --
    try:
        _counts = _live_st.arb_counts_by_strategy()
        _total_session = sum(_counts.values())
        _alerts_on = st.session_state.get("p02_alerts_on", True)
        if _alerts_on and _total_session > 0:
            st.markdown(
                f"<div style='background:#1e3a2e;border:1px solid #22c55e;border-radius:4px;"
                f"padding:0.45rem 1rem;margin-bottom:0.6rem;font-family:JetBrains Mono,monospace;"
                f"font-size:0.72rem;color:#86efac;letter-spacing:0.04em;'>"
                f"&#128680; YNC: <strong>{_counts['ync']}</strong> "
                f"&nbsp;|&nbsp; CE: <strong>{_counts['ce']}</strong> "
                f"&nbsp;|&nbsp; ME: <strong>{_counts['me']}</strong> "
                f"&nbsp;|&nbsp; TH: <strong>{_counts['th']}</strong> "
                f"&nbsp;&nbsp;arbs detected this session"
                f"</div>",
                unsafe_allow_html=True,
            )
    except Exception:
        pass

    st.markdown(
        f"<div style='font-size:0.65rem;color:{TEXT3};margin-bottom:0.4rem;'>"
        f"Real-time scan — 5 strategies: YES/NO complement, collectively exhaustive, mutually exclusive, superset, threshold order. "
        f"Scanning {n_markets:,} live markets via Synthesis WebSocket.</div>",
        unsafe_allow_html=True,
    )

    # -- Strategy summary line
    _n_ync_sum = len([a for a in arbs if a.get("strategy") == "yes_no_complement"])
    _n_ce_sum  = len([a for a in arbs if a.get("strategy") == "collectively_exhaustive"])
    _n_me_sum  = len([a for a in arbs if a.get("strategy") == "mutually_exclusive"])
    _n_th_sum  = len([a for a in arbs if a.get("strategy") == "threshold_order"])
    _n_ss_sum  = len([a for a in arbs if a.get("strategy") == "superset"])
    if arbs:
        _sum_parts = []
        if _n_ync_sum: _sum_parts.append(f"<span style='color:#0f766e;font-weight:700;'>{_n_ync_sum} YNC</span>")
        if _n_ce_sum:  _sum_parts.append(f"<span style='color:#1d4ed8;font-weight:700;'>{_n_ce_sum} CE</span>")
        if _n_me_sum:  _sum_parts.append(f"<span style='color:#7c3aed;font-weight:700;'>{_n_me_sum} ME</span>")
        if _n_th_sum:  _sum_parts.append(f"<span style='color:#b45309;font-weight:700;'>{_n_th_sum} TH</span>")
        if _n_ss_sum:  _sum_parts.append(f"<span style='color:#7c3aed;font-weight:700;'>{_n_ss_sum} SS</span>")
        _sum_html = " &nbsp;·&nbsp; ".join(_sum_parts)
        st.markdown(
            f"<div style='font-size:0.68rem;color:{TEXT3};margin-bottom:0.75rem;'>"
            f"Showing <strong style='color:{TEXT};'>{len(arbs)}</strong> arbs: {_sum_html}</div>",
            unsafe_allow_html=True,
        )

    if not arbs:
        _ws_feed_stats = _gls().get_stats()
        _last_scan_ts_raw = _live_st.get_session_stats().get("last_arb_ts") or _live_st.get_stats().get("last_message_ts")
        _last_scan_str = "--"
        if _last_scan_ts_raw:
            try:
                _last_scan_str = datetime.fromtimestamp(_last_scan_ts_raw, tz=timezone.utc).strftime("%H:%M:%S UTC")
            except Exception:
                pass
        _no_opps_banner(
            n_markets=n_markets,
            mps=_ws_feed_stats.get("messages_per_sec", 0.0),
            min_edge_cents=min_edge_cents,
            last_scan_str=_last_scan_str,
        )
        try:
            import time as _time_cd
            _time_until_next = 30 - (int(_time_cd.time()) % 30)
            st.caption(f"🔄 Next scan in {_time_until_next}s · Scanner runs continuously (30s cycle)")
        except Exception:
            pass
        st.markdown("<hr>", unsafe_allow_html=True)
        for _strat in ["yes_no_complement", "collectively_exhaustive",
                       "mutually_exclusive", "superset", "threshold_order"]:
            _relationship_panel(_strat)
            st.markdown("<div style='margin-top:0.5rem;'></div>", unsafe_allow_html=True)
        return

    # -- SQLite DB path for close_time lookups
    _db_path = None
    for _cand in [
        _Path(__file__).parent.parent / "dashboard" / "dashboard.db",
        _Path(__file__).parent / "dashboard.db",
        _Path("dashboard/dashboard.db"),
    ]:
        if _cand.exists():
            _db_path = str(_cand)
            break

    def _batch_get_close_times(tickers):
        _unique = list(dict.fromkeys(t for t in tickers if t))
        if not _db_path or not _unique:
            return {}
        try:
            _c = _sq3.connect(_db_path, check_same_thread=False)
            _ph = ",".join("?" * len(_unique))
            _rows = _c.execute(
                f"SELECT ticker, close_time FROM markets WHERE ticker IN ({_ph})",
                _unique,
            ).fetchall()
            _c.close()
            return {r[0]: r[1] for r in _rows}
        except Exception:
            return {}

    def _fmt_ttl(ct):
        if not ct:
            return "--"
        try:
            from datetime import datetime as _dt2, timezone as _tz2
            _parsed = _dt2.fromisoformat(str(ct).replace("Z", "+00:00"))
            _now = _dt2.now(_tz2.utc)
            _total_s = int((_parsed - _now).total_seconds())
            if _total_s < 0:
                return "SETTLED"
            _d, _rem = divmod(_total_s, 86400)
            _h, _rem2 = divmod(_rem, 3600)
            _m = _rem2 // 60
            if _d > 0:
                return f"{_d}d {_h}h"
            elif _h > 0:
                return f"{_h}h {_m}m"
            else:
                return f"{_m}m"
        except Exception:
            return str(ct)[:16]

    # -- Live state for L2 depth (already assigned above for KPI row)

    # -- Batch pre-fetch: open one SQLite connection for all arb close times
    _arbs_slice = arbs[:20]
    _batch_lookup_tickers = [str(a.get("ticker", "")).split(" (")[0] for a in _arbs_slice]
    _close_times_map = _batch_get_close_times(_batch_lookup_tickers)

    # -- Pre-warm event title cache in parallel (avoids up to 10 × 4s sequential HTTP calls)
    from concurrent.futures import ThreadPoolExecutor as _TPE

    def _arb_event_ticker(arb_d):
        _t = str(arb_d.get("ticker", "")).split(" (")[0]
        return _t if arb_d.get("legs") else _t.rsplit("-", 1)[0]

    _evt_tickers_batch = list(dict.fromkeys(_arb_event_ticker(a) for a in _arbs_slice))
    if _evt_tickers_batch:
        with _TPE(max_workers=min(10, len(_evt_tickers_batch))) as _pool:
            list(_pool.map(_fetch_event_title, _evt_tickers_batch))

    def _section_header(title: str, subtitle: str):
        st.markdown(
            f"<div style='font-size:0.68rem;font-weight:700;letter-spacing:0.1em;"
            f"text-transform:uppercase;color:{AMBER};margin:1rem 0 0.25rem 0;'>"
            f"{title} <span style='font-size:0.6rem;color:{TEXT3};font-weight:400;'>"
            f"— {subtitle}</span></div>",
            unsafe_allow_html=True,
        )

    # -- Complement arb cards
    _complement_slice = [a for a in arbs if a.get("strategy") == "yes_no_complement"][:10]
    _ce_slice         = [a for a in arbs if a.get("strategy") == "collectively_exhaustive"][:10]

    if _complement_slice:
        _section_header("YES / NO COMPLEMENT", "Buy YES + NO on same market")
    for _arb in _complement_slice:
        _ticker = _html.escape(str(_arb.get("ticker", "--")))
        _ya = _arb.get("yes_ask", 0)
        _na = _arb.get("no_ask", 0)
        _gross = _arb.get("gross_edge_cents", 0)
        _fees = _arb.get("fees_cents", 0)
        _net = _arb.get("net_edge_cents", 0)
        _strategy = _arb.get("strategy", "yes_no_complement")
        _legs = _arb.get("legs", [])  # CE arbs carry individual leg tickers
        _is_ce = _strategy == "collectively_exhaustive"

        # For complement arbs use the plain ticker; for CE, the ticker has " (N legs)" suffix
        _lookup_ticker = _ticker.split(" (")[0] if _is_ce else _ticker

        # L2 depth from live state
        _book = _live_st.get_book(_lookup_ticker)
        _yes_asks_l2 = []
        _yes_bids_l2 = []
        _exec_qty = 0

        if _is_ce:
            # For CE arbs, look up L2 for each individual leg and take the minimum available qty
            _leg_qtys = []
            _leg_books = []
            for _leg_tk in _legs:
                _lb = _live_st.get_book(_leg_tk)
                if _lb:
                    _lb_asks = _lb.get("yes_asks", [])
                    _leg_books.append((_leg_tk, _lb_asks))
                    if _lb_asks and len(_lb_asks[0]) > 1:
                        _leg_qtys.append(int(_lb_asks[0][1]))
            _exec_qty = min(_leg_qtys) if _leg_qtys else 0
        else:
            if _book:
                _yes_asks_l2 = _book.get("yes_asks", [])
                _yes_bids_l2 = _book.get("yes_bids", [])
                _max_qty_yes = int(_yes_asks_l2[0][1]) if _yes_asks_l2 and len(_yes_asks_l2[0]) > 1 else 0
                _max_qty_no = int(_yes_bids_l2[0][1]) if _yes_bids_l2 and len(_yes_bids_l2[0]) > 1 else 0
                if _max_qty_yes > 0 and _max_qty_no > 0:
                    _exec_qty = min(_max_qty_yes, _max_qty_no)
                else:
                    _exec_qty = max(_max_qty_yes, _max_qty_no)

        # Use executable_contracts from ws_bridge's order-book walk (sums all arb-profitable levels)
        _exec_qty_ws = _arb.get("executable_contracts", 0)
        if _exec_qty_ws and _exec_qty_ws > 0:
            _exec_qty = _exec_qty_ws  # prefer depth-walked value from ws_bridge
        _exp_pnl = (_net / 100.0) * _exec_qty if _exec_qty > 0 else 0.0
        _close_raw = _close_times_map.get(_lookup_ticker)
        _ttl = _fmt_ttl(_close_raw)
        _edge_col = GREEN if _net >= 1.0 else AMBER
        _exec_str = f"{_exec_qty:,}" if _exec_qty > 0 else "--"
        _pnl_str = f"${_exp_pnl:.4f}" if _exp_pnl > 0 else "--"

        # Capital metrics
        _capital = _exec_qty * (_ya + _na) if _exec_qty > 0 else 0.0
        _capital_str = f"${_capital:.2f}" if _capital > 0 else "--"

        _ttl_days = None
        if _close_raw:
            try:
                _close_dt = datetime.fromisoformat(str(_close_raw).replace("Z", "+00:00"))
                _ttl_secs = (_close_dt - datetime.now(timezone.utc)).total_seconds()
                _ttl_days = max(_ttl_secs / 86400.0, 1.0 / 24)  # floor at 1h to avoid /0
            except Exception:
                pass

        if _capital > 0 and _ttl_days:
            _roi = (_exp_pnl / _capital)  # fractional return on deployed capital
            _apr = _roi / (_ttl_days / 365.25) * 100
            _apr_str = f"{_apr:,.0f}%"
            _apr_col = GREEN if _apr >= 50 else AMBER
        else:
            _apr_str = "--"
            _apr_col = TEXT3

        # Timestamp — show both absolute and relative age
        _det_ts = _arb.get("detected_at_ts")
        _now_ts = datetime.now(timezone.utc).timestamp()
        if _det_ts:
            _age_s = int(_now_ts - _det_ts)
            _det_abs = datetime.fromtimestamp(_det_ts, tz=timezone.utc).strftime("%H:%M:%S UTC")
            if _age_s < 60:
                _det_str = f"Detected {_age_s}s ago ({_det_abs})"
            elif _age_s < 3600:
                _det_str = f"Detected {_age_s//60}m {_age_s%60}s ago ({_det_abs})"
            else:
                _det_str = f"Detected {_age_s//3600}h ago ({_det_abs})"
        else:
            _det_str = "--"

        # Compact opp-age badge + quote freshness for card metadata row
        import time as _time_mod
        try:
            _opp_age_s2 = int(_time_mod.time() - float(_det_ts or _time_mod.time()))
            if _det_ts:
                if _opp_age_s2 < 10:
                    _age_badge_icon = "🟢"
                    _age_badge_label = f"FRESH · {_opp_age_s2}s ago"
                    _age_badge_col = GREEN
                elif _opp_age_s2 < 60:
                    _age_badge_icon = "🟡"
                    _age_badge_label = f"AGING · {_opp_age_s2}s ago"
                    _age_badge_col = AMBER
                else:
                    _age_badge_icon = "🔴"
                    _age_badge_label = f"STALE · {_opp_age_s2 // 60}m ago"
                    _age_badge_col = RED
            else:
                _age_badge_icon = ""
                _age_badge_label = "--"
                _age_badge_col = TEXT3
        except Exception:
            _age_badge_icon = ""
            _age_badge_label = "--"
            _age_badge_col = TEXT3
        try:
            _quote_age_s = int(float(_arb.get("age_seconds") or _arb.get("quote_age_s") or 0))
        except Exception:
            _quote_age_s = 0
        _quote_col = AMBER if _quote_age_s > 30 else GREEN
        _quote_age_meta = (
            f"&nbsp;&#xB7;&nbsp;<span style='color:{_quote_col};'>quotes: {_quote_age_s}s old</span>"
            if _quote_age_s > 0 else ""
        )
        _opp_age_meta_html = (
            f"<div style='font-size:0.57rem;color:{_age_badge_col};font-family:Inter,"
            f"sans-serif;margin-top:2px;letter-spacing:0;text-transform:none;font-weight:600;'>"
            f"{_age_badge_icon} {_age_badge_label}{_quote_age_meta}</div>"
        )

        # -- PERSISTENCE metric: how long this arb has been continuously visible --
        _persistent_badge_html = ""
        _scan_idx_caption = None
        try:
            _first_seen_ts = float(_arb.get("first_seen") or _arb.get("ts") or _det_ts or _time_mod.time())
            _persistence_s = int(_time_mod.time() - _first_seen_ts)
            _persist_m = _persistence_s // 60
            _persist_s_rem = _persistence_s % 60
            if _persistence_s < 60:
                _persist_str = f"⏱ Alive {_persistence_s}s"
            else:
                _persist_str = f"⏱ Alive {_persist_m}m {_persist_s_rem}s"
            if _persistence_s >= 300:
                _persistent_badge_html = (
                    f"&nbsp;<span style='background:#14532d;color:#86efac;padding:1px 6px;"
                    f"border-radius:2px;font-size:0.55rem;font-weight:700;"
                    f"font-family:Inter,sans-serif;'>🔥 PERSISTENT</span>"
                )
            _opp_age_meta_html = (
                f"<div style='font-size:0.57rem;color:{_age_badge_col};font-family:Inter,"
                f"sans-serif;margin-top:2px;letter-spacing:0;text-transform:none;font-weight:600;'>"
                f"{_age_badge_icon} {_age_badge_label}{_quote_age_meta}"
                f"&nbsp;·&nbsp;<span style='color:{TEXT3};font-weight:400;'>{_persist_str}</span>"
                f"{_persistent_badge_html}</div>"
            )
            _scan_idx = _arb.get("scan_index", None)
            if _scan_idx is not None:
                _scan_idx_caption = f"scan #{_scan_idx}"
        except Exception:
            pass

        _is_me = _strategy == "mutually_exclusive"
        _is_threshold = _strategy == "threshold_order"

        # Strategy abbreviation badge (YNC/CE/ME/TH/SS)
        _strat_abbrev_map = {
            "yes_no_complement": "YNC",
            "collectively_exhaustive": "CE",
            "mutually_exclusive": "ME",
            "threshold_order": "TH",
            "superset": "SS",
        }
        _strat_abbrev = _strat_abbrev_map.get(_strategy, _strategy[:3].upper())
        _abbrev_bg = {
            "YNC": "#0f766e", "CE": "#1d4ed8", "ME": "#7c3aed", "TH": "#b45309", "SS": "#475569",
        }.get(_strat_abbrev, "#475569")
        _strat_badge_html = (
            f"<div style='font-size:0.5rem;font-weight:700;letter-spacing:0.08em;"
            f"background:{_abbrev_bg};color:#fff;border-radius:2px;padding:1px 6px;"
            f"font-family:Inter,sans-serif;'>{_strat_abbrev}</div>"
        )

        # Execution instruction (per-card, shown as card footer)
        if _strategy == "yes_no_complement":
            _exec_instr = (
                f"BUY YES on <strong>{_ticker}</strong> at {_ya*100:.0f}&#162;"
                f" + BUY NO on <strong>{_ticker}</strong> at {_na*100:.0f}&#162;"
                f" &nbsp;({(_ya+_na)*100:.0f}&#162; total cost, locked {_ttl})"
            )
        elif _is_ce:
            _n_legs_exec = len(_legs) if _legs else "?"
            _exec_instr = f"BUY YES on all {_n_legs_exec} candidates simultaneously &nbsp;(sum YES ask: {_ya*100:.0f}&#162;, net: +{_net:.2f}&#162;/set)"
        elif _is_me:
            _n_legs_exec = len(_legs) if _legs else "?"
            _exec_instr = f"BUY NO on all {_n_legs_exec} candidates simultaneously &nbsp;(net: +{_net:.2f}&#162;/set)"
        elif _is_threshold:
            _l0_exec = _legs[0] if _legs else _ticker
            _l1_exec = _legs[1] if len(_legs) > 1 else _ticker
            _exec_instr = (
                f"BUY YES on <strong>{_l0_exec}</strong> at {_ya*100:.0f}&#162;"
                f" + BUY NO on <strong>{_l1_exec}</strong> at {_na*100:.0f}&#162;"
            )
        else:
            _exec_instr = "Review leg prices and place trades accordingly."
        _exec_instr_html = (
            f"<div style='margin-top:0.45rem;border-top:1px solid {BORDER};"
            f"padding-top:0.35rem;font-size:0.63rem;font-family:JetBrains Mono,monospace;"
            f"color:{GREEN};'>"
            f"<span style='color:{TEXT3};font-size:0.52rem;letter-spacing:0.06em;"
            f"text-transform:uppercase;font-family:Inter,sans-serif;'>EXECUTE: </span>"
            f"{_exec_instr}</div>"
        )

        # Human-readable event title (for searching in WS Predict)
        # Derive event ticker: CE arbs store "KXEVENT (N legs)" in ticker field
        _arb_ticker_raw = str(_arb.get("ticker", ""))
        _event_ticker_for_title = _arb_ticker_raw.split(" (")[0]
        if not _legs:
            # single-market arb: strip last suffix to get event ticker
            _event_ticker_for_title = _event_ticker_for_title.rsplit("-", 1)[0]
        _raw_title = _fetch_event_title(_event_ticker_for_title)
        _title_html = (
            "<div style='font-size:0.62rem;color:#94a3b8;font-family:Inter,"
            "sans-serif;margin-top:3px;font-style:italic;'>"
            + _html.escape(_raw_title) + "</div>"
        ) if _raw_title else ""

        # WS Predict eligibility badge (category check via background cache)
        _ws_eligible = False
        try:
            from dashboard.ws_predict import any_leg_ws_eligible
            _check_tickers = _legs if _legs else [_lookup_ticker]
            _ws_eligible = any_leg_ws_eligible(_check_tickers, _ttl_days)
        except Exception:
            pass

        # Column 2 label/value differs by strategy
        if _is_ce:
            _col2_label = "# LEGS"
            _col2_val = str(len(_legs)) if _legs else str(len(_legs or [_lookup_ticker]))
        elif _is_me:
            _col2_label = "# LEGS"
            _col2_val = str(len(_legs)) if _legs else "--"
        elif _is_threshold:
            _col2_label = "NO ASK"
            _col2_val = f"{_na:.4f}"
        else:
            _col2_label = "NO ASK"
            _col2_val = f"{_na:.4f}"

        # Build L2 depth section
        _depth_html = ""
        if _is_me and _legs:
            # ME: show each leg ticker and its best NO ask
            _depth_html = (
                f"<div style='margin-top:0.5rem;border-top:1px solid {BORDER};"
                f"padding-top:0.4rem;'>"
                f"<div style='font-size:0.55rem;letter-spacing:0.08em;color:{AMBER};"
                f"text-transform:uppercase;margin-bottom:4px;font-family:Inter,sans-serif;'>"
                f"LEGS — BUY NO IN EACH</div>"
            )
            for _leg_tk in _legs[:6]:
                _lb = _live_st.get_book(_leg_tk)
                _leg_ask = _lb.get("no_asks", [[0]])[0][0] if _lb and _lb.get("no_asks") else 0
                _depth_html += (
                    f"<div style='font-family:JetBrains Mono,monospace;font-size:0.65rem;"
                    f"display:flex;justify-content:space-between;color:{TEXT};margin-bottom:1px;'>"
                    f"<span>{_leg_tk}</span>"
                    f"<span style='color:{AMBER};'>NO {_leg_ask:.3f}</span></div>"
                )
            if len(_legs) > 6:
                _depth_html += f"<div style='font-size:0.6rem;color:{TEXT3};'>+ {len(_legs)-6} more legs</div>"
            _depth_html += "</div>"
        elif _is_threshold and _legs:
            # Threshold: show lower/upper threshold legs with prices and violation check
            _th_leg_lower = _legs[0] if _legs else "?"
            _th_leg_upper = _legs[1] if len(_legs) > 1 else "?"
            # YES ask on lower leg = _ya; infer YES price on upper leg from 1 - NO ask
            _th_yes_lower = _ya  # price of YES on lower threshold
            _th_yes_upper = round(1.0 - _na, 4)  # implied YES price on upper threshold
            # Violation: upper threshold YES > lower threshold YES (monotonicity broken)
            _th_violated = _th_yes_upper > _th_yes_lower
            _th_status_icon = "✅ In range" if not _th_violated else "❌ Violated"
            _th_status_col = GREEN if not _th_violated else RED
            # Try to parse price level from ticker suffix (e.g. "KXBTC-25OCT-B50000" → "$50,000")
            def _parse_th_level(tk: str) -> str:
                import re as _re_th
                _m = _re_th.search(r"[TB](\d+(?:\.\d+)?)$", str(tk))
                return f"${float(_m.group(1)):,.0f}" if _m else "--"
            _th_lower_level = _parse_th_level(_th_leg_lower)
            _th_upper_level = _parse_th_level(_th_leg_upper)
            _depth_html = (
                f"<div style='margin-top:0.5rem;border-top:1px solid {BORDER};"
                f"padding-top:0.4rem;'>"
                f"<div style='font-size:0.55rem;letter-spacing:0.08em;color:{GREEN};"
                f"text-transform:uppercase;margin-bottom:6px;font-family:Inter,sans-serif;'>"
                f"THRESHOLD LEGS</div>"
                f"<div style='display:grid;grid-template-columns:3fr 1fr 1fr;"
                f"gap:2px;margin-bottom:3px;'>"
                f"<div style='font-size:0.52rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>LEG / CONTRACT</div>"
                f"<div style='font-size:0.52rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;text-align:right;'>LEVEL</div>"
                f"<div style='font-size:0.52rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;text-align:right;'>YES PRICE</div>"
                f"</div>"
                f"<div style='display:grid;grid-template-columns:3fr 1fr 1fr;"
                f"gap:2px;margin-bottom:2px;border-bottom:1px solid {BORDER};padding-bottom:2px;'>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.63rem;color:{TEXT2};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>"
                f"<span style='color:{GREEN};font-size:0.52rem;'>BUY YES</span> {_th_leg_lower[:38]}</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.63rem;color:{TEXT3};text-align:right;'>{_th_lower_level}</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.63rem;color:{RED};text-align:right;'>{_th_yes_lower:.4f}</div>"
                f"</div>"
                f"<div style='display:grid;grid-template-columns:3fr 1fr 1fr;"
                f"gap:2px;margin-bottom:4px;border-bottom:1px solid {BORDER};padding-bottom:2px;'>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.63rem;color:{TEXT2};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>"
                f"<span style='color:{AMBER};font-size:0.52rem;'>BUY NO</span> {_th_leg_upper[:38]}</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.63rem;color:{TEXT3};text-align:right;'>{_th_upper_level}</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.63rem;color:{RED};text-align:right;'>{_th_yes_upper:.4f} <span style='color:{TEXT3};font-size:0.55rem;'>(implied)</span></div>"
                f"</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.63rem;font-weight:700;"
                f"color:{_th_status_col};'>"
                f"YES lower: {_th_yes_lower:.4f} &nbsp;|&nbsp; YES upper: {_th_yes_upper:.4f} &nbsp;|&nbsp; {_th_status_icon}</div>"
                f"</div>"
            )
        elif _is_ce and _legs:
            # CE: show each leg ticker and its best YES ask
            _depth_html = (
                f"<div style='margin-top:0.5rem;border-top:1px solid {BORDER};"
                f"padding-top:0.4rem;'>"
                f"<div style='font-size:0.55rem;letter-spacing:0.08em;color:{GREEN};"
                f"text-transform:uppercase;margin-bottom:4px;font-family:Inter,sans-serif;'>"
                f"LEGS — BUY YES IN EACH</div>"
            )
            for _leg_tk in _legs[:6]:
                _lb = _live_st.get_book(_leg_tk)
                _leg_ask = _lb.get("yes_asks", [[0]])[0][0] if _lb and _lb.get("yes_asks") else 0
                _leg_qty_disp = ""
                if _lb and _lb.get("yes_asks") and len(_lb["yes_asks"][0]) > 1:
                    _leg_qty_disp = f" · {int(_lb['yes_asks'][0][1]):,} avail"
                _depth_html += (
                    f"<div style='font-family:JetBrains Mono,monospace;font-size:0.65rem;"
                    f"display:flex;justify-content:space-between;color:{TEXT};margin-bottom:2px;'>"
                    f"<span style='color:{TEXT2};truncate;'>{_leg_tk[:40]}</span>"
                    f"<span style='color:{RED};'>{_leg_ask:.3f}{_leg_qty_disp}</span></div>"
                )
            _depth_html += "</div>"
        elif _yes_asks_l2 or _yes_bids_l2:
            _depth_html = (
                f"<div style='margin-top:0.5rem;display:grid;grid-template-columns:1fr 1fr;"
                f"gap:0.75rem;border-top:1px solid {BORDER};padding-top:0.4rem;'>"
            )
            _depth_html += (
                f"<div><div style='font-size:0.55rem;letter-spacing:0.08em;color:{GREEN};"
                f"text-transform:uppercase;margin-bottom:3px;font-family:Inter,sans-serif;'>YES ASK (Buy YES)</div>"
            )
            for _lvl in _yes_asks_l2[:3]:
                _lp = _lvl[0] if _lvl else 0
                _ls = int(_lvl[1]) if len(_lvl) > 1 else 0
                _depth_html += (
                    f"<div style='font-family:JetBrains Mono,monospace;font-size:0.68rem;"
                    f"display:flex;justify-content:space-between;color:{TEXT};margin-bottom:1px;'>"
                    f"<span style='color:{RED};'>{_lp:.3f}</span>"
                    f"<span style='color:{TEXT3};'>{_ls:,}</span></div>"
                )
            _depth_html += "</div>"
            _depth_html += (
                f"<div><div style='font-size:0.55rem;letter-spacing:0.08em;color:{RED};"
                f"text-transform:uppercase;margin-bottom:3px;font-family:Inter,sans-serif;'>NO ASK (Buy NO)</div>"
            )
            for _lvl in _yes_bids_l2[:3]:
                _lp = round(1.0 - _lvl[0], 4) if _lvl else 0
                _ls = int(_lvl[1]) if len(_lvl) > 1 else 0
                _depth_html += (
                    f"<div style='font-family:JetBrains Mono,monospace;font-size:0.68rem;"
                    f"display:flex;justify-content:space-between;color:{TEXT};margin-bottom:1px;'>"
                    f"<span style='color:{RED};'>{_lp:.3f}</span>"
                    f"<span style='color:{TEXT3};'>{_ls:,}</span></div>"
                )
            _depth_html += "</div></div>"

        _ws_badge_html = (
            "<div style='font-size:0.55rem;font-weight:600;letter-spacing:0.06em;"
            "background:#16a34a;color:#fff;border-radius:3px;padding:1px 5px;"
            "font-family:Inter,sans-serif;'>WS</div>"
        ) if _ws_eligible else ""
        # YNC: prominent side-by-side YES ask / NO ask prices in card header
        _ync_price_row_html = ""
        if _strategy == "yes_no_complement":
            _ync_price_row_html = (
                f"<div style='display:flex;gap:1.5rem;margin-top:0.3rem;'>"
                f"<span style='font-size:0.62rem;color:{TEXT3};font-family:Inter,sans-serif;'>"
                f"YES ASK&nbsp;<span style='font-family:JetBrains Mono,monospace;color:{RED};font-size:0.78rem;font-weight:600;'>{_ya:.4f}</span></span>"
                f"<span style='font-size:0.62rem;color:{TEXT3};font-family:Inter,sans-serif;'>"
                f"NO ASK&nbsp;<span style='font-family:JetBrains Mono,monospace;color:{RED};font-size:0.78rem;font-weight:600;'>{_na:.4f}</span></span>"
                f"<span style='font-size:0.62rem;color:{TEXT3};font-family:Inter,sans-serif;'>"
                f"COMBINED&nbsp;<span style='font-family:JetBrains Mono,monospace;color:{AMBER};font-size:0.78rem;'>{(_ya+_na):.4f}</span></span>"
                f"</div>"
            )
        _col2_label_key = "SUM YES" if _is_ce else "YES ASK"
        _strategy_label = _html.escape(_strategy.replace("_", " "))
        _det_label = _html.escape(_det_str)
        # Kalshi market URL for this arb card
        _card_event_ticker = (_legs[0].rsplit("-", 1)[0] if _legs else _lookup_ticker.rsplit("-", 1)[0])
        _kalshi_card_url = _kalshi_event_url(_card_event_ticker)
        _kalshi_card_display = _kalshi_card_url.replace("https://", "")
        _kalshi_link_html = (
            f"<a href='{_kalshi_card_url}' target='_blank' style='font-size:0.58rem;"
            f"color:#64748b;font-family:Inter,sans-serif;text-decoration:none;"
            f"letter-spacing:0.02em;'>{_kalshi_card_display} ↗</a>"
        )
        _card_html = (
            f"<div style='background:{PANEL};border:1px solid {BORDER};"
            f"border-left:4px solid {_edge_col};border-radius:3px;"
            f"padding:0.85rem 1.1rem;margin-bottom:0.65rem;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.5rem;'>"
            f"<div>"
            f"<div style='display:flex;align-items:center;gap:0.4rem;'>"
            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.82rem;color:{TEXT};font-weight:500;'>{_ticker}</div>"
            f"{_strat_badge_html}"
            f"{_ws_badge_html}"
            f"</div>"
            f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;color:{TEXT3};font-family:Inter,sans-serif;margin-top:2px;'>{_strategy_label} &nbsp;·&nbsp; <span style='color:{TEXT3};letter-spacing:0;text-transform:none;'>{_det_label}</span></div>"
            f"{_title_html}"
            f"{_ync_price_row_html}"
            f"{_opp_age_meta_html}"
            f"<div style='margin-top:3px;'>{_kalshi_link_html}</div>"
            f"</div>"
            f"<div style='text-align:right;'>"
            f"<div style='font-family:JetBrains Mono,monospace;font-size:1.1rem;color:{_edge_col};letter-spacing:-0.01em;'>+{_net:.2f}c</div>"
            f"<div style='font-size:0.55rem;color:{TEXT3};font-family:Inter,sans-serif;text-transform:uppercase;'>NET EDGE</div>"
            f"</div>"
            f"</div>"
            f"<div style='display:grid;grid-template-columns:repeat(10,1fr);gap:0.4rem;'>"
            f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>{_col2_label_key}</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{RED};'>{_ya:.4f}</div></div>"
            f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>{_col2_label}</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{RED};'>{_col2_val}</div></div>"
            + (
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>COMPLEMENT SUM</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{AMBER if (_ya+_na)*100 < 100 else RED};'>{(_ya+_na)*100:.1f}¢</div></div>"
                if _strategy == "yes_no_complement" else
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>--</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>--</div></div>"
            ) +
            f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>GROSS</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_gross:.2f}c</div></div>"
            f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>FEES</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{AMBER};'>{_fees:.2f}c</div></div>"
            f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>EXEC QTY</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_exec_str}</div></div>"
            f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>CAPITAL REQ</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_capital_str}</div></div>"
            f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>EXP. P&amp;L</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{GREEN};'>{_pnl_str}</div></div>"
            f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>APR</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{_apr_col};'>{_apr_str}</div></div>"
            f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>LOCKED FOR</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_ttl}</div></div>"
            f"</div>"
            f"{_depth_html}"
            f"{_exec_instr_html}"
            f"</div>"
        )
        st.markdown(_card_html, unsafe_allow_html=True)
        if _scan_idx_caption:
            st.caption(_scan_idx_caption)
        # -- Execution checklist per complement arb card
        try:
            _arb_id_key = (
                str(_arb.get("ticker", "")).replace(" ", "_").replace("(", "").replace(")", "")[:30]
                + "_" + str(_arb.get("strategy", "ync"))[:6]
            )
            _net_ync  = float(_arb.get("net_edge_cents", 0))
            _ya_c_ync = float(_arb.get("yes_ask", 0)) * 100
            _na_c_ync = float(_arb.get("no_ask", 0)) * 100
            _fee_ync  = float(_arb.get("fees_cents", 0))
            with st.expander("Execution Checklist", expanded=False):
                _cb1 = st.checkbox(
                    f"✅ Verified YES bid ≥ {_ya_c_ync:.0f}¢ still holds (check live feed)",
                    key=f"check_{_arb_id_key}_1",
                )
                _cb2 = st.checkbox(
                    f"✅ Verified NO bid ≥ {_na_c_ync:.0f}¢ still holds",
                    key=f"check_{_arb_id_key}_2",
                )
                _cb3 = st.checkbox(
                    f"✅ Calculated net edge ≥ 2¢ after {_fee_ync:.2f}¢ fees per leg",
                    key=f"check_{_arb_id_key}_3",
                )
                _cb4 = st.checkbox(
                    "✅ Position size within Kelly fraction (see Backtest page)",
                    key=f"check_{_arb_id_key}_4",
                )
                _cb5 = st.checkbox(
                    "✅ Not already in this position",
                    key=f"check_{_arb_id_key}_5",
                )
                _cb6 = st.checkbox(
                    "✅ Verified prices in Kalshi UI before executing",
                    key=f"check_{_arb_id_key}_6",
                )
                if _cb1 and _cb2 and _cb3 and _cb4 and _cb5 and _cb6:
                    _ync_tk2  = str(_arb.get("ticker", "--")).split(" (")[0]
                    _ync_snippet2 = (
                        f"# Arb: {_ync_tk2}\n"
                        f"# Strategy: {str(_arb.get('strategy','ync')).upper()}\n"
                        f"# Leg 1: BUY YES on {_ync_tk2} @ {_ya_c_ync:.0f}¢\n"
                        f"# Leg 2: BUY NO on {_ync_tk2} @ {_na_c_ync:.0f}¢\n"
                        f"# Est. net edge: +{_net_ync:.2f}¢ per contract"
                    )
                    st.success("\U0001f680 Ready to execute — place orders on Kalshi.com")
                    st.code(_ync_snippet2, language="bash")
                else:
                    st.info("Complete all checks before executing")
        except Exception:
            pass
        # -- Copy-ready execution summary (YNC only, shown outside checklist when not all checked)
        if _strategy == "yes_no_complement":
            try:
                _ync_tk   = str(_arb.get("ticker", "--")).split(" (")[0]
                _ync_ya_c = float(_arb.get("yes_ask", 0)) * 100
                _ync_na_c = float(_arb.get("no_ask", 0)) * 100
                _ync_net  = float(_arb.get("net_edge_cents", 0))
                _ync_snippet = (
                    f"# Arb: {_ync_tk}\n"
                    f"# Strategy: YNC\n"
                    f"# Leg 1: BUY YES on {_ync_tk} @ {_ync_ya_c:.0f}¢\n"
                    f"# Leg 2: BUY NO on {_ync_tk} @ {_ync_na_c:.0f}¢\n"
                    f"# Est. net edge: +{_ync_net:.2f}¢ per contract"
                )
                st.code(_ync_snippet, language="bash")
            except Exception:
                pass

    # -- CE arb cards
    if _ce_slice:
        _section_header("COLLECTIVELY EXHAUSTIVE", "Buy YES on all legs")
        for _arb in _ce_slice:
            _ticker = _html.escape(str(_arb.get("ticker", "--")))
            _ya = _arb.get("yes_ask", 0)
            _na = _arb.get("no_ask", 0)
            _gross = _arb.get("gross_edge_cents", 0)
            _fees = _arb.get("fees_cents", 0)
            _net = _arb.get("net_edge_cents", 0)
            _strategy = _arb.get("strategy", "collectively_exhaustive")
            _legs = _arb.get("legs", [])
            _is_ce = True

            _lookup_ticker = _ticker.split(" (")[0]
            _close_time = _close_times_map.get(_lookup_ticker)
            _ttl = _fmt_ttl(_close_time)

            _event_ticker = _lookup_ticker
            _title = _fetch_event_title(_event_ticker) or _ticker
            _title_html = _html.escape(_title)

            _edge_col = GREEN if _net >= 2 else AMBER if _net >= 1 else TEXT
            _n_legs = len(_legs)
            _action_str = "Buy YES on all {} candidates{}".format(
                _n_legs if _n_legs else "",
                f" — {_title}" if _title and _title != _ticker else "",
            ).strip()
            _ce_event_ticker = _lookup_ticker.rsplit("-", 1)[0] if not _legs else (_legs[0].rsplit("-", 1)[0] if _legs else _lookup_ticker)
            _ce_kalshi_url = _kalshi_event_url(_ce_event_ticker)
            _ce_kalshi_display = _ce_kalshi_url.replace("https://", "")
            _ce_kalshi_link = (
                f"<a href='{_ce_kalshi_url}' target='_blank' style='font-size:0.58rem;"
                f"color:#64748b;font-family:Inter,sans-serif;text-decoration:none;"
                f"letter-spacing:0.02em;'>{_ce_kalshi_display} ↗</a>"
            )

            _qty = _arb.get("executable_contracts") or _arb.get("max_qty") or _arb.get("qty") or 1
            _capital = (_ya + _na) * _qty
            _pnl = (_net / 100.0) * _qty
            _capital_str = f"${_capital:.2f}" if _capital > 0 else "--"
            _pnl_str = f"+${_pnl:.2f}" if _pnl > 0 else "--"
            _exec_str = str(_qty)

            # CE opp age + quote freshness (colored age badge)
            import time as _time_mod2
            try:
                _det_ts_ce = _arb.get("detected_at_ts")
                _age_s_ce = int(_time_mod2.time() - float(_det_ts_ce or _time_mod2.time()))
                if _det_ts_ce:
                    if _age_s_ce < 10:
                        _ce_badge_icon = "🟢"
                        _ce_badge_label = f"FRESH · {_age_s_ce}s ago"
                        _ce_badge_col = GREEN
                    elif _age_s_ce < 60:
                        _ce_badge_icon = "🟡"
                        _ce_badge_label = f"AGING · {_age_s_ce}s ago"
                        _ce_badge_col = AMBER
                    else:
                        _ce_badge_icon = "🔴"
                        _ce_badge_label = f"STALE · {_age_s_ce // 60}m ago"
                        _ce_badge_col = RED
                else:
                    _ce_badge_icon = ""
                    _ce_badge_label = "--"
                    _ce_badge_col = TEXT3
            except Exception:
                _ce_badge_icon = ""
                _ce_badge_label = "--"
                _ce_badge_col = TEXT3
            try:
                _quote_age_s_ce = int(float(_arb.get("age_seconds") or _arb.get("quote_age_s") or 0))
            except Exception:
                _quote_age_s_ce = 0
            _quote_col_ce = AMBER if _quote_age_s_ce > 30 else GREEN
            _quote_meta_ce = (
                f"&nbsp;&#xB7;&nbsp;<span style='color:{_quote_col_ce};'>quotes: {_quote_age_s_ce}s old</span>"
                if _quote_age_s_ce > 0 else ""
            )
            _opp_age_meta_ce = (
                f"<div style='font-size:0.57rem;color:{_ce_badge_col};font-family:Inter,"
                f"sans-serif;margin-top:2px;font-weight:600;'>"
                f"{_ce_badge_icon} {_ce_badge_label}{_quote_meta_ce}</div>"
            )

            # CE execution instruction
            _ce_exec_instr_html = (
                f"<div style='margin-top:0.4rem;border-top:1px solid {BORDER};"
                f"padding-top:0.35rem;font-size:0.63rem;font-family:JetBrains Mono,monospace;color:{GREEN};'>"
                f"<span style='color:{TEXT3};font-size:0.52rem;letter-spacing:0.06em;"
                f"text-transform:uppercase;font-family:Inter,sans-serif;'>EXECUTE: </span>"
                f"BUY YES on all {_n_legs} legs simultaneously &nbsp;(sum YES ask: {_ya*100:.0f}&#162;, net: +{_net:.2f}&#162;/set)"
                f"</div>"
            )

            _days_locked = None
            if _close_time:
                try:
                    from datetime import datetime as _dt3, timezone as _tz3
                    _ct = _dt3.fromisoformat(str(_close_time).replace("Z", "+00:00"))
                    _days_locked = (_ct - _dt3.now(_tz3.utc)).total_seconds() / 86400
                except Exception:
                    pass
            if _days_locked and _days_locked > 0 and _capital > 0 and _pnl > 0:
                _apr_v = (_pnl / _capital) / (_days_locked / 365.25) * 100
                _apr_str = f"{_apr_v:.0f}%"
                _apr_col = GREEN if _apr_v >= 20 else AMBER
            else:
                _apr_str = "--"
                _apr_col = TEXT3

            _ce_leg_count_badge = (
                f"<div style='font-size:0.5rem;font-weight:700;letter-spacing:0.06em;"
                f"background:#1e3a5f;color:{CYAN};border-radius:2px;padding:1px 6px;"
                f"font-family:Inter,sans-serif;border:1px solid {CYAN};'>"
                f"{_n_legs}-leg CE</div>"
            ) if _n_legs else ""
            _series_mtype = _arb.get("series_market_type", "")
            _mtype_badge = ""
            if _series_mtype:
                _mtype_short = _series_mtype.replace("_", " ").upper()
                _mtype_col = "#22C55E" if _series_mtype in ("binary_race", "tournament_champion", "multi_race_closed") else "#F59E0B"
                _mtype_badge = (
                    f"<div style='font-size:0.48rem;font-weight:600;letter-spacing:0.05em;"
                    f"background:{_mtype_col}18;color:{_mtype_col};border-radius:2px;padding:1px 5px;"
                    f"font-family:Inter,sans-serif;border:1px solid {_mtype_col}44;'>"
                    f"{_mtype_short}</div>"
                )

            # Compact comma-separated leg tickers
            _ce_legs_compact = ", ".join(
                l if isinstance(l, str) else l.get("ticker", str(l))
                for l in _legs
            ) if _legs else ""
            _ce_legs_compact_html = (
                f"<div style='font-size:0.58rem;color:#64748b;font-family:JetBrains Mono,monospace;"
                f"margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>"
                f"LEGS: {_html.escape(_ce_legs_compact)}</div>"
            ) if _ce_legs_compact else ""

            _card_html = (
                f"<div style='background:{PANEL};border:1px solid {BORDER};"
                f"border-left:3px solid {CYAN};border-radius:4px;"
                f"padding:0.6rem 0.8rem;margin-bottom:0.5rem;'>"
                f"<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.4rem;'>"
                f"<div>"
                f"<div style='display:flex;align-items:center;gap:0.4rem;margin-bottom:2px;flex-wrap:wrap;'>"
                f"<div style='font-size:0.5rem;font-weight:700;letter-spacing:0.08em;"
                f"background:#1d4ed8;color:#fff;border-radius:2px;padding:1px 6px;"
                f"font-family:Inter,sans-serif;'>CE</div>"
                f"{_ce_leg_count_badge}"
                f"{_mtype_badge}"
                f"<div style='font-size:0.6rem;color:{CYAN};font-family:Inter,sans-serif;"
                f"letter-spacing:0.1em;text-transform:uppercase;'>{_action_str}</div>"
                f"</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;color:{TEXT};'>{_ticker}</div>"
                f"{_ce_legs_compact_html}"
                f"<div style='font-size:0.65rem;color:{TEXT2};margin-top:2px;'>{_title_html}</div>"
                f"{_opp_age_meta_ce}"
                f"<div style='margin-top:3px;'>{_ce_kalshi_link}</div>"
                f"</div>"
                f"<div style='text-align:right;'>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:1.1rem;color:{_edge_col};'>+{_net:.2f}c</div>"
                f"<div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>NET EDGE</div>"
                f"</div></div>"
                f"<div style='display:grid;grid-template-columns:repeat(8,1fr);gap:0.4rem;'>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>SUM YES ASK</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{RED};'>{_ya:.4f}</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>TOTAL OUTLAY</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{AMBER};'>{_ya*100:.1f}¢</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>GROSS</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_gross:.2f}c</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>FEES</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{AMBER};'>{_fees:.2f}c</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>EXEC QTY</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_exec_str}</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>CAPITAL REQ</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_capital_str}</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>EXP. P&amp;L</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{GREEN};'>{_pnl_str}</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>LOCKED FOR</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_ttl}</div></div>"
                f"</div>"
                f"{_ce_exec_instr_html}"
                f"</div>"
            )
            st.markdown(_card_html, unsafe_allow_html=True)
            # -- Improved CE leg detail with YES ask, size, cost, total outlay, edge -----
            try:
                if _legs:
                    # Collect per-leg data
                    _leg_data = []
                    _sum_yes_cents = 0.0
                    for _leg_tk in _legs:
                        _lb = _live_st.get_book(_leg_tk)
                        _leg_yes_raw = _lb.get("yes_asks", [[0]])[0][0] if _lb and _lb.get("yes_asks") else 0
                        _leg_yes_cents = float(_leg_yes_raw) * 100
                        _leg_avail = int(_lb["yes_asks"][0][1]) if _lb and _lb.get("yes_asks") and len(_lb["yes_asks"][0]) > 1 else 0
                        _leg_cost_1 = float(_leg_yes_raw)  # cost for 1 contract (dollars)
                        _sum_yes_cents += _leg_yes_cents
                        _leg_data.append((_leg_tk, _leg_yes_cents, _leg_avail, _leg_cost_1))

                    # Build HTML table for legs
                    _legs_html = (
                        f"<div style='margin-top:0.5rem;border-top:1px solid {BORDER};padding-top:0.4rem;'>"
                        f"<div style='font-size:0.55rem;letter-spacing:0.08em;color:{CYAN};"
                        f"text-transform:uppercase;margin-bottom:5px;font-family:Inter,sans-serif;'>"
                        f"LEG DETAIL — BUY YES IN EACH</div>"
                        f"<div style='display:grid;grid-template-columns:3fr 1fr 1fr 1fr;"
                        f"gap:2px;margin-bottom:3px;'>"
                        f"<div style='font-size:0.52rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>TICKER</div>"
                        f"<div style='font-size:0.52rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;text-align:right;'>YES ASK</div>"
                        f"<div style='font-size:0.52rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;text-align:right;'>AVAIL SIZE</div>"
                        f"<div style='font-size:0.52rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;text-align:right;'>COST/1</div>"
                        f"</div>"
                    )
                    for _ltk, _lyc, _lavail, _lcost in _leg_data:
                        _avail_str = f"{_lavail:,}" if _lavail > 0 else "--"
                        _legs_html += (
                            f"<div style='display:grid;grid-template-columns:3fr 1fr 1fr 1fr;"
                            f"gap:2px;margin-bottom:2px;border-bottom:1px solid {BORDER};padding-bottom:2px;'>"
                            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.63rem;color:{TEXT2};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>{_ltk[:40]}</div>"
                            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.63rem;color:{RED};text-align:right;'>{_lyc:.1f}¢</div>"
                            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.63rem;color:{TEXT};text-align:right;'>{_avail_str}</div>"
                            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.63rem;color:{TEXT};text-align:right;'>${_lcost:.4f}</div>"
                            f"</div>"
                        )

                    # Total outlay
                    _total_outlay_c = _sum_yes_cents
                    _deviation = _total_outlay_c - 100.0
                    if _deviation < 0:
                        _edge_disp = abs(_deviation)
                        _edge_html = (
                            f"<div style='margin-top:4px;'>"
                            f"<span style='font-family:JetBrains Mono,monospace;font-size:0.68rem;color:{GREEN};font-weight:700;'>"
                            f"✅ Edge: {_edge_disp:.2f}¢ (sum {_total_outlay_c:.1f}¢ &lt; 100¢)</span></div>"
                        )
                    else:
                        _edge_html = (
                            f"<div style='margin-top:4px;'>"
                            f"<span style='font-family:JetBrains Mono,monospace;font-size:0.68rem;color:{RED};font-weight:700;'>"
                            f"❌ No edge: sum exceeds 100¢ by {_deviation:.2f}¢ (sum {_total_outlay_c:.1f}¢)</span></div>"
                        )

                    _legs_html += (
                        f"<div style='display:flex;justify-content:space-between;margin-top:5px;padding-top:4px;'>"
                        f"<span style='font-family:JetBrains Mono,monospace;font-size:0.65rem;color:{TEXT3};'>TOTAL OUTLAY</span>"
                        f"<span style='font-family:JetBrains Mono,monospace;font-size:0.65rem;color:{TEXT};font-weight:700;'>{_total_outlay_c:.2f}¢</span>"
                        f"</div>"
                        f"<div style='display:flex;justify-content:space-between;'>"
                        f"<span style='font-family:JetBrains Mono,monospace;font-size:0.65rem;color:{TEXT3};'>IMPLIED COMPLEMENT SUM</span>"
                        f"<span style='font-family:JetBrains Mono,monospace;font-size:0.65rem;color:{TEXT2};'>{_total_outlay_c:.2f}¢ (dev: {_deviation:+.2f}¢ vs 100¢)</span>"
                        f"</div>"
                        f"{_edge_html}"
                        f"</div>"
                    )
                    st.markdown(_legs_html, unsafe_allow_html=True)
            except Exception:
                pass
            # -- Execution checklist per CE arb card
            try:
                _arb_id_ce = str(_arb.get("ticker", "")).replace(" ", "_").replace("(", "").replace(")", "")[:40]
                _net_ce_chk = float(_arb.get("net_edge_cents", 0))
                with st.expander("Execution Checklist", expanded=False):
                    st.checkbox("✅ Verified both legs are still tradeable", key=f"check_{_arb_id_ce}_1")
                    st.checkbox("✅ Order size fits within available quantity", key=f"check_{_arb_id_ce}_2")
                    st.checkbox(f"✅ Confirmed net edge after fees: +{_net_ce_chk:.2f}¢", key=f"check_{_arb_id_ce}_3")
                    st.checkbox("✅ Verified prices in Kalshi UI before executing", key=f"check_{_arb_id_ce}_4")
            except Exception:
                pass

    # -- ME / TH / SS arb cards
    import time as _time_mod3

    def _render_other_arb_cards(arb_list, section_title, section_sub, badge_label, badge_bg, border_col):
        if not arb_list:
            return
        _section_header(section_title, section_sub)
        for _arb in arb_list:
            _ticker = _html.escape(str(_arb.get("ticker", "--")))
            _ya    = _arb.get("yes_ask", 0)
            _na    = _arb.get("no_ask", 0)
            _gross = _arb.get("gross_edge_cents", 0)
            _fees  = _arb.get("fees_cents", 0)
            _net   = _arb.get("net_edge_cents", 0)
            _strategy_oth = _arb.get("strategy", "")
            _legs  = _arb.get("legs", [])
            _lookup_ticker = _ticker.split(" (")[0]
            _close_raw = _close_times_map.get(_lookup_ticker)
            _ttl   = _fmt_ttl(_close_raw)
            _edge_col = GREEN if _net >= 2 else AMBER if _net >= 1 else TEXT
            _qty   = _arb.get("executable_contracts") or 0
            _exec_str  = f"{int(_qty):,}" if _qty else "--"
            _pnl   = (_net / 100.0) * _qty if _qty > 0 else 0.0
            _pnl_str   = f"+${_pnl:.4f}" if _pnl > 0 else "--"
            _capital   = (_ya + _na) * _qty if _qty > 0 else 0.0
            _capital_str = f"${_capital:.2f}" if _capital > 0 else "--"

            # Age badge
            _det_ts_oth = _arb.get("detected_at_ts")
            try:
                _age_s_oth = int(_time_mod3.time() - float(_det_ts_oth or _time_mod3.time()))
                if _det_ts_oth:
                    if _age_s_oth < 10:
                        _oth_icon, _oth_label, _oth_col = "🟢", f"FRESH · {_age_s_oth}s ago", GREEN
                    elif _age_s_oth < 60:
                        _oth_icon, _oth_label, _oth_col = "🟡", f"AGING · {_age_s_oth}s ago", AMBER
                    else:
                        _oth_icon, _oth_label, _oth_col = "🔴", f"STALE · {_age_s_oth // 60}m ago", RED
                else:
                    _oth_icon, _oth_label, _oth_col = "", "--", TEXT3
            except Exception:
                _oth_icon, _oth_label, _oth_col = "", "--", TEXT3

            _age_meta_oth = (
                f"<div style='font-size:0.57rem;color:{_oth_col};font-family:Inter,"
                f"sans-serif;margin-top:2px;font-weight:600;'>"
                f"{_oth_icon} {_oth_label}</div>"
            )

            # Execution instruction
            _n_legs_oth = len(_legs) if _legs else "?"
            if _strategy_oth == "mutually_exclusive":
                _exec_instr_oth = f"BUY NO on all {_n_legs_oth} legs simultaneously &nbsp;(net: +{_net:.2f}&#162;/set)"
            elif _strategy_oth == "threshold_order":
                _l0 = _legs[0] if _legs else _ticker
                _l1 = _legs[1] if len(_legs) > 1 else _ticker
                _exec_instr_oth = (
                    f"BUY YES on <strong>{_l0}</strong> at {_ya*100:.0f}&#162;"
                    f" + BUY NO on <strong>{_l1}</strong> at {_na*100:.0f}&#162;"
                )
            elif _strategy_oth == "superset":
                _ss_super = _legs[0] if _legs else _ticker
                _ss_sub   = _legs[1] if len(_legs) > 1 else "--"
                _exec_instr_oth = (
                    f"Superset price must dominate subset price. "
                    f"Sell YES on <strong>{_ss_super}</strong> (superset) and Buy YES on <strong>{_ss_sub}</strong> (subset), "
                    f"or equivalent spread. Net edge: +{_net:.2f}&#162;/contract."
                )
            else:
                _exec_instr_oth = "Review leg prices and place trades accordingly."

            _exec_instr_oth_html = (
                f"<div style='margin-top:0.4rem;border-top:1px solid {BORDER};"
                f"padding-top:0.35rem;font-size:0.63rem;font-family:JetBrains Mono,monospace;color:{GREEN};'>"
                f"<span style='color:{TEXT3};font-size:0.52rem;letter-spacing:0.06em;"
                f"text-transform:uppercase;font-family:Inter,sans-serif;'>EXECUTE: </span>"
                f"{_exec_instr_oth}</div>"
            )

            _badge_oth_html = (
                f"<div style='font-size:0.5rem;font-weight:700;letter-spacing:0.08em;"
                f"background:{badge_bg};color:#fff;border-radius:2px;padding:1px 6px;"
                f"font-family:Inter,sans-serif;'>{badge_label}</div>"
            )

            # Legs depth section
            _legs_depth_oth = ""
            if _strategy_oth == "mutually_exclusive" and _legs:
                _legs_depth_oth = (
                    f"<div style='margin-top:0.5rem;border-top:1px solid {BORDER};"
                    f"padding-top:0.4rem;'>"
                    f"<div style='font-size:0.55rem;letter-spacing:0.08em;color:{AMBER};"
                    f"text-transform:uppercase;margin-bottom:4px;font-family:Inter,sans-serif;'>"
                    f"LEGS — BUY NO IN EACH</div>"
                )
                for _leg_tk in _legs[:6]:
                    _lb = _live_st.get_book(_leg_tk)
                    _leg_ask = _lb.get("no_asks", [[0]])[0][0] if _lb and _lb.get("no_asks") else 0
                    _legs_depth_oth += (
                        f"<div style='font-family:JetBrains Mono,monospace;font-size:0.65rem;"
                        f"display:flex;justify-content:space-between;color:{TEXT};margin-bottom:1px;'>"
                        f"<span>{_leg_tk}</span><span style='color:{AMBER};'>NO {_leg_ask:.3f}</span></div>"
                    )
                if len(_legs) > 6:
                    _legs_depth_oth += f"<div style='font-size:0.6rem;color:{TEXT3};'>+ {len(_legs)-6} more legs</div>"
                _legs_depth_oth += "</div>"
            elif _strategy_oth == "threshold_order" and _legs:
                _legs_depth_oth = (
                    f"<div style='margin-top:0.5rem;border-top:1px solid {BORDER};"
                    f"padding-top:0.4rem;'>"
                    f"<div style='font-size:0.55rem;letter-spacing:0.08em;color:{GREEN};"
                    f"text-transform:uppercase;margin-bottom:4px;font-family:Inter,sans-serif;'>"
                    f"BUY YES ({_legs[0] if _legs else '?'}) + NO ({_legs[1] if len(_legs) > 1 else '?'})</div>"
                    f"</div>"
                )
            elif _strategy_oth == "superset" and _legs:
                _ss_super_tk = _legs[0] if _legs else _ticker
                _ss_sub_tk   = _legs[1] if len(_legs) > 1 else "--"
                _ss_overlap  = _arb.get("overlap_pct") or _arb.get("overlap") or "--"
                _ss_overlap_str = (
                    f"{float(_ss_overlap)*100:.1f}%" if _ss_overlap != "--"
                    else "--"
                )
                try:
                    _ss_overlap_str = f"{float(_ss_overlap):.1f}%" if _ss_overlap != "--" else "--"
                except Exception:
                    pass
                _legs_depth_oth = (
                    f"<div style='margin-top:0.5rem;border-top:1px solid {BORDER};"
                    f"padding-top:0.4rem;'>"
                    f"<div style='font-size:0.55rem;letter-spacing:0.08em;color:#7c3aed;"
                    f"text-transform:uppercase;margin-bottom:6px;font-family:Inter,sans-serif;'>"
                    f"SUPERSET / SUBSET PAIR</div>"
                    f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:0.5rem;'>"
                    f"<div><div style='font-size:0.5rem;color:{TEXT3};text-transform:uppercase;margin-bottom:2px;'>SUPERSET (broader)</div>"
                    f"<div style='font-family:JetBrains Mono,monospace;font-size:0.65rem;color:#a78bfa;'>{_ss_super_tk}</div></div>"
                    f"<div><div style='font-size:0.5rem;color:{TEXT3};text-transform:uppercase;margin-bottom:2px;'>SUBSET (narrower)</div>"
                    f"<div style='font-family:JetBrains Mono,monospace;font-size:0.65rem;color:#c4b5fd;'>{_ss_sub_tk}</div></div>"
                    f"<div><div style='font-size:0.5rem;color:{TEXT3};text-transform:uppercase;margin-bottom:2px;'>OVERLAP %</div>"
                    f"<div style='font-family:JetBrains Mono,monospace;font-size:0.65rem;color:{TEXT};'>{_ss_overlap_str}</div></div>"
                    f"</div>"
                    f"</div>"
                )

            _card_oth_html = (
                f"<div style='background:{PANEL};border:1px solid {BORDER};"
                f"border-left:3px solid {border_col};border-radius:4px;"
                f"padding:0.6rem 0.8rem;margin-bottom:0.5rem;'>"
                f"<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.4rem;'>"
                f"<div>"
                f"<div style='display:flex;align-items:center;gap:0.4rem;margin-bottom:2px;'>"
                f"{_badge_oth_html}"
                f"<div style='font-size:0.6rem;color:{border_col};font-family:Inter,sans-serif;"
                f"letter-spacing:0.1em;text-transform:uppercase;'>{_strategy_oth.replace('_',' ').upper()}</div>"
                f"</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;color:{TEXT};'>{_ticker}</div>"
                f"{_age_meta_oth}"
                f"</div>"
                f"<div style='text-align:right;'>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:1.1rem;color:{_edge_col};'>+{_net:.2f}c</div>"
                f"<div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>NET EDGE</div>"
                f"</div></div>"
                f"<div style='display:grid;grid-template-columns:repeat(6,1fr);gap:0.4rem;'>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>GROSS</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_gross:.2f}c</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>FEES</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{AMBER};'>{_fees:.2f}c</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>NET EDGE</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{_edge_col};'>+{_net:.2f}c</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>EXEC QTY</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_exec_str}</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>CAPITAL</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_capital_str}</div></div>"
                f"<div><div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>LOCKED FOR</div><div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_ttl}</div></div>"
                f"</div>"
                f"{_legs_depth_oth}"
                f"{_exec_instr_oth_html}"
                f"</div>"
            )
            st.markdown(_card_oth_html, unsafe_allow_html=True)
            if _strategy_oth == "superset":
                st.caption(
                    "Superset arb: broader market price must dominate narrower market price. "
                    "When the superset contract trades below the subset contract, a price violation exists."
                )
            try:
                _arb_id_oth = str(_arb.get("ticker", "")).replace(" ", "_").replace("(", "").replace(")", "")[:40]
                _net_oth = float(_arb.get("net_edge_cents", 0))
                with st.expander("Execution Checklist", expanded=False):
                    st.checkbox("✅ Verified all legs are still tradeable", key=f"check_{_arb_id_oth}_1")
                    st.checkbox(f"✅ Confirmed net edge after fees: +{_net_oth:.2f}¢", key=f"check_{_arb_id_oth}_3")
                    st.checkbox("✅ Verified prices in Kalshi UI before executing", key=f"check_{_arb_id_oth}_4")
            except Exception:
                pass

    _me_slice = [a for a in arbs if a.get("strategy") == "mutually_exclusive"][:10]
    _th_slice = [a for a in arbs if a.get("strategy") == "threshold_order"][:10]
    _ss_slice = [a for a in arbs if a.get("strategy") == "superset"][:10]
    _render_other_arb_cards(_me_slice, "MUTUALLY EXCLUSIVE", "Buy NO on all legs",        "ME", "#7c3aed", "#7c3aed")
    _render_other_arb_cards(_th_slice, "THRESHOLD ORDER",    "Buy YES (low) + NO (high)", "TH", "#b45309", AMBER)

    # -- Dedicated SS (Superset) arb cards
    if _ss_slice:
        st.markdown(
            f"<div style='font-size:0.68rem;font-weight:700;letter-spacing:0.1em;"
            f"text-transform:uppercase;color:#a78bfa;margin:1rem 0 0.25rem 0;'>"
            f"🔗 SUPERSET ARB <span style='font-size:0.6rem;color:{TEXT3};font-weight:400;'>"
            f"— Buy YES on broader outcome + NO on narrower outcome</span></div>",
            unsafe_allow_html=True,
        )
        for _ss_arb in _ss_slice:
            _ss_ticker   = _html.escape(str(_ss_arb.get("ticker", "--")))
            _ss_legs     = _ss_arb.get("legs", [])
            _ss_super_tk = _ss_legs[0] if _ss_legs else _ss_ticker
            _ss_sub_tk   = _ss_legs[1] if len(_ss_legs) > 1 else "--"
            _ss_ya       = _ss_arb.get("yes_ask", 0)   # YES ask on superset leg
            _ss_na       = _ss_arb.get("no_ask", 0)    # NO ask on subset leg
            _ss_gross    = _ss_arb.get("gross_edge_cents", 0)
            _ss_fees     = _ss_arb.get("fees_cents", 0)
            _ss_net      = _ss_arb.get("net_edge_cents", 0)
            _ss_overlap  = _ss_arb.get("overlap_pct") or _ss_arb.get("overlap")
            try:
                _ss_overlap_str = f"{float(_ss_overlap):.1f}%" if _ss_overlap is not None else "--"
            except Exception:
                _ss_overlap_str = "--"
            _ss_edge_col = GREEN if _ss_net >= 2 else AMBER if _ss_net >= 1 else TEXT
            # Age badge
            _ss_det_ts = _ss_arb.get("detected_at_ts")
            try:
                import time as _time_ss
                _ss_age_s = int(_time_ss.time() - float(_ss_det_ts or _time_ss.time()))
                if _ss_det_ts:
                    if _ss_age_s < 10:
                        _ss_age_icon, _ss_age_label, _ss_age_col = "🟢", f"FRESH · {_ss_age_s}s ago", GREEN
                    elif _ss_age_s < 60:
                        _ss_age_icon, _ss_age_label, _ss_age_col = "🟡", f"AGING · {_ss_age_s}s ago", AMBER
                    else:
                        _ss_age_icon, _ss_age_label, _ss_age_col = "🔴", f"STALE · {_ss_age_s // 60}m ago", RED
                else:
                    _ss_age_icon, _ss_age_label, _ss_age_col = "", "--", TEXT3
            except Exception:
                _ss_age_icon, _ss_age_label, _ss_age_col = "", "--", TEXT3
            _ss_card_html = (
                f"<div style='background:{PANEL};border:1px solid #4c1d95;"
                f"border-left:4px solid #a78bfa;border-radius:4px;"
                f"padding:0.75rem 1rem;margin-bottom:0.6rem;'>"
                # Header row
                f"<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.5rem;'>"
                f"<div>"
                f"<div style='display:flex;align-items:center;gap:0.5rem;margin-bottom:3px;'>"
                f"<div style='font-size:0.5rem;font-weight:700;letter-spacing:0.08em;"
                f"background:#5b21b6;color:#ddd6fe;border-radius:2px;padding:1px 6px;"
                f"font-family:Inter,sans-serif;'>SS</div>"
                f"<div style='font-size:0.62rem;color:#a78bfa;font-family:Inter,sans-serif;"
                f"letter-spacing:0.08em;text-transform:uppercase;font-weight:600;'>🔗 SUPERSET ARB</div>"
                f"</div>"
                f"<div style='font-size:0.57rem;color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:3px;'>"
                f"Buy YES on broader outcome + NO on narrower outcome</div>"
                f"<div style='font-size:0.57rem;color:{_ss_age_col};font-family:Inter,sans-serif;font-weight:600;'>"
                f"{_ss_age_icon} {_ss_age_label}</div>"
                f"</div>"
                f"<div style='text-align:right;'>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:1.1rem;color:{_ss_edge_col};'>+{_ss_net:.2f}c</div>"
                f"<div style='font-size:0.55rem;color:{TEXT3};text-transform:uppercase;'>NET EDGE</div>"
                f"</div></div>"
                # Tickers row
                f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;margin-bottom:0.5rem;'>"
                f"<div style='background:#2e1065;border:1px solid #5b21b6;border-radius:3px;padding:0.4rem 0.6rem;'>"
                f"<div style='font-size:0.5rem;color:#a78bfa;text-transform:uppercase;letter-spacing:0.08em;"
                f"font-family:Inter,sans-serif;margin-bottom:2px;'>SUPERSET (broader) — BUY YES</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:#ddd6fe;'>{_html.escape(str(_ss_super_tk))}</div>"
                f"<div style='font-size:0.6rem;color:{RED};font-family:JetBrains Mono,monospace;margin-top:2px;'>YES ASK: {_ss_ya:.4f}</div>"
                f"</div>"
                f"<div style='background:#1e1b4b;border:1px solid #4338ca;border-radius:3px;padding:0.4rem 0.6rem;'>"
                f"<div style='font-size:0.5rem;color:#c4b5fd;text-transform:uppercase;letter-spacing:0.08em;"
                f"font-family:Inter,sans-serif;margin-bottom:2px;'>SUBSET (narrower) — BUY NO</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:#c4b5fd;'>{_html.escape(str(_ss_sub_tk))}</div>"
                f"<div style='font-size:0.6rem;color:{AMBER};font-family:JetBrains Mono,monospace;margin-top:2px;'>NO ASK: {_ss_na:.4f}</div>"
                f"</div>"
                f"</div>"
                # Edge metrics row
                f"<div style='display:grid;grid-template-columns:repeat(4,1fr);gap:0.4rem;margin-bottom:0.4rem;'>"
                f"<div><div style='font-size:0.52rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>GROSS EDGE</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_ss_gross:.2f}c</div></div>"
                f"<div><div style='font-size:0.52rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>FEES</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{AMBER};'>{_ss_fees:.2f}c</div></div>"
                f"<div><div style='font-size:0.52rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>NET EDGE</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{_ss_edge_col};font-weight:700;'>+{_ss_net:.2f}c</div></div>"
                f"<div><div style='font-size:0.52rem;color:{TEXT3};text-transform:uppercase;font-family:Inter,sans-serif;'>OVERLAP</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT};'>{_ss_overlap_str}</div></div>"
                f"</div>"
                # Execute instruction
                f"<div style='border-top:1px solid #4c1d95;padding-top:0.35rem;"
                f"font-size:0.62rem;font-family:JetBrains Mono,monospace;color:#a78bfa;'>"
                f"<span style='color:{TEXT3};font-size:0.52rem;letter-spacing:0.06em;"
                f"text-transform:uppercase;font-family:Inter,sans-serif;'>EXECUTE: </span>"
                f"Buy YES on <strong>{_html.escape(str(_ss_super_tk))}</strong> (superset) "
                f"+ Buy NO on <strong>{_html.escape(str(_ss_sub_tk))}</strong> (subset) &nbsp;·&nbsp; "
                f"Net +{_ss_net:.2f}&#162;/contract</div>"
                f"</div>"
            )
            st.markdown(_ss_card_html, unsafe_allow_html=True)
            try:
                _ss_arb_id = str(_ss_arb.get("ticker", "")).replace(" ", "_")[:40]
                with st.expander("Execution Checklist", expanded=False):
                    st.checkbox("✅ Verified superset and subset prices still hold", key=f"check_ss_{_ss_arb_id}_1")
                    st.checkbox(f"✅ Confirmed net edge after fees: +{float(_ss_arb.get('net_edge_cents', 0)):.2f}¢", key=f"check_ss_{_ss_arb_id}_2")
                    st.checkbox("✅ Understand superset monotonicity constraint", key=f"check_ss_{_ss_arb_id}_3")
            except Exception:
                pass

    # -- Expandable detail / execution guide for selected arb --
    if arbs:
        _render_arb_inspect(arbs)

    # Edge distribution (if multiple arbs)
    if len(arbs) >= 3:
        edges = [a["net_edge_cents"] for a in arbs]
        fig = go.Figure(go.Histogram(
            x=edges, nbinsx=20,
            marker_color=BLUE, marker_line_width=0,
        ))
        fig.update_layout(
            **plotly_dark_layout(
            title={"text": "NET EDGE DISTRIBUTION (c)", "font": {"size": 10, "color": TEXT3}},
            height=220, xaxis_title="Net Edge (c)", yaxis_title="Count", bargap=0.05,
        ))
        st.plotly_chart(fig, use_container_width=True)

    # Strategy info panels
    st.markdown("<hr>", unsafe_allow_html=True)
    for _strat in ["yes_no_complement", "collectively_exhaustive",
                   "mutually_exclusive", "superset", "threshold_order"]:
        _relationship_panel(_strat)
        st.markdown("<div style='margin-top:0.5rem;'></div>", unsafe_allow_html=True)

    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {BLUE};
padding:0.75rem 1rem;border-radius:3px;margin-top:1rem;'>
<div style='font-size:0.62rem;letter-spacing:0.08em;color:{BLUE};
text-transform:uppercase;margin-bottom:4px;'>DATA SOURCES</div>
<div style='font-size:0.7rem;color:{TEXT2};line-height:1.6;'>
Live complement arb detected from Synthesis WebSocket.
Relationship data (30k market pairs) loaded from local database.
</div>
</div>""",
        unsafe_allow_html=True,
    )


def _relationship_panel(strategy: str):
    from dashboard.styles import PANEL, BORDER, AMBER, TEXT3, TEXT
    descriptions = {
        "yes_no_complement": (
            "YES / NO COMPLEMENT",
            "P(YES) + P(NO) = 100c for every contract.\n"
            "Arb exists when YES ask + NO ask < 100c (both sides tradeable simultaneously)."
        ),
        "mutually_exclusive": (
            "MUTUALLY EXCLUSIVE",
            "Exactly one of N outcomes can resolve YES.\n"
            "Arb exists when sum of NO asks < N−1 (buy all NOs; N−1 pay out $1 each)."
        ),
        "nested_logical": (
            "NESTED CONTRACTS",
            "P(above X) >= P(above Y) when X < Y.\n"
            "Arb exists when this monotonicity is violated in the live order book."
        ),
        "threshold": (
            "THRESHOLD RELATIONSHIP",
            "Adjacent threshold contracts are related by logical inclusion.\n"
            "Price violations create bounded risk-free opportunities."
        ),
        "collectively_exhaustive": (
            "COLLECTIVELY EXHAUSTIVE",
            "The set of outcomes covers all possible results — exactly one must occur.\n"
            "Arb exists when the sum of YES asks < 100c (can buy all outcomes cheaply)."
        ),
        "superset": (
            "SUPERSET RELATIONSHIP",
            "One contract's outcome strictly includes another's outcome set.\n"
            "The superset contract must be priced >= the subset contract; violations are arb."
        ),
        "threshold_order": (
            "THRESHOLD ORDER",
            "Ordered threshold contracts enforce price monotonicity.\n"
            "If P(above X) > P(above Y) where X > Y, a risk-free spread trade exists."
        ),
    }
    title, desc = descriptions.get(strategy, (strategy.upper(), ""))
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};
border-left:3px solid {AMBER};border-radius:3px;padding:0.75rem 1rem;'>
<div style='font-size:0.62rem;letter-spacing:0.1em;color:{AMBER};
text-transform:uppercase;font-family:Inter,sans-serif;margin-bottom:4px;'>
{title}
</div>
<div style='font-size:0.75rem;color:{TEXT};font-family:JetBrains Mono,monospace;
white-space:pre-line;'>
{desc}
</div>
</div>""",
        unsafe_allow_html=True,
    )
