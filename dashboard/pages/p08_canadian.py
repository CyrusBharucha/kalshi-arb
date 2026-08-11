"""dashboard/pages/p08_canadian.py --  Canadian Market Monitor"""
from __future__ import annotations
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data_layer import (
    get_canadian_markets, get_live_arb_opportunities,
    get_canadian_historical_arb_stats, get_system_health,
)
from dashboard.boc_panel import render_boc_panel
from dashboard.styles import plotly_dark_layout, GREEN, RED, AMBER, BLUE, TEXT, TEXT2, TEXT3, PANEL, BORDER


_CATEGORIES = {
    "Bank of Canada":           ["boc", "bank of canada", "corra", "interest rate", "monetary policy"],
    "Canadian Politics":        ["canada", "liberal", "conservative", "ndp", "trudeau", "carney", "poilievre",
                                 "parliament", "house of commons", "senate", "prime minister"],
    "Canadian Economy":         ["cad", "canadian dollar", "tsx", "cpi", "gdp", "inflation",
                                 "unemployment", "housing", "tariff"],
    "Canadian Sports":          ["toronto", "edmonton", "calgary", "ottawa", "winnipeg", "vancouver",
                                 "montreal", "leafs", "oilers", "flames", "senators", "jets",
                                 "canucks", "canadiens", "blue jays", "raptors"],
    "Elections/Multi-Candidate": [],  # SHARD tickers from KXMVECROSSCATEGORY multi-victor events
    "Other Canada":             [],   # catch-all
}


def render():
    st.markdown("""
<div style='margin-bottom:0.5rem;'>
<span style='font-size:1rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;'>
CANADIAN MARKET MONITOR
</span>
</div>
<div style='font-size:0.7rem;color:#64748B;letter-spacing:0.04em;margin-bottom:0.5rem;'>
Kalshi markets relevant to Canadian researchers and market participants
</div>
""", unsafe_allow_html=True)
    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    _health = get_system_health()
    _is_sqlite = not _health.get("db_connected", False) and _health.get("db_mode") == "sqlite"

    from dashboard.live_state import get_live_state as get_live_state_p08

    # --- Market prefix legend -----
    st.caption(
        "Market prefixes — "
        "KXBOC / KXCBDECISIONCANADA: Bank of Canada policy rate decision markets "
        "(e.g. KXBOC-26SEP-T2.25 = will BOC set rate at 2.25% at the September 2026 meeting?); "
        "KXCAD: CAD/USD exchange rate threshold markets "
        "(e.g. KXCAD-0.72 = will CAD/USD close above 0.72?); "
        "KXCORR: CORRA overnight rate markets; "
        "KXCAHOUSEDEM: Canadian House of Commons seat count markets."
    )

    # --- Data sources banner -----
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {AMBER};
padding:0.6rem 1rem;border-radius:3px;margin-bottom:0.75rem;font-family:JetBrains Mono,monospace;
font-size:0.7rem;color:{TEXT2};line-height:1.7;'>
<strong style='color:{TEXT};'>DATA SOURCES</strong><br>
&bull; <strong>Kalshi markets</strong> — live via Synthesis WebSocket or database snapshot<br>
&bull; <strong>Bank of Canada rate &amp; decisions</strong> — BOC Valet public API
(<code>bankofcanada.ca/valet</code>); shown below when reachable<br>
&bull; <strong>Statistics Canada (CPI, GDP, labour)</strong> — not yet integrated;
BOC Valet covers rate decisions only<br>
&bull; BOC Valet and Kalshi both require internet access; the dashboard falls back
to database snapshot values when either is unavailable
</div>""",
        unsafe_allow_html=True,
    )

    # --- BOC next decision countdown — use authoritative list from boc_panel -----
    import datetime as _dt_boc
    from dashboard.boc_panel import BOC_MEETING_DATES as _BOC_UPCOMING
    _today_boc = _dt_boc.date.today()
    _future_boc = [d for d in _BOC_UPCOMING if d >= _today_boc]
    if _future_boc:
        _next_boc = min(_future_boc)
        _days_to_boc = (_next_boc - _today_boc).days
        st.metric("NEXT BOC DECISION", f"in {_days_to_boc} days",
                  help=f"Next scheduled meeting: {_next_boc.strftime('%Y-%m-%d')}")
        st.caption("Bank of Canada rate decisions occur approximately every 6 weeks")

    # --- Historical BOC Rate Chart -----
    st.markdown("### 📈 Historical BOC Rate")
    import pandas as _pd_boc_hist
    boc_history = _pd_boc_hist.DataFrame({
        "date": ["2023-07-12","2023-09-06","2023-10-25","2024-01-24","2024-03-06",
                 "2024-04-10","2024-06-05","2024-07-24","2024-09-04","2024-10-23",
                 "2024-12-11","2025-01-29","2025-03-12","2025-04-16","2025-06-04",
                 "2025-07-30","2025-09-17","2026-01-28","2026-03-11","2026-04-16",
                 "2026-06-04","2026-07-30"],
        "rate": [5.00, 5.00, 5.00, 5.00, 5.00, 5.00, 4.75, 4.50, 4.25, 3.75,
                 3.25, 3.00, 2.75, 2.75, 2.75, 2.50, 2.50, 2.25, 2.25, 2.25,
                 2.25, 2.25],
    })
    boc_history["date"] = _pd_boc_hist.to_datetime(boc_history["date"])
    st.line_chart(boc_history.set_index("date"))
    st.caption("Source: Bank of Canada — hardcoded through 2026-07-30; next decision Sep 9, 2026")

    # --- Recent BOC rate decisions (hardcoded reference table) -----
    _boc_history = pd.DataFrame([
        {"Date": "2026-07-30", "Decision": "Hold",    "Rate After": "2.25%"},
        {"Date": "2026-06-04", "Decision": "Hold",    "Rate After": "2.25%"},
        {"Date": "2026-04-16", "Decision": "Hold",    "Rate After": "2.25%"},
        {"Date": "2026-03-11", "Decision": "Hold",    "Rate After": "2.25%"},
        {"Date": "2026-01-28", "Decision": "-25bps",  "Rate After": "2.25%"},
        {"Date": "2025-09-17", "Decision": "Hold",    "Rate After": "2.50%"},
        {"Date": "2025-07-30", "Decision": "-25bps",  "Rate After": "2.50%"},
        {"Date": "2025-06-04", "Decision": "Hold",    "Rate After": "2.75%"},
        {"Date": "2025-03-12", "Decision": "-25bps",  "Rate After": "2.75%"},
        {"Date": "2025-01-29", "Decision": "-25bps",  "Rate After": "3.00%"},
    ])
    st.dataframe(_boc_history, use_container_width=False, hide_index=True)
    st.caption("Recent BOC decisions through 2026-07-30 — next decision 2026-09-09 (reference only)")

    # --- Live BOC data + next policy meeting countdown -----
    render_boc_panel(show_countdown=True)

    # --- Upcoming Canadian economic calendar -----
    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.12em;text-transform:uppercase;"
        f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
        f"UPCOMING CANADIAN ECONOMIC RELEASES</div>",
        unsafe_allow_html=True,
    )
    _econ_calendar = pd.DataFrame([
        {"Date": "2026-09-09", "Release": "BOC Rate Decision",          "Detail": "market implied: hold at 2.25%"},
        {"Date": "2026-09-11", "Release": "Canada CPI (Aug)",           "Detail": "expected 2.1% YoY"},
        {"Date": "2026-09-19", "Release": "Canada Retail Sales (Jul)",  "Detail": ""},
        {"Date": "2026-10-01", "Release": "Canada GDP (Q2 Final)",      "Detail": ""},
        {"Date": "2026-10-17", "Release": "Canada CPI (Sep)",           "Detail": ""},
        {"Date": "2026-10-28", "Release": "BOC Rate Decision",          "Detail": ""},
        {"Date": "2026-10-30", "Release": "Canada GDP (Aug)",           "Detail": ""},
        {"Date": "2026-12-09", "Release": "BOC Rate Decision",          "Detail": ""},
    ])

    # Find the nearest upcoming event date (for bold/colored row highlight)
    import datetime as _dt_cal
    _today_cal = _dt_cal.date.today()
    _next_event_date: str | None = None
    for _ecal_row in _econ_calendar.itertuples():
        try:
            _ecal_d = _dt_cal.date.fromisoformat(_ecal_row.Date)
            if _ecal_d >= _today_cal:
                _next_event_date = _ecal_row.Date
                break
        except Exception:
            pass

    def _highlight_cal(row):
        if row["Release"] == "BOC Rate Decision":
            return ["background-color: #78350f; color: #fde68a;"] * len(row)
        if _next_event_date and row["Date"] == _next_event_date:
            return ["background-color: #1e3a5f; color: #93c5fd; font-weight: bold;"] * len(row)
        return [""] * len(row)

    _styled_cal = _econ_calendar.style.apply(_highlight_cal, axis=1)
    st.dataframe(_styled_cal, use_container_width=False, hide_index=True)
    if _next_event_date:
        st.caption(f"Scheduled releases that may move KXBOC and KXCAD markets · Next: {_next_event_date} (highlighted)")
    else:
        st.caption("Scheduled releases that may move KXBOC and KXCAD markets")

    # --- Load data -----
    df, err = get_canadian_markets()
    arb_df, _ = get_live_arb_opportunities(canadian_only=True, min_net_edge_cents=0.0)
    hist_stats = get_canadian_historical_arb_stats()

    # Numeric columns arrive as object dtype whenever the latest snapshot is
    # missing (LEFT JOIN LATERAL yields NULLs), which breaks .sum()/.nlargest().
    for _col in ("volume", "open_interest", "yes_bid", "yes_ask", "last_price", "spread"):
        if _col in df.columns:
            df[_col] = pd.to_numeric(df[_col], errors="coerce")

    # --- KPI row -----
    k1, k2, k3, k4, k5 = st.columns(5)

    n_can = len(df) if not df.empty else 0
    # In snapshot mode, supplement markets table count with candlestick count
    _can_cs_count = 0
    _can_cs_vol = 0
    if _is_sqlite:
        try:
            from dashboard.data_layer import _sqlite_conn as _gsc_p08
            _csc = _gsc_p08()
            if _csc:
                _csc_r = _csc.execute("""
SELECT COUNT(DISTINCT market_id), SUM(volume)
FROM candlesticks
WHERE market_id LIKE 'KXCB%' OR market_id LIKE 'KXBOC%'
""").fetchone()
                _csc.close()
                if _csc_r:
                    _can_cs_count = int(_csc_r[0] or 0)
                    _can_cs_vol   = int(_csc_r[1] or 0)
        except Exception:
            pass
    _n_can_total = n_can + _can_cs_count
    k1.metric("CANADIAN MARKETS",
              f"{_n_can_total:,}" + ("*" if _can_cs_count > 0 and n_can == 0 else ""),
              help="*From candlestick data — main markets table has no Canadian rows in snapshot")

    n_arb = len(arb_df) if not arb_df.empty else 0
    k2.metric("ACTIVE CANADIAN ARB", f"{n_arb:,}")

    if _is_sqlite:
        _vol_label = f"{_can_cs_vol:,}" if _can_cs_vol > 0 else "--"
        k3.metric("TOTAL VOLUME", _vol_label)
    else:
        vol = df["volume"].sum() if not df.empty and "volume" in df.columns else 0
        k3.metric("TOTAL VOLUME", f"{int(vol):,}")

    # Best opportunity
    if not arb_df.empty and "net_edge_cents" in arb_df.columns:
        best = arb_df["net_edge_cents"].max()
        k4.metric("BEST ARB EDGE", f"+{float(best):.2f}c" if pd.notna(best) else "--")
    else:
        k4.metric("BEST ARB EDGE", "--")

    # Liquid markets (has both bid & ask) — use live WS data when available
    _ws_state_k5 = get_live_state_p08()
    _ws_conn_k5  = _ws_state_k5.get_stats().get("connected", False)
    if _ws_conn_k5:
        _all_q_k5 = _ws_state_k5.snapshot_all()
        _CAN_PREFIXES = ("KXCBDECISIONCANADA", "KXBOC", "KXCAD", "KXCORR", "KXCAHOUSEDEM")
        _can_liquid_ws = sum(
            1 for t, q in _all_q_k5.items()
            if any(t.upper().startswith(pfx) for pfx in _CAN_PREFIXES)
            and (q.yes_bid or 0) > 0 and (q.yes_ask or 0) > 0
        )
        k5.metric("LIQUID MARKETS", f"{_can_liquid_ws:,}", help="Canadian tickers with live bid & ask from WS")
    elif _is_sqlite:
        k5.metric("LIQUID MARKETS", "--")
    elif not df.empty and "yes_bid" in df.columns and "yes_ask" in df.columns:
        liquid = df[df["yes_bid"].notna() & df["yes_ask"].notna()]
        k5.metric("LIQUID MARKETS", f"{len(liquid):,}")
    else:
        k5.metric("LIQUID MARKETS", "--")

    st.markdown("<hr>", unsafe_allow_html=True)

    # --- Live WS Canadian quotes (when WebSocket connected) -----
    _ws_state = get_live_state_p08()
    _ws_connected_p08 = _ws_state.get_stats().get("connected", False)
    if not _ws_connected_p08:
        st.markdown(
            f"<div style='font-size:0.7rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
            f"margin-bottom:0.5rem;'>Live Canadian quotes will appear here when the Synthesis "
            f"WebSocket is connected (add <code>SYNTHESIS_SECRET_KEY</code> to Streamlit secrets).</div>",
            unsafe_allow_html=True,
        )
    if _ws_connected_p08:
        _all_quotes_p08 = _ws_state.snapshot_all()
        _CAN_PREFIXES_LIVE = ("KXCBDECISIONCANADA", "KXBOC", "KXCAD", "KXCORR", "KXCAHOUSEDEM")
        _can_live = {
            t: q for t, q in _all_quotes_p08.items()
            if any(t.upper().startswith(pfx) for pfx in _CAN_PREFIXES_LIVE)
        }
        if _can_live:
            st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)
            st.markdown(
                f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                f"color:{GREEN};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
                f"● LIVE WS — CANADIAN MARKET QUOTES ({len(_can_live)} tickers)</div>",
                unsafe_allow_html=True,
            )
            import pandas as _pd_p08
            _live_rows = []
            for _t, _q in sorted(_can_live.items()):
                _yes_bid = _q.yes_bid if (_q.yes_bid or 0) > 0 else None
                _yes_ask = _q.yes_ask if (_q.yes_ask or 0) > 0 else None
                # Complement spread: cost of owning YES + NO on same contract
                # no_ask (implied) = 1 - yes_bid; combined should be >= 1.00
                _no_ask_impl = round(1.0 - _yes_bid, 4) if _yes_bid is not None else None
                _compl = round(_yes_ask + _no_ask_impl, 4) if (_yes_ask is not None and _no_ask_impl is not None) else None
                _age_s = _q.age_seconds if (_q.age_seconds is not None) else 0
                _live_rows.append({
                    "TICKER":       _t,
                    "BID":          f"{_yes_bid*100:.0f}c" if _yes_bid is not None else "--",
                    "ASK":          f"{_yes_ask*100:.0f}c" if _yes_ask is not None else "--",
                    "MID":          f"{(_q.mid or 0)*100:.1f}c" if (_q.mid or 0) > 0 else "--",
                    "SPREAD":       f"{(_q.spread or 0)*100:.0f}c",
                    "COMPL SPREAD": f"{_compl:.4f}" if _compl is not None else "--",
                    "AGE":          (
                        f"{int(_age_s//3600)}h{int((_age_s%3600)//60)}m"
                        if _age_s >= 3600
                        else f"{int(_age_s//60)}m{int(_age_s%60)}s"
                        if _age_s >= 60
                        else f"{int(_age_s)}s"
                    ),
                })
            # --- Quote staleness warning -----
            _all_ages = [
                _q.age_seconds for _q in _can_live.values()
                if _q.age_seconds is not None
            ]
            if _all_ages:
                _max_age = max(_all_ages)
                _age_col = AMBER if _max_age > 60 else GREEN
                st.markdown(
                    f"<div style='font-size:0.6rem;color:{_age_col};font-family:JetBrains Mono,monospace;"
                    f"margin-bottom:0.3rem;'>● quotes {int(_max_age)}s old</div>",
                    unsafe_allow_html=True,
                )

            st.dataframe(
                _pd_p08.DataFrame(_live_rows),
                use_container_width=True,
                height=min(300, 36 + 35 * len(_live_rows)),
                hide_index=True,
            )
            st.caption(
                "Column guide — "
                "BID/ASK: YES contract best bid and ask (cents). "
                "MID: midpoint of bid and ask. "
                "SPREAD: bid-ask spread width (cents). "
                "COMPL SPREAD: combined cost of owning YES + NO on the same contract "
                "(YES ask + (1 − YES bid)); should be ≥ 1.00 — values below 1.00 indicate a "
                "crossed/inverted market (potential riskless two-leg arb). "
                "AGE: time since last quote update from the WebSocket feed."
            )
        st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)

    # --- Classifier note -----
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {BLUE};
padding:0.75rem 1rem;border-radius:3px;margin-bottom:1rem;'>
<div style='font-size:0.72rem;color:{TEXT2};font-family:JetBrains Mono,monospace;line-height:1.6;'>
Kalshi currently has limited Canadian-specific market coverage.<br>
The classifier continuously monitors the full venue for newly listed
Canadian-relevant contracts.
</div>
</div>""",
        unsafe_allow_html=True,
    )

    # --- Historical arb section -----
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.12em;text-transform:uppercase;"
        f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
        f"HISTORICAL ARB (CANADIAN MARKETS)</div>",
        unsafe_allow_html=True,
    )
    _render_historical_arb(hist_stats)
    st.markdown("<hr>", unsafe_allow_html=True)

    # --- FED WATCH section -----
    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.12em;text-transform:uppercase;"
        f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
        f"FED WATCH — US FEDERAL RESERVE RATE MARKETS</div>",
        unsafe_allow_html=True,
    )

    # --- Next FOMC countdown -----
    import datetime as _dt_fomc
    _FOMC_2026 = [
        _dt_fomc.date(2026, 1, 29),
        _dt_fomc.date(2026, 3, 18),
        _dt_fomc.date(2026, 5, 6),
        _dt_fomc.date(2026, 6, 17),
        _dt_fomc.date(2026, 7, 29),
        _dt_fomc.date(2026, 9, 17),
        _dt_fomc.date(2026, 10, 28),
        _dt_fomc.date(2026, 12, 9),
    ]
    _today_fomc = _dt_fomc.date.today()
    _now_fomc_dt = _dt_fomc.datetime.utcnow()
    _future_fomc = [d for d in _FOMC_2026 if d >= _today_fomc]
    if _future_fomc:
        _next_fomc = min(_future_fomc)
        # FOMC decisions typically announced ~2pm ET = 19:00 UTC
        _next_fomc_dt = _dt_fomc.datetime(_next_fomc.year, _next_fomc.month, _next_fomc.day, 19, 0, 0)
        _td_fomc = _next_fomc_dt - _now_fomc_dt
        _total_secs_fomc = max(0, int(_td_fomc.total_seconds()))
        _days_to_fomc = _total_secs_fomc // 86400
        _hours_to_fomc = (_total_secs_fomc % 86400) // 3600
        if _days_to_fomc > 0:
            _fomc_countdown_str = f"in {_days_to_fomc}d {_hours_to_fomc}h"
        elif _hours_to_fomc > 0:
            _fomc_countdown_str = f"in {_hours_to_fomc}h"
        else:
            _fomc_countdown_str = "today"
        st.metric("NEXT FOMC DECISION", _fomc_countdown_str,
                  help=f"Next FOMC meeting: {_next_fomc.strftime('%Y-%m-%d')} (~2pm ET)")
        st.caption("Federal Reserve rate decisions — FOMC meets approximately every 6 weeks")

    # --- Pull KXFED from LiveState snapshot + DB -----
    _fed_tickers_p08 = pd.DataFrame()
    # Try live WS first
    _ws_state_fed = get_live_state_p08()
    _ws_conn_fed = _ws_state_fed.get_stats().get("connected", False)
    _fed_implied_cut_prob = None  # for Fed vs BOC divergence
    _boc_implied_cut_prob = None

    if _ws_conn_fed:
        _all_q_fed = _ws_state_fed.snapshot_all()
        _kxfed_live = {t: q for t, q in _all_q_fed.items() if t.upper().startswith("KXFED")}
        if _kxfed_live:
            import pandas as _pd_fed_ws
            _fed_ws_rows = []
            for _ft, _fq in sorted(_kxfed_live.items()):
                _fbid = _fq.yes_bid if (_fq.yes_bid or 0) > 0 else None
                _fask = _fq.yes_ask if (_fq.yes_ask or 0) > 0 else None
                _fmid = _fq.mid if (_fq.mid or 0) > 0 else None
                _fed_ws_rows.append({
                    "ticker": _ft, "title": _ft,
                    "last_price": _fmid, "yes_bid": _fbid, "yes_ask": _fask,
                })
            _fed_tickers_p08 = _pd_fed_ws.DataFrame(_fed_ws_rows)

    # Fallback to DB snapshot
    if _fed_tickers_p08.empty and not df.empty and "ticker" in df.columns:
        _fed_tickers_p08 = df[df["ticker"].str.upper().str.startswith("KXFED")].copy()

    if _fed_tickers_p08.empty:
        st.info("No KXFED markets in current snapshot")
    else:
        # Implied Fed funds probability distribution
        _fed_outcomes = {"Hold": 0.0, "+25 bps": 0.0, "+50 bps": 0.0, "-25 bps": 0.0}
        _fed_outcome_kws = {
            "Hold":    ["hold", "unchanged", "no change"],
            "+25 bps": ["+25", "25bp", "hike", "increase"],
            "+50 bps": ["+50", "50bp"],
            "-25 bps": ["-25", "25bp cut", "cut", "decrease", "lower"],
        }
        _fed_found = False
        for _, _frow in _fed_tickers_p08.iterrows():
            _ftitle_l = ((str(_frow.get("title", "")) or "") + " " + (str(_frow.get("ticker", "")) or "")).lower()
            _fmid = _frow.get("last_price") if pd.notna(_frow.get("last_price") if _frow.get("last_price") is not None else float("nan")) else _frow.get("yes_bid")
            if _fmid is None or (isinstance(_fmid, float) and pd.isna(_fmid)):
                continue
            _fmid = float(_fmid)
            for _fout, _fkws in _fed_outcome_kws.items():
                if any(_fkw in _ftitle_l for _fkw in _fkws):
                    _fed_outcomes[_fout] = max(_fed_outcomes[_fout], _fmid)
                    _fed_found = True
        _fed_total = sum(_fed_outcomes.values())
        if _fed_found and _fed_total > 0:
            _fed_vals_norm = {k: round(v / _fed_total * 100, 1) for k, v in _fed_outcomes.items()}
            _fed_implied_cut_prob = _fed_vals_norm.get("-25 bps", 0.0)

            _fed_labels = list(_fed_outcomes.keys())
            _fed_vals   = [_fed_vals_norm[k] for k in _fed_labels]
            _fed_colors = [BLUE, AMBER, RED, GREEN]
            # Horizontal bar chart — one bar per outcome (same style as BOC section)
            _fig_fed = go.Figure()
            for _fl, _fv, _fc in zip(_fed_labels, _fed_vals, _fed_colors):
                _fig_fed.add_trace(go.Bar(
                    name=_fl,
                    x=[_fv],
                    y=[_fl],
                    orientation="h",
                    marker_color=_fc,
                    text=[f"{_fv:.1f}%"],
                    textposition="outside",
                    textfont={"size": 10},
                    hovertemplate=f"{_fl}: %{{x:.1f}}%<extra></extra>",
                ))
            _fig_fed.update_layout(
                **plotly_dark_layout(
                    title={"text": "FED RATE DECISION — IMPLIED PROBABILITIES (from market prices)",
                           "font": {"size": 9, "color": TEXT3}},
                    barmode="group", height=220, showlegend=False,
                    margin={"t": 35, "b": 10, "l": 80, "r": 60},
                    xaxis={"range": [0, 110], "ticksuffix": "%", "tickfont": {"size": 8}},
                    yaxis={"tickfont": {"size": 10}, "autorange": "reversed"},
                )
            )
            st.plotly_chart(_fig_fed, use_container_width=True)
        else:
            st.info("No KXFED markets with valid prices found to build distribution.")

        _show_cols_fed = [c for c in ["ticker", "title", "last_price", "yes_bid", "yes_ask"] if c in _fed_tickers_p08.columns]
        if _show_cols_fed:
            st.dataframe(
                _fed_tickers_p08[_show_cols_fed].rename(
                    columns={"ticker": "TICKER", "title": "TITLE",
                             "last_price": "LAST", "yes_bid": "BID", "yes_ask": "ASK"}
                ),
                use_container_width=True, hide_index=True,
            )

    # --- Fed vs BOC Divergence -----
    # Derive BOC implied cut probability from KXBOC markets in df
    try:
        if not df.empty and "ticker" in df.columns:
            _boc_mkts = df[df["ticker"].str.upper().str.startswith("KXBOC")]
            _boc_cut_prob = 0.0
            for _, _br in _boc_mkts.iterrows():
                _bt_l = ((str(_br.get("title", "")) or "") + " " + (str(_br.get("ticker", "")) or "")).lower()
                _bmid = _br.get("last_price") if pd.notna(_br.get("last_price") if _br.get("last_price") is not None else float("nan")) else _br.get("yes_bid")
                if _bmid is None or (isinstance(_bmid, float) and pd.isna(_bmid)):
                    continue
                if any(kw in _bt_l for kw in ["cut", "-25", "-50", "decrease", "lower"]):
                    _boc_cut_prob = max(_boc_cut_prob, float(_bmid))
            if _boc_cut_prob > 0:
                _boc_implied_cut_prob = _boc_cut_prob * 100.0
    except Exception:
        pass

    if _fed_implied_cut_prob is not None and _boc_implied_cut_prob is not None:
        st.markdown("<hr style='margin:0.5rem 0 0.5rem 0;'>", unsafe_allow_html=True)
        _divergence = round(_fed_implied_cut_prob - _boc_implied_cut_prob, 1)
        # BOC vs Fed divergence score: |BOC_implied_prob - FOMC_implied_prob| × 100
        # Both probs are already in percent so score = |diff| as percentage points
        _div_score = round(abs(_fed_implied_cut_prob - _boc_implied_cut_prob), 1)
        _div_col1, _div_col2 = st.columns(2)
        _div_col1.metric(
            "FED vs BOC DIVERGENCE",
            f"{_divergence:+.1f}pp",
            help=(
                f"Fed implied cut probability: {_fed_implied_cut_prob:.1f}% — "
                f"BOC implied cut probability: {_boc_implied_cut_prob:.1f}%. "
                "Positive = Fed more dovish than BOC."
            ),
        )
        _div_col2.metric(
            "BOC–FED DIVERGENCE SCORE",
            f"{_div_score:.1f}",
            help=(
                f"|BOC implied cut prob − FOMC implied cut prob| × 100 = "
                f"|{_boc_implied_cut_prob:.1f}% − {_fed_implied_cut_prob:.1f}%|. "
                "Higher score = markets disagree more on rate direction. "
                "Score > 20 = significant divergence; < 5 = broadly aligned."
            ),
        )
        if _divergence > 5:
            st.info(f"Fed more dovish than BOC ({_fed_implied_cut_prob:.0f}% vs {_boc_implied_cut_prob:.0f}%) — rate differential may narrow")
        elif _divergence < -5:
            st.info(f"BOC more dovish than Fed ({_boc_implied_cut_prob:.0f}% vs {_fed_implied_cut_prob:.0f}%) — CAD may weaken vs USD")
        else:
            st.caption(f"Fed and BOC broadly aligned (Fed cut: {_fed_implied_cut_prob:.0f}%, BOC cut: {_boc_implied_cut_prob:.0f}%)")

    # --- FOMC Meeting Calendar 2026 -----
    st.markdown("<hr style='margin:0.5rem 0 0.5rem 0;'>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.3rem;'>"
        f"FOMC MEETING CALENDAR 2026</div>",
        unsafe_allow_html=True,
    )
    _fomc_cal_rows = []
    for _fd in _FOMC_2026:
        _fd_days = (_fd - _today_fomc).days
        _fd_status = "PAST" if _fd_days < 0 else ("TODAY" if _fd_days == 0 else f"in {_fd_days}d")
        _fomc_cal_rows.append({"Date": _fd.strftime("%Y-%m-%d"), "Days": _fd_status})
    _fomc_cal_df = pd.DataFrame(_fomc_cal_rows)
    st.dataframe(_fomc_cal_df, use_container_width=False, hide_index=True)

    st.caption("Fed decisions affect CAD/USD and indirectly influence BOC policy")
    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    # --- Category sections -----
    if df.empty:
        if err and not _is_sqlite:
            _unavailable()
        else:
            _empty_state()
        # Even with empty markets table, surface Canadian markets from candlestick DB
        if _is_sqlite:
            _render_canadian_from_candlesticks()
        return

    # Categorise markets by keyword matching in title
    def _classify(row) -> str:
        title  = (row["title"]  if "title"  in row.index else "") or ""
        ticker = (row["ticker"] if "ticker" in row.index else "") or ""
        title_low = title.lower()
        for cat, keywords in _CATEGORIES.items():
            if cat in ("Other Canada", "Elections/Multi-Candidate"):
                continue
            if keywords and any(kw in title_low for kw in keywords):
                return cat
        # SHARD tickers are multi-victor election events
        if ticker and "SHARD" in str(ticker).upper():
            return "Elections/Multi-Candidate"
        return "Other Canada"

    df["_category"] = df.apply(_classify, axis=1)

    for cat_name in _CATEGORIES:
        cat_df = df[df["_category"] == cat_name]
        _render_category(cat_name, cat_df)

    # --- Volume chart -----
    if "volume" in df.columns and df["volume"].notna().any():
        st.markdown("<hr>", unsafe_allow_html=True)
        top_by_vol = df.dropna(subset=["volume"]).nlargest(20, "volume")
        fig = go.Figure(go.Bar(
            y=top_by_vol["ticker"],
            x=top_by_vol["volume"],
            orientation="h",
            marker_color=BLUE, marker_line_width=0,
            text=top_by_vol["volume"].apply(lambda v: f"{int(v):,}"),
            textposition="outside",
            textfont={"size": 9, "family": "JetBrains Mono"},
        ))
        fig.update_layout(
            **plotly_dark_layout(
            title={"text": "TOP 20 CANADIAN MARKETS BY VOLUME", "font": {"size": 10, "color": TEXT3}},
            height=450,
            xaxis_title="Volume", yaxis_title="",
            yaxis={"autorange": "reversed", "tickfont": {"size": 9}},
        ))
        st.plotly_chart(fig, use_container_width=True)

    # --- Auto-refresh when WS live -----
    _ws_live_p08 = get_live_state_p08().get_stats().get("connected", False)
    if _ws_live_p08:
        import time as _t_p08
        _now_p08 = _t_p08.time()
        if st.session_state.get("_p08_next_refresh", 0) <= _now_p08:
            st.session_state["_p08_next_refresh"] = _now_p08 + 10
            st.rerun()


_CATEGORY_EMPTY_NOTES = {
    "Bank of Canada": (
        "No active BOC rate markets found. Markets typically open 4–6 weeks before each "
        "policy meeting date. Check back closer to the next scheduled meeting."
    ),
    "Canadian Economy": (
        "No active Canadian economy markets (GDP, CPI, housing, FX). "
        "Housing market contracts (KXCAHOUSEDEM) appear when new seat-count events are listed. "
        "FX markets (KXCAD-*) are usually listed around major data releases."
    ),
    "Canadian Politics": (
        "No active Canadian politics markets. These appear around elections, "
        "leadership races, and parliamentary confidence votes."
    ),
    "Canadian Sports": (
        "No active Canadian sports markets. Hockey, baseball, and basketball markets "
        "are typically listed during regular season and playoffs."
    ),
}


def _render_category(name: str, df: pd.DataFrame):
    if df.empty:
        _note = _CATEGORY_EMPTY_NOTES.get(name, "")
        _note_html = (
            f"<span style='font-family:JetBrains Mono,monospace;font-size:0.62rem;"
            f"color:{TEXT3};margin-left:0.5rem;'>{_note}</span>"
            if _note else ""
        )
        st.markdown(
            f"""<div style='margin-bottom:0.5rem;'>
<span style='font-size:0.65rem;letter-spacing:0.1em;text-transform:uppercase;
color:{TEXT3};font-family:Inter,sans-serif;'>{name}</span>
<span style='font-family:JetBrains Mono,monospace;font-size:0.65rem;
color:{TEXT3};margin-left:0.75rem;'>NO ACTIVE MARKETS</span>
{_note_html}
</div>""",
            unsafe_allow_html=True,
        )
        return

    with st.expander(f"{name} ({len(df)} markets)", expanded=(name == "Bank of Canada")):
        # --- BOC rate probability bar -----
        if name == "Bank of Canada":
            # Check if any KXBOC tickers are present in this category
            _has_kxboc = (
                "ticker" in df.columns
                and df["ticker"].str.upper().str.startswith("KXBOC").any()
            ) if not df.empty else False
            if not _has_kxboc:
                st.info(
                    "No KXBOC rate markets found in current snapshot. "
                    "BOC rate probability markets (KXBOC-*) are listed by Kalshi approximately "
                    "4–6 weeks before each Bank of Canada policy meeting. Check back closer to "
                    "the next scheduled decision date."
                )
            else:
                _boc_outcomes = {
                    "HOLD": 0.0, "CUT25": 0.0, "CUT50": 0.0, "HIKE": 0.0,
                }
                _outcome_kws = {
                    "HOLD":  ["hold", "unchanged", "no change"],
                    "CUT25": ["-25", "25bp cut", "cut 25", "decrease 25", "lower 25"],
                    "CUT50": ["-50", "50bp cut", "cut 50", "decrease 50", "lower 50"],
                    "HIKE":  ["+25", "+50", "hike", "increase"],
                }
                _outcome_colors = {
                    "HOLD":  "#6B7280",   # gray
                    "CUT25": "#3B82F6",   # blue
                    "CUT50": "#1E3A8A",   # dark blue
                    "HIKE":  "#EF4444",   # red
                }
                _outcome_labels = {
                    "HOLD":  "Hold",
                    "CUT25": "Cut 25bps",
                    "CUT50": "Cut 50bps",
                    "HIKE":  "Hike",
                }
                _boc_found = False
                for _, _row in df.iterrows():
                    _title_l = ((str(_row.get("title", "")) or "") + " " + (str(_row.get("ticker", "")) or "")).lower()
                    _mid_val = _row.get("last_price") if pd.notna(_row.get("last_price")) else _row.get("yes_bid")
                    if pd.isna(_mid_val):
                        continue
                    _mid_val = float(_mid_val)
                    for _out, _kws in _outcome_kws.items():
                        if any(_kw in _title_l for _kw in _kws):
                            _boc_outcomes[_out] = max(_boc_outcomes[_out], _mid_val)
                            _boc_found = True
                _total = sum(_boc_outcomes.values())
                if _boc_found and _total > 0:
                    _vals_pct = {k: round(v / _total * 100, 1) for k, v in _boc_outcomes.items()}
                    # Consensus = outcome with highest probability
                    _consensus_key = max(_vals_pct, key=lambda k: _vals_pct[k])
                    _consensus_pct = _vals_pct[_consensus_key]
                    _consensus_label = _outcome_labels[_consensus_key]
                    st.metric(
                        "MARKET CONSENSUS",
                        f"{_consensus_label} ({_consensus_pct:.0f}%)",
                        help="Outcome with the highest implied probability from KXBOC market prices",
                    )
                    # Progress bar: distance from 50% consensus threshold
                    _pb_color = GREEN if _consensus_pct >= 70 else (AMBER if _consensus_pct >= 55 else RED)
                    _dist_50 = abs(_consensus_pct - 50.0)
                    st.markdown(
                        f"<div style='margin:0.35rem 0 0.6rem 0;'>"
                        f"<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.08em;"
                        f"text-transform:uppercase;font-family:Inter,sans-serif;margin-bottom:3px;'>"
                        f"CONSENSUS STRENGTH (vs 50% threshold)</div>"
                        f"<div style='background:{BORDER};border-radius:2px;height:6px;"
                        f"width:100%;position:relative;overflow:hidden;'>"
                        f"<div style='background:{_pb_color};height:6px;width:{min(_consensus_pct,100):.1f}%;"
                        f"border-radius:2px;'></div></div>"
                        f"<div style='font-size:0.6rem;color:{_pb_color};font-family:JetBrains Mono,monospace;"
                        f"margin-top:2px;'>{_consensus_pct:.1f}% implied · {_dist_50:.1f}pp from 50% threshold"
                        f"{'  ✓ strong conviction' if _dist_50 >= 20 else '  ~ moderate' if _dist_50 >= 10 else '  ~ near coin-flip'}"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )
                    # Horizontal bar chart — one bar per outcome
                    _fig_boc = go.Figure()
                    for _out_key in ["HOLD", "CUT25", "CUT50", "HIKE"]:
                        _v = _vals_pct[_out_key]
                        _fig_boc.add_trace(go.Bar(
                            name=_outcome_labels[_out_key],
                            x=[_v],
                            y=[_outcome_labels[_out_key]],
                            orientation="h",
                            marker_color=_outcome_colors[_out_key],
                            text=[f"{_v:.1f}%"],
                            textposition="outside",
                            textfont={"size": 10},
                            hovertemplate=f"{_outcome_labels[_out_key]}: %{{x:.1f}}%<extra></extra>",
                        ))
                    _fig_boc.update_layout(
                        **plotly_dark_layout(
                            title={"text": "BOC RATE DECISION — IMPLIED PROBABILITIES (from market prices)",
                                   "font": {"size": 9, "color": TEXT3}},
                            barmode="group",
                            height=220,
                            showlegend=False,
                            margin={"t": 35, "b": 10, "l": 80, "r": 60},
                            xaxis={"range": [0, 110], "ticksuffix": "%", "tickfont": {"size": 8}},
                            yaxis={"tickfont": {"size": 10}, "autorange": "reversed"},
                        )
                    )
                    st.plotly_chart(_fig_boc, use_container_width=True)
                else:
                    st.info("No KXBOC rate markets with valid prices found to build probability chart.")

        # --- CAD/USD FX implied rate -----
        if name == "Canadian Economy":
            # Check live WS for KXCAD markets regardless of DB snapshot
            _live_state_cad = get_live_state_p08()
            _kxcad_live: dict = {}
            if _live_state_cad.get_stats().get("connected", False):
                _all_q_cad = _live_state_cad.snapshot_all()
                _kxcad_live = {t: q for t, q in _all_q_cad.items() if t.upper().startswith("KXCAD")}
            # Build distribution DataFrame from live WS KXCAD markets
            import re as _re_cad
            _dist_rows = []
            for _ct, _cq in _kxcad_live.items():
                _cbid = _cq.yes_bid if (_cq.yes_bid or 0) > 0 else None
                if _cbid is None:
                    continue
                # Parse strike: KXCAD-25DEC25-B0.75 → 0.75, or KXCAD-0.72 → 0.72
                _cstrike_match = _re_cad.search(r"-B([\d.]+)", _ct.upper())
                if not _cstrike_match:
                    _cstrike_match = _re_cad.search(r"-([\d.]+)$", _ct)
                if _cstrike_match:
                    try:
                        _dist_rows.append({"strike": float(_cstrike_match.group(1)), "yes_bid": _cbid, "ticker": _ct})
                    except ValueError:
                        pass
            if _dist_rows:
                import pandas as _pd_cad_dist
                _dist_df = _pd_cad_dist.DataFrame(_dist_rows).sort_values("strike")
                _fig_cad = go.Figure()
                _fig_cad.add_trace(go.Scatter(
                    x=_dist_df["strike"].tolist(),
                    y=(_dist_df["yes_bid"] * 100).tolist(),
                    mode="lines+markers",
                    line={"shape": "hv", "color": BLUE, "width": 2},
                    marker={"size": 6, "color": CYAN},
                    name="YES bid (%)",
                    hovertemplate="Strike: %{x:.4f}<br>Implied prob: %{y:.1f}%<extra></extra>",
                ))
                _fig_cad.update_layout(**plotly_dark_layout(
                    title={"text": "KXCAD — IMPLIED CAD/USD PROBABILITY DISTRIBUTION",
                           "font": {"size": 9, "color": TEXT3}},
                    height=220,
                    xaxis_title="Implied CAD/USD Strike",
                    yaxis_title="YES Bid (implied prob %)",
                    margin={"l": 50, "r": 20, "t": 35, "b": 40},
                    yaxis={"range": [0, 105]},
                ))
                st.plotly_chart(_fig_cad, use_container_width=True)
                st.caption("Implied CAD/USD probability distribution from Kalshi prediction markets")
            else:
                st.info("No KXCAD markets currently tracked — check back when BOC decision approaches")

            _cad_rows = df[df["ticker"].str.upper().str.startswith("KXCAD")] if "ticker" in df.columns else pd.DataFrame()
            if not _cad_rows.empty:
                # Each KXCAD-0.XX contract: YES price ≈ P(CAD/USD > threshold)
                # Implied spot ≈ weighted midpoint of thresholds
                _fx_rows = []
                for _, _fr in _cad_rows.iterrows():
                    _tick = str(_fr.get("ticker", ""))
                    try:
                        _thresh = float(_tick.split("-")[-1])
                    except ValueError:
                        continue
                    _mid_fx = _fr.get("last_price") if pd.notna(_fr.get("last_price")) else _fr.get("yes_bid")
                    if pd.notna(_mid_fx):
                        _fx_rows.append((_thresh, float(_mid_fx)))
                if _fx_rows:
                    # Weighted midpoint: sum(thresh * p) / sum(p)
                    _tot_p = sum(p for _, p in _fx_rows)
                    _cadusd = sum(t * p for t, p in _fx_rows) / _tot_p if _tot_p > 0 else None
                    if _cadusd:
                        import datetime as _dt_fx
                        _ts_fx = _dt_fx.datetime.utcnow().strftime("%H:%M:%S UTC")
                        st.metric(
                            label="IMPLIED CAD/USD (from market prices)",
                            value=f"1 USD = {1/_cadusd:.4f} CAD" if _cadusd > 0 else "--",
                            help=f"Weighted midpoint from {len(_fx_rows)} KXCAD threshold market(s). Updated {_ts_fx}.",
                        )
                        st.caption("Source: Kalshi FX markets (implied from YES prices)")
                        # CAD/USD implied vs spot comparison
                        from dashboard.data_layer import get_latest_external_prices as _get_ext_p08
                        _ext_p08 = _get_ext_p08() or {}
                        _spot_cadusd_p08 = float(_ext_p08.get("CADUSD") or 0)
                        if _spot_cadusd_p08 > 0 and _cadusd > 0:
                            _diff_pct_p08 = (_cadusd - _spot_cadusd_p08) / _spot_cadusd_p08 * 100
                            _cmp1, _cmp2, _cmp3 = st.columns(3)
                            _cmp1.metric("SPOT CAD/USD (yfinance)", f"{_spot_cadusd_p08:.4f}")
                            _cmp2.metric("IMPLIED CAD/USD (Kalshi)", f"{_cadusd:.4f}")
                            _cmp3.metric(
                                "IMPLIED vs SPOT",
                                f"{_diff_pct_p08:+.2f}%",
                                help="Positive = Kalshi implies CAD stronger than yfinance spot",
                            )
                            st.caption(
                                "Implied vs spot divergence: positive means prediction markets imply "
                                "a stronger CAD than the current spot rate; negative means weaker."
                            )
                        else:
                            st.info(
                                "Spot CAD/USD not available from external data feed. "
                                "Connect the yfinance pipeline (run the external market data ingestion script) "
                                "to enable the implied vs spot comparison. "
                                "The implied rate above is derived from Kalshi prediction markets only."
                            )

                        # ---- PARITY PROBABILITY ----
                        st.markdown("<br>", unsafe_allow_html=True)
                        st.markdown(
                            f"<div style='font-size:0.62rem;letter-spacing:0.1em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.4rem;'>PARITY PROBABILITY</div>",
                            unsafe_allow_html=True,
                        )
                        _parity_ticker = None
                        _parity_prob = None
                        for _pt, _pp in _fx_rows:
                            # KXCAD-1.00 resolves YES if CAD/USD >= 1.00 (parity)
                            if abs(_pt - 1.00) < 0.001:
                                _parity_prob = _pp
                                break
                        if _parity_prob is not None:
                            st.metric("PARITY PROBABILITY (CAD=USD)", f"{_parity_prob*100:.1f}c")
                        else:
                            st.metric("PARITY PROBABILITY (CAD=USD)", "No parity market in snapshot")
                        st.caption("CAD/USD parity = 1 USD buys exactly 1 CAD. Last occurred in 2012.")
                        # ---- END PARITY PROBABILITY ----

                        with st.expander("📐 How this is calculated", expanded=False):
                            st.markdown(
                                "- Each **KXCAD-X.XX** market resolves YES if CAD/USD closes above X.XX\n"
                                "- The **YES price** = the market's implied probability of being above that threshold\n"
                                "- We compute the **probability-weighted midpoint** of all threshold strikes as the implied spot rate\n"
                                "- This is an **approximation** — actual market rates may differ"
                            )

        display = df.copy()

        if "yes_bid" in display.columns:
            display["BID"] = display["yes_bid"].apply(
                lambda v: f"{float(v)*100:.0f}c" if pd.notna(v) else "--"
            )
        if "yes_ask" in display.columns:
            display["ASK"] = display["yes_ask"].apply(
                lambda v: f"{float(v)*100:.0f}c" if pd.notna(v) else "--"
            )
        if "last_price" in display.columns:
            display["LAST"] = display["last_price"].apply(
                lambda v: f"{float(v)*100:.0f}c" if pd.notna(v) else "--"
            )
        if "volume" in display.columns:
            display["VOLUME"] = display["volume"].apply(
                lambda v: f"{int(v):,}" if pd.notna(v) else "--"
            )
        if "close_time" in display.columns:
            display["EXPIRES"] = pd.to_datetime(
                display["close_time"], utc=True, errors="coerce"
            ).dt.strftime("%Y-%m-%d")

        show_cols = [c for c in [
            "ticker", "title", "LAST", "BID", "ASK", "VOLUME", "EXPIRES"
        ] if c in display.columns]

        st.dataframe(
            display[show_cols].rename(columns={"ticker": "TICKER", "title": "TITLE"}),
            use_container_width=True,
            height=min(200, 35 * len(df) + 45),
            hide_index=True,
        )


def _render_historical_arb(hist_stats: dict):
    """Historical arb summary for Canadian markets."""
    if hist_stats.get("error") or hist_stats.get("total_opps", 0) == 0:
        _health = get_system_health()
        _is_sqlite = not _health.get("db_connected", False) and _health.get("db_mode") == "sqlite"
        if hist_stats.get("error"):
            st.info("Historical arb scan data not yet available. Connect the data pipeline to populate Canadian market history.")
        elif _is_sqlite:
            st.markdown(
                f"<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {BLUE};"
                f"padding:0.6rem 1rem;border-radius:3px;font-family:JetBrains Mono,monospace;"
                f"font-size:0.72rem;color:{TEXT3};'>"
                f"No Canadian arb opportunities in database. "
                f"The current dataset covers general market strategies. "
                f"Live Canadian arb scanning activates when the Synthesis WebSocket is connected.</div>",
                unsafe_allow_html=True,
            )
        else:
            # Live mode, no opps yet
            st.markdown(
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT3};"
                f"background:{PANEL};border:1px solid {BORDER};padding:0.75rem;border-radius:3px;'>"
                f"No historical Canadian arb opportunities recorded yet.<br>"
                f"<span style='font-size:0.65rem;'>Run the l2_scan_canadian scanner to detect opportunities.</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        return

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("TOTAL OPPS", f"{int(hist_stats.get('total_opps', 0)):,}")
    h2.metric("CLASS A (EXECUTABLE)", f"{int(hist_stats.get('executable_opps', 0)):,}")
    h3.metric("MEDIAN EDGE", f"{float(hist_stats.get('median_edge_cents', 0)):.2f}c")
    med_lt = hist_stats.get("median_lifetime_s", 0) or 0
    _lt_label = f"{int(med_lt)}s" if med_lt < 3600 else f"{int(med_lt//3600)}h{int((med_lt%3600)//60)}m"
    h4.metric("MEDIAN LIFETIME", _lt_label)
    if med_lt == 0:
        st.caption("Median lifetime = 0s — scanner detected these opportunities at price update time; actual window may be sub-second.")


def _empty_state():
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:2rem;
border-radius:3px;text-align:center;'>
<div style='font-family:JetBrains Mono,monospace;font-size:0.85rem;color:{TEXT3};'>
NO CANADIAN MARKETS FOUND
</div>
<div style='font-size:0.72rem;color:{TEXT3};margin-top:0.5rem;'>
The classifier found no Canadian-relevant markets in the database.<br>
Ensure markets have been loaded and the events table is populated.
</div>
</div>""",
        unsafe_allow_html=True,
    )


def _render_canadian_from_candlesticks():
    """Show Canadian-relevant markets sourced from the candlestick table (snapshot fallback)."""
    from dashboard.data_layer import _sqlite_conn as _gsc_ca
    try:
        _cc = _gsc_ca()
        if not _cc:
            return
        _ca_rows = _cc.execute("""
SELECT market_id,
SUM(volume) AS total_vol,
COUNT(*) AS n_candles,
MIN(period_end_ts) AS earliest,
MAX(period_end_ts) AS latest
FROM candlesticks
WHERE market_id LIKE 'KXCB%' OR market_id LIKE 'KXBOC%'
OR market_id LIKE '%BOC%' OR market_id LIKE '%CORRA%'
OR market_id LIKE '%CANADA%'
GROUP BY market_id
ORDER BY total_vol DESC
""").fetchall()
        _cc.close()
        if not _ca_rows:
            return
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.12em;text-transform:uppercase;"
            f"color:{TEXT3};margin-bottom:0.4rem;'>CANADIAN MARKETS IN CANDLESTICK DATA</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='font-size:0.7rem;color:{TEXT2};margin-bottom:0.5rem;'>"
            f"{len(_ca_rows)} Canadian-relevant market(s) found in historical candlestick data.</div>",
            unsafe_allow_html=True,
        )
        _ca_df = pd.DataFrame(_ca_rows, columns=["TICKER", "VOLUME", "CANDLES", "EARLIEST", "LATEST"])
        _ca_df["EARLIEST"] = _ca_df["EARLIEST"].astype(str).str[:10]
        _ca_df["LATEST"] = _ca_df["LATEST"].astype(str).str[:10]
        _ca_df["VOLUME"] = _ca_df["VOLUME"].apply(lambda v: f"{int(v):,}" if (v is not None and v == v) else "--")
        st.dataframe(_ca_df, use_container_width=True, hide_index=True)
    except Exception:
        pass


def _unavailable():
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:1rem;
border-radius:3px;color:{TEXT3};font-size:0.75rem;font-family:JetBrains Mono,monospace;'>
Database unavailable. Connect a PostgreSQL or SQLite database to load Canadian market data.
</div>""",
        unsafe_allow_html=True,
    )

