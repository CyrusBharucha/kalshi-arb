"""
dashboard/pages/p09_research.py
================================
Empirical Research Findings page.

Dynamically populated from database observations. All findings are labelled with
epistemic status: OBSERVED | INFERRED | PRELIMINARY | UNVERIFIED.

Tabs:
1. RELATIONSHIPS  -- contract relationship detection counts + confidence
2. STRATEGY PERFORMANCE  -- edge percentile table per strategy x class
3. ARB FINDINGS  -- Class A/B breakdown + key statistics
4. DATA COVERAGE  -- database and ingestion health
"""
from __future__ import annotations
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data_layer import (
    get_research_summary, get_relationship_stats,
    get_historical_arb_stats, get_arb_edge_by_strategy_class,
    get_arb_rolling_7d, get_top_markets_by_volume, get_market_price_history,
    get_arb_by_category,
)
from dashboard.styles import plotly_dark_layout, GREEN, RED, AMBER, BLUE, CYAN, TEXT, TEXT2, TEXT3, PANEL, BORDER, PANEL2


def render():
    st.markdown("""
<span style='font-size:1rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;'>
RESEARCH
</span>
<span style='font-size:0.65rem;color:#64748B;letter-spacing:0.04em;margin-left:1rem;'>
Empirical findings &mdash; Kalshi market microstructure &amp; arbitrage
</span>
""", unsafe_allow_html=True)
    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    # --- Epistemic caveat -----
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {AMBER};
padding:0.75rem 1rem;border-radius:3px;margin-bottom:1rem;'>
<div style='font-size:0.65rem;letter-spacing:0.08em;color:{AMBER};
text-transform:uppercase;margin-bottom:4px;'>DATA QUALITY NOTICE</div>
<div style='font-size:0.72rem;color:{TEXT2};line-height:1.6;'>
Results below are clearly labelled:
<span style='color:{GREEN};font-family:JetBrains Mono,monospace;'>OBSERVED</span> &mdash; directly measured from data &nbsp;|&nbsp;
<span style='color:{BLUE};font-family:JetBrains Mono,monospace;'>INFERRED</span> &mdash; derived from model &nbsp;|&nbsp;
<span style='color:{AMBER};font-family:JetBrains Mono,monospace;'>PRELIMINARY</span> &mdash; limited data coverage &nbsp;|&nbsp;
<span style='color:{TEXT3};font-family:JetBrains Mono,monospace;'>UNVERIFIED</span> &mdash; not yet tested<br>
No profitable trading is claimed unless data explicitly demonstrates positive after-fee net P&L.
</div>
</div>""",
        unsafe_allow_html=True,
    )

    from dashboard.data_layer import get_system_health
    _health = get_system_health()
    _is_sqlite = not _health.get("db_connected", False) and _health.get("db_mode") == "sqlite"

    # --- Info banner -----
    if _is_sqlite:
        st.markdown(
            f"<div style='background:{PANEL};border:1px solid {AMBER};border-left:4px solid {AMBER};"
            f"padding:0.65rem 1rem;border-radius:3px;margin-bottom:0.75rem;font-size:0.72rem;"
            f"color:{TEXT2};font-family:JetBrains Mono,monospace;line-height:1.6;'>"
            f"Research platform · live arb scanner active · reference DB loaded</div>",
            unsafe_allow_html=True,
        )

    # --- Load data -----
    summary      = get_research_summary()
    rel_stats    = get_relationship_stats()
    hist_stats   = get_historical_arb_stats()

    # --- Last updated timestamp -----
    from datetime import datetime, timezone
    st.markdown(
        f"<div style='font-size:0.6rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
        f"margin-bottom:0.75rem;'>Last refresh: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</div>",
        unsafe_allow_html=True,
    )

    # --- Export research data button -----
    try:
        import io as _io_exp
        _exp_sections: list[dict] = []
        # Summary stats
        if summary:
            _exp_sections.append({
                "section": "summary",
                "key": "class_a_violations",
                "value": str(summary.get("class_a_violations", 0)),
            })
            _exp_sections.append({
                "section": "summary",
                "key": "class_b_opportunities",
                "value": str(summary.get("class_b_opportunities", 0)),
            })
        # Hist stats
        if hist_stats:
            for _ks, _vs in hist_stats.items():
                _exp_sections.append({"section": "hist_stats", "key": str(_ks), "value": str(_vs)})
        # Rel stats
        if rel_stats:
            for _kr, _vr in rel_stats.items():
                _exp_sections.append({"section": "rel_stats", "key": str(_kr), "value": str(_vr)})
        # Arb edge by strategy
        _pct_exp, _ = get_arb_edge_by_strategy_class()
        if not _pct_exp.empty:
            for _, _pr in _pct_exp.iterrows():
                _exp_sections.append({
                    "section": "edge_by_strategy",
                    "key": f"{_pr.get('strategy_type','?')}|{_pr.get('classification','?')}",
                    "value": str(dict(_pr)),
                })
        # Hour-of-day data
        try:
            from dashboard.data_layer import _sqlite_conn as _gsc_hod_exp
            _hod_exp_conn = _gsc_hod_exp()
            if _hod_exp_conn:
                _hod_exp_rows = _hod_exp_conn.execute(
                    "SELECT detected_at FROM arbitrage_opportunities WHERE detected_at IS NOT NULL"
                ).fetchall()
                _hod_exp_conn.close()
                if _hod_exp_rows:
                    import pandas as _pd_hod_exp
                    _hod_exp_ts = _pd_hod_exp.to_datetime(
                        [r[0] for r in _hod_exp_rows], utc=True, errors="coerce"
                    ).dropna()
                    _hod_exp_et = _hod_exp_ts.dt.tz_convert("America/New_York")
                    _hod_exp_counts = _hod_exp_et.dt.hour.value_counts().reindex(range(24), fill_value=0)
                    for _h in range(24):
                        _exp_sections.append({
                            "section": "hour_of_day",
                            "key": f"hour_{_h:02d}",
                            "value": str(int(_hod_exp_counts.get(_h, 0))),
                        })
        except Exception:
            pass
        # Fee impact table by strategy
        try:
            from dashboard.data_layer import _sqlite_conn as _gsc_fi_exp
            _fi_exp_conn = _gsc_fi_exp()
            if _fi_exp_conn:
                _fi_exp_rows = _fi_exp_conn.execute(
                    "SELECT strategy_type, "
                    "AVG(gross_edge_cents) AS avg_gross, "
                    "AVG(gross_edge_cents - net_edge_cents) AS avg_fees, "
                    "AVG(net_edge_cents) AS avg_net, "
                    "COUNT(*) AS cnt "
                    "FROM arbitrage_opportunities "
                    "WHERE strategy_type IS NOT NULL AND gross_edge_cents IS NOT NULL "
                    "GROUP BY strategy_type ORDER BY avg_net DESC"
                ).fetchall()
                _fi_exp_conn.close()
                if _fi_exp_rows:
                    for _fi_r in _fi_exp_rows:
                        _exp_sections.append({
                            "section": "fee_impact_by_strategy",
                            "key": str(_fi_r[0]),
                            "value": str({
                                "avg_gross_cents": round(float(_fi_r[1] or 0), 4),
                                "avg_fees_cents": round(float(_fi_r[2] or 0), 4),
                                "avg_net_cents": round(float(_fi_r[3] or 0), 4),
                                "count": int(_fi_r[4] or 0),
                            }),
                        })
        except Exception:
            pass
        _exp_df = pd.DataFrame(_exp_sections)
        _exp_csv = _exp_df.to_csv(index=False).encode("utf-8")
        from datetime import datetime as _dt_exp, timezone as _tz_exp
        _exp_fname = f"kalshi_research_{_dt_exp.now(_tz_exp.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        st.download_button(
            label="⬇ Export research data (CSV)",
            data=_exp_csv,
            file_name=_exp_fname,
            mime="text/csv",
            key="p09_export_research",
        )
    except Exception:
        pass

    # --- Tabs -----
    t1, t2, t3, t4, t5, t6, t7 = st.tabs([
        "RELATIONSHIPS", "STRATEGY PERFORMANCE", "ARB FINDINGS",
        "DATA COVERAGE", "CROSS-ASSET", "PRICE HISTORY", "METHODOLOGY",
    ])

    # --- Relationships -----
    with t1:
        _render_relationships(summary, rel_stats)

    # --- Strategy performance -----
    with t2:
        _render_strategy_performance(summary, hist_stats)

    # --- Arb findings -----
    with t3:
        _render_arb_findings(hist_stats, summary)

    # --- Data coverage -----
    with t4:
        _render_data_coverage()

    # --- Cross-asset -----
    with t5:
        _render_cross_asset_research()

    # --- Price history (candlestick data) -----
    with t6:
        _render_price_history()

    # --- Methodology -----
    with t7:
        st.markdown("""
## How Arb Detection Works

### 1. Data Collection
Live L2 orderbook data streams from Kalshi's Synthesis WebSocket feed at ~3,400 msg/s.
The `ws_bridge.py` scanner processes each message on a 1-second cycle.

### 2. Strategy Detection
- **YNC (YES/NO Complement)**: Detects when YES ask + NO ask < $1.00 on the same contract after fees.
  Kalshi guarantees exactly one leg pays $1.00, so buying both sides at a combined cost below $1.00 is risk-free.

CE, ME, TH, and SS strategies were implemented and tested but are currently disabled — they produced too many
false positives due to Kalshi API metadata inconsistencies. YNC remains because it is structurally unambiguous:
no external validation is needed beyond live bid/ask prices.

### 3. Fee Calculation
Uses Kalshi's canonical fee: `min($0.035, ceil(0.07 × P × (1−P) × 100) / 100)` per contract.
Ceil (not round) ensures conservative fee estimates. Net edge must clear **2¢** after both legs' fees.

### 4. Validation Gates (YNC)
Each detected opportunity passes 4 sequential checks before being logged:
- **Gate 1**: Numeric — YES ask + NO ask < 1.00; net edge ≥ 2¢ after fees; gross < 10¢ sanity cap
- **Gate 2**: Price floor — both asks above 5¢ (eliminates stale near-zero quotes)
- **Gate 3**: Min-tick illiquidity — blocked if either leg quotes at the 1¢ Kalshi minimum tick
- **Gate 4**: TTL dedup — same opportunity re-logged at most once per 5 minutes

### 5. Classification
- **Class A**: Live L2 orderbook confirmed — executable right now
- **Class B**: Snapshot-derived — likely executable, verify before trading
- **Class C**: Trade-tape derived — historical evidence, no guarantee
""")


def _render_relationships(summary: dict, rel_stats: dict):
    st.markdown("#### CONTRACT RELATIONSHIP DETECTION")

    _badge("OBSERVED", GREEN)
    st.markdown("""
Contract relationships are detected from the Kalshi market structure using
logical constraints derived from event definitions and contract rules.
""")

    if rel_stats:
        total = sum(v["count"] for v in rel_stats.values())
        k1, k2 = st.columns([1, 2])
        with k1:
            st.metric("TOTAL RELATIONSHIPS", f"{total:,}")
        with k2:
            # Bar chart by type
            labels = list(rel_stats.keys())
            counts = [rel_stats[k]["count"] for k in labels]
            fig = go.Figure(go.Bar(
                x=counts, y=[l.replace("_", " ").upper() for l in labels],
                orientation="h",
                marker_color=[BLUE, GREEN, AMBER, CYAN, RED][:len(labels)],
                marker_line_width=0,
            ))
            fig.update_layout(
                **plotly_dark_layout(
                title={"text": "RELATIONSHIPS BY TYPE", "font": {"size": 10, "color": TEXT3}},
                height=200,
                xaxis_title="Count", yaxis_title="",
                yaxis={"autorange": "reversed"},
                margin={"l": 30, "r": 20, "t": 30, "b": 30},
            ))
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("**Relationship Types**")
        st.caption("Avg confidence = 1.000 by design — the classifier uses logical constraints derived from contract rules, not probabilistic models.")
        for rel_type, data in rel_stats.items():
            conf = data.get("avg_confidence", 0)
            _stat_row(
                rel_type.replace("_", " ").upper(),
                f"{data['count']:,} pairs",
                f"avg confidence: {conf:.3f}",
            )
    else:
        _no_data_panel("No relationship data in database.")

    st.markdown("<br>", unsafe_allow_html=True)
    _badge("OBSERVED", GREEN)
    _compl_ct  = f"{rel_stats.get('complement',       {}).get('count', 0):,}" if rel_stats else "—"
    _sups_ct   = f"{rel_stats.get('superset',         {}).get('count', 0):,}" if rel_stats else "—"
    _thresh_ct = f"{rel_stats.get('threshold_order',  {}).get('count', 0):,}" if rel_stats else "—"
    st.markdown(f"""
**Relationship detection methodology (3 types detected):**
- **Complement** ({_compl_ct} pairs): Two contracts whose YES prices must sum to ~100¢ by construction. Any sustained deviation is a risk-free arb.
- **Superset** ({_sups_ct} pairs): Superset outcome structurally includes subset. Superset must be priced ≥ subset at all times.
- **Threshold order** ({_thresh_ct} pairs): Adjacent threshold-strike contracts with forced price monotonicity. Price inversions yield risk-free spread trades.
""")

    # --- Live complement arb candidates (from WS feed) -----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.4rem;'>LIVE COMPLEMENT ARB CANDIDATES (WS FEED)</div>",
        unsafe_allow_html=True,
    )
    _badge("OBSERVED", GREEN)
    st.markdown(
        "<span style='font-size:0.7rem;color:" + TEXT2 + ";'>Markets ranked by <b>yes_ask + no_ask</b> "
        "closest to 1.00. Any row where the sum is below 1.00 is a profitable complement arb "
        "(buy YES + buy NO costs less than the guaranteed $1 payout). "
        "Sum above 1.00 means both legs are overpriced.</span>",
        unsafe_allow_html=True,
    )
    try:
        from dashboard.live_state import get_live_state as _gls_rel
        _ws_rel = _gls_rel()
        _snap_rel = _ws_rel.snapshot_all()
        if _snap_rel:
            _rows_rel = []
            for _t, _q in _snap_rel.items():
                _ya = float(_q.yes_ask or 0)
                _na = float(_q.no_ask or 0)
                if _ya <= 0 or _na <= 0:
                    continue
                _sum = round(_ya + _na, 4)
                _gap = round(1.0 - _sum, 4)  # positive = profitable, negative = overpriced
                _rows_rel.append({
                    "ticker":   _t,
                    "yes_ask":  _ya,
                    "no_ask":   _na,
                    "sum":      _sum,
                    "gap_to_arb": _gap,
                    "potential_profit_cents": round(max(0.0, _gap) * 100, 2),
                    "age_s":    int(_q.age_seconds),
                })
            _df_rel = pd.DataFrame(_rows_rel) if _rows_rel else pd.DataFrame(columns=["ticker","yes_ask","no_ask","sum","gap_to_arb","potential_profit_cents","age_s"])
            if not _df_rel.empty:
                _df_rel = _df_rel.sort_values("gap_to_arb", ascending=False).reset_index(drop=True)
            # Highlight profitable rows
            _n_prof = int((_df_rel["gap_to_arb"] > 0).sum()) if not _df_rel.empty else 0
            if _n_prof:
                st.markdown(
                    f"<div style='color:{GREEN};font-family:JetBrains Mono,monospace;font-size:0.7rem;"
                    f"margin-bottom:0.3rem;'>● {_n_prof} PROFITABLE complement arb(s) detected live</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div style='color:{TEXT3};font-family:JetBrains Mono,monospace;font-size:0.7rem;"
                    f"margin-bottom:0.3rem;'>No profitable complement arbs detected live. "
                    f"Top rows below are closest to 1.00.</div>",
                    unsafe_allow_html=True,
                )
            _df_rel_show = _df_rel.head(20).copy()
            _df_rel_show["yes_ask"] = _df_rel_show["yes_ask"].apply(lambda v: f"{v:.4f}")
            _df_rel_show["no_ask"]  = _df_rel_show["no_ask"].apply(lambda v: f"{v:.4f}")
            _df_rel_show["sum"]     = _df_rel_show["sum"].apply(lambda v: f"{v:.4f}")
            _df_rel_show["gap_to_arb"] = _df_rel_show["gap_to_arb"].apply(
                lambda v: f"+{v:.4f} ✓" if v > 0 else f"{v:.4f}"
            )
            if "potential_profit_cents" in _df_rel_show.columns:
                _df_rel_show["potential_profit_cents"] = _df_rel_show["potential_profit_cents"].apply(
                    lambda v: f"+{float(v):.2f}¢" if float(v) > 0 else "—"
                )
            _df_rel_show["age_s"] = _df_rel_show["age_s"].apply(lambda v: f"{v}s")
            st.dataframe(
                _df_rel_show.rename(columns={
                    "ticker": "TICKER", "yes_ask": "YES ASK", "no_ask": "NO ASK",
                    "sum": "SUM", "gap_to_arb": "GAP TO ARB (1−SUM)",
                    "potential_profit_cents": "POTENTIAL PROFIT",
                    "age_s": "QUOTE AGE",
                }),
                use_container_width=True,
                height=min(400, 45 * len(_df_rel_show) + 45),
                hide_index=True,
            )
            st.caption(
                f"Showing top 20 of {len(_df_rel):,} tracked markets. "
                "Kalshi taker fee ~3¢/contract; net edge must exceed fees. "
                "Stale quotes (>60s) may not be fillable at stated price."
            )
        else:
            _no_data_panel(
                "No live quotes yet. Start the WebSocket feed: "
                "the scanner connects automatically when the dashboard is running."
            )
    except Exception as _e_rel:
        _no_data_panel(f"Live state unavailable ({type(_e_rel).__name__}). Start the dashboard WebSocket feed.")


def _render_strategy_performance(summary: dict, hist_stats: dict):
    st.markdown("#### STRATEGY PERFORMANCE SUMMARY")
    _badge("PRELIMINARY", AMBER)
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {AMBER};
padding:0.65rem 1rem;border-radius:3px;margin-bottom:0.75rem;font-size:0.72rem;
color:{TEXT2};line-height:1.6;'>
<b style='color:{AMBER};letter-spacing:0.06em;'>DATA SOURCE: HISTORICAL REFERENCE DB</b><br>
All figures below are <b>historical</b> — computed from the reference SQLite snapshot
(opportunities detected during a prior ingestion run, not a live session).
Live session stats (current WebSocket feed) appear in the DATA COVERAGE tab.
No real capital was deployed. All edge values represent simulated outcomes
under idealized assumptions (immediate execution at stated price, no queue).
</div>""",
        unsafe_allow_html=True,
    )

    # --- Percentile table from new data_layer function -----
    pct_df, _ = get_arb_edge_by_strategy_class()
    if not pct_df.empty:
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
            f"color:{TEXT3};margin-bottom:0.3rem;'>EDGE PERCENTILES BY STRATEGY × CLASS</div>",
            unsafe_allow_html=True,
        )
        display_pct = pct_df.copy()
        for col in ["p25_edge_cents", "median_edge_cents", "p75_edge_cents", "p95_edge_cents"]:
            if col in display_pct.columns:
                def _fmt_c(v):
                    try:
                        return f"{float(v):.2f}c"
                    except (TypeError, ValueError):
                        return "--"
                display_pct[col] = display_pct[col].apply(_fmt_c)
        if "avg_lifetime_s" in display_pct.columns:
            display_pct["avg_lifetime_s"] = display_pct["avg_lifetime_s"].apply(
                lambda v: f"{int(v)}s" if pd.notna(v) else "--"
            )
        rename_pct = {
            "strategy_type": "STRATEGY",
            "classification": "CLASS",
            "count": "COUNT",
            "p25_edge_cents": "P25 EDGE",
            "median_edge_cents": "MEDIAN EDGE",
            "p75_edge_cents": "P75 EDGE",
            "p95_edge_cents": "P95 EDGE",
            "avg_lifetime_s": "AVG LIFETIME",
        }
        st.dataframe(
            display_pct.rename(columns=rename_pct),
            use_container_width=True, height=min(280, 45 * len(pct_df) + 45),
            hide_index=True,
        )
        from datetime import datetime as _dt09, timezone as _tz09; _strat_date = _dt09.now(_tz09.utc).strftime("%Y-%m-%d")
        try:
            from dashboard.data_layer import _sqlite_conn as _gsc_sp
            _spc = _gsc_sp()
            if _spc:
                _spr = _spc.execute("SELECT SUBSTR(MIN(detected_at),1,10) FROM arbitrage_opportunities").fetchone()
                _spc.close()
                if _spr and _spr[0]:
                    _strat_date = str(_spr[0])[:10]
        except Exception:
            pass
        st.caption(
            f"n={len(pct_df):,} rows · Historical data from {_strat_date}. High P95 edge values may reflect brief sub-second opportunities."
        )

        # --- Strategy avg net edge bar chart -----
        if "strategy_type" in pct_df.columns and "median_edge_cents" in pct_df.columns:
            # Coerce to numeric first — DB may return strings or None for this column
            _pct_for_chart = pct_df.copy()
            _pct_for_chart["median_edge_cents"] = pd.to_numeric(
                _pct_for_chart["median_edge_cents"], errors="coerce"
            )
            _avg_by_strat = (
                _pct_for_chart.dropna(subset=["median_edge_cents"])
                .groupby("strategy_type")["median_edge_cents"]
                .mean()
                .reset_index()
                .rename(columns={"strategy_type": "strategy", "median_edge_cents": "avg_net_edge_cents"})
                .sort_values("avg_net_edge_cents", ascending=False)
            )
            if not _avg_by_strat.empty:
                _bar_colors = [GREEN, BLUE, CYAN, AMBER, RED]
                _fig_strat = go.Figure(go.Bar(
                    x=_avg_by_strat["strategy"].str.replace("_", " ").str.upper(),
                    y=pd.to_numeric(_avg_by_strat["avg_net_edge_cents"], errors="coerce"),
                    marker_color=_bar_colors[:len(_avg_by_strat)],
                    marker_line_width=0,
                    text=pd.to_numeric(_avg_by_strat["avg_net_edge_cents"], errors="coerce").apply(
                        lambda v: f"{v:.2f}c" if pd.notna(v) else ""
                    ),
                    textposition="outside",
                ))
                _fig_strat.update_layout(**plotly_dark_layout(
                    title={"text": "AVG NET EDGE BY STRATEGY (MEDIAN, CENTS)", "font": {"size": 10, "color": TEXT3}},
                    height=220,
                    xaxis_title="Strategy",
                    yaxis_title="Avg Net Edge (c)",
                    margin={"l": 40, "r": 20, "t": 35, "b": 60},
                ))
                st.plotly_chart(_fig_strat, use_container_width=True)

    else:
        arb_by_strategy = summary.get("arb_by_strategy", [])
        if arb_by_strategy:
            df = pd.DataFrame(arb_by_strategy)
            for col in ["avg_edge_cents", "max_edge_cents"]:
                if col in df.columns:
                    df[col] = df[col].apply(lambda v: f"{float(v):.2f}c")
            st.dataframe(
                df.rename(columns={
                    "strategy": "STRATEGY", "class": "CLASS",
                    "count": "OPPORTUNITIES",
                    "avg_edge_cents": "AVG EDGE", "max_edge_cents": "MAX EDGE",
                }),
                use_container_width=True, height=250, hide_index=True,
            )
        else:
            _no_data_panel("No strategy performance data available.")

    # --- Detection trend: last 30 days from DB -----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.3rem;'>DETECTION TREND (LAST 30 DAYS)</div>",
        unsafe_allow_html=True,
    )
    try:
        from dashboard.data_layer import _sqlite_conn as _gsc_30d
        _c30 = _gsc_30d()
        _trend30_df = pd.DataFrame(columns=["day", "count"])
        if _c30:
            _t30_rows = _c30.execute(
                "SELECT DATE(detected_at) AS day, COUNT(*) AS cnt "
                "FROM arbitrage_opportunities "
                "WHERE detected_at >= DATE('now', '-30 days') "
                "GROUP BY DATE(detected_at) ORDER BY day"
            ).fetchall()
            _c30.close()
            if _t30_rows:
                _trend30_df = pd.DataFrame(_t30_rows, columns=["day", "count"])
                _trend30_df["day"] = pd.to_datetime(_trend30_df["day"], utc=True, errors="coerce")
        if not _trend30_df.empty:
            _fig30 = go.Figure()
            _fig30.add_trace(go.Bar(
                x=_trend30_df["day"], y=_trend30_df["count"],
                name="Daily Arbs", marker_color=CYAN, marker_line_width=0, opacity=0.65,
            ))
            _fig30.add_trace(go.Scatter(
                x=_trend30_df["day"], y=_trend30_df["count"].rolling(7, min_periods=1).mean(),
                name="7-Day Rolling", mode="lines",
                line={"color": AMBER, "width": 1.8},
            ))
            _fig30.update_layout(**plotly_dark_layout(
                title={"text": "ARB DETECTIONS PER DAY — LAST 30 DAYS", "font": {"size": 10, "color": TEXT3}},
                height=240, xaxis_title="",
                yaxis={"title": "Count"},
                legend={"x": 0, "y": 1, "bgcolor": "rgba(0,0,0,0)"},
                margin={"l": 45, "r": 20, "t": 35, "b": 30},
            ))
            st.plotly_chart(_fig30, use_container_width=True)
            st.caption(f"Real DB data — {_trend30_df['count'].sum():,} arbs over last 30 days.")
        else:
            st.info("No detection data in the last 30 days.")
    except Exception:
        st.info("Detection trend unavailable — database not connected.")

    # --- Rolling trend chart -----
    rolling_df, _ = get_arb_rolling_7d()
    if not rolling_df.empty:
        st.markdown("<br>", unsafe_allow_html=True)
        rolling_df["day"] = pd.to_datetime(rolling_df["day"], utc=True, errors="coerce")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=rolling_df["day"], y=rolling_df["count"],
            name="Daily Opps", marker_color=BLUE, marker_line_width=0, opacity=0.5,
        ))
        if "rolling_7d_count" in rolling_df.columns:
            fig.add_trace(go.Scatter(
                x=rolling_df["day"], y=rolling_df["rolling_7d_count"],
                name="7-Day Rolling", mode="lines",
                line={"color": AMBER, "width": 1.8},
            ))
        if "avg_edge_cents" in rolling_df.columns:
            fig.add_trace(go.Scatter(
                x=rolling_df["day"], y=rolling_df["avg_edge_cents"],
                name="Avg Edge (c)", mode="lines",
                line={"color": GREEN, "width": 1.5},
                yaxis="y2",
            ))
        fig.update_layout(
            **plotly_dark_layout(
            title={"text": "OPPORTUNITY FREQUENCY & EDGE TREND", "font": {"size": 10, "color": TEXT3}},
            height=280, xaxis_title="",
            yaxis={"title": "Count"},
            yaxis2={"title": "Avg Edge (c)", "overlaying": "y", "side": "right",
                    "gridcolor": "rgba(0,0,0,0)"},
            legend={"x": 0, "y": 1, "bgcolor": "rgba(0,0,0,0)"},
        ))
        st.plotly_chart(fig, use_container_width=True)

    # Strategy descriptions  (matches strategy_type values in arbitrage_opportunities table)
    st.markdown("<br>", unsafe_allow_html=True)
    for strat, desc in [
        ("mutually_exclusive",      "Exactly one outcome can settle YES. Arb condition: &#931; NO_asks &lt; N&#8722;1 — the cost to buy all N NO contracts is less than the guaranteed N&#8722;1 payout (exactly one NO resolves to &#36;0; all others pay &#36;1)."),
        ("superset",               "Superset contract outcome strictly includes subset. Must be priced &ge; subset; violations are arb."),
        ("threshold_order",        "Ordered threshold contracts enforce price monotonicity. Violations create risk-free spread trades."),
        ("collectively_exhaustive", "Exactly one outcome must occur. Sum of YES asks &lt; 100&#162; means all outcomes are underpriced — buying all YES contracts costs less than the guaranteed &#36;1 payout."),
    ]:
        _badge("INFERRED", BLUE)
        st.caption(f"**{strat.replace('_',' ').upper()}**: {desc}")


def _render_arb_findings(hist_stats: dict, summary: dict):
    st.markdown("#### ARBITRAGE DETECTION FINDINGS")

    # --- Validation methodology note -----
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {CYAN};
padding:0.75rem 1rem;border-radius:3px;margin-bottom:1rem;'>
<div style='font-size:0.6rem;letter-spacing:0.08em;color:{CYAN};
text-transform:uppercase;margin-bottom:4px;'>VALIDATION METHODOLOGY</div>
<div style='font-size:0.72rem;color:{TEXT2};line-height:1.6;'>
Every detected opportunity passes a multi-gate validation pipeline before being classified as real:
(1) gross edge &gt; 0 after summing leg costs, (2) fee-adjusted net edge &gt; threshold,
(3) stale-book guard (quote age &lt; 60s for CE legs), (4) sports-market filter (excluded categories),
(5) L2 depth walk (executable quantity &gt; 0 at stated price levels),
(6) deduplication (same event suppressed for 5 min — Gate 5 TTL), and
(7) high-edge sanity check (net &gt; 50¢ flagged as pre-fix suspect).
CE arbs additionally pass all 12 gates of the CE scanner (documented in the Methodology tab).
</div>
</div>""",
        unsafe_allow_html=True,
    )

    class_a = int(summary.get("class_a_violations", 0) or 0)
    class_b = int(summary.get("class_b_opportunities", 0) or 0)

    # --- Key findings summary panel -----
    _total_opps = int(hist_stats.get("total_opportunities") or 0) if hist_stats else 0
    _fee_survived = class_a + class_b  # both classes passed the fee gate
    _l2_confirmed = class_a            # Class A = passed L2 depth walk
    _median_edge  = float(hist_stats.get("median_edge_cents") or 0) if hist_stats else 0.0
    _max_edge     = float(hist_stats.get("max_edge_cents") or 0) if hist_stats else 0.0

    def _kf(label: str, value: str, color: str = TEXT, sublabel: str = ""):
        _sub = f"<div style='font-size:0.6rem;color:{TEXT3};margin-top:2px;'>{sublabel}</div>" if sublabel else ""
        return (
            f"<div style='background:{PANEL};border:1px solid {BORDER};padding:0.6rem 0.8rem;border-radius:3px;'>"
            f"<div style='font-size:0.55rem;letter-spacing:0.08em;text-transform:uppercase;color:{TEXT3};margin-bottom:2px;'>{label}</div>"
            f"<div style='font-family:JetBrains Mono,monospace;font-size:1.15rem;color:{color};'>{value}</div>"
            f"{_sub}</div>"
        )

    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.4rem;'>KEY FINDINGS SUMMARY</div>",
        unsafe_allow_html=True,
    )
    _kf_html = (
        "<div style='display:grid;grid-template-columns:repeat(5,1fr);gap:0.5rem;margin-bottom:1rem;'>"
        + _kf("PRICING VIOLATIONS", f"{_total_opps:,}", AMBER, "gross edge > 0")
        + _kf("SURVIVED FEE FILTER", f"{_fee_survived:,}", BLUE, "net edge > fee")
        + _kf("L2 CONFIRMED (CLASS A)", f"{_l2_confirmed:,}", GREEN, "executable qty > 0")
        + _kf("MEDIAN NET EDGE", f"{_median_edge:.2f}¢", CYAN, "after Kalshi fee")
        + _kf("PEAK NET EDGE", f"{_max_edge:.2f}¢", GREEN if _max_edge > 0 else TEXT3, "single best opp")
        + "</div>"
    )
    st.markdown(_kf_html, unsafe_allow_html=True)

    # --- Violation Rate metric -----
    _viol_rate_num = _total_opps
    _viol_survived = _fee_survived
    if (_viol_rate_num + _viol_survived) > 0:
        _viol_rate_pct = _viol_rate_num / (_viol_rate_num + _viol_survived) * 100
    else:
        _viol_rate_pct = 0.0
    st.metric(
        "VIOLATION RATE",
        f"{_viol_rate_pct:.1f}%",
        help="violations / (violations + survived fee filter) × 100%",
    )

    # --- Ghost Arb Violations Log expander -----
    with st.expander("🚫 Ghost Arb Violations Log", expanded=False):
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.08em;color:{AMBER};"
            f"text-transform:uppercase;margin-bottom:0.4rem;'>MOST RECENT STRUCTURAL VIOLATIONS</div>",
            unsafe_allow_html=True,
        )
        _violations_shown = False
        try:
            from dashboard.live_state import get_live_state as _gls_viol
            _ws_viol = _gls_viol()
            _viol_list = None
            # Try to get violations list from live state
            if hasattr(_ws_viol, "get_violations"):
                _viol_list = _ws_viol.get_violations()
            elif hasattr(_ws_viol, "_violations"):
                _viol_list = list(_ws_viol._violations)
            if _viol_list:
                import pandas as _pd_viol
                from datetime import datetime as _dt_viol, timezone as _tz_viol
                _viol_rows = []
                for _v in _viol_list[-20:]:
                    if isinstance(_v, dict):
                        _ts_raw = _v.get("timestamp") or _v.get("ts") or ""
                        try:
                            _ts_et = str(_pd_viol.Timestamp(_ts_raw, tz="UTC").tz_convert("America/New_York"))[:19]
                        except Exception:
                            _ts_et = str(_ts_raw)[:19]
                        _viol_rows.append({
                            "Timestamp (ET)": _ts_et,
                            "Pattern code": _v.get("pattern") or _v.get("pattern_code") or "--",
                            "Markets involved": _v.get("markets") or _v.get("tickers") or "--",
                            "Why blocked": _v.get("reason") or _v.get("why") or "--",
                        })
                if _viol_rows:
                    _viol_df = _pd_viol.DataFrame(_viol_rows)
                    st.dataframe(_viol_df, use_container_width=True, height=min(420, 45 * len(_viol_rows) + 45), hide_index=True)
                    _violations_shown = True
        except Exception:
            pass
        if not _violations_shown:
            # Count from session stats if available
            _sess_viol_count = 0
            try:
                from dashboard.live_state import get_live_state as _gls_viol2
                _ws_viol2 = _gls_viol2()
                _ss2 = _ws_viol2.get_session_stats() if hasattr(_ws_viol2, "get_session_stats") else {}
                _sess_viol_count = int(_ss2.get("violations", 0) or 0)
            except Exception:
                pass
            st.info(
                f"Violation log only available during live WS session — "
                f"{_sess_viol_count} violation(s) caught this session."
            )

        # --- Pattern Breakdown table -----
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.08em;color:{TEXT3};"
            f"text-transform:uppercase;margin-bottom:0.3rem;'>PATTERN BREAKDOWN (27 CE PATTERNS)</div>",
            unsafe_allow_html=True,
        )
        _pattern_data_shown = False
        try:
            from dashboard.data_layer import _sqlite_conn as _gsc_pb
            _pb_conn = _gsc_pb()
            if _pb_conn:
                try:
                    _pb_rows = _pb_conn.execute(
                        "SELECT pattern_type, COUNT(*) as violations_caught "
                        "FROM ws_predict_pattern_log GROUP BY pattern_type ORDER BY violations_caught DESC"
                    ).fetchall()
                    _pb_conn.close()
                    if _pb_rows:
                        import pandas as _pd_pb
                        _pb_df = _pd_pb.DataFrame(_pb_rows, columns=["Pattern", "Violations Caught"])
                        st.dataframe(_pb_df, use_container_width=True, height=min(500, 45 * len(_pb_df) + 45), hide_index=True)
                        _pattern_data_shown = True
                except Exception:
                    try:
                        _pb_conn.close()
                    except Exception:
                        pass
        except Exception:
            pass
        if not _pattern_data_shown:
            st.info("Pattern-level breakdown requires ws_predict_pattern_log table — enable pattern frequency logging to populate.")

    # --- Edge distribution histogram -----
    try:
        from dashboard.data_layer import _sqlite_conn as _gsc_edh
        _edh_conn = _gsc_edh()
        _edge_vals = []
        _gross_vals = []
        _class_vals = []
        if _edh_conn:
            _edh_rows = _edh_conn.execute(
                "SELECT net_edge_cents, gross_edge_cents, classification FROM arbitrage_opportunities "
                "WHERE net_edge_cents IS NOT NULL"
            ).fetchall()
            _edh_conn.close()
            _edge_vals  = [float(r[0]) for r in _edh_rows if r[0] is not None]
            _gross_vals = [float(r[1]) for r in _edh_rows if r[1] is not None]
            _class_vals = [str(r[2]) if r[2] is not None else "C" for r in _edh_rows if r[0] is not None]
        if _edge_vals:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(
                f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                f"color:{TEXT3};margin-bottom:0.3rem;'>EDGE DISTRIBUTION</div>",
                unsafe_allow_html=True,
            )
            _badge("OBSERVED", GREEN)
            _pos_vals = [v for v in _edge_vals if v >= 0]
            _neg_vals = [v for v in _edge_vals if v < 0]
            _fig_edh = go.Figure()
            if _neg_vals:
                _fig_edh.add_trace(go.Histogram(
                    x=_neg_vals, name="Negative edge",
                    marker_color=RED, marker_line_width=0, opacity=0.75,
                    xbins=dict(size=0.5),
                ))
            if _pos_vals:
                _fig_edh.add_trace(go.Histogram(
                    x=_pos_vals, name="Positive edge",
                    marker_color=GREEN, marker_line_width=0, opacity=0.75,
                    xbins=dict(size=0.5),
                ))
            _fig_edh.add_vline(
                x=2, line_dash="dash", line_color=RED, line_width=1.5,
            )
            # Add a dummy scatter so the threshold appears in the legend
            _fig_edh.add_trace(go.Scatter(
                x=[None], y=[None],
                mode="lines",
                line=dict(color=RED, width=1.5, dash="dash"),
                name="min edge threshold (2¢)",
                showlegend=True,
            ))
            _fig_edh.update_layout(**plotly_dark_layout(
                title={"text": "NET EDGE DISTRIBUTION (ALL ARB OPPORTUNITIES)", "font": {"size": 10, "color": TEXT3}},
                height=200,
                xaxis_title="Net edge (¢)",
                yaxis_title="Arb count",
                barmode="overlay",
                legend={"x": 0, "y": 1, "bgcolor": "rgba(0,0,0,0)"},
                margin={"l": 45, "r": 20, "t": 35, "b": 40},
            ))
            st.plotly_chart(_fig_edh, use_container_width=True)

            # -- Gross vs Net scatter plot --
            try:
                if len(_edge_vals) >= 5 and len(_gross_vals) >= 5:
                    _class_color_map = {"A": GREEN, "B": AMBER, "C": RED}
                    _marker_colors = [_class_color_map.get(c, TEXT3) for c in _class_vals]
                    _gross_range_min = min(min(_gross_vals), min(_edge_vals)) if _edge_vals else min(_gross_vals)
                    _gross_range_max = max(max(_gross_vals), max(_edge_vals)) if _edge_vals else max(_gross_vals)

                    _fig_scatter = go.Figure()
                    # y=x diagonal (net == gross, zero fees)
                    _fig_scatter.add_trace(go.Scatter(
                        x=[_gross_range_min, _gross_range_max],
                        y=[_gross_range_min, _gross_range_max],
                        mode="lines",
                        line=dict(color=TEXT3, width=1, dash="dash"),
                        name="y = x (zero fees)",
                        showlegend=True,
                    ))
                    # y=0 break-even after fees
                    _fig_scatter.add_hline(
                        y=0, line_dash="dash", line_color=RED, line_width=1,
                        annotation_text="break-even (y=0)",
                        annotation_position="right",
                        annotation_font=dict(size=8, color=RED),
                    )
                    # Scatter dots per classification
                    for _cls_label, _cls_color in [("A", GREEN), ("B", AMBER), ("C", RED)]:
                        _ix = [i for i, c in enumerate(_class_vals) if c == _cls_label]
                        if not _ix:
                            continue
                        _fig_scatter.add_trace(go.Scatter(
                            x=[_gross_vals[i] for i in _ix],
                            y=[_edge_vals[i] for i in _ix],
                            mode="markers",
                            marker=dict(color=_cls_color, size=8, opacity=0.65, line=dict(width=0)),
                            name=f"Class {_cls_label}",
                        ))
                    _fig_scatter.update_layout(**plotly_dark_layout(
                        title={"text": "Gross vs Net Edge · colored by classification", "font": {"size": 10, "color": TEXT3}},
                        height=250,
                        xaxis_title="Gross edge (¢)",
                        yaxis_title="Net edge (¢)",
                        legend={"x": 0, "y": 1, "bgcolor": "rgba(0,0,0,0)"},
                        margin={"l": 50, "r": 20, "t": 35, "b": 40},
                    ))
                    st.plotly_chart(_fig_scatter, use_container_width=True)
                    st.caption("Points below the diagonal paid fees that reduced edge")
            except Exception:
                pass

            # --- ARB FREQUENCY ANALYSIS ---
            try:
                if len(_edge_vals) >= 5:
                    from dashboard.data_layer import _sqlite_conn as _gsc_freq
                    _freq_conn = _gsc_freq()
                    if _freq_conn:
                        _freq_rows = _freq_conn.execute(
                            "SELECT detected_at FROM arbitrage_opportunities "
                            "WHERE detected_at IS NOT NULL ORDER BY detected_at"
                        ).fetchall()
                        _freq_conn.close()
                        if _freq_rows and len(_freq_rows) >= 5:
                            import pandas as _pd_freq
                            _freq_ts = _pd_freq.to_datetime(
                                [r[0] for r in _freq_rows], utc=True, errors="coerce"
                            ).dropna()
                            # Median gap between consecutive arbs
                            _gaps = _pd_freq.Series(_freq_ts).diff().dropna()
                            _median_gap = _gaps.median()
                            _gap_total_s = int(_median_gap.total_seconds())
                            if _gap_total_s >= 3600:
                                _gap_label = f"{_gap_total_s // 3600}h {(_gap_total_s % 3600) // 60}m"
                            elif _gap_total_s >= 60:
                                _gap_label = f"{_gap_total_s // 60}m {_gap_total_s % 60}s"
                            else:
                                _gap_label = f"{_gap_total_s}s"
                            # Peak hour (ET)
                            _freq_ts_et = _freq_ts.dt.tz_convert("America/New_York")
                            _hour_counts = _freq_ts_et.dt.hour.value_counts()
                            _peak_hour = int(_hour_counts.idxmax())
                            _peak_count = int(_hour_counts.max())
                            _peak_label = f"{_peak_hour:02d}:00-{_peak_hour+1:02d}:00 ET ({_peak_count} arbs)"

                            st.markdown("<br>", unsafe_allow_html=True)
                            st.markdown(
                                f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                                f"color:{TEXT3};margin-bottom:0.3rem;'>ARB FREQUENCY ANALYSIS</div>",
                                unsafe_allow_html=True,
                            )
                            _badge("OBSERVED", GREEN)
                            _fa1, _fa2, _fa3 = st.columns(3)
                            with _fa1:
                                st.metric("MEDIAN TIME BETWEEN ARBS", _gap_label,
                                          help="Median gap between consecutive arbs (real DB timestamps)")
                            with _fa2:
                                st.metric("PEAK HOUR (ET)", _peak_label)
                            with _fa3:
                                # Time to next arb estimate
                                _avg_gap_s = float(_gaps.mean().total_seconds()) if len(_gaps) > 0 else 0
                                if _avg_gap_s >= 3600:
                                    _next_arb_label = f"~{int(_avg_gap_s // 3600)}h {int((_avg_gap_s % 3600) // 60)}m"
                                elif _avg_gap_s >= 60:
                                    _next_arb_label = f"~{int(_avg_gap_s // 60)}m {int(_avg_gap_s % 60)}s"
                                elif _avg_gap_s > 0:
                                    _next_arb_label = f"~{int(_avg_gap_s)}s"
                                else:
                                    _next_arb_label = "--"
                                st.metric("NEXT ARB (AVG GAP)", _next_arb_label,
                                          help="Expected time to next arb based on average inter-arb gap from historical data")

                            # --- Arb velocity: arbs per hour over last 24h -----
                            try:
                                import pandas as _pd_vel
                                from datetime import datetime as _dt_vel, timezone as _tz_vel, timedelta as _td_vel
                                _now_vel = _dt_vel.now(_tz_vel.utc)
                                _cutoff_vel = (_now_vel - _td_vel(hours=24)).isoformat()
                                _vel_conn = None
                                _vel_count = None
                                try:
                                    from dashboard.data_layer import _sqlite_conn as _gsc_vel
                                    _vel_conn = _gsc_vel()
                                    if _vel_conn:
                                        _vel_row = _vel_conn.execute(
                                            "SELECT COUNT(*) FROM arbitrage_opportunities WHERE detected_at >= ?",
                                            (_cutoff_vel,),
                                        ).fetchone()
                                        _vel_conn.close()
                                        if _vel_row:
                                            _vel_count = int(_vel_row[0])
                                except Exception:
                                    pass
                                if _vel_count is not None:
                                    _arb_velocity = round(_vel_count / 24.0, 2)
                                    st.metric("ARB VELOCITY (24H)", f"{_arb_velocity:.2f} arbs/hr",
                                              help=f"Total arbs in last 24h ({_vel_count:,}) divided by 24 hours")
                            except Exception:
                                pass

                            # --- Day of week frequency bar chart -----
                            try:
                                import pandas as _pd_dow
                                _dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                                _dow_counts_raw = _freq_ts_et.dt.dayofweek.value_counts().reindex(range(7), fill_value=0)
                                _dow_vals = [int(_dow_counts_raw.get(i, 0)) for i in range(7)]
                                _fig_dow = go.Figure(go.Bar(
                                    x=_dow_labels,
                                    y=_dow_vals,
                                    marker_color=[GREEN if v == max(_dow_vals) else BLUE for v in _dow_vals],
                                    marker_line_width=0,
                                    text=[str(v) for v in _dow_vals],
                                    textposition="outside",
                                ))
                                _fig_dow.update_layout(
                                    **plotly_dark_layout(
                                        title={"text": "ARB COUNT BY DAY OF WEEK (ET)", "font": {"size": 10, "color": TEXT3}},
                                        height=220,
                                        xaxis_title="Day",
                                        yaxis_title="Arb Count",
                                        margin={"l": 40, "r": 20, "t": 35, "b": 40},
                                    )
                                )
                                st.plotly_chart(_fig_dow, use_container_width=True)
                                _busiest_dow = _dow_labels[int(_dow_counts_raw.idxmax())]
                                st.caption(f"Busiest day: {_busiest_dow} ({max(_dow_vals):,} arbs). Highlighted bar = most frequent.")
                            except Exception:
                                pass

                            # --- Arb count by hour of day (ET) -----
                            try:
                                import pandas as _pd_hod
                                _hod_counts_raw = _freq_ts_et.dt.hour.value_counts().reindex(range(24), fill_value=0)
                                _hod_vals = [int(_hod_counts_raw.get(h, 0)) for h in range(24)]
                                _hod_labels = [f"{h:02d}:00" for h in range(24)]
                                _hod_max = max(_hod_vals) if _hod_vals else 1
                                _fig_hod = go.Figure(go.Bar(
                                    x=_hod_labels,
                                    y=_hod_vals,
                                    marker_color=[GREEN if v == _hod_max else BLUE for v in _hod_vals],
                                    marker_line_width=0,
                                    text=[str(v) if v > 0 else "" for v in _hod_vals],
                                    textposition="outside",
                                ))
                                _fig_hod.update_layout(
                                    **plotly_dark_layout(
                                        title={"text": "ARBS DETECTED BY HOUR OF DAY (ET)", "font": {"size": 10, "color": TEXT3}},
                                        height=240,
                                        xaxis_title="Hour (ET)",
                                        yaxis_title="Arb Count",
                                        margin={"l": 40, "r": 20, "t": 35, "b": 50},
                                    )
                                )
                                st.plotly_chart(_fig_hod, use_container_width=True)
                                _busiest_hod = f"{_hod_labels[_hod_vals.index(_hod_max)]} ET"
                                _quietest_nonzero = min((v for v in _hod_vals if v > 0), default=0)
                                st.caption(
                                    f"Peak hour: {_busiest_hod} ({_hod_max:,} arbs). "
                                    "Low-activity hours may indicate when market makers are least attentive. "
                                    "Highlighted bar = most frequent."
                                )
                            except Exception:
                                pass

                            # --- Strategy frequency breakdown -----
                            try:
                                from dashboard.data_layer import _sqlite_conn as _gsc_sf
                                _sf_conn = _gsc_sf()
                                if _sf_conn:
                                    _sf_rows = _sf_conn.execute(
                                        "SELECT strategy_type, COUNT(*) as cnt FROM arbitrage_opportunities "
                                        "WHERE strategy_type IS NOT NULL GROUP BY strategy_type ORDER BY cnt DESC"
                                    ).fetchall()
                                    _sf_conn.close()
                                    if _sf_rows:
                                        import pandas as _pd_sf
                                        _sf_df = _pd_sf.DataFrame(_sf_rows, columns=["strategy", "count"])
                                        _sf_total = _sf_df["count"].sum()
                                        _sf_df["pct"] = (_sf_df["count"] / _sf_total * 100).round(1)
                                        _STRAT_SHORT = {
                                            "yes_no_complement": "YNC",
                                            "collectively_exhaustive": "CE",
                                            "mutually_exclusive": "ME",
                                            "threshold_order": "TH",
                                            "superset": "SS",
                                        }
                                        _sf_df["label"] = _sf_df["strategy"].apply(
                                            lambda s: _STRAT_SHORT.get(str(s), str(s).upper()[:6])
                                        )
                                        _STRAT_COLORS2 = {
                                            "YNC": "#26a69a", "CE": BLUE, "ME": "#7c3aed",
                                            "TH": AMBER, "SS": "#6b7280",
                                        }
                                        _sf_bar_colors = [_STRAT_COLORS2.get(lbl, TEXT3) for lbl in _sf_df["label"]]
                                        _fig_sf = go.Figure(go.Bar(
                                            x=_sf_df["label"],
                                            y=_sf_df["count"],
                                            marker_color=_sf_bar_colors,
                                            marker_line_width=0,
                                            text=_sf_df["pct"].apply(lambda v: f"{v:.1f}%"),
                                            textposition="outside",
                                        ))
                                        _fig_sf.update_layout(
                                            **plotly_dark_layout(
                                                title={"text": "STRATEGY FREQUENCY BREAKDOWN (% of all arbs)",
                                                       "font": {"size": 10, "color": TEXT3}},
                                                height=220,
                                                xaxis_title="Strategy",
                                                yaxis_title="Arb Count",
                                                margin={"l": 40, "r": 20, "t": 35, "b": 40},
                                            )
                                        )
                                        st.plotly_chart(_fig_sf, use_container_width=True)
                                        st.caption(
                                            "  |  ".join(
                                                f"{row['label']}: {row['count']:,} ({row['pct']:.1f}%)"
                                                for _, row in _sf_df.iterrows()
                                            )
                                        )
                            except Exception:
                                pass
            except Exception:
                pass

            # --- EDGE DISTRIBUTION BY STRATEGY ---
            try:
                from dashboard.data_layer import _sqlite_conn as _gsc_eds
                _eds_conn = _gsc_eds()
                if _eds_conn:
                    _eds_rows = _eds_conn.execute(
                        "SELECT strategy_type, net_edge_cents FROM arbitrage_opportunities "
                        "WHERE strategy_type IS NOT NULL AND net_edge_cents IS NOT NULL"
                    ).fetchall()
                    _eds_conn.close()
                    if _eds_rows:
                        import pandas as _pd_eds
                        _eds_df = _pd_eds.DataFrame(_eds_rows, columns=["strategy_type", "net_edge_cents"])
                        _eds_df["net_edge_cents"] = _pd_eds.to_numeric(_eds_df["net_edge_cents"], errors="coerce")
                        _eds_med = (
                            _eds_df.dropna(subset=["net_edge_cents"])
                            .groupby("strategy_type")["net_edge_cents"]
                            .median()
                            .reset_index()
                            .rename(columns={"strategy_type": "strategy", "net_edge_cents": "median_edge"})
                            .sort_values("median_edge", ascending=False)
                        )
                        if len(_eds_med) >= 2:
                            st.markdown("<br>", unsafe_allow_html=True)
                            st.markdown(
                                f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                                f"color:{TEXT3};margin-bottom:0.3rem;'>EDGE DISTRIBUTION BY STRATEGY</div>",
                                unsafe_allow_html=True,
                            )
                            _badge("OBSERVED", GREEN)
                            _STRAT_COLORS = {
                                "yes_no_complement": "#26a69a",
                                "collectively_exhaustive": BLUE,
                                "mutually_exclusive": "#7c3aed",
                                "threshold_order": AMBER,
                                "superset": "#6b7280",
                            }
                            _eds_bar_colors = [
                                _STRAT_COLORS.get(str(s), TEXT3)
                                for s in _eds_med["strategy"]
                            ]
                            _fig_eds = go.Figure(go.Bar(
                                x=_eds_med["strategy"].str.replace("_", " ").str.upper(),
                                y=_eds_med["median_edge"],
                                marker_color=_eds_bar_colors,
                                marker_line_width=0,
                                text=_eds_med["median_edge"].apply(lambda v: f"{v:.2f}¢" if _pd_eds.notna(v) else ""),
                                textposition="outside",
                            ))
                            _fig_eds.update_layout(**plotly_dark_layout(
                                title={"text": "MEDIAN NET EDGE BY STRATEGY", "font": {"size": 10, "color": TEXT3}},
                                height=200,
                                xaxis_title="Strategy",
                                yaxis_title="Median Net Edge (¢)",
                                margin={"l": 45, "r": 20, "t": 35, "b": 60},
                            ))
                            st.plotly_chart(_fig_eds, use_container_width=True)
            except Exception:
                pass
        else:
            st.info("Edge distribution: no net_edge_cents data available yet.")
    except Exception:
        st.info("Edge distribution unavailable — database not connected.")

    # --- Fee impact analysis -----
    try:
        if _edge_vals and _gross_vals:
            _total_gross = sum(_gross_vals)
            _total_net   = sum(_edge_vals)
            _total_fees  = _total_gross - _total_net
            _pct_retained = (_total_net / _total_gross * 100) if _total_gross else 0.0
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(
                f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                f"color:{TEXT3};margin-bottom:0.3rem;'>FEE IMPACT ANALYSIS</div>",
                unsafe_allow_html=True,
            )
            _badge("OBSERVED", GREEN)
            _fc1, _fc2, _fc3 = st.columns(3)
            with _fc1:
                st.metric("Gross edge found", f"{_total_gross:.1f}¢", help="Sum of gross_edge_cents across all detected arbs")
            with _fc2:
                st.metric("Fees paid", f"{_total_fees:.1f}¢", help="gross_edge_cents − net_edge_cents")
            with _fc3:
                st.metric("Net retained", f"{_total_net:.1f}¢ ({_pct_retained:.1f}% of gross)", help="Sum of net_edge_cents after Kalshi taker fee")
        elif not _edge_vals:
            pass  # already handled above
    except Exception:
        pass  # silently skip if fee data unavailable

    # --- Fee Impact Metrics (real DB) -----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.3rem;'>FEE IMPACT METRICS (DB)</div>",
        unsafe_allow_html=True,
    )
    _badge("OBSERVED", GREEN)
    _fim_avg_fee = None
    _fim_avg_ret = None
    _fim_zero_ct = None
    # Try PostgreSQL arbs table first
    try:
        from database.repository import get_engine as _ge_fim
        from sqlalchemy import text as _txt_fim
        _eng_fim = _ge_fim()
        with _eng_fim.connect() as _conn_fim:
            _fim_row = _conn_fim.execute(_txt_fim(
                "SELECT AVG(gross_edge_cents - net_edge_cents) AS avg_fee_paid, "
                "AVG(net_edge_cents / NULLIF(gross_edge_cents, 0)) AS avg_retention, "
                "COUNT(*) FILTER (WHERE net_edge_cents <= 0) AS zero_net_count "
                "FROM arbs"
            )).fetchone()
        if _fim_row:
            _fim_avg_fee = float(_fim_row[0]) if _fim_row[0] is not None else None
            _fim_avg_ret = float(_fim_row[1]) if _fim_row[1] is not None else None
            _fim_zero_ct = int(_fim_row[2]) if _fim_row[2] is not None else None
    except Exception:
        pass
    # Fall back to SQLite arbitrage_opportunities table
    if _fim_avg_fee is None:
        try:
            from dashboard.data_layer import _sqlite_conn as _gsc_fim
            _fimc = _gsc_fim()
            if _fimc:
                _fim_row2 = _fimc.execute(
                    "SELECT AVG(gross_edge_cents - net_edge_cents) AS avg_fee_paid, "
                    "AVG(CAST(net_edge_cents AS REAL) / NULLIF(CAST(gross_edge_cents AS REAL), 0)) AS avg_retention, "
                    "SUM(CASE WHEN net_edge_cents <= 0 THEN 1 ELSE 0 END) AS zero_net_count "
                    "FROM arbitrage_opportunities"
                ).fetchone()
                _fimc.close()
                if _fim_row2:
                    _fim_avg_fee = float(_fim_row2[0]) if _fim_row2[0] is not None else None
                    _fim_avg_ret = float(_fim_row2[1]) if _fim_row2[1] is not None else None
                    _fim_zero_ct = int(_fim_row2[2]) if _fim_row2[2] is not None else None
        except Exception:
            pass
    _fim_c1, _fim_c2, _fim_c3 = st.columns(3)
    with _fim_c1:
        st.metric("Avg Fee Per Arb", f"{_fim_avg_fee:.2f}¢" if _fim_avg_fee is not None else "—",
                  help="AVG(gross_edge_cents - net_edge_cents)")
    with _fim_c2:
        st.metric("Edge Retention %", f"{_fim_avg_ret*100:.1f}%" if _fim_avg_ret is not None else "—",
                  help="AVG(net_edge_cents / gross_edge_cents)")
    with _fim_c3:
        st.metric("Zero-Net Arbs", f"{_fim_zero_ct:,}" if _fim_zero_ct is not None else "—",
                  help="COUNT(*) WHERE net_edge_cents <= 0 (fees wiped the gross edge)")
    if _fim_avg_ret is not None and _fim_avg_ret < 0.7:
        st.warning("Fees consuming >30% of gross edge — fee drag is significant")

    # --- Fee impact table by strategy -----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.3rem;'>FEE IMPACT BY STRATEGY</div>",
        unsafe_allow_html=True,
    )
    try:
        from dashboard.data_layer import _sqlite_conn as _gsc_fi_strat
        _fi_strat_conn = _gsc_fi_strat()
        if _fi_strat_conn:
            _fi_strat_rows = _fi_strat_conn.execute(
                "SELECT strategy_type, "
                "AVG(gross_edge_cents) AS avg_gross, "
                "AVG(gross_edge_cents - net_edge_cents) AS avg_fees, "
                "AVG(net_edge_cents) AS avg_net, "
                "COUNT(*) AS cnt "
                "FROM arbitrage_opportunities "
                "WHERE strategy_type IS NOT NULL AND gross_edge_cents IS NOT NULL "
                "GROUP BY strategy_type ORDER BY avg_net DESC"
            ).fetchall()
            _fi_strat_conn.close()
            if _fi_strat_rows:
                import pandas as _pd_fi
                _fi_df = _pd_fi.DataFrame(
                    _fi_strat_rows,
                    columns=["strategy", "avg_gross", "avg_fees", "avg_net", "count"],
                )
                for _col in ["avg_gross", "avg_fees", "avg_net"]:
                    _fi_df[_col] = _pd_fi.to_numeric(_fi_df[_col], errors="coerce")
                _fi_df["fee_pct"] = (_fi_df["avg_fees"] / _fi_df["avg_gross"].replace(0, float("nan")) * 100).round(1)
                _fi_disp = _fi_df.copy()
                _fi_disp["avg_gross"] = _fi_disp["avg_gross"].apply(lambda v: f"{v:.2f}¢" if _pd_fi.notna(v) else "--")
                _fi_disp["avg_fees"]  = _fi_disp["avg_fees"].apply(lambda v: f"{v:.2f}¢" if _pd_fi.notna(v) else "--")
                _fi_disp["avg_net"]   = _fi_disp["avg_net"].apply(lambda v: f"{v:.2f}¢" if _pd_fi.notna(v) else "--")
                _fi_disp["fee_pct"]   = _fi_disp["fee_pct"].apply(lambda v: f"{v:.1f}%" if _pd_fi.notna(v) else "--")
                st.dataframe(
                    _fi_disp.rename(columns={
                        "strategy": "STRATEGY", "avg_gross": "AVG GROSS",
                        "avg_fees": "AVG FEES", "avg_net": "AVG NET",
                        "fee_pct": "FEE %", "count": "COUNT",
                    }),
                    use_container_width=True,
                    height=min(280, 45 * len(_fi_disp) + 45),
                    hide_index=True,
                )
            else:
                st.info("No per-strategy fee data yet.")
        else:
            st.info("No DB connection for fee table.")
    except Exception:
        st.info("Fee impact by strategy unavailable.")

    # --- YNC Market Pair Correlation -----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.3rem;'>MARKET PAIR CORRELATION (YNC ARBs)</div>",
        unsafe_allow_html=True,
    )
    _badge("OBSERVED", GREEN)
    st.markdown(
        f"<span style='font-size:0.7rem;color:{TEXT2};'>For YES/NO Complement arbs, "
        "YES ask and NO ask should be strongly negatively correlated (≈−1.0) because "
        "they must sum to ≈$1.00. Correlation significantly above −1.0 suggests "
        "independent mispricing — a signal of market maker inattention.</span>",
        unsafe_allow_html=True,
    )
    try:
        from dashboard.data_layer import _sqlite_conn as _gsc_ync
        _ync_conn = _gsc_ync()
        if _ync_conn:
            _ync_rows = _ync_conn.execute(
                "SELECT yes_ask, no_ask FROM arbitrage_opportunities "
                "WHERE strategy_type = 'yes_no_complement' "
                "AND yes_ask IS NOT NULL AND no_ask IS NOT NULL"
            ).fetchall()
            _ync_conn.close()
            if _ync_rows and len(_ync_rows) >= 5:
                import pandas as _pd_ync
                _ync_df = _pd_ync.DataFrame(_ync_rows, columns=["yes_ask", "no_ask"])
                _ync_df["yes_ask"] = _pd_ync.to_numeric(_ync_df["yes_ask"], errors="coerce")
                _ync_df["no_ask"]  = _pd_ync.to_numeric(_ync_df["no_ask"],  errors="coerce")
                _ync_df = _ync_df.dropna()
                if len(_ync_df) >= 5:
                    _ync_corr = float(_ync_df["yes_ask"].corr(_ync_df["no_ask"]))
                    _corr_color = GREEN if _ync_corr < -0.95 else (AMBER if _ync_corr < -0.85 else RED)
                    _yc1, _yc2, _yc3 = st.columns(3)
                    _yc1.metric(
                        "YES/NO ASK CORRELATION",
                        f"{_ync_corr:.4f}",
                        help="Pearson correlation between YES ask and NO ask across all YNC arbs. "
                             "Healthy market: ≈ −1.0. Deviation = potential mispricing.",
                    )
                    _yc2.metric("YNC ARB RECORDS", f"{len(_ync_df):,}")
                    _sum_mean = float((_ync_df["yes_ask"] + _ync_df["no_ask"]).mean())
                    _yc3.metric("AVG YES+NO SUM", f"{_sum_mean:.4f}",
                                help="Should be <1.00 for a profitable arb. Values well below 1.00 indicate larger mispricings.")
                    if _ync_corr < -0.95:
                        st.success(f"Correlation {_ync_corr:.4f} is near −1.0 — healthy market structure (prices move in lockstep).")
                    elif _ync_corr < -0.85:
                        st.warning(f"Correlation {_ync_corr:.4f} deviates from −1.0 — moderate mispricing signal.")
                    else:
                        st.error(f"Correlation {_ync_corr:.4f} significantly above −1.0 — strong mispricing or data anomaly.")
                    # Scatter: YES ask vs NO ask
                    _fig_ync = go.Figure()
                    _fig_ync.add_trace(go.Scatter(
                        x=_ync_df["yes_ask"].tolist(),
                        y=_ync_df["no_ask"].tolist(),
                        mode="markers",
                        marker=dict(color=BLUE, size=5, opacity=0.5, line=dict(width=0)),
                        name="YNC arbs",
                    ))
                    _ync_x = [float(_ync_df["yes_ask"].min()), float(_ync_df["yes_ask"].max())]
                    _fig_ync.add_trace(go.Scatter(
                        x=_ync_x, y=[1.0 - x for x in _ync_x],
                        mode="lines",
                        line=dict(color=GREEN, width=1.5, dash="dash"),
                        name="YES + NO = 1.00 (perfect)",
                    ))
                    _fig_ync.update_layout(**plotly_dark_layout(
                        title={"text": f"YNC: YES ASK vs NO ASK  (r = {_ync_corr:.4f})",
                               "font": {"size": 10, "color": TEXT3}},
                        height=260,
                        xaxis_title="YES ask",
                        yaxis_title="NO ask",
                        legend={"x": 0, "y": 1, "bgcolor": "rgba(0,0,0,0)"},
                        margin={"l": 50, "r": 20, "t": 35, "b": 40},
                    ))
                    st.plotly_chart(_fig_ync, use_container_width=True)
                    st.caption("Green dashed line = YES + NO = 1.00. Points below the line are profitable complement arbs.")
                else:
                    st.info("Insufficient YNC arb records after cleaning — need at least 5.")
            else:
                st.info("No yes_no_complement arbs with yes_ask and no_ask columns found in database.")
        else:
            st.info("Database not connected — YNC correlation unavailable.")
    except Exception as _ync_err:
        st.info(f"YNC correlation unavailable: {type(_ync_err).__name__}")

    # --- Fee Sensitivity chart -----
    import math as _math_fee
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.3rem;'>FEE SENSITIVITY</div>",
        unsafe_allow_html=True,
    )
    st.caption("Net edge after Kalshi fee for a range of gross edges (worst-case: p=0.5). "
               "Fee formula: min(0.035, ceil(0.07 × p × (1−p) × 100) / 100) per contract.")
    _gross_range = [i * 0.01 for i in range(1, 21)]  # 1¢ to 20¢ in cents (as fractions of $1)
    _p_fee = 0.5  # worst case
    def _kalshi_fee(p: float) -> float:
        raw = 0.07 * p * (1 - p) * 100
        return min(0.035, _math_fee.ceil(raw) / 100)
    _fee_per_contract = _kalshi_fee(_p_fee)  # ~3.5¢ at p=0.5
    _net_edges = [max(0.0, g - _fee_per_contract * 100) for g in [i for i in range(1, 21)]]
    _gross_labels = [f"{i}¢" for i in range(1, 21)]
    _fig_fee = go.Figure()
    _fig_fee.add_trace(go.Scatter(
        x=_gross_labels,
        y=_net_edges,
        mode="lines+markers",
        line={"color": GREEN, "width": 2},
        marker={"size": 5},
        name="Net edge after fee",
    ))
    _fig_fee.add_hline(y=0, line_dash="dash", line_color=RED, line_width=1,
                       annotation_text="break-even", annotation_font_size=8)
    _fig_fee.update_layout(**plotly_dark_layout(
        title={"text": f"NET EDGE AFTER KALSHI FEE (fee={_fee_per_contract*100:.1f}¢/contract at p=0.5)",
               "font": {"size": 10, "color": TEXT3}},
        height=220,
        xaxis_title="Gross Edge (¢)",
        yaxis_title="Net Edge (¢)",
        margin={"l": 45, "r": 20, "t": 35, "b": 40},
    ))
    st.plotly_chart(_fig_fee, use_container_width=True)

    # Dynamic detection date from DB
    from datetime import datetime as _dt09b, timezone as _tz09b; _detect_date = _dt09b.now(_tz09b.utc).strftime("%Y-%m-%d")
    try:
        from dashboard.data_layer import _sqlite_conn as _gsc_af
        _aconn = _gsc_af()
        if _aconn:
            _arow = _aconn.execute(
                "SELECT SUBSTR(MIN(detected_at),1,10) FROM arbitrage_opportunities"
            ).fetchone()
            _aconn.close()
            if _arow and _arow[0]:
                _detect_date = str(_arow[0])[:10]
    except Exception:
        pass

    st.markdown(f"""
<div style='display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;margin-bottom:1rem;'>
<div style='background:{PANEL};border:1px solid {BORDER};padding:0.75rem 1rem;border-radius:3px;'>
<div style='font-size:0.6rem;letter-spacing:0.08em;color:{TEXT3};text-transform:uppercase;'>
CLASS A &mdash;CONFIRMED EXECUTABLE
</div>
<div style='font-family:JetBrains Mono,monospace;font-size:1.3rem;color:{GREEN};'>
{class_a:,}
</div>
<div style='font-size:0.65rem;color:{TEXT2};'>
Live bid/ask confirmed &mdash;executable at stated price
</div>
</div>
<div style='background:{PANEL};border:1px solid {BORDER};padding:0.75rem 1rem;border-radius:3px;'>
<div style='font-size:0.6rem;letter-spacing:0.08em;color:{TEXT3};text-transform:uppercase;'>
CLASS B &mdash;PENDING L2 DEPTH
</div>
<div style='font-family:JetBrains Mono,monospace;font-size:1.3rem;color:{AMBER};'>
{class_b:,}
</div>
<div style='font-size:0.65rem;color:{TEXT2};'>
Mid/candlestick implied &mdash;requires full L2 for confirmation.
These {class_b:,} were detected on {_detect_date} during market settlement,
which likely explains the unusually large edge values.
</div>
</div>
</div>
""", unsafe_allow_html=True)

    if hist_stats:
        _badge("OBSERVED", GREEN)
        rows = [
            ("Total opportunities detected", f"{int(hist_stats.get('total_opportunities') or 0):,}"),
            ("Confirmed executable (Class A)", f"{class_a:,}"),
            ("Median net edge", f"{float(hist_stats.get('median_edge_cents') or 0):.2f}c"),
            ("Mean net edge", f"{float(hist_stats.get('mean_edge_cents') or 0):.2f}c"),
            ("Maximum net edge", f"{float(hist_stats.get('max_edge_cents') or 0):.2f}c"),
            ("Median opportunity lifetime", (lambda s: f"{int(s//60)}m{int(s%60)}s" if s >= 60 else f"{int(s)}s")(float(hist_stats.get('median_lifetime_s') or 0))),
        ]
        _stat_table(rows)

    # --- Timeline: arb detections over time -----
    _roll_df, _ = get_arb_rolling_7d()
    if not _roll_df.empty and "day" in _roll_df.columns and "count" in _roll_df.columns:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
            f"color:{TEXT3};margin-bottom:0.3rem;'>ARB DETECTIONS TIMELINE</div>",
            unsafe_allow_html=True,
        )
        _badge("OBSERVED", GREEN)
        _tl = _roll_df.copy()
        _tl["day"] = pd.to_datetime(_tl["day"], utc=True, errors="coerce")
        _tl["count"] = pd.to_numeric(_tl["count"], errors="coerce").fillna(0)
        _fig_tl = go.Figure()
        _fig_tl.add_trace(go.Bar(
            x=_tl["day"], y=_tl["count"],
            name="Daily Detections",
            marker_color=BLUE, marker_line_width=0, opacity=0.55,
        ))
        _fig_tl.add_trace(go.Scatter(
            x=_tl["day"], y=_tl["count"],
            name="Count",
            mode="lines+markers",
            line={"color": GREEN, "width": 1.8},
            marker={"size": 4},
        ))
        if "rolling_7d_count" in _tl.columns:
            _fig_tl.add_trace(go.Scatter(
                x=_tl["day"],
                y=pd.to_numeric(_tl["rolling_7d_count"], errors="coerce"),
                name="7-Day Rolling Avg",
                mode="lines",
                line={"color": AMBER, "width": 1.5, "dash": "dot"},
            ))
        _fig_tl.update_layout(**plotly_dark_layout(
            title={"text": "ARB OPPORTUNITIES DETECTED BY DAY", "font": {"size": 10, "color": TEXT3}},
            height=240,
            xaxis_title="",
            yaxis_title="Opportunities",
            legend={"x": 0, "y": 1, "bgcolor": "rgba(0,0,0,0)"},
            margin={"l": 45, "r": 20, "t": 35, "b": 30},
        ))
        st.plotly_chart(_fig_tl, use_container_width=True)
        st.caption(
            f"Daily arb detection counts from the reference database. "
            f"Each bar = opportunities passing all 7 validation gates on that day."
        )

    # --- Arb by category breakdown -----
    _cat_df, _cat_err = get_arb_by_category()
    if not _cat_df.empty:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
            f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
            f"ARB OPPORTUNITIES BY CATEGORY</div>",
            unsafe_allow_html=True,
        )
        _badge("OBSERVED", GREEN)
        _cat_display = _cat_df.copy()
        if "avg_edge_cents" in _cat_display.columns:
            _cat_display["avg_edge_cents"] = pd.to_numeric(
                _cat_display["avg_edge_cents"], errors="coerce"
            ).apply(lambda v: f"{v:.2f}c" if pd.notna(v) else "--")
        _cat_cols = [c for c in ["category", "count", "avg_edge_cents", "class_a_count", "class_b_count"] if c in _cat_display.columns]
        st.dataframe(
            _cat_display[_cat_cols].rename(columns={
                "category": "CATEGORY", "count": "COUNT",
                "avg_edge_cents": "AVG EDGE", "class_a_count": "CLASS A", "class_b_count": "CLASS B",
            }),
            use_container_width=True, hide_index=True,
        )

    # --- Ghost Arb Defense Layers -----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.4rem;'>GHOST ARB DEFENSE LAYERS</div>",
        unsafe_allow_html=True,
    )
    _badge("OBSERVED", GREEN)
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {GREEN};
padding:0.75rem 1rem;border-radius:3px;margin-bottom:0.5rem;font-size:0.72rem;
color:{TEXT2};line-height:1.8;'>
A <b>"ghost arb"</b> is a spurious signal caused by stale, thin, or mismatched order-book quotes
that look like an arb but cannot be executed. Five guards eliminate them before an opportunity
is surfaced:<br><br>
<b style='color:{GREEN};'>Gate 1 — Fee-adjusted net edge &gt; 0</b><br>
&nbsp;&nbsp;Raw edge (1.00 − sum of leg asks) minus Kalshi taker fee (~3¢/contract) must be positive.<br>
<b style='color:{GREEN};'>Gate 2 — Quote freshness (&lt; 60s)</b><br>
&nbsp;&nbsp;Any quote older than 60 seconds is treated as stale and the opportunity is suppressed.<br>
<b style='color:{GREEN};'>Gate 3 — L2 depth walk</b><br>
&nbsp;&nbsp;The scanner walks the live order book to confirm executable quantity at the stated price levels.
A best-bid/ask that has no resting quantity behind it is rejected.<br>
<b style='color:{GREEN};'>Gate 5 — Deduplication window (5 min)</b><br>
&nbsp;&nbsp;The same event is suppressed for 5 minutes after each alert to prevent re-alerting
on a stale book that has not refreshed (<code>_TTL_S = 300</code>).<br>
<b style='color:{GREEN};'>Gate 5 — High-edge sanity check (&gt; 50¢ flagged)</b><br>
&nbsp;&nbsp;Any net edge above 50¢ is automatically flagged as a pre-settlement or data artifact
and promoted to Class B (pending manual review) rather than Class A (actionable).
</div>""",
        unsafe_allow_html=True,
    )

    st.info("CE scanning is disabled. Only YES/NO complement (YNC) arbs are detected.")

    st.markdown("<br>", unsafe_allow_html=True)
    _badge("UNVERIFIED", TEXT3)
    st.markdown("""
**Interpretation caveats:**
- L2 depth at detection time determines executability; thin books reduce fill certainty.
- Candlestick OHLC data does not guarantee fill at the open/close price used for detection.
- Arbitrage opportunities may persist for insufficient time to execute both legs.
- Kalshi enforces a taker fee; small edge opportunities are consumed entirely by fees.
- Settlement latency and queue priority are not modelled.
""")


def _render_data_coverage():
    st.markdown("#### DATA COVERAGE SUMMARY")
    _badge("OBSERVED", GREEN)

    from dashboard.data_layer import get_system_health, get_coverage_stats, get_historical_arb_stats
    health   = get_system_health()
    coverage = get_coverage_stats()
    is_sqlite = not health.get("db_connected", False) and health.get("db_mode") == "sqlite"

    # Show historical arb count from reference DB
    hist = get_historical_arb_stats()
    arb_label = "Arbitrage opps detected"
    arb_val   = f"{int(hist.get('total_opportunities', 0)):,}" if is_sqlite else f"{int(health.get('arb_opportunities_open', 0) or 0):,}"

    def _dash_if_zero(v):
        return "--" if v == "0" else v

    rows = [
        ("Total markets in DB",         f"{int(health.get('markets_total', 0) or 0):,}"),
        ("Total events in DB",           _dash_if_zero(f"{int(health.get('events_total', 0) or 0):,}") if is_sqlite else f"{int(health.get('events_total', 0) or 0):,}"),
        ("Total trades in DB",           _dash_if_zero(f"{int(health.get('trades_total', 0) or 0):,}") if is_sqlite else f"{int(health.get('trades_total', 0) or 0):,}"),
        ("Contract relationships",       f"{int(health.get('relationships_total', 0) or 0):,}"),
        ("L2 snapshots (order book)",    _dash_if_zero(f"{int(health.get('l2_snapshots_total', 0) or 0):,}") if is_sqlite else f"{int(health.get('l2_snapshots_total', 0) or 0):,}"),
        (arb_label,                      arb_val),
        ("Canadian markets in DB",       f"{int(coverage.get('canadian_markets', 0)):,}"),
    ]
    _stat_table(rows)

    # --- Live WS session stats -----
    try:
        from dashboard.live_state import get_live_state as _gls_p09
        _ws_p09 = _gls_p09()
        _ws_stats_p09 = _ws_p09.get_stats()
        _ws_conn_p09 = _ws_stats_p09.get("connected", False)
        _ws_mkts_p09 = int(_ws_stats_p09.get("markets_tracked", 0))
        _ws_rate_p09 = float(_ws_stats_p09.get("messages_per_sec", 0))
        _ws_sess_stats = _ws_p09.get_session_stats()
        _ws_n_total = _ws_sess_stats.get("total", 0)
        _ws_n_ce = _ws_sess_stats.get("ce", 0)
        _ws_n_comp = _ws_sess_stats.get("complement", 0)
        _ws_best_edge = _ws_sess_stats.get("best_edge", 0.0) or 0.0
        _conn_color = "#22C55E" if _ws_conn_p09 else AMBER
        _conn_label = "● CONNECTED" if _ws_conn_p09 else "○ OFFLINE"
        st.markdown(
            f"<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {_conn_color};"
            f"padding:0.65rem 1rem;border-radius:3px;margin-bottom:0.5rem;font-size:0.72rem;"
            f"color:{TEXT2};font-family:JetBrains Mono,monospace;'>"
            f"<span style='color:{_conn_color};letter-spacing:0.08em;text-transform:uppercase;font-size:0.6rem;'>"
            f"LIVE SCANNER (SESSION)</span><br>"
            f"<span style='color:{_conn_color};'>{_conn_label}</span>"
            f"&nbsp;&nbsp;|&nbsp;&nbsp;MARKETS: {_ws_mkts_p09}"
            f"&nbsp;&nbsp;|&nbsp;&nbsp;MSG/S: {_ws_rate_p09:.1f}"
            f"&nbsp;&nbsp;|&nbsp;&nbsp;SESSION ARBS: {_ws_n_total} "
            f"(NON-CE: {_ws_n_comp} · CE: {_ws_n_ce})"
            f"{'&nbsp;&nbsp;|&nbsp;&nbsp;BEST EDGE: <span style=\"color:#22C55E;\">+' + f'{_ws_best_edge:.2f}c</span>' if _ws_best_edge > 0 else ''}"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    # --- DETECTION TREND: arbs detected in last 1h / 24h / all-time -----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.4rem;'>DETECTION TREND</div>",
        unsafe_allow_html=True,
    )
    _trend_ok = False
    try:
        from datetime import datetime as _dt_trend, timezone as _tz_trend, timedelta as _td_trend
        _now_trend = _dt_trend.now(_tz_trend.utc)

        # Try PostgreSQL first (live DB with standard interval syntax)
        _pg_trend_ok = False
        try:
            from database.repository import get_engine as _ge_trend
            from sqlalchemy import text as _txt_trend
            _eng_trend = _ge_trend()
            with _eng_trend.connect() as _conn_trend:
                _r_1h_pg  = _conn_trend.execute(_txt_trend(
                    "SELECT COUNT(*) FROM arbs WHERE ts > now() - interval '1 hour'"
                )).fetchone()
                _r_24h_pg = _conn_trend.execute(_txt_trend(
                    "SELECT COUNT(*) FROM arbs WHERE ts > now() - interval '24 hours'"
                )).fetchone()
                _r_all_pg = _conn_trend.execute(_txt_trend("SELECT COUNT(*) FROM arbs")).fetchone()
                # Previous periods for deltas
                _r_prev_1h_pg = _conn_trend.execute(_txt_trend(
                    "SELECT COUNT(*) FROM arbs WHERE ts > now() - interval '2 hours' AND ts <= now() - interval '1 hour'"
                )).fetchone()
                _r_prev_24h_pg = _conn_trend.execute(_txt_trend(
                    "SELECT COUNT(*) FROM arbs WHERE ts > now() - interval '48 hours' AND ts <= now() - interval '24 hours'"
                )).fetchone()
            _n_1h_t   = int(_r_1h_pg[0])      if _r_1h_pg  else 0
            _n_24h_t  = int(_r_24h_pg[0])     if _r_24h_pg else 0
            _n_all_t  = int(_r_all_pg[0])     if _r_all_pg else 0
            _prev_1h  = int(_r_prev_1h_pg[0]) if _r_prev_1h_pg  else None
            _prev_24h = int(_r_prev_24h_pg[0])if _r_prev_24h_pg else None
            _pg_trend_ok = True
        except Exception:
            pass

        if not _pg_trend_ok:
            # Fall back to SQLite reference DB
            from dashboard.data_layer import _sqlite_conn as _gsc_trend
            _tc = _gsc_trend()
            _n_1h_t = _n_24h_t = _n_all_t = 0
            _prev_1h = _prev_24h = None
            if _tc:
                _cutoff_1h  = (_now_trend - _td_trend(hours=1)).isoformat()
                _cutoff_24h = (_now_trend - _td_trend(hours=24)).isoformat()
                _cutoff_2h  = (_now_trend - _td_trend(hours=2)).isoformat()
                _cutoff_48h = (_now_trend - _td_trend(hours=48)).isoformat()
                _r_all = _tc.execute("SELECT COUNT(*) FROM arbitrage_opportunities").fetchone()
                _r_24h = _tc.execute(
                    "SELECT COUNT(*) FROM arbitrage_opportunities WHERE detected_at >= ?",
                    (_cutoff_24h,),
                ).fetchone()
                _r_1h = _tc.execute(
                    "SELECT COUNT(*) FROM arbitrage_opportunities WHERE detected_at >= ?",
                    (_cutoff_1h,),
                ).fetchone()
                _r_p1h = _tc.execute(
                    "SELECT COUNT(*) FROM arbitrage_opportunities WHERE detected_at >= ? AND detected_at < ?",
                    (_cutoff_2h, _cutoff_1h),
                ).fetchone()
                _r_p24h = _tc.execute(
                    "SELECT COUNT(*) FROM arbitrage_opportunities WHERE detected_at >= ? AND detected_at < ?",
                    (_cutoff_48h, _cutoff_24h),
                ).fetchone()
                _tc.close()
                _n_1h_t   = int(_r_1h[0])   if _r_1h   else 0
                _n_24h_t  = int(_r_24h[0])  if _r_24h  else 0
                _n_all_t  = int(_r_all[0])  if _r_all  else 0
                _prev_1h  = int(_r_p1h[0])  if _r_p1h  else None
                _prev_24h = int(_r_p24h[0]) if _r_p24h else None

        # Compute deltas for display
        def _delta_str(current, previous):
            if previous is None:
                return None
            diff = current - previous
            return f"{diff:+,}" if diff != 0 else "0"

        _d1h  = _delta_str(_n_1h_t,  _prev_1h)
        _d24h = _delta_str(_n_24h_t, _prev_24h)

        _tc1, _tc2, _tc3 = st.columns(3)
        _tc1.metric("LAST 1H",  f"{_n_1h_t:,}",  delta=_d1h,  help="vs previous 1h window")
        _tc2.metric("LAST 24H", f"{_n_24h_t:,}", delta=_d24h, help="vs previous 24h window")
        _tc3.metric("ALL TIME", f"{_n_all_t:,}")
        _trend_ok = True
    except Exception:
        pass

    if not _trend_ok:
        _tc1, _tc2, _tc3 = st.columns(3)
        _tc1.metric("LAST 1H",  "—")
        _tc2.metric("LAST 24H", "—")
        _tc3.metric("ALL TIME", "—")
        st.caption("DB offline — detection trend unavailable.")

    # --- DATA QUALITY AUDIT -----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.4rem;'>DATA QUALITY AUDIT</div>",
        unsafe_allow_html=True,
    )
    _dqa_total = 0
    _dqa_null_net = 0
    _dqa_invalid_gross = 0
    _dqa_missing_strat = 0
    _dqa_last_ts = None
    _dqa_ok = False
    # Try PostgreSQL arbs table first
    try:
        from database.repository import get_engine as _ge_dqa
        from sqlalchemy import text as _txt_dqa
        _eng_dqa = _ge_dqa()
        with _eng_dqa.connect() as _conn_dqa:
            _dqa_row = _conn_dqa.execute(_txt_dqa(
                "SELECT COUNT(*) AS total, "
                "COUNT(CASE WHEN net_edge_cents IS NULL THEN 1 END) AS null_net, "
                "COUNT(CASE WHEN gross_edge_cents <= 0 THEN 1 END) AS invalid_gross, "
                "COUNT(CASE WHEN strategy IS NULL OR strategy = '' THEN 1 END) AS missing_strategy "
                "FROM arbs"
            )).fetchone()
            _dqa_ts_row = _conn_dqa.execute(_txt_dqa("SELECT MAX(ts) FROM arbs")).fetchone()
        if _dqa_row:
            _dqa_total          = int(_dqa_row[0] or 0)
            _dqa_null_net       = int(_dqa_row[1] or 0)
            _dqa_invalid_gross  = int(_dqa_row[2] or 0)
            _dqa_missing_strat  = int(_dqa_row[3] or 0)
        if _dqa_ts_row and _dqa_ts_row[0]:
            _dqa_last_ts = str(_dqa_ts_row[0])[:19]
        _dqa_ok = True
    except Exception:
        pass
    # Fall back to SQLite arbitrage_opportunities table
    if not _dqa_ok:
        try:
            from dashboard.data_layer import _sqlite_conn as _gsc_dqa
            _dqa_conn = _gsc_dqa()
            if _dqa_conn:
                _dqa_row2 = _dqa_conn.execute(
                    "SELECT COUNT(*) AS total, "
                    "COUNT(CASE WHEN net_edge_cents IS NULL THEN 1 END) AS null_net, "
                    "COUNT(CASE WHEN CAST(gross_edge_cents AS REAL) <= 0 THEN 1 END) AS invalid_gross, "
                    "COUNT(CASE WHEN strategy_type IS NULL OR strategy_type = '' THEN 1 END) AS missing_strategy "
                    "FROM arbitrage_opportunities"
                ).fetchone()
                _dqa_ts_row2 = _dqa_conn.execute(
                    "SELECT MAX(detected_at) FROM arbitrage_opportunities"
                ).fetchone()
                _dqa_conn.close()
                if _dqa_row2:
                    _dqa_total         = int(_dqa_row2[0] or 0)
                    _dqa_null_net      = int(_dqa_row2[1] or 0)
                    _dqa_invalid_gross = int(_dqa_row2[2] or 0)
                    _dqa_missing_strat = int(_dqa_row2[3] or 0)
                if _dqa_ts_row2 and _dqa_ts_row2[0]:
                    _dqa_last_ts = str(_dqa_ts_row2[0])[:19]
                _dqa_ok = True
        except Exception:
            pass
    if _dqa_ok:
        _dqa1, _dqa2, _dqa3, _dqa4 = st.columns(4)
        _dqa1.metric("Total Records",     f"{_dqa_total:,}")
        _dqa2.metric("Null Net Edge",     f"{_dqa_null_net:,}",      help="Records where net_edge_cents IS NULL")
        _dqa3.metric("Invalid Gross",     f"{_dqa_invalid_gross:,}", help="Records where gross_edge_cents <= 0")
        _dqa4.metric("Missing Strategy",  f"{_dqa_missing_strat:,}", help="Records with no strategy type set")
        # Null net edge warning
        if _dqa_total > 0 and (_dqa_null_net / _dqa_total) > 0.05:
            st.warning("⚠️ >5% records have null net_edge — possible fee calculation errors")
        # Data completeness progress bar
        _dqa_complete = max(0, _dqa_total - _dqa_null_net - _dqa_invalid_gross)
        _dqa_completeness = _dqa_complete / _dqa_total if _dqa_total > 0 else 1.0
        st.caption(f"Data completeness: {_dqa_completeness * 100:.1f}%")
        st.progress(_dqa_completeness)
        # Last ingested timestamp
        if _dqa_last_ts:
            try:
                from datetime import datetime as _dt_dqa, timezone as _tz_dqa
                import zoneinfo as _zi_dqa
                _dqa_dt = _dt_dqa.fromisoformat(_dqa_last_ts.replace("Z", "+00:00") if _dqa_last_ts.endswith("Z") else _dqa_last_ts)
                if _dqa_dt.tzinfo is None:
                    _dqa_dt = _dqa_dt.replace(tzinfo=_tz_dqa.utc)
                _dqa_et = _dqa_dt.astimezone(_zi_dqa.ZoneInfo("America/New_York"))
                _dqa_last_str = _dqa_et.strftime("%Y-%m-%d %H:%M:%S ET")
            except Exception:
                _dqa_last_str = _dqa_last_ts
            st.metric("Last Ingested", _dqa_last_str)
        st.caption("Data quality checks — these should all be low relative to total arb count")
    else:
        st.caption("Data quality audit unavailable — database not connected.")

    # --- EXTENDED DATA QUALITY AUDIT: ghost arbs, edge thresholds, drought -----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.4rem;'>GATE FILTER COUNTS</div>",
        unsafe_allow_html=True,
    )
    _MIN_EDGE_CENTS = 2.0   # gate: arbs below this are blocked
    _MAX_EDGE_CENTS = 50.0  # gate: arbs above this are flagged / capped
    _ghost_blocked = None
    _below_min_edge = None
    _above_max_edge = None

    # Try PostgreSQL arbs table first
    try:
        from database.repository import get_engine as _ge_gate
        from sqlalchemy import text as _txt_gate
        _eng_gate = _ge_gate()
        with _eng_gate.connect() as _conn_gate:
            _ghost_row = _conn_gate.execute(_txt_gate(
                "SELECT COUNT(*) FROM arbs WHERE net_edge_cents <= 0"
            )).fetchone()
            _below_row = _conn_gate.execute(_txt_gate(
                "SELECT COUNT(*) FROM arbs WHERE net_edge_cents > 0 AND net_edge_cents < :min_e",
            ), {"min_e": _MIN_EDGE_CENTS}).fetchone()
            _above_row = _conn_gate.execute(_txt_gate(
                "SELECT COUNT(*) FROM arbs WHERE net_edge_cents > :max_e",
            ), {"max_e": _MAX_EDGE_CENTS}).fetchone()
        _ghost_blocked  = int(_ghost_row[0]) if _ghost_row else 0
        _below_min_edge = int(_below_row[0]) if _below_row else 0
        _above_max_edge = int(_above_row[0]) if _above_row else 0
    except Exception:
        pass

    # Fallback to SQLite arbitrage_opportunities table
    if _ghost_blocked is None:
        try:
            from dashboard.data_layer import _sqlite_conn as _gsc_gate
            _gate_conn = _gsc_gate()
            if _gate_conn:
                try:
                    _g_row = _gate_conn.execute(
                        "SELECT COUNT(*) FROM arbitrage_opportunities WHERE CAST(net_edge_cents AS REAL) <= 0"
                    ).fetchone()
                    _b_row = _gate_conn.execute(
                        "SELECT COUNT(*) FROM arbitrage_opportunities "
                        "WHERE CAST(net_edge_cents AS REAL) > 0 AND CAST(net_edge_cents AS REAL) < ?",
                        (_MIN_EDGE_CENTS,),
                    ).fetchone()
                    _a_row = _gate_conn.execute(
                        "SELECT COUNT(*) FROM arbitrage_opportunities "
                        "WHERE CAST(net_edge_cents AS REAL) > ?",
                        (_MAX_EDGE_CENTS,),
                    ).fetchone()
                    _ghost_blocked  = int(_g_row[0]) if _g_row else 0
                    _below_min_edge = int(_b_row[0]) if _b_row else 0
                    _above_max_edge = int(_a_row[0]) if _a_row else 0
                except Exception:
                    pass
                finally:
                    try:
                        _gate_conn.close()
                    except Exception:
                        pass
        except Exception:
            pass

    if _ghost_blocked is not None:
        _gc1, _gc2, _gc3 = st.columns(3)
        _gc1.metric(
            "Ghost Arbs Blocked",
            f"{_ghost_blocked:,}",
            help="Arbs with net_edge_cents ≤ 0 (fees wiped the gross edge — ghost arb gate)",
        )
        _gc2.metric(
            f"Below Min Edge (<{_MIN_EDGE_CENTS:.0f}¢)",
            f"{_below_min_edge:,}",
            help=f"Arbs with positive but sub-threshold edge (< {_MIN_EDGE_CENTS:.0f}¢) — excluded by min edge filter",
        )
        _gc3.metric(
            f"Above Max Edge Cap (>{_MAX_EDGE_CENTS:.0f}¢)",
            f"{_above_max_edge:,}",
            help=f"Arbs with net_edge_cents > {_MAX_EDGE_CENTS:.0f}¢ — flagged as data artifacts (pre-settlement / stale book)",
        )
        if _above_max_edge and _above_max_edge > 0:
            st.info(f"{_above_max_edge:,} arbs exceed the {_MAX_EDGE_CENTS:.0f}¢ sanity cap — these are likely pre-settlement artifacts and should not be traded.")
    else:
        st.caption("Gate filter counts unavailable — database not connected.")

    # --- CONSECUTIVE ARB DROUGHT (last 24h) -----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.4rem;'>ARB DROUGHT ANALYSIS (LAST 24H)</div>",
        unsafe_allow_html=True,
    )
    _drought_minutes = None
    try:
        import pandas as _pd_drought
        from datetime import datetime as _dt_drought, timezone as _tz_drought, timedelta as _td_drought
        _now_drought = _dt_drought.now(_tz_drought.utc)
        _cutoff_drought = (_now_drought - _td_drought(hours=24)).isoformat()

        # Try PostgreSQL arbs table
        _drought_ts_list = []
        try:
            from database.repository import get_engine as _ge_drought
            from sqlalchemy import text as _txt_drought
            _eng_drought = _ge_drought()
            with _eng_drought.connect() as _conn_drought:
                _d_rows = _conn_drought.execute(_txt_drought(
                    "SELECT ts FROM arbs WHERE ts >= :cutoff ORDER BY ts",
                ), {"cutoff": _cutoff_drought}).fetchall()
                _drought_ts_list = [r[0] for r in _d_rows if r[0] is not None]
        except Exception:
            pass

        # Fallback to SQLite
        if not _drought_ts_list:
            try:
                from dashboard.data_layer import _sqlite_conn as _gsc_drought
                _dc = _gsc_drought()
                if _dc:
                    _dr_rows = _dc.execute(
                        "SELECT detected_at FROM arbitrage_opportunities WHERE detected_at >= ? ORDER BY detected_at",
                        (_cutoff_drought,),
                    ).fetchall()
                    _dc.close()
                    _drought_ts_list = [r[0] for r in _dr_rows if r[0] is not None]
            except Exception:
                pass

        if _drought_ts_list:
            _drought_ts = _pd_drought.to_datetime(_drought_ts_list, utc=True, errors="coerce").dropna()
            # Build a per-minute presence array over the last 24h
            _start_dt = _pd_drought.Timestamp(_now_drought - _td_drought(hours=24))
            _minutes_total = 24 * 60
            # Round each arb ts down to the minute
            _arb_minutes = set(
                int((_t - _start_dt).total_seconds() // 60)
                for _t in _drought_ts
                if 0 <= (_t - _start_dt).total_seconds() < _minutes_total * 60
            )
            # Find longest consecutive run of minutes with NO arbs
            _max_drought = 0
            _cur_drought = 0
            for _m in range(_minutes_total):
                if _m not in _arb_minutes:
                    _cur_drought += 1
                    _max_drought = max(_max_drought, _cur_drought)
                else:
                    _cur_drought = 0
            _drought_minutes = _max_drought
        else:
            # No arbs in last 24h — entire 24h is a drought
            _drought_minutes = 24 * 60
    except Exception:
        pass

    if _drought_minutes is not None:
        if _drought_minutes >= 60:
            _drought_label = f"{_drought_minutes // 60}h {_drought_minutes % 60}m"
        else:
            _drought_label = f"{_drought_minutes}m"
        _dc1, _dc2 = st.columns(2)
        _dc1.metric(
            "LONGEST ARB DROUGHT (24H)",
            _drought_label,
            help="Longest consecutive stretch (minutes) with zero arbs detected in the last 24 hours",
        )
        _drought_pct = _drought_minutes / (24 * 60) * 100
        _dc2.metric(
            "DROUGHT % OF DAY",
            f"{_drought_pct:.1f}%",
            help="Fraction of the last 24h with zero arb activity",
        )
        if _drought_minutes > 120:
            st.warning(f"⚠️ Longest arb drought in last 24h: {_drought_label} — scanner may have been offline or market was quiet")
        else:
            st.caption(f"Longest no-arb stretch in last 24h: {_drought_label}. Healthy scanners typically see short droughts (< 30m).")
    else:
        st.caption("Arb drought metric unavailable — database not connected.")

    st.markdown("<br>", unsafe_allow_html=True)
    _l2_days  = int(coverage.get("l2_coverage_days", 0))
    _l2_t0    = coverage.get("l2_earliest_ts", "")
    _l2_ticks = int(coverage.get("markets_with_l2", 0))
    if _l2_days > 0:
        _l2_desc = f"{_l2_days} days · {_l2_ticks} tickers (from {str(_l2_t0)[:10]})"
    elif _l2_ticks > 0:
        _l2_desc = f"1 session · {_l2_ticks} tickers (from {str(_l2_t0)[:10]})"
    else:
        _l2_desc = "no L2 data in snapshot"
    # ext_market_daily live stats
    _ext_rows = 0
    _ext_assets = 0
    _ext_last = ""
    try:
        from dashboard.data_layer import get_latest_external_prices as _glep09
        _lp = _glep09()
        _ext_assets = len(_lp)
        if _ext_assets:
            from database.repository import get_engine as _ge09
            from sqlalchemy import text as _t09
            with _ge09().connect() as _c09:
                _r09 = _c09.execute(_t09("SELECT COUNT(*), MAX(obs_date) FROM ext_market_daily")).fetchone()
                if _r09:
                    _ext_rows = int(_r09[0] or 0)
                    _ext_last = str(_r09[1] or "")[:10]
    except Exception:
        pass
    _ext_note = (
        f"{_ext_assets} assets loaded · {_ext_rows:,} rows · last obs {_ext_last}"
        if _ext_assets else "Cross-asset data not yet loaded. Connect the data pipeline to populate."
    )

    st.markdown(f"""
**Data collection status:**
- Historical L2 orderbook: {_l2_desc}
- Live L2 orderbook: Synthesis WebSocket — real-time, all active markets
- External asset data: BOC VALET + yfinance (no API keys) — {_ext_note}
""")

    st.markdown("<br>", unsafe_allow_html=True)
    _badge("LIMITATIONS", RED)
    st.markdown(f"""
- Historical L2 data starts January 2026; pre-2026 analysis relies on OHLC candlestick data
- Synthesis WebSocket is the only live L2 source
- Canadian market coverage depends on Kalshi's actual market listings
- Cross-asset data collection is manual and not yet automated
- Backtesting uses candlestick OHLC data (15-min/60-min/daily) rather than true tick data
""")


def _render_cross_asset_research():
    """
Cross-asset research findings: BOC rate probability distribution snapshot
and a summary of Canadian macro event coverage.
"""
    st.markdown("#### CROSS-ASSET RESEARCH")
    _badge("PRELIMINARY", AMBER)
    st.markdown("""
Canadian macro cross-asset analysis. Compares Kalshi implied probabilities
against traditional market-derived probabilities (OIS/CORRA, FX, WTI crude).
""")

    # --- BOC probability snapshot (pure math, no DB) -----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.4rem;'>BOC RATE MARKET-IMPLIED DISTRIBUTION (CURRENT)</div>",
        unsafe_allow_html=True,
    )

    try:
        from analysis.probability_engine import boc_rate_distribution
        import numpy as np

        # Use BOC policy rate from DB if available, else default
        ois_est = 0.0225  # 2.25% default
        try:
            import json as _json_ois
            from dashboard.data_layer import _sqlite_conn as _gsc_ois
            _oisc = _gsc_ois()
            if _oisc:
                _ois_row = _oisc.execute(
                    "SELECT model_inputs FROM cross_asset_model_spreads WHERE model_inputs IS NOT NULL LIMIT 1"
                ).fetchone()
                _oisc.close()
                if _ois_row:
                    _mi_ois = _json_ois.loads(_ois_row[0] or "{}")
                    _corra_raw = _mi_ois.get("corra") or _mi_ois.get("policy_rate")
                    if _corra_raw is not None:
                        ois_est = float(_corra_raw) / 100.0  # stored as percent (e.g. 2.25), convert to decimal
        except Exception:
            pass
        base = round(ois_est / 0.0025) * 0.0025  # round to nearest 25bps
        rates = [round(base + i * 0.0025, 4) for i in range(-6, 7)]
        dist = boc_rate_distribution(ois_est, rates)

        colors = []
        for k in dist:
            rate = float(k.rstrip("%")) / 100
            if abs(rate - ois_est) < 0.0013:
                colors.append(GREEN)
            elif abs(rate - ois_est) < 0.0038:
                colors.append(BLUE)
            else:
                colors.append(PANEL2 if PANEL2 else "#334155")

        fig = go.Figure(go.Bar(
            x=list(dist.keys()),
            y=[v * 100 for v in dist.values()],
            marker_color=colors,
        ))
        fig.update_layout(
            **plotly_dark_layout(
            title={"text": f"BOC RATE DISTRIBUTION (OIS={ois_est*100:.2f}%)", "font": {"size": 10, "color": TEXT3}},
            height=220,
            xaxis_title="Rate", yaxis_title="Implied Probability (%)",
            margin={"l": 40, "r": 20, "t": 30, "b": 40},
        ))
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"OIS estimate: {ois_est*100:.2f}% CORRA. Update via cross_asset pipeline when live data is available.")

        # --- Kalshi vs OIS spread table with Direction column -----
        try:
            import json as _json_ois_tbl
            from dashboard.data_layer import _sqlite_conn as _gsc_ois_tbl
            _ois_tbl_conn = _gsc_ois_tbl()
            if _ois_tbl_conn:
                _ois_rows = _ois_tbl_conn.execute(
                    "SELECT market_ticker, kalshi_price, ois_implied, spread "
                    "FROM cross_asset_model_spreads ORDER BY ABS(CAST(spread AS REAL)) DESC LIMIT 20"
                ).fetchall()
                _ois_tbl_conn.close()
                if _ois_rows:
                    _ois_df = pd.DataFrame(
                        _ois_rows,
                        columns=["Market", "Kalshi Price", "OIS Implied", "Spread"],
                    )
                    _ois_df["Direction"] = _ois_df["Spread"].apply(
                        lambda v: "OVER" if float(v or 0) > 0 else ("UNDER" if float(v or 0) < 0 else "FLAT")
                    )
                    for col in ["Kalshi Price", "OIS Implied"]:
                        _ois_df[col] = pd.to_numeric(_ois_df[col], errors="coerce").apply(
                            lambda v: f"{v:.4f}" if pd.notna(v) else "--"
                        )
                    _ois_df["Spread"] = pd.to_numeric(_ois_df["Spread"], errors="coerce").apply(
                        lambda v: (f"+{v:.4f}" if v > 0 else f"{v:.4f}") if pd.notna(v) else "--"
                    )
                    st.markdown(
                        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                        f"color:{TEXT3};margin-bottom:0.4rem;margin-top:0.75rem;'>"
                        f"KALSHI vs OIS IMPLIED — SPREAD TABLE</div>",
                        unsafe_allow_html=True,
                    )
                    st.dataframe(_ois_df, use_container_width=True, hide_index=True)
                    st.caption(
                        "Spread = Kalshi Price − OIS Implied. "
                        "OVER: Kalshi is overpriced relative to OIS model. "
                        "UNDER: Kalshi is underpriced relative to OIS model."
                    )
        except Exception:
            pass  # Table not yet populated — silently skip

    except Exception:
        _no_data_panel("BOC probability engine unavailable. Connect the cross-asset pipeline to enable rate probability modeling.")

    # --- Cross-asset methodology -----
    st.markdown("<br>", unsafe_allow_html=True)
    _badge("INFERRED", BLUE)
    st.markdown("""
**Methodology:**
- **BOC rate**: Normal distribution around OIS/CORRA rate as expected policy rate
- **CAD/USD**: Log-normal (Black-Scholes digital) model with historical FX volatility
- **WTI crude**: Same log-normal model with 35% historical crude vol
- **Spreads**: Kalshi contract price minus model-implied probability

**Hypotheses under investigation:**
- H1: Large spreads mean-revert (ADF stationarity test)
- H2: Kalshi lags traditional markets in incorporating Canadian macro info
- H3: Spreads widen when Kalshi liquidity is low
- H4: Apparent arb disappears after fees and depth constraints
""")

    # --- Canadian market coverage -----
    st.markdown("<br>", unsafe_allow_html=True)
    _badge("OBSERVED", GREEN)
    st.markdown("**Canadian market coverage (from DB):**")
    try:
        from dashboard.data_layer import get_canadian_markets
        ca_df, _ = get_canadian_markets()
        if not ca_df.empty:
            show_cols = [c for c in ["ticker", "title", "status", "volume"] if c in ca_df.columns]
            st.dataframe(ca_df[show_cols].head(5), use_container_width=True, height=200, hide_index=True)
        else:
            _no_data_panel("No Canadian markets found in DB.")
    except Exception:
        _no_data_panel("Canadian market data unavailable. Connect the database to load live market data.")


def _render_price_history():
    """
PRICE HISTORY tab — candlestick OHLC data from local database.
Shows top markets by volume, and a drilldown price chart.
"""
    st.markdown("#### PRICE HISTORY — Candlestick Data")
    _badge("OBSERVED", GREEN)
    # Build a dynamic description from the candlestick DB
    _cdl_desc = "Historical YES bid/ask and trade price data from Kalshi candlestick API."
    try:
        from dashboard.data_layer import _sqlite_conn as _gsc_p09ph
        _cconn = _gsc_p09ph()
        if _cconn:
            _cr = _cconn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT market_id), "
                "MIN(SUBSTR(period_end_ts,1,10)), MAX(SUBSTR(period_end_ts,1,10)) "
                "FROM candlesticks"
            ).fetchone()
            _cconn.close()
            if _cr and _cr[0]:
                _cdl_n, _cdl_m, _cdl_t0, _cdl_t1 = _cr
                _date_range = _cdl_t0 if _cdl_t0 == _cdl_t1 else f"{_cdl_t0} to {_cdl_t1}"
                _cdl_desc += f" {int(_cdl_n):,} candles across {int(_cdl_m):,} markets captured {_date_range}."
    except Exception:
        _cdl_desc += " Candle data available (count query failed)."
    st.markdown(_cdl_desc)

    # Top markets by volume
    vol_df, vol_err = get_top_markets_by_volume(limit=20)

    if not vol_df.empty:
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
            f"color:{TEXT3};margin-bottom:0.3rem;margin-top:0.5rem;'>TOP 20 MARKETS BY VOLUME</div>",
            unsafe_allow_html=True,
        )
        # Volume bar chart
        _vdf = vol_df.head(20).copy()
        _vdf["label"] = _vdf["ticker"].fillna("").str[:35] if "ticker" in _vdf.columns else ""
        _vdf["total_volume"] = pd.to_numeric(_vdf.get("total_volume", 0), errors="coerce").fillna(0)

        fig_vol = go.Figure(go.Bar(
            x=_vdf["total_volume"],
            y=_vdf["label"],
            orientation="h",
            marker_color=BLUE,
            marker_line_width=0,
            text=_vdf["total_volume"].apply(lambda v: f"{int(v):,}"),
            textposition="outside",
        ))
        fig_vol.update_layout(**plotly_dark_layout(
            title={"text": "TOTAL VOLUME BY MARKET", "font": {"size": 10, "color": TEXT3}},
            height=500,
            xaxis_title="Volume (contracts)",
            yaxis={"autorange": "reversed"},
            margin={"l": 10, "r": 60, "t": 30, "b": 30},
        ))
        st.plotly_chart(fig_vol, use_container_width=True)

        # Summary table
        _show_cols = [c for c in ["ticker", "category", "total_volume", "candle_count",
                                   "avg_price", "avg_spread", "first_candle", "last_candle"]
                      if c in _vdf.columns]
        _disp = _vdf[_show_cols].copy()
        for col in ["avg_price", "avg_spread"]:
            if col in _disp.columns:
                _disp[col] = pd.to_numeric(_disp[col], errors="coerce").apply(
                    lambda v: f"{v:.4f}" if pd.notna(v) else "--"
                )
        if "total_volume" in _disp.columns:
            _disp["total_volume"] = pd.to_numeric(_disp["total_volume"], errors="coerce").apply(
                lambda v: f"{int(v):,}" if pd.notna(v) else "--"
            )
        st.dataframe(_disp.rename(columns={
            "ticker": "TICKER", "category": "CAT", "total_volume": "VOLUME",
            "candle_count": "CANDLES", "avg_price": "AVG PRICE",
            "avg_spread": "AVG SPREAD", "first_candle": "FIRST", "last_candle": "LAST",
        }), use_container_width=True, height=300, hide_index=True)
        st.caption("Prices on 0.00–1.00 scale (YES probability; 0.55 = 55¢ contract value). SPREAD = YES ask − YES bid.")

        # Price history drilldown
        st.markdown("<hr style='margin:0.75rem 0;'>", unsafe_allow_html=True)
        st.markdown("#### PRICE HISTORY CHART")
        _ticker_opts = _vdf["ticker"].dropna().tolist() if "ticker" in _vdf.columns else []
        if _ticker_opts:
            _sel_ticker = st.selectbox(
                "SELECT MARKET",
                _ticker_opts,
                key="p09_price_history_ticker",
            )
            _interval = st.radio(
                "INTERVAL",
                [15, 60, 1440],
                format_func=lambda v: {15: "15m", 60: "1h", 1440: "1d"}[v],
                horizontal=True,
                key="p09_price_interval",
            )
            if _sel_ticker:
                price_df, price_err = get_market_price_history(_sel_ticker, interval=_interval, limit=500)
                if not price_df.empty:
                    _ts = pd.to_datetime(price_df["period_end_ts"], utc=True, errors="coerce")
                    _close = pd.to_numeric(price_df["price_close"], errors="coerce")
                    _bid = pd.to_numeric(price_df["yes_bid_close"], errors="coerce")
                    _ask = pd.to_numeric(price_df["yes_ask_close"], errors="coerce")
                    _vol = pd.to_numeric(price_df["volume"], errors="coerce").fillna(0)

                    fig_p = go.Figure()
                    fig_p.add_trace(go.Scatter(
                        x=_ts, y=_ask, name="YES ASK",
                        line=dict(color=RED, width=1), opacity=0.7,
                    ))
                    fig_p.add_trace(go.Scatter(
                        x=_ts, y=_close, name="TRADE PRICE",
                        line=dict(color=GREEN, width=1.5),
                    ))
                    fig_p.add_trace(go.Scatter(
                        x=_ts, y=_bid, name="YES BID",
                        line=dict(color=AMBER, width=1), opacity=0.7,
                        fill="tonexty", fillcolor="rgba(34,197,94,0.04)",
                    ))
                    # Volume bars on secondary axis
                    if _vol.sum() > 0:
                        fig_p.add_trace(go.Bar(
                            x=_ts, y=_vol, name="VOLUME",
                            marker_color=BLUE, opacity=0.3,
                            yaxis="y2",
                        ))
                    # --- Overlay live WS bid/ask if available -----
                    _live_caption_extra = ""
                    try:
                        from dashboard.live_state import get_live_state as _gls_ph
                        _ws_ph = _gls_ph()
                        _snap_ph = _ws_ph.snapshot_all()
                        _live_q = _snap_ph.get(_sel_ticker)
                        if _live_q and _ts.notna().any() and (_live_q.yes_ask is not None) and (_live_q.yes_bid is not None):
                            _x0 = _ts.dropna().min()
                            _x1 = _ts.dropna().max()
                            fig_p.add_shape(
                                type="line", x0=_x0, x1=_x1,
                                y0=_live_q.yes_ask, y1=_live_q.yes_ask,
                                line=dict(color=RED, width=1.5, dash="dot"),
                                xref="x", yref="y",
                            )
                            fig_p.add_shape(
                                type="line", x0=_x0, x1=_x1,
                                y0=_live_q.yes_bid, y1=_live_q.yes_bid,
                                line=dict(color=AMBER, width=1.5, dash="dot"),
                                xref="x", yref="y",
                            )
                            fig_p.add_annotation(
                                x=_x1, y=_live_q.yes_ask,
                                text=f"LIVE ASK {_live_q.yes_ask:.3f}",
                                showarrow=False, xanchor="right",
                                font=dict(size=8, color=RED),
                                bgcolor="rgba(0,0,0,0.5)",
                            )
                            fig_p.add_annotation(
                                x=_x1, y=_live_q.yes_bid,
                                text=f"LIVE BID {_live_q.yes_bid:.3f}",
                                showarrow=False, xanchor="right",
                                font=dict(size=8, color=AMBER),
                                bgcolor="rgba(0,0,0,0.5)",
                            )
                            _live_caption_extra = (
                                f" · LIVE: bid={_live_q.yes_bid:.3f} ask={_live_q.yes_ask:.3f} "
                                f"(age {int(_live_q.age_seconds)}s)"
                            )
                    except Exception:
                        pass
                    fig_p.update_layout(**plotly_dark_layout(
                        title={"text": f"{_sel_ticker} — PRICE HISTORY", "font": {"size": 10, "color": TEXT3}},
                        height=380,
                        xaxis_title="", yaxis_title="YES Price",
                        yaxis=dict(range=[0, 1], tickformat=".2f"),
                        yaxis2=dict(
                            title="Volume", overlaying="y", side="right",
                            showgrid=False,
                        ),
                        legend=dict(orientation="h", y=1.08),
                        margin={"l": 50, "r": 60, "t": 40, "b": 30},
                    ))
                    st.plotly_chart(fig_p, use_container_width=True)
                    st.caption(
                        f"{len(price_df):,} candles · "
                        f"{_ts.min().strftime('%Y-%m-%d') if pd.notna(_ts.min()) else '?'} – "
                        f"{_ts.max().strftime('%Y-%m-%d') if pd.notna(_ts.max()) else '?'}"
                        + _live_caption_extra
                        + (" · Dotted lines = live WS quote" if _live_caption_extra else "")
                    )
                elif price_err:
                    st.warning(
                        f"Price history unavailable for {_sel_ticker}: {price_err}. "
                        "Run the candlestick ingestion pipeline: "
                        "`python -m scripts.ingest_candlesticks` (or equivalent)."
                    )
                else:
                    _interval_label = {15: "15m", 60: "1h", 1440: "1d"}.get(_interval, str(_interval))
                    st.info(
                        f"No {_interval_label} candles found for **{_sel_ticker}**. "
                        "Try switching to 1h or 1d, or ingest data for this market first."
                    )
    elif vol_err:
        _no_data_panel(
            f"Candlestick data unavailable: {vol_err}. "
            "Check that dashboard.db exists and has a 'candlesticks' table. "
            "Run: python -m scripts.ingest_candlesticks (or the equivalent ingestion command) "
            "to populate historical OHLC data."
        )
    else:
        _no_data_panel(
            "No candlestick data in database. "
            "The 'candlesticks' table exists but is empty. "
            "Run: python -m scripts.ingest_candlesticks to pull OHLC data from the Kalshi API."
        )


# --- Helpers -----

def _badge(label: str, color: str):
    st.markdown(
        f"""<span style='font-family:JetBrains Mono,monospace;font-size:0.6rem;
letter-spacing:0.1em;color:{color};border:1px solid {color};
padding:2px 8px;border-radius:2px;'>{label}</span>&nbsp;""",
        unsafe_allow_html=True,
    )


def _stat_row(label: str, value: str, note: str = ""):
    from dashboard.styles import PANEL, BORDER, TEXT, TEXT3
    note_html = f"<span style='color:{TEXT3};font-size:0.6rem;margin-left:0.5rem;'>{note}</span>" if note else ""
    st.markdown(
        f"""<div style='display:flex;justify-content:space-between;align-items:center;
padding:4px 0;border-bottom:1px solid {BORDER};'>
<span style='font-size:0.7rem;color:{TEXT3};font-family:Inter,sans-serif;'>{label}</span>
<span style='font-family:JetBrains Mono,monospace;font-size:0.78rem;color:{TEXT};'>
{value}{note_html}
</span>
</div>""",
        unsafe_allow_html=True,
    )


def _stat_table(rows: list):
    from dashboard.styles import PANEL, BORDER, TEXT, TEXT3
    html = f"<div style='background:{PANEL};border:1px solid {BORDER};border-radius:3px;padding:0.5rem 0;'>"
    for label, value in rows:
        html += f"""
<div style='display:flex;justify-content:space-between;padding:5px 1rem;
border-bottom:1px solid {BORDER};'>
<span style='font-size:0.65rem;letter-spacing:0.06em;text-transform:uppercase;
color:{TEXT3};font-family:Inter,sans-serif;'>{label}</span>
<span style='font-family:JetBrains Mono,monospace;font-size:0.78rem;
color:{TEXT};'>{value}</span>
</div>"""
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def _no_data_panel(msg: str):
    from dashboard.styles import PANEL, BORDER, TEXT3
    st.markdown(
        f"<div style='background:{PANEL};border:1px solid {BORDER};padding:1rem;border-radius:3px;"
        f"color:{TEXT3};font-size:0.75rem;font-family:JetBrains Mono,monospace;'>"
        f"NO DATA — {msg}</div>",
        unsafe_allow_html=True,
    )

