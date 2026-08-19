"""dashboard/pages/p06_backtest.py --  Strategy Backtesting"""
from __future__ import annotations
import subprocess
import sys
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime, timezone, timedelta

from dashboard.data_layer import get_backtest_runs, get_backtest_trades, get_top_markets_by_volume
from dashboard.styles import plotly_dark_layout, GREEN, RED, AMBER, BLUE, TEXT, TEXT2, TEXT3, PANEL, BORDER, PANEL2

try:
    from backtest.metrics import compute_performance_metrics
except ImportError:
    compute_performance_metrics = None


def render():
    st.markdown("""
<span style='font-size:1rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;'>
STRATEGY BACKTEST
</span>
""", unsafe_allow_html=True)
    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    st.caption("SIMULATION ONLY — hypothetical P&L from historical snapshots · no real trades")

    # --- SQLite mode guard -----
    from dashboard.data_layer import get_system_health as _gh_bt
    _h_bt = _gh_bt()
    _is_sqlite_bt = not _h_bt.get("db_connected", False) and _h_bt.get("db_mode") == "sqlite"
    # Suppress SQLite banner if sidebar already confirmed Neon is reachable
    if _is_sqlite_bt and bool(st.session_state.get("_sidebar_neon_ok", False)):
        _is_sqlite_bt = False
    if _is_sqlite_bt:
        from dashboard.styles import AMBER as _A_BT, PANEL as _P_BT, BORDER as _B_BT, TEXT2 as _T2_BT, GREEN as _G_BT
        # Check if Neon has arb history we can surface
        _bt_neon_count = 0
        try:
            import dashboard.live_arb_store as _las_bt
            from sqlalchemy import text as _bt_text
            _bt_eng = getattr(_las_bt, "_pg_engine", None) or _las_bt.get_pg_engine_cached()
            if _bt_eng is not None:
                with _bt_eng.connect() as _bt_c:
                    _bt_row = _bt_c.execute(_bt_text(
                        "SELECT COUNT(*) FROM live_arbs_cloud WHERE strategy_type NOT IN ('yes_no_complement')"
                    )).fetchone()
                _bt_neon_count = int(_bt_row[0]) if _bt_row else 0
        except Exception:
            pass
        _neon_note = f" · {_bt_neon_count} arbs in Neon cloud" if _bt_neon_count > 0 else ""
        st.caption(f"RUN BACKTEST requires direct PostgreSQL connection · launch from desktop app{_neon_note}")
        # Neon edge distribution summary (shown instead of backtest results when no local DB)
        if _bt_neon_count > 0:
            try:
                from sqlalchemy import text as _bt_sql
                with _bt_eng.connect() as _bt_ec:
                    _bt_strat_rows = _bt_ec.execute(_bt_sql(
                        "SELECT strategy_type, COUNT(*) AS n, "
                        "AVG(net_edge_cents) AS avg_edge, MAX(net_edge_cents) AS best_edge, "
                        "MIN(net_edge_cents) AS min_edge "
                        "FROM live_arbs_cloud "
                        "WHERE strategy_type NOT IN ('yes_no_complement') "
                        "AND net_edge_cents > 0 "
                        "GROUP BY strategy_type ORDER BY n DESC"
                    )).fetchall()
                    _bt_30d = _bt_ec.execute(_bt_sql(
                        "SELECT DATE(detected_at AT TIME ZONE 'UTC')::text AS d, COUNT(*) AS n "
                        "FROM live_arbs_cloud "
                        "WHERE strategy_type NOT IN ('yes_no_complement') "
                        "AND net_edge_cents > 0 "
                        "AND detected_at >= NOW() - INTERVAL '30 days' "
                        "GROUP BY 1 ORDER BY 1"
                    )).fetchall()
                if _bt_strat_rows:
                    st.markdown(
                        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                        f"color:#22C55E;font-family:Inter,sans-serif;margin:0.75rem 0 0.35rem;'>"
                        f"● NEON CLOUD — ARB EDGE SUMMARY (CE/ME/TH)</div>",
                        unsafe_allow_html=True,
                    )
                    _bt_cols = st.columns(len(_bt_strat_rows))
                    for _bi, _br in enumerate(_bt_strat_rows):
                        _bn, _bavg, _bbest = int(_br[1]), float(_br[2] or 0), float(_br[3] or 0)
                        _bt_strat_names = {"mutually_exclusive": "ME", "threshold_order": "TH", "yes_no_complement": "YNC", "collectively_exhaustive": "CE", "superset": "SS"}
                        with _bt_cols[_bi]:
                            st.metric(
                                _bt_strat_names.get(str(_br[0]).lower(), str(_br[0]).upper()[:6]),
                                f"{_bn:,} arbs",
                                help=f"avg {_bavg:.2f}¢ · best {_bbest:.2f}¢",
                            )
                            st.caption(f"AVG {_bavg:.2f}¢ · BEST {_bbest:.2f}¢")
                if _bt_30d:
                    import pandas as _pd_bt30
                    _bt30_df = _pd_bt30.DataFrame(_bt_30d, columns=["date", "count"])
                    _bt30_fig = go.Figure(go.Bar(
                        x=_bt30_df["date"], y=_bt30_df["count"],
                        marker_color="#22C55E", marker_line_width=0,
                    ))
                    _bt30_fig.update_layout(**plotly_dark_layout(
                        height=130,
                        margin={"l": 30, "r": 10, "t": 10, "b": 30},
                        xaxis={"tickformat": "%b %d", "tickfont": {"size": 8, "family": "JetBrains Mono"}, "showgrid": False},
                        yaxis={"tickfont": {"size": 8, "family": "JetBrains Mono"}, "showgrid": False, "zeroline": False},
                        showlegend=False,
                    ))
                    st.markdown(
                        f"<div style='font-size:0.58rem;letter-spacing:0.08em;text-transform:uppercase;"
                        f"color:#64748B;font-family:Inter,sans-serif;margin-top:0.5rem;margin-bottom:0.2rem;'>"
                        f"30-DAY NEON DETECTION RATE</div>",
                        unsafe_allow_html=True,
                    )
                    st.plotly_chart(_bt30_fig, use_container_width=True)
            except Exception:
                pass

    # --- Parameters -----
    with st.expander("BACKTEST PARAMETERS", expanded=False):
        _today = datetime.now(timezone.utc).date()
        _default_start = _today - timedelta(days=30)
        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            start_date = st.date_input(
                "START DATE",
                value=_default_start,
            )
            end_date = st.date_input(
                "END DATE",
                value=_today,
            )
        with pc2:
            strategy_sel = st.selectbox(
                "STRATEGY",
                ["collectively_exhaustive", "mutually_exclusive", "threshold_order", "yes_no_complement"],
                help="Strategy to backtest — CE/ME/TH are executable arbs · YNC not executable on Kalshi",
            )
            init_capital = st.number_input("INITIAL CAPITAL ($)", value=10000, step=1000)
        with pc3:
            pos_size = st.number_input("POSITION SIZE (contracts)", value=10, step=1)
            fee_override = st.number_input(
                "FEE OVERRIDE (¢, 0=auto)", value=0.0, step=0.1, format="%.2f",
                help="Per-contract fee in ¢ · 0 = use fee from database",
            )
            slippage_bps = st.number_input("SLIPPAGE (bps)", value=5, step=1)

        col_run, col_status = st.columns([1, 3])
        with col_run:
            run_clicked = st.button("▶ RUN BACKTEST", use_container_width=True, disabled=_is_sqlite_bt)
        with col_status:
            st.markdown(
                "<small style='color:#888;'>Runs backtest engine in a subprocess. "
                "Requires PostgreSQL + historical data in DB.</small>",
                unsafe_allow_html=True,
            )
        st.caption("Parameter inputs are informational — backtest engine uses configured defaults")

        if run_clicked:
            with st.spinner("Running backtest engine…"):
                try:
                    result = subprocess.run(
                        [sys.executable, "-c",
                         f"from backtest.engine import BacktestEngine; "
                         f"BacktestEngine().run_all(strategy='{strategy_sel}')"],
                        capture_output=True, text=True, timeout=300,
                    )
                    if result.returncode == 0:
                        st.success("Backtest complete — reload to see results")
                        if result.stdout.strip():
                            st.code(result.stdout[-2000:], language="text")
                    else:
                        st.error("BACKTEST FAILED:")
                        st.code((result.stderr or result.stdout)[-2000:], language="text")
                except subprocess.TimeoutExpired:
                    st.warning("BACKTEST TIMED OUT AFTER 5 MINUTES")
                except FileNotFoundError:
                    st.error("Python executable not found — backtest environment misconfigured")

    st.caption("Simulation uses Kalshi OHLC candles (1-hour bins) · no bid/ask spread · slippage not modeled")

    # --- Available runs -----
    runs_df, err = get_backtest_runs()

    if err and runs_df.empty:
        st.caption("NO BACKTEST RUNS")

        # Show strategy methodology even without data
        from dashboard.styles import AMBER as _AMBER
        st.markdown("<hr>", unsafe_allow_html=True)
        st.caption("YNC: YES+NO sum ≥100¢ structurally · sum <100¢ in DB = feed/rounding artifact · historical scanner data only · not executable")
        # --- Top markets by volume -----
        st.markdown("<hr>", unsafe_allow_html=True)
        _vol_df, _ = get_top_markets_by_volume(limit=20)
        if not _vol_df.empty:
            _vol_display = _vol_df.head(20).copy()
            _vol_display["label"] = _vol_display["ticker"].fillna("").str[:35]
            _vol_display["total_volume"] = pd.to_numeric(_vol_display["total_volume"], errors="coerce").fillna(0)
            fig_v = go.Figure(go.Bar(
                x=_vol_display["total_volume"],
                y=_vol_display["label"],
                orientation="h",
                marker_color=BLUE, marker_line_width=0,
                text=_vol_display["total_volume"].apply(lambda v: f"{int(v):,}"),
                textposition="outside",
            ))
            fig_v.update_layout(**plotly_dark_layout(
                title={"text": "TOP 20 MARKETS BY VOLUME", "font": {"size": 10, "color": TEXT3}},
                height=500,
                xaxis_title="VOLUME (CONTRACTS)",
                yaxis={"autorange": "reversed"},
                margin={"l": 10, "r": 60, "t": 30, "b": 30},
            ))
            st.plotly_chart(fig_v, use_container_width=True)

        return

    tab_runs, tab_compare = st.tabs(["AVAILABLE RUNS", "STRATEGY COMPARISON"])

    with tab_runs:
        if runs_df.empty:
            st.caption("NO BACKTEST RUNS IN DATABASE")
        else:
            display_runs = runs_df.copy()
            for col in ["total_pnl", "avg_pnl"]:
                if col in display_runs.columns:
                    display_runs[col] = display_runs[col].apply(
                        lambda v: f"${float(v):.4f}" if pd.notna(v) else "--"
                    )
            if "win_rate" in display_runs.columns:
                display_runs["win_rate"] = display_runs["win_rate"].apply(
                    lambda v: f"{float(v)*100:.1f}%" if pd.notna(v) else "--"
                )
            st.dataframe(display_runs, use_container_width=True, height=200, hide_index=True)

    with tab_compare:
        _render_strategy_comparison(runs_df)

    # --- Run selection -----
    st.markdown("<hr>", unsafe_allow_html=True)
    run_id = st.text_input(
        "RUN ID",
        placeholder="e.g. bt_20260101_120000",
        help="ENTER A RUN_ID FROM THE TABLE ABOVE",
    )

    if not run_id:
        if not runs_df.empty and "run_id" in runs_df.columns:
            run_id = st.selectbox("OR SELECT RUN", ["--"] + runs_df["run_id"].tolist())
            if run_id == "--":
                return
        else:
            return

    # --- Load trades -----
    trades_df, err2 = get_backtest_trades(run_id)

    if err2 or trades_df.empty:
        st.caption(f"NO TRADES FOR RUN '{run_id}'")
        return

    # --- Compute metrics -----
    pnl_series = pd.to_numeric(trades_df.get("net_pnl", pd.Series()), errors="coerce").dropna()
    if compute_performance_metrics and len(pnl_series) >= 2:
        metrics = compute_performance_metrics(pnl_series)
    else:
        metrics = _basic_metrics(pnl_series)

    # --- KPI row -----
    k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
    k1.metric("TRADES",       str(metrics.get("n_trades", len(trades_df))))
    k2.metric("TOTAL P&L",    f"${float(metrics.get('total_pnl', 0)):.4f}")
    k3.metric("WIN RATE",     f"{float(metrics.get('win_rate', 0))*100:.1f}%")
    def _fmt_ratio(v) -> str:
        """Format a ratio metric, returning '--' for None/NaN."""
        try:
            return f"{float(v):.3f}" if v is not None and pd.notna(v) else "--"
        except (TypeError, ValueError):
            return "--"
    k4.metric("SHARPE",       _fmt_ratio(metrics.get("sharpe_ratio")))
    k5.metric("SORTINO",      _fmt_ratio(metrics.get("sortino_ratio")))
    k6.metric("CALMAR",       _fmt_ratio(metrics.get("calmar_ratio")))
    _mdd_raw = metrics.get("max_drawdown", 0)
    try:
        _mdd_str = f"${float(_mdd_raw):.4f}" if _mdd_raw is not None and pd.notna(_mdd_raw) else "--"
    except (TypeError, ValueError):
        _mdd_str = "--"
    k7.metric("MAX DRAWDOWN", _mdd_str)

    # --- Summary table -----
    _avg_pnl_raw = metrics.get("avg_pnl", 0)
    try:
        _avg_pnl_str = f"${float(_avg_pnl_raw):.4f}" if _avg_pnl_raw is not None and pd.notna(_avg_pnl_raw) else "--"
    except (TypeError, ValueError):
        _avg_pnl_str = "--"
    _summary_data = {
        "METRIC": ["TOTAL TRADES", "WIN RATE", "AVG P&L / TRADE", "SHARPE RATIO", "MAX DRAWDOWN"],
        "VALUE": [
            str(metrics.get("n_trades", len(trades_df))),
            f"{float(metrics.get('win_rate', 0))*100:.1f}%",
            _avg_pnl_str,
            _fmt_ratio(metrics.get("sharpe_ratio")),
            _mdd_str,
        ],
    }
    st.dataframe(pd.DataFrame(_summary_data), use_container_width=True, hide_index=True, height=210)

    # --- Win streak metric -----
    if len(pnl_series) > 0:
        _streak, _max_streak = 0, 0
        for _v in pnl_series:
            if _v > 0:
                _streak += 1
                _max_streak = max(_max_streak, _streak)
            else:
                _streak = 0
        st.metric("MAX WIN STREAK", _max_streak)

    # --- Trade P&L histogram (cents) -----
    _pnl_cents = pnl_series * 100
    if len(_pnl_cents) > 0:
        fig_hist = go.Figure(go.Histogram(
            x=_pnl_cents,
            nbinsx=40,
            marker_color="#26a69a",
            marker_line_width=0,
        ))
        fig_hist.add_vline(x=0, line_dash="dash", line_color=TEXT3, line_width=0.8)
        fig_hist.update_layout(
            **plotly_dark_layout(
            title={"text": "TRADE P&L DISTRIBUTION (¢)", "font": {"size": 10, "color": TEXT3}},
            height=200, xaxis_title="P&L PER TRADE (¢)", yaxis_title="COUNT",
        ))
        st.plotly_chart(fig_hist, use_container_width=True)

    st.markdown("<hr>", unsafe_allow_html=True)

    # --- Charts -----
    if "entry_ts" in trades_df.columns:
        trades_df["entry_ts"] = pd.to_datetime(trades_df["entry_ts"], utc=True)
        trades_df = trades_df.sort_values("entry_ts")

    trades_df["cum_pnl"] = pnl_series.reindex(trades_df.index).fillna(0).cumsum()
    trades_df["drawdown"] = (
        trades_df["cum_pnl"] - trades_df["cum_pnl"].cummax()
    )

    # --- Underwater equity curve & time-in-drawdown -----
    _init_cap_val = float(init_capital)
    _equity_curve = trades_df["cum_pnl"] + _init_cap_val
    _eq_running_max = _equity_curve.cummax()
    _in_dd_mask = _equity_curve < _eq_running_max
    _time_in_dd_pct = float(_in_dd_mask.sum() / len(_in_dd_mask) * 100) if len(_in_dd_mask) > 0 else 0.0
    trades_df["dd_pct"] = (
        (_equity_curve / _eq_running_max.replace(0, float("nan"))) - 1
    ) * 100

    ch1, ch2 = st.columns(2)

    with ch1:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=trades_df.get("entry_ts", trades_df.index),
            y=trades_df["cum_pnl"],
            mode="lines", name="EQUITY",
            line={"color": GREEN, "width": 1.5},
            fill="tozeroy", fillcolor="rgba(34,197,94,0.08)",
        ))
        fig.add_hline(y=0, line_dash="dash", line_color=TEXT3, line_width=0.8)
        # Max drawdown annotation
        _dd_col = trades_df["drawdown"]
        if len(_dd_col) > 0 and _dd_col.min() < 0:
            _dd_idx = _dd_col.idxmin()
            _dd_x = trades_df.loc[_dd_idx, "entry_ts"] if "entry_ts" in trades_df.columns else _dd_idx
            _dd_y = float(_dd_col.loc[_dd_idx]) + float(trades_df["cum_pnl"].loc[_dd_idx])
            fig.add_annotation(
                x=_dd_x, y=_dd_y,
                text="MAX DD",
                arrowhead=2,
                arrowcolor="red",
                font_color="red",
                font={"size": 9},
                showarrow=True,
                ax=20, ay=-30,
            )
        fig.update_layout(
            **plotly_dark_layout(
            title={"text": "EQUITY CURVE", "font": {"size": 10, "color": TEXT3}},
            height=260, xaxis_title="", yaxis_title="CUMULATIVE P&L ($)",
        ))
        st.plotly_chart(fig, use_container_width=True)

    with ch2:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=trades_df.get("entry_ts", trades_df.index),
            y=trades_df["drawdown"],
            mode="lines", name="DRAWDOWN",
            line={"color": RED, "width": 1.5},
            fill="tozeroy", fillcolor="rgba(239,68,68,0.10)",
        ))
        fig2.update_layout(
            **plotly_dark_layout(
            title={"text": "DRAWDOWN", "font": {"size": 10, "color": TEXT3}},
            height=260, xaxis_title="", yaxis_title="DRAWDOWN ($)",
        ))
        st.plotly_chart(fig2, use_container_width=True)

    # --- Rolling Sharpe ratio chart (real trade P&L from DB) -----
    _n_real = len(pnl_series)
    if _n_real < 20:
        st.caption(f"NEED 20+ ARBS FOR ROLLING SHARPE — {_n_real} LOGGED SO FAR")
    else:
        _roll_mean = pnl_series.rolling(20).mean()
        _roll_std  = pnl_series.rolling(20).std()
        _roll_sharpe = (_roll_mean / _roll_std.replace(0, float("nan"))) * (252 ** 0.5)
        _x_sharpe = trades_df.get("entry_ts", trades_df.index)
        fig_rs = go.Figure()
        fig_rs.add_trace(go.Scatter(
            x=_x_sharpe,
            y=_roll_sharpe.values,
            mode="lines",
            name="ROLLING SHARPE",
            line={"color": AMBER, "width": 1.5},
        ))
        fig_rs.add_hline(
            y=1.0,
            line_dash="dash",
            line_color=GREEN,
            line_width=0.9,
            annotation_text="Sharpe=1 (good)",
            annotation_font_size=9,
            annotation_font_color=GREEN,
        )
        fig_rs.add_hline(
            y=0.5,
            line_dash="dot",
            line_color=AMBER,
            line_width=0.8,
            annotation_text="Sharpe=0.5 (acceptable)",
            annotation_font_size=9,
            annotation_font_color=AMBER,
        )
        fig_rs.update_layout(
            **plotly_dark_layout(
            title={"text": "ROLLING SHARPE (20-TRADE)", "font": {"size": 10, "color": TEXT3}},
            height=180, xaxis_title="", yaxis_title="ROLLING SHARPE (20-TRADE)",
        ))
        st.plotly_chart(fig_rs, use_container_width=True)
        st.caption("Rolling 20-trade Sharpe ratio — values >1.0 indicate strong risk-adjusted returns")

        # Longest win / loss streaks
        _win_streak, _loss_streak = 0, 0
        _max_win_streak, _max_loss_streak = 0, 0
        for _pv in pnl_series:
            if _pv > 0:
                _win_streak += 1
                _loss_streak = 0
                _max_win_streak = max(_max_win_streak, _win_streak)
            elif _pv < 0:
                _loss_streak += 1
                _win_streak = 0
                _max_loss_streak = max(_max_loss_streak, _loss_streak)
            else:
                _win_streak = 0
                _loss_streak = 0
        _streak_c1, _streak_c2 = st.columns(2)
        _streak_c1.metric("LONGEST WIN STREAK", _max_win_streak)
        _streak_c2.metric("LONGEST LOSS STREAK", _max_loss_streak)

    ch3, ch4 = st.columns(2)

    with ch3:
        # Monthly returns heatmap approximation
        if "entry_ts" in trades_df.columns and "net_pnl" in trades_df.columns:
            trades_df["month"] = trades_df["entry_ts"].dt.to_period("M").astype(str)
            monthly = trades_df.groupby("month")["net_pnl"].apply(lambda s: pd.to_numeric(s, errors="coerce").sum())
            colors  = [GREEN if v > 0 else RED for v in monthly.values]
            fig3 = go.Figure(go.Bar(
                x=monthly.index, y=monthly.values,
                marker_color=colors, marker_line_width=0,
            ))
            fig3.update_layout(
                **plotly_dark_layout(
                title={"text": "MONTHLY RETURNS", "font": {"size": 10, "color": TEXT3}},
                height=240, xaxis_title="", yaxis_title="P&L ($)",
            ))
            st.plotly_chart(fig3, use_container_width=True)

    with ch4:
        # Trade P&L distribution
        fig4 = go.Figure(go.Histogram(
            x=pnl_series,
            nbinsx=40,
            marker_color=BLUE, marker_line_width=0,
        ))
        fig4.add_vline(x=0, line_dash="dash", line_color=TEXT3, line_width=0.8)
        fig4.update_layout(
            **plotly_dark_layout(
            title={"text": "TRADE P&L DISTRIBUTION", "font": {"size": 10, "color": TEXT3}},
            height=240, xaxis_title="P&L ($)", yaxis_title="COUNT",
        ))
        st.plotly_chart(fig4, use_container_width=True)

    # --- Kelly Criterion (Live Arb Data) -----
    with st.expander("KELLY CRITERION — ARB SIZING", expanded=False):
        # --- Configurable bankroll input -----
        _bankroll_input = st.number_input(
            "BANKROLL ($)",
            min_value=100,
            max_value=10_000_000,
            value=10_000,
            step=1_000,
            help="Total trading capital — Kelly position size is a fraction of this",
            key="p06_kelly_bankroll",
        )

        # --- Pull real arb stats from live arbs DB -----
        try:
            from dashboard.data_layer import get_live_arbs_cloud_stats as _glas_kelly
            _kelly_stats = _glas_kelly()
            _live_arb_count = _kelly_stats.get("count", 0)
            _live_avg_net_edge_cents = _kelly_stats.get("avg_net_edge_cents", 0.0)
        except Exception:
            _live_arb_count = 0
            _live_avg_net_edge_cents = 0.0
        # Fallback to live_arbs SQLite
        if _live_arb_count == 0:
            try:
                import sqlite3 as _sq_kelly
                from pathlib import Path as _P_kelly
                _la_db = _P_kelly(__file__).parent.parent / "live_arbs.db"
                if _la_db.exists():
                    _lc = _sq_kelly.connect(str(_la_db), check_same_thread=False)
                    _lk = _lc.execute(
                        "SELECT COUNT(*), AVG(net_edge_cents) FROM live_arbs "
                        "WHERE net_edge_cents > 0 AND strategy != 'yes_no_complement' "
                        "AND strategy NOT IN ('yes_no_complement')"
                    ).fetchone()
                    _lc.close()
                    if _lk and _lk[0]:
                        _live_arb_count = int(_lk[0])
                        _live_avg_net_edge_cents = float(_lk[1] or 0)
            except Exception:
                pass
        # Fallback: Neon cloud (live_arbs_cloud) — used on Streamlit Cloud where SQLite has no data
        if _live_arb_count == 0:
            try:
                import dashboard.live_arb_store as _las_kelly
                from sqlalchemy import text as _kelly_text
                _kelly_eng = getattr(_las_kelly, "_pg_engine", None) or _las_kelly.get_pg_engine_cached()
                if _kelly_eng is not None:
                    with _kelly_eng.connect() as _kelly_c:
                        _kelly_row = _kelly_c.execute(_kelly_text(
                            "SELECT COUNT(*), AVG(net_edge_cents) FROM live_arbs_cloud "
                            "WHERE net_edge_cents > 0 AND strategy_type NOT IN "
                            "('yes_no_complement')"
                        )).fetchone()
                    if _kelly_row and _kelly_row[0]:
                        _live_arb_count = int(_kelly_row[0])
                        _live_avg_net_edge_cents = float(_kelly_row[1] or 0)
            except Exception:
                pass
        # Final fallback: use backtest trade stats if no live data
        if _live_arb_count == 0 and len(pnl_series) > 0:
            _live_arb_count = int(metrics.get("n_trades", len(pnl_series)))
            _live_avg_net_edge_cents = float(metrics.get("avg_pnl", 0)) * 100

        # --- Display live arb stats -----
        _lk_c1, _lk_c2 = st.columns(2)
        _lk_c1.metric("LIVE ARB RECORDS", f"{_live_arb_count:,}", help="CE/ME/TH arbs in live_arbs DB with net_edge_cents > 0")
        _lk_c2.metric("AVG NET EDGE", f"{_live_avg_net_edge_cents:.2f}¢", help="AVERAGE NET EDGE PER ARB AFTER KALSHI FEES")

        # --- Kelly formula ---
        _P_CE = 0.95  # assumed win probability (conservative estimate)
        _net_edge_frac = _live_avg_net_edge_cents / 100.0  # convert cents to dollars (fraction of $1)
        _cost_to_enter = max(1.0 - _net_edge_frac, 0.01)  # avoid divide-by-zero
        _b_ratio = _net_edge_frac / _cost_to_enter if _cost_to_enter > 0 else 0.0
        if _b_ratio > 0 and _live_avg_net_edge_cents > 0:
            _kelly_live = (_P_CE * _b_ratio - (1 - _P_CE)) / _b_ratio
            _kelly_live = max(min(_kelly_live, 1.0), 0.0)
            _half_kelly_live = _kelly_live * 0.5
            _rec_pos_size = _bankroll_input * _half_kelly_live

            _kl_c1, _kl_c2, _kl_c3 = st.columns(3)
            _kl_c1.metric("KELLY FRACTION", f"{_kelly_live * 100:.2f}%", help="Full Kelly bet size as % of bankroll (p=0.95)")
            _kl_c2.metric("HALF-KELLY", f"{_half_kelly_live * 100:.2f}%", help="RECOMMENDED SAFER BET SIZE")
            _kl_c3.metric(
                "RECOMMENDED POSITION SIZE",
                f"${_rec_pos_size:,.2f}",
                help=f"Half-Kelly × bankroll (${_bankroll_input:,}). Based on avg arb edge {_live_avg_net_edge_cents:.2f}¢ .",
            )
            st.progress(min(_kelly_live, 1.0))
            st.caption(
                f"KELLY: P={_P_CE} · B={_b_ratio:.4f} (NET/COST) · NET={_live_avg_net_edge_cents:.2f}¢ · "
                f"COST={_cost_to_enter*100:.2f}¢ · HALF-KELLY = ${_rec_pos_size:,.2f} ON ${_bankroll_input:,}"
            )
            if _kelly_live > 0.25:
                st.caption(f"HIGH KELLY FRACTION ({_kelly_live:.0%}) — CONSIDER HALF-KELLY")
            # Position sizing table for common bankrolls
            _kelly_br_rows = []
            for _br in [1_000, 5_000, _bankroll_input, 50_000, 100_000]:
                _kelly_br_rows.append({
                    "BANKROLL": f"${_br:,}",
                    "FULL KELLY $": f"${_br * _kelly_live:,.2f}",
                    "HALF KELLY $": f"${_br * _half_kelly_live:,.2f}",
                    "QUARTER KELLY $": f"${_br * _kelly_live * 0.25:,.2f}",
                })
            # Deduplicate rows by bankroll label
            _seen_br = set()
            _dedup_rows = []
            for _r in _kelly_br_rows:
                if _r["BANKROLL"] not in _seen_br:
                    _seen_br.add(_r["BANKROLL"])
                    _dedup_rows.append(_r)
            st.dataframe(pd.DataFrame(_dedup_rows), use_container_width=True, hide_index=True)
            st.caption("P=0.95 ASSUMED WIN PROBABILITY · ADJUST BANKROLL ABOVE TO UPDATE")
        else:
            st.caption("INSUFFICIENT ARB DATA FOR KELLY FRACTION")

    # --- Kelly Criterion (Backtest) -----
    with st.expander("KELLY CRITERION — BACKTEST HISTORY", expanded=False):
        try:
            _wr = float(metrics.get("win_rate", 0))
            _wins = pnl_series[pnl_series > 0]
            _losses = pnl_series[pnl_series < 0]
            _avg_win_k = float(_wins.mean()) if len(_wins) > 0 else 0.0
            _avg_loss_k = float(_losses.abs().mean()) if len(_losses) > 0 else 0.0
            if _wr < 0.5 or _avg_win_k <= 0:
                st.caption("Kelly undefined or negative — strategy has negative edge")
            elif _wr == 0 or _avg_win_k == 0:
                st.caption("INSUFFICIENT DATA FOR KELLY FRACTION")
            else:
                _kelly = (_wr * _avg_win_k - (1 - _wr) * _avg_loss_k) / _avg_win_k
                _kelly = min(_kelly, 1.0)  # cap at 100%
                _half_kelly = _kelly * 0.5
                _kc1, _kc2 = st.columns(2)
                _kc1.metric("KELLY FRACTION", f"{_kelly * 100:.1f}%")
                _kc2.metric("HALF-KELLY", f"{_half_kelly * 100:.1f}%")
                # Visual gauge: Kelly fraction vs full Kelly
                st.progress(min(_kelly, 1.0))
                st.caption(f"RECOMMENDED BET SIZE: {_half_kelly * 100:.1f}% OF BANKROLL (HALF-KELLY)")
                if _kelly > 0.25:
                    st.caption(f"HIGH KELLY FRACTION (>{_kelly:.0%}) — CONSIDER HALF-KELLY")
                # Bankroll sizing table
                _bankrolls = [1_000, 5_000, 10_000, 50_000, 100_000]
                _br_rows = []
                for _br in _bankrolls:
                    _br_rows.append({
                        "BANKROLL": f"${_br:,}",
                        "FULL KELLY $": f"${_br * _kelly:,.2f}",
                        "HALF KELLY $": f"${_br * _half_kelly:,.2f}",
                        "QUARTER KELLY $": f"${_br * _kelly * 0.25:,.2f}",
                    })
                st.dataframe(pd.DataFrame(_br_rows), use_container_width=True, hide_index=True)
                st.caption("KELLY FRACTION = OPTIMAL BET SIZE % OF BANKROLL · HALF-KELLY SAFER IN PRACTICE")
        except Exception as _ke:
            st.caption(f"KELLY CRITERION UNAVAILABLE: {_ke}")

    # --- Position Sizing Recommendations -----
    st.markdown(
        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
        f"color:{TEXT3};margin-bottom:0.4rem;margin-top:0.75rem;'>POSITION SIZING RECOMMENDATIONS</div>",
        unsafe_allow_html=True,
    )
    try:
        _wr_ps = float(metrics.get("win_rate", 0))
        _wins_ps = pnl_series[pnl_series > 0]
        _losses_ps = pnl_series[pnl_series < 0]
        _avg_win_ps = float(_wins_ps.mean()) if len(_wins_ps) > 0 else 0.0
        _avg_loss_ps = float(_losses_ps.abs().mean()) if len(_losses_ps) > 0 else 0.0
        if _wr_ps > 0 and _avg_win_ps > 0:
            _kelly_ps = (_wr_ps * _avg_win_ps - (1 - _wr_ps) * _avg_loss_ps) / _avg_win_ps
            _kelly_ps = min(_kelly_ps, 1.0)
            if _kelly_ps < 0:
                st.caption("NEGATIVE KELLY — STRATEGY HAS NEGATIVE EDGE IN THIS BACKTEST WINDOW")
            elif _kelly_ps <= 0.25:
                st.caption(f"KELLY FRACTION: {_kelly_ps*100:.0f}% OF BANKROLL PER TRADE")
            else:
                st.caption(f"KELLY FRACTION: {_kelly_ps*100:.0f}% — CONSIDER HALF-KELLY ({_kelly_ps*50:.0f}%)")
        else:
            st.caption("INSUFFICIENT TRADE DATA TO COMPUTE POSITION SIZING")
    except Exception as _ps_err:
        st.caption(f"POSITION SIZING UNAVAILABLE: {_ps_err}")

    # --- Monte Carlo Simulation -----
    with st.expander("MONTE CARLO SIMULATION", expanded=False):
        try:
            if len(pnl_series) < 5:
                st.caption("Fewer than 5 trades — Monte Carlo skipped")
            else:
                _pnl_arr = pnl_series.values
                _n = len(_pnl_arr)
                n_sims = st.slider(
                    "SIMULATION PATHS",
                    min_value=100, max_value=2000, value=500, step=100,
                    key="p06_mc_n_sims",
                    help="Bootstrap paths to simulate — more = smoother fan chart, slower render",
                )
                results = []
                for _ in range(n_sims):
                    path = np.cumsum(np.random.choice(_pnl_arr, size=_n, replace=True))
                    results.append(path)
                results = np.array(results)
                median_path = np.median(results, axis=0)
                low_path    = np.percentile(results, 5, axis=0)
                high_path   = np.percentile(results, 95, axis=0)

                # P(Positive Final P&L)
                _final_pnls = results[:, -1]
                _p_pos = float(((_final_pnls > 0).sum()) / n_sims)
                # Ruin probability: fraction of paths ending below 50% of starting bankroll
                _init_bk = float(init_capital)
                _ruin_threshold = _init_bk * 0.5 - _init_bk  # net P&L at which bankroll hits 50%
                _p_ruin = float((_final_pnls < _ruin_threshold).sum() / n_sims)

                _mc_m1, _mc_m2, _mc_m3 = st.columns(3)
                _mc_m1.metric("P&L 5th pct (final)",  f"${float(low_path[-1]):.4f}")
                _mc_m2.metric("P&L 95th pct (final)", f"${float(high_path[-1]):.4f}")
                _mc_m3.metric("P(POSITIVE FINAL P&L)", f"{_p_pos*100:.1f}%")

                _trade_nums = list(range(1, _n + 1))

                # Fan chart using plotly
                _fig_mc = go.Figure()
                # Shaded confidence band (5th–95th)
                _fig_mc.add_trace(go.Scatter(
                    x=_trade_nums + _trade_nums[::-1],
                    y=list(high_path) + list(low_path[::-1]),
                    fill="toself",
                    fillcolor="rgba(59,130,246,0.12)",
                    line={"width": 0},
                    name="5TH–95TH PCT BAND",
                    showlegend=True,
                    hoverinfo="skip",
                ))
                # 5th percentile lower bound
                _fig_mc.add_trace(go.Scatter(
                    x=_trade_nums,
                    y=low_path,
                    mode="lines",
                    name="5TH PCT",
                    line={"color": RED, "width": 1.2, "dash": "dot"},
                ))
                # 95th percentile upper bound
                _fig_mc.add_trace(go.Scatter(
                    x=_trade_nums,
                    y=high_path,
                    mode="lines",
                    name="95TH PCT",
                    line={"color": GREEN, "width": 1.2, "dash": "dot"},
                ))
                # Median path
                _fig_mc.add_trace(go.Scatter(
                    x=_trade_nums,
                    y=median_path,
                    mode="lines",
                    name="MEDIAN (50TH PCT)",
                    line={"color": BLUE, "width": 2.0},
                ))
                _fig_mc.add_hline(y=0, line_dash="dash", line_color=TEXT3, line_width=0.8)
                _fig_mc.update_layout(
                    **plotly_dark_layout(
                    title={"text": "MONTE CARLO SIMULATION (1000 PATHS, 5TH/95TH CONFIDENCE BAND)",
                           "font": {"size": 10, "color": TEXT3}},
                    height=300,
                    xaxis_title="TRADE NUMBER",
                    yaxis_title="CUMULATIVE P&L ($)",
                    legend={"x": 0, "y": 1, "bgcolor": "rgba(0,0,0,0)"},
                    margin={"l": 55, "r": 20, "t": 35, "b": 40},
                ))
                st.plotly_chart(_fig_mc, use_container_width=True)
                st.caption(f"BOOTSTRAP SIMULATION ({n_sims} ITERATIONS) · SENSITIVITY TO TRADE ORDER · BLUE = MEDIAN PATH · SHADED = 5TH–95TH PERCENTILE")

                # Ruin probability metric
                st.metric(
                    "RUIN PROBABILITY",
                    f"{_p_ruin*100:.1f}%",
                    help=f"Fraction of {n_sims} paths whose final P&L puts bankroll below "
                         f"50% of the initial ${_init_bk:,.0f} capital "
                         f"(i.e. cumulative P&L < ${_ruin_threshold:,.2f}).",
                )
                if _p_ruin > 0.10:
                    st.caption(f"RUIN PROBABILITY {_p_ruin:.0%} EXCEEDS 10%")
        except Exception as _mc_err:
            st.caption(f"MONTE CARLO SIMULATION UNAVAILABLE: {_mc_err}")

    # --- Full metrics -----
    with st.expander("ALL METRICS"):
        _render_metrics_table(metrics)

    # --- Per-strategy breakdown -----
    if "strategy_type" in trades_df.columns and "net_pnl" in trades_df.columns:
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown("#### PER-STRATEGY BREAKDOWN")
        strat_groups = trades_df.groupby("strategy_type").agg(
            trades=("net_pnl", "count"),
            total_pnl=("net_pnl", "sum"),
            avg_pnl=("net_pnl", "mean"),
            win_rate=("net_pnl", lambda x: (x > 0).sum() / len(x) if len(x) else 0),
            avg_win=("net_pnl", lambda x: x[x > 0].mean() if (x > 0).any() else 0),
            avg_loss=("net_pnl", lambda x: x[x < 0].mean() if (x < 0).any() else 0),
        ).reset_index()
        # Format for display
        disp = strat_groups.copy()
        disp["total_pnl"] = disp["total_pnl"].apply(lambda v: f"${float(v):.4f}")
        disp["avg_pnl"]   = disp["avg_pnl"].apply(lambda v: f"${float(v):.4f}")
        disp["win_rate"]  = disp["win_rate"].apply(lambda v: f"{float(v)*100:.1f}%")
        disp["avg_win"]   = disp["avg_win"].apply(lambda v: f"${float(v):.4f}" if v != 0 else "--")
        disp["avg_loss"]  = disp["avg_loss"].apply(lambda v: f"${float(v):.4f}" if v != 0 else "--")
        st.dataframe(disp, use_container_width=True, hide_index=True)

        # Strategy comparison bar chart
        fig_strat = go.Figure()
        def _pnl_color(v_str: str) -> str:
            try:
                return GREEN if float(str(v_str).replace("$", "").replace(",", "")) > 0 else RED
            except (ValueError, TypeError):
                return TEXT3
        colors_strat = [_pnl_color(v) for v in disp["total_pnl"]]
        fig_strat.add_trace(go.Bar(
            x=strat_groups["strategy_type"],
            y=strat_groups["total_pnl"].astype(float),
            marker_color=colors_strat,
            marker_line_width=0,
            text=disp["total_pnl"],
            textposition="outside",
        ))
        fig_strat.update_layout(
            **plotly_dark_layout(
            title={"text": "TOTAL P&L BY STRATEGY", "font": {"size": 10, "color": TEXT3}},
            height=220, xaxis_title="", yaxis_title="TOTAL P&L ($)",
        ))
        st.plotly_chart(fig_strat, use_container_width=True)

    # --- Per-strategy Sharpe comparison -----
    if "strategy_type" in trades_df.columns and "net_pnl" in trades_df.columns and len(trades_df) >= 5:
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown("#### STRATEGY COMPARISON")
        _pnl_num = pd.to_numeric(trades_df["net_pnl"], errors="coerce")
        _sc_rows = []
        for _strat_name, _grp in trades_df.groupby("strategy_type"):
            _s = pd.to_numeric(_grp["net_pnl"], errors="coerce").dropna()
            if len(_s) < 2:
                continue
            _n = len(_s)
            _wr = float((_s > 0).sum() / _n)
            _avg = float(_s.mean())
            _std = float(_s.std())
            _sharpe = float((_s.mean() / _std) * (252 ** 0.5)) if _std > 0 else 0.0
            # Max drawdown
            _cum = _s.cumsum()
            _mdd = float((_cum - _cum.cummax()).min())
            # Profit factor
            _gross_wins = float(_s[_s > 0].sum()) if (_s > 0).any() else 0.0
            _gross_losses = float(abs(_s[_s < 0].sum())) if (_s < 0).any() else 0.0
            _pf = (_gross_wins / _gross_losses) if _gross_losses > 0 else float("inf")
            _pf_str = f"{_pf:.2f}" if _gross_losses > 0 else "∞"
            _sc_rows.append({
                "STRATEGY": {"mutually_exclusive": "ME", "threshold_order": "TH", "yes_no_complement": "YNC", "collectively_exhaustive": "CE", "superset": "SS"}.get(str(_strat_name).lower(), str(_strat_name).upper()[:6]),
                "TRADES": _n,
                "WIN RATE": f"{_wr * 100:.1f}%",
                "AVG P&L (¢)": f"{_avg * 100:.2f}",
                "SHARPE": _sharpe,
                "MAX DRAWDOWN": f"${_mdd:.4f}",
                "PROFIT FACTOR": _pf_str,
                "_sharpe_sort": _sharpe,
            })
        if _sc_rows:
            _sc_df = pd.DataFrame(_sc_rows).sort_values("_sharpe_sort", ascending=False).drop(columns=["_sharpe_sort"])

            # Color-code Sharpe column via Styler
            def _color_sharpe(val):
                try:
                    v = float(val)
                    if v > 1.0:
                        return "color: #22c55e; font-weight: 600"
                    elif v >= 0.5:
                        return "color: #f59e0b; font-weight: 600"
                    else:
                        return "color: #ef4444; font-weight: 600"
                except (TypeError, ValueError):
                    return ""

            _sc_display = _sc_df.copy()
            _sc_display["SHARPE"] = _sc_display["SHARPE"].apply(lambda v: f"{v:.3f}")

            def _color_profit_factor(val):
                try:
                    v = float(str(val).replace("∞", "999"))
                    if v > 1.5:
                        return "color: #22c55e; font-weight: 600"
                    elif v >= 1.0:
                        return "color: #f59e0b; font-weight: 600"
                    else:
                        return "color: #ef4444; font-weight: 600"
                except (TypeError, ValueError):
                    return ""

            _sc_styler = (
                _sc_display.style
                .applymap(_color_sharpe, subset=["SHARPE"])
                .applymap(_color_profit_factor, subset=["PROFIT FACTOR"])
            )
            st.dataframe(_sc_styler, use_container_width=True, hide_index=True)

            # Best strategy callout (only when more than one row has data)
            if len(_sc_rows) > 1:
                _best_row = max(_sc_rows, key=lambda r: r["_sharpe_sort"])
                _best_name = _best_row["STRATEGY"]
                _best_sharpe = float(_best_row["_sharpe_sort"])
                st.caption(f"BEST BY SHARPE: {_best_name} ({_best_sharpe:.2f})")

    # --- Interactive filter controls -----
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("#### TRADE LOG FILTERS")
    _fc1, _fc2, _fc3 = st.columns(3)
    with _fc1:
        fee_mult = st.slider(
            "FEE MULTIPLIER",
            min_value=0.5, max_value=2.0, value=1.0, step=0.1,
            key="p06_fee_mult",
        )
        st.caption("1.0 = standard Kalshi fees · >1.0 = stress test with higher fees")
    with _fc2:
        min_edge_cents = st.slider(
            "MIN NET EDGE (¢)",
            min_value=0, max_value=20, value=2, step=1,
            key="p06_min_edge",
        )
    with _fc3:
        _STRATEGY_ABBREV = {
            "YNC": "yes_no_complement",
            "CE":  "collectively_exhaustive",
            "ME":  "mutually_exclusive",
            "TH":  "threshold_order",
            "SS":  "superset",
        }
        selected_abbrevs = st.multiselect(
            "STRATEGIES TO INCLUDE",
            options=list(_STRATEGY_ABBREV.keys()),
            default=list(_STRATEGY_ABBREV.keys()),
            key="p06_strategies",
        )

    # Apply fee multiplier to net_pnl if fee column is available
    _filtered = trades_df.copy()
    if fee_mult != 1.0 and "fee" in _filtered.columns:
        _fee_col = pd.to_numeric(_filtered["fee"], errors="coerce").fillna(0)
        _orig_fee = _fee_col
        _extra_fee = _orig_fee * (fee_mult - 1.0)
        if "net_pnl" in _filtered.columns:
            _filtered["net_pnl"] = pd.to_numeric(_filtered["net_pnl"], errors="coerce") - _extra_fee

    # Apply min net edge filter (net_pnl in dollars; convert slider cents → dollars)
    _min_edge_dollars = min_edge_cents / 100.0
    if "net_pnl" in _filtered.columns:
        _filtered = _filtered[pd.to_numeric(_filtered["net_pnl"], errors="coerce") >= _min_edge_dollars]

    # Apply strategy filter
    if "strategy_type" in _filtered.columns and selected_abbrevs:
        _selected_full = [_STRATEGY_ABBREV[a] for a in selected_abbrevs]
        _filtered = _filtered[_filtered["strategy_type"].isin(_selected_full)]
    elif not selected_abbrevs:
        _filtered = _filtered.iloc[0:0]  # empty

    # --- Trade log -----
    st.markdown("#### TRADE LOG")
    st.caption("Simulated trades only — hypothetical entries from historical order-book replay · no real orders placed")
    if _filtered.empty:
        st.caption("NO TRADES MATCH THE CURRENT FILTER SETTINGS")
    else:
        st.dataframe(_filtered, use_container_width=True, height=350, hide_index=True)

    # --- Trade Duration Analysis -----
    st.markdown("#### TRADE DURATION ANALYSIS")
    try:
        if not _filtered.empty and "entry_ts" in _filtered.columns and "exit_ts" in _filtered.columns:
            _ets = pd.to_datetime(_filtered["entry_ts"], utc=True, errors="coerce")
            _xts = pd.to_datetime(_filtered["exit_ts"], utc=True, errors="coerce")
            _dur = (_xts - _ets).dt.total_seconds().dropna()
            if len(_dur) > 0:
                avg_duration = float(_dur.mean())
                max_duration = float(_dur.max())
                _td1, _td2 = st.columns(2)
                _td1.metric("AVG HOLD TIME", f"{avg_duration:.0f}s")
                _td2.metric("MAX HOLD TIME", f"{max_duration:.0f}s")
            else:
                st.caption("TRADE DURATION DATA NOT AVAILABLE IN THIS DATASET")
        else:
            st.caption("TRADE DURATION DATA NOT AVAILABLE IN THIS DATASET")
    except Exception:
        st.caption("TRADE DURATION DATA NOT AVAILABLE IN THIS DATASET")

    # --- Full Trade Log Export -----
    with st.expander("FULL TRADE LOG", expanded=False):
        if _filtered.empty:
            st.caption("NO TRADES MATCH FILTERS")
        else:
            _log = _filtered.copy()

            # Build display columns
            _log_display = pd.DataFrame()
            _log_display["#"] = range(1, len(_log) + 1)

            # Date (ET)
            if "entry_ts" in _log.columns:
                _ts_col = pd.to_datetime(_log["entry_ts"], utc=True, errors="coerce")
                try:
                    _log_display["DATE (ET)"] = _ts_col.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    _log_display["DATE (ET)"] = _ts_col.dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                _log_display["DATE (ET)"] = "--"

            _log_display["TICKER"] = _log.get("ticker", pd.Series(["--"] * len(_log))).fillna("--").values
            _log_display["STRATEGY"] = _log.get("strategy_type", pd.Series(["--"] * len(_log))).fillna("--").values

            _pnl_col = pd.to_numeric(_log.get("net_pnl", pd.Series([0.0] * len(_log))), errors="coerce").fillna(0)
            _gross_col = pd.to_numeric(_log.get("gross_pnl", _pnl_col), errors="coerce").fillna(0)

            _log_display["GROSS (¢)"] = (_gross_col * 100).round(2).values
            _log_display["NET (¢)"] = (_pnl_col * 100).round(2).values
            _log_display["P&L ($)"] = _pnl_col.round(6).values
            _log_display["CLASSIFICATION"] = _log.get("classification", pd.Series(["--"] * len(_log))).fillna("--").values

            # Cumulative P&L column
            _log_display["CUMULATIVE P&L ($)"] = _pnl_col.cumsum().round(6).values

            # Color-code P&L column via Styler
            def _color_pnl_row(val):
                try:
                    v = float(val)
                    return "color: #22c55e; font-weight: 600" if v > 0 else ("color: #ef4444; font-weight: 600" if v < 0 else "")
                except (TypeError, ValueError):
                    return ""

            _log_styler = _log_display.style.applymap(_color_pnl_row, subset=["P&L ($)", "CUMULATIVE P&L ($)"])
            st.dataframe(_log_styler, use_container_width=True, height=400, hide_index=True)

            # Summary line
            _n_trades_log = len(_log_display)
            _wins_log = int((_pnl_col > 0).sum())
            _losses_log = int((_pnl_col < 0).sum())
            _total_pnl_log = float(_pnl_col.sum())
            st.markdown(
                f"**Total: {_n_trades_log} trades | {_wins_log} wins | {_losses_log} losses | "
                f"Net P&L: ${_total_pnl_log:.2f}**"
            )

            # CSV export — all columns (not truncated to display subset)
            _csv_bytes = _filtered.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="⬇️ Export CSV",
                data=_csv_bytes,
                file_name="kalshi_trade_log.csv",
                mime="text/csv",
                use_container_width=False,
            )


def _basic_metrics(pnl: pd.Series) -> dict:
    n = len(pnl)
    return {
        "n_trades": n,
        "total_pnl": float(pnl.sum()),
        "avg_pnl": float(pnl.mean()) if n else 0,
        "win_rate": float((pnl > 0).sum() / n) if n else 0,
        "max_drawdown": float((pnl.cumsum() - pnl.cumsum().cummax()).min()) if n else 0,
    }


_DOLLAR_METRIC_KEYS = {"total_pnl", "avg_pnl", "avg_win", "avg_loss", "max_drawdown",
                       "gross_profit", "gross_loss", "net_pnl"}
_PCT_METRIC_KEYS    = {"win_rate", "loss_rate", "win_pct"}
_INT_METRIC_KEYS    = {"n_trades", "n_wins", "n_losses"}


def _fmt_metric_value(k: str, v) -> str:
    """Format a metric value with appropriate units based on the key name."""
    if v is None:
        return "--"
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return str(v)
    key = k.lower()
    if key in _INT_METRIC_KEYS:
        return f"{int(fv):,}"
    if key in _DOLLAR_METRIC_KEYS:
        return f"${fv:.4f}"
    if key in _PCT_METRIC_KEYS:
        # stored as 0–1 fraction
        return f"{fv * 100:.2f}%"
    # ratios and other dimensionless quantities
    return f"{fv:.4f}"


def _render_metrics_table(metrics: dict):
    from dashboard.styles import PANEL, BORDER, TEXT, TEXT3
    html = f"<div style='background:{PANEL};border:1px solid {BORDER};border-radius:3px;padding:0.5rem 0;'>"
    for k, v in metrics.items():
        if v is None:
            continue
        val_str = _fmt_metric_value(k, v)
        html += f"""
<div style='display:flex;justify-content:space-between;padding:4px 1rem;
border-bottom:1px solid {BORDER};'>
<span style='font-size:0.65rem;letter-spacing:0.06em;text-transform:uppercase;
color:{TEXT3};font-family:Inter,sans-serif;'>{k.replace("_"," ")}</span>
<span style='font-family:JetBrains Mono,monospace;font-size:0.78rem;
color:{TEXT};'>{val_str}</span>
</div>"""
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def _render_strategy_comparison(runs_df: pd.DataFrame):
    """Render a multi-run comparison chart from the runs summary DataFrame."""
    if runs_df.empty or "run_id" not in runs_df.columns:
        st.caption("NO RUNS AVAILABLE FOR COMPARISON")
        return

    # Scatter: n_trades vs total_pnl, colored by win_rate
    if "total_pnl" in runs_df.columns and "n_trades" in runs_df.columns:
        fig = go.Figure()

        pnl_vals  = pd.to_numeric(runs_df["total_pnl"], errors="coerce").fillna(0)
        n_vals    = pd.to_numeric(runs_df["n_trades"],  errors="coerce").fillna(0)
        wr_vals   = pd.to_numeric(runs_df.get("win_rate", pd.Series([0.5]*len(runs_df))),
                                  errors="coerce").fillna(0.5)
        run_ids   = runs_df["run_id"].astype(str)

        marker_colors = [
            f"rgba(34,197,94,{max(0.3, float(w))})" if float(p) >= 0
            else f"rgba(239,68,68,{max(0.3, 1-float(w))})"
            for p, w in zip(pnl_vals, wr_vals)
        ]

        fig.add_trace(go.Scatter(
            x=n_vals,
            y=pnl_vals,
            mode="markers+text",
            marker=dict(size=14, color=marker_colors, line=dict(width=1, color=BORDER)),
            text=run_ids,
            textposition="top center",
            textfont=dict(size=9, color=TEXT3),
        ))
        fig.add_hline(y=0, line_dash="dash", line_color=TEXT3, line_width=0.8)
        fig.update_layout(
            **plotly_dark_layout(
            title={"text": "RUNS: TRADES vs TOTAL P&L (size=win rate)",
                   "font": {"size": 10, "color": TEXT3}},
            height=280,
            xaxis_title="# TRADES",
            yaxis_title="TOTAL P&L ($)",
        ))
        st.plotly_chart(fig, use_container_width=True)

    # Win rate bar for each run
    if "win_rate" in runs_df.columns:
        wr = pd.to_numeric(runs_df["win_rate"], errors="coerce").fillna(0)
        fig2 = go.Figure(go.Bar(
            x=runs_df["run_id"].astype(str),
            y=(wr * 100).round(1),
            marker_color=[GREEN if v >= 50 else RED for v in (wr * 100)],
            marker_line_width=0,
            text=(wr * 100).round(1).astype(str) + "%",
            textposition="outside",
        ))
        fig2.add_hline(y=50, line_dash="dot", line_color=AMBER, line_width=0.8,
                       annotation_text="50%", annotation_font_size=8)
        fig2.update_layout(
            **plotly_dark_layout(
            title={"text": "WIN RATE BY RUN", "font": {"size": 10, "color": TEXT3}},
            height=200, xaxis_title="", yaxis_title="WIN RATE (%)",
        ))
        st.plotly_chart(fig2, use_container_width=True)

    # Summary table — ensure Win Rate shown as formatted percentage
    _runs_display = runs_df.copy()
    if "win_rate" in _runs_display.columns:
        _runs_display["WIN RATE %"] = pd.to_numeric(_runs_display["win_rate"], errors="coerce").apply(
            lambda v: f"{v*100:.1f}%" if pd.notna(v) else "--"
        )
    elif "n_trades" in _runs_display.columns and "n_wins" in _runs_display.columns:
        _nt = pd.to_numeric(_runs_display["n_trades"], errors="coerce")
        _nw = pd.to_numeric(_runs_display["n_wins"], errors="coerce")
        _runs_display["WIN RATE %"] = (_nw / _nt.replace(0, float("nan")) * 100).apply(
            lambda v: f"{v:.1f}%" if pd.notna(v) else "--"
        )
    st.dataframe(_runs_display, use_container_width=True, height=180, hide_index=True)


def _no_data(title: str, msg: str):
    st.caption((f"{title} — {msg}" if msg else title).upper())


