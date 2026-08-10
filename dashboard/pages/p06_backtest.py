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

    st.caption(
        "SIMULATION ONLY — hypothetical P&L from historical snapshots. "
        "No real trades. Past simulated performance does not guarantee future results."
    )

    # --- SQLite mode guard -----
    from dashboard.data_layer import get_system_health as _gh_bt
    _h_bt = _gh_bt()
    _is_sqlite_bt = not _h_bt.get("db_connected", False) and _h_bt.get("db_mode") == "sqlite"
    if _is_sqlite_bt:
        from dashboard.styles import AMBER as _A_BT, PANEL as _P_BT, BORDER as _B_BT, TEXT2 as _T2_BT
        st.markdown(
            f"<div style='background:{_P_BT};border:1px solid {_A_BT};border-left:4px solid {_A_BT};"
            f"padding:0.75rem 1rem;border-radius:3px;margin-bottom:1rem;font-size:0.72rem;"
            f"color:{_T2_BT};font-family:JetBrains Mono,monospace;line-height:1.6;'>"
            f"▶ RUN BACKTEST requires a direct PostgreSQL connection. "
            f"Launch the backtest runner from the Kalshi Arb desktop application.</div>",
            unsafe_allow_html=True,
        )

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
                ["yes_no_complement"],
                help="yes_no_complement — YES+NO must sum to 100¢; deviation = riskless arb",
            )
            init_capital = st.number_input("INITIAL CAPITAL ($)", value=10000, step=1000)
        with pc3:
            pos_size = st.number_input("POSITION SIZE (contracts)", value=10, step=1)
            fee_override = st.number_input(
                "FEE OVERRIDE (cents, 0=auto)", value=0.0, step=0.1, format="%.2f",
                help="Per-contract fee in cents. 0 = use the fee stored in the database.",
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
        st.caption(
            "⚠ Parameter inputs (date range, capital, position size, fees, slippage) are "
            "informational — the backtest engine uses its own configured defaults. "
            "The STRATEGY selector is passed to the engine."
        )

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
                        st.success("Backtest complete. Reload to see results.")
                        if result.stdout.strip():
                            st.code(result.stdout[-2000:], language="text")
                    else:
                        st.error("Backtest failed:")
                        st.code((result.stderr or result.stdout)[-2000:], language="text")
                except subprocess.TimeoutExpired:
                    st.warning("Backtest timed out after 5 minutes.")
                except FileNotFoundError:
                    st.error("Python executable not found. Ensure the backtest environment is configured correctly.")

    st.caption(
        "Simulation uses Kalshi historical OHLC candles — open/high/low/close prices per 1-hour bin. "
        "No bid/ask spread; slippage not modeled."
    )

    # --- Available runs -----
    runs_df, err = get_backtest_runs()

    if err and runs_df.empty:
        st.markdown(
            f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {BLUE};
padding:1.25rem 1.5rem;border-radius:3px;margin-bottom:1rem;'>
<div style='font-size:0.62rem;letter-spacing:0.1em;color:{BLUE};text-transform:uppercase;margin-bottom:0.5rem;'>
NO BACKTEST RUNS YET
</div>
<div style='font-size:0.72rem;color:{TEXT2};line-height:1.8;'>
No backtest runs found. Connect a local PostgreSQL database and run the historical scan pipeline to populate backtest results. Use the RUN BACKTEST button above once the database is configured.
</div>
<div style='font-size:0.65rem;color:{TEXT3};margin-top:0.75rem;'>
Data available: historical arb records from the database. Adjust the date range to load them.
</div>
</div>""",
            unsafe_allow_html=True,
        )

        # Show strategy methodology even without data
        from dashboard.styles import AMBER as _AMBER
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown("#### STRATEGY METHODOLOGY")
        for strat, desc in [
            ("mutually_exclusive",      "Sum of NO asks < N−1 across N exclusive outcomes → buy all NOs; N−1 pay out"),
            ("superset",               "Superset contract must price ≥ subset; violations are arb"),
            ("threshold_order",        "Ordered thresholds enforce monotonicity; violations create spread arb"),
            ("collectively_exhaustive", "Sum of YES asks < 100c → buy all outcomes for guaranteed profit"),
            ("yes_no_complement",      "YES + NO of same contract must sum to 100c; any deviation is riskless two-leg arb"),
        ]:
            st.markdown(
                f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:0.75rem 1rem;
border-radius:3px;margin-bottom:0.5rem;'>
<div style='font-size:0.62rem;letter-spacing:0.1em;color:{AMBER};
text-transform:uppercase;margin-bottom:3px;'>
{strat.replace("_"," ").upper()}
</div>
<div style='font-size:0.72rem;color:{TEXT2};font-family:JetBrains Mono,monospace;'>{desc}</div>
</div>""",
                unsafe_allow_html=True,
            )
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
                xaxis_title="Volume (contracts)",
                yaxis={"autorange": "reversed"},
                margin={"l": 10, "r": 60, "t": 30, "b": 30},
            ))
            st.plotly_chart(fig_v, use_container_width=True)

        return

    tab_runs, tab_compare = st.tabs(["AVAILABLE RUNS", "STRATEGY COMPARISON"])

    with tab_runs:
        if runs_df.empty:
            st.info("No backtest runs found in database.")
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
        help="Enter a run_id from the table above",
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
        st.warning(f"No trades found for run '{run_id}'. Check the run ID and ensure the backtest completed successfully.")
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
        "Metric": ["Total Trades", "Win Rate", "Avg P&L / Trade", "Sharpe Ratio", "Max Drawdown"],
        "Value": [
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
            title={"text": "TRADE P&L DISTRIBUTION (CENTS)", "font": {"size": 10, "color": TEXT3}},
            height=200, xaxis_title="P&L per trade (¢)", yaxis_title="Count",
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
            mode="lines", name="Equity",
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
                text="Max DD",
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
            height=260, xaxis_title="", yaxis_title="Cumulative P&L ($)",
        ))
        st.plotly_chart(fig, use_container_width=True)

    with ch2:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=trades_df.get("entry_ts", trades_df.index),
            y=trades_df["drawdown"],
            mode="lines", name="Drawdown",
            line={"color": RED, "width": 1.5},
            fill="tozeroy", fillcolor="rgba(239,68,68,0.10)",
        ))
        fig2.update_layout(
            **plotly_dark_layout(
            title={"text": "DRAWDOWN", "font": {"size": 10, "color": TEXT3}},
            height=260, xaxis_title="", yaxis_title="Drawdown ($)",
        ))
        st.plotly_chart(fig2, use_container_width=True)

    # --- Rolling Sharpe ratio chart (real trade P&L from DB) -----
    _n_real = len(pnl_series)
    if _n_real < 20:
        st.info(f"Need 20+ logged arbs for rolling Sharpe — {_n_real} logged so far")
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
            name="Rolling Sharpe",
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
            height=180, xaxis_title="", yaxis_title="Rolling Sharpe (20-trade)",
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
            height=240, xaxis_title="P&L ($)", yaxis_title="Count",
        ))
        st.plotly_chart(fig4, use_container_width=True)

    # --- Kelly Criterion (Live Arb Data) -----
    with st.expander("📊 Kelly Criterion — Live Arb Sizing", expanded=False):
        # --- Configurable bankroll input -----
        _bankroll_input = st.number_input(
            "BANKROLL ($)",
            min_value=100,
            max_value=10_000_000,
            value=10_000,
            step=1_000,
            help="Your total trading capital. Kelly position size is computed as a fraction of this amount.",
            key="p06_kelly_bankroll",
        )

        # --- Pull real arb stats from live arbs DB -----
        _live_arb_count = 0
        _live_avg_net_edge_cents = 0.0
        try:
            # Try PostgreSQL live_arbs_cloud first
            from database.repository import get_engine as _ge_kelly
            from sqlalchemy import text as _txt_kelly
            _eng_kelly = _ge_kelly()
            with _eng_kelly.connect() as _conn_kelly:
                _k_row = _conn_kelly.execute(_txt_kelly(
                    "SELECT COUNT(*), AVG(net_edge_cents) FROM live_arbs_cloud "
                    "WHERE net_edge_cents > 0"
                )).fetchone()
                if _k_row and _k_row[0]:
                    _live_arb_count = int(_k_row[0])
                    _live_avg_net_edge_cents = float(_k_row[1] or 0)
        except Exception:
            pass
        # Fallback to live_arbs SQLite
        if _live_arb_count == 0:
            try:
                import sqlite3 as _sq_kelly
                from pathlib import Path as _P_kelly
                _la_db = _P_kelly(__file__).parent.parent / "live_arbs.db"
                if _la_db.exists():
                    _lc = _sq_kelly.connect(str(_la_db), check_same_thread=False)
                    _lk = _lc.execute(
                        "SELECT COUNT(*), AVG(net_edge_cents) FROM live_arbs WHERE net_edge_cents > 0"
                    ).fetchone()
                    _lc.close()
                    if _lk and _lk[0]:
                        _live_arb_count = int(_lk[0])
                        _live_avg_net_edge_cents = float(_lk[1] or 0)
            except Exception:
                pass
        # Final fallback: use backtest trade stats if no live data
        if _live_arb_count == 0 and len(pnl_series) > 0:
            _live_arb_count = int(metrics.get("n_trades", len(pnl_series)))
            _live_avg_net_edge_cents = float(metrics.get("avg_pnl", 0)) * 100

        # --- Display live arb stats -----
        _lk_c1, _lk_c2 = st.columns(2)
        _lk_c1.metric("LIVE ARB RECORDS", f"{_live_arb_count:,}", help="Count of YNC arbs in live_arbs DB with net_edge_cents > 0")
        _lk_c2.metric("AVG NET EDGE", f"{_live_avg_net_edge_cents:.2f}¢", help="Average net edge per arb after Kalshi fees")

        # --- Kelly formula for YNC arbs -----
        _P_CE = 0.95  # assumed win probability for YES/NO complement arbs
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
            _kl_c2.metric("HALF-KELLY", f"{_half_kelly_live * 100:.2f}%", help="Recommended safer bet size")
            _kl_c3.metric(
                "RECOMMENDED POSITION SIZE",
                f"${_rec_pos_size:,.2f}",
                help=f"Half-Kelly × bankroll (${_bankroll_input:,}). Based on avg YNC arb edge {_live_avg_net_edge_cents:.2f}¢.",
            )
            st.progress(min(_kelly_live, 1.0))
            st.caption(
                f"Kelly formula: (p×b − (1−p)) / b · p={_P_CE} (YNC win prob) · "
                f"b={_b_ratio:.4f} (net_edge/cost_to_enter) · "
                f"net_edge={_live_avg_net_edge_cents:.2f}¢ · "
                f"cost_to_enter={_cost_to_enter*100:.2f}¢. "
                f"Half-Kelly = ${_rec_pos_size:,.2f} per arb on a ${_bankroll_input:,} bankroll."
            )
            if _kelly_live > 0.25:
                st.warning(f"High Kelly fraction ({_kelly_live:.0%}) — use half-Kelly or less to control ruin risk")
            # Position sizing table for common bankrolls
            _kelly_br_rows = []
            for _br in [1_000, 5_000, _bankroll_input, 50_000, 100_000]:
                _kelly_br_rows.append({
                    "Bankroll": f"${_br:,}",
                    "Full Kelly $": f"${_br * _kelly_live:,.2f}",
                    "Half Kelly $": f"${_br * _half_kelly_live:,.2f}",
                    "Quarter Kelly $": f"${_br * _kelly_live * 0.25:,.2f}",
                })
            # Deduplicate rows by bankroll label
            _seen_br = set()
            _dedup_rows = []
            for _r in _kelly_br_rows:
                if _r["Bankroll"] not in _seen_br:
                    _seen_br.add(_r["Bankroll"])
                    _dedup_rows.append(_r)
            st.dataframe(pd.DataFrame(_dedup_rows), use_container_width=True, hide_index=True)
            st.caption("p=0.95 assumed win probability for YNC arbs. Adjust bankroll input above to update position sizes.")
        else:
            st.info("Insufficient live arb data to compute Kelly fraction. Ensure the live arb scanner is running and has logged arbs.")

    # --- Kelly Criterion (Backtest) -----
    with st.expander("📊 Kelly Criterion — Backtest History", expanded=False):
        try:
            _wr = float(metrics.get("win_rate", 0))
            _wins = pnl_series[pnl_series > 0]
            _losses = pnl_series[pnl_series < 0]
            _avg_win_k = float(_wins.mean()) if len(_wins) > 0 else 0.0
            _avg_loss_k = float(_losses.abs().mean()) if len(_losses) > 0 else 0.0
            if _wr < 0.5 or _avg_win_k <= 0:
                st.error("Kelly undefined or negative — strategy has negative edge")
            elif _wr == 0 or _avg_win_k == 0:
                st.info("Insufficient data — need at least one winning and one losing trade to compute Kelly fraction.")
            else:
                _kelly = (_wr * _avg_win_k - (1 - _wr) * _avg_loss_k) / _avg_win_k
                _kelly = min(_kelly, 1.0)  # cap at 100%
                _half_kelly = _kelly * 0.5
                _kc1, _kc2 = st.columns(2)
                _kc1.metric("KELLY FRACTION", f"{_kelly * 100:.1f}%")
                _kc2.metric("HALF-KELLY", f"{_half_kelly * 100:.1f}%")
                # Visual gauge: Kelly fraction vs full Kelly
                st.progress(min(_kelly, 1.0))
                st.caption(f"Recommended bet size: {_half_kelly * 100:.1f}% of bankroll (half-Kelly)")
                if _kelly > 0.25:
                    st.warning(f"⚠️ High Kelly fraction (>{_kelly:.0%}) — consider sizing down to avoid ruin risk")
                # Bankroll sizing table
                _bankrolls = [1_000, 5_000, 10_000, 50_000, 100_000]
                _br_rows = []
                for _br in _bankrolls:
                    _br_rows.append({
                        "Bankroll": f"${_br:,}",
                        "Full Kelly $": f"${_br * _kelly:,.2f}",
                        "Half Kelly $": f"${_br * _half_kelly:,.2f}",
                        "Quarter Kelly $": f"${_br * _kelly * 0.25:,.2f}",
                    })
                st.dataframe(pd.DataFrame(_br_rows), use_container_width=True, hide_index=True)
                st.caption("Kelly fraction = optimal bet size as % of bankroll. Half-Kelly is safer in practice.")
        except Exception as _ke:
            st.caption(f"Kelly Criterion unavailable: {_ke}")

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
                st.warning("❌ Negative Kelly — do not trade this strategy")
            elif _kelly_ps < 0.05:
                st.info("📊 Small edge: consider fixed-size trades (e.g. 5 contracts per arb)")
            elif _kelly_ps <= 0.25:
                st.info(f"✅ Standard position: {_kelly_ps*100:.0f}% of bankroll per trade")
            else:
                st.warning(f"⚠️ Kelly suggests >25% of bankroll — use half-Kelly (cap at 10%)")
        else:
            st.info("Insufficient trade data to compute position sizing recommendation.")
    except Exception as _ps_err:
        st.caption(f"Position sizing unavailable: {_ps_err}")

    # --- Monte Carlo Simulation -----
    with st.expander("🎲 Monte Carlo Simulation", expanded=False):
        try:
            if len(pnl_series) < 5:
                st.info("Fewer than 5 trades — Monte Carlo simulation skipped.")
            else:
                _pnl_arr = pnl_series.values
                _n = len(_pnl_arr)
                n_sims = st.slider(
                    "Simulation paths",
                    min_value=100, max_value=2000, value=500, step=100,
                    key="p06_mc_n_sims",
                    help="Number of bootstrap paths to simulate. More paths = smoother fan chart, slower render.",
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
                _mc_m3.metric("P(Positive Final P&L)", f"{_p_pos*100:.1f}%")

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
                    name="5th–95th pct band",
                    showlegend=True,
                    hoverinfo="skip",
                ))
                # 5th percentile lower bound
                _fig_mc.add_trace(go.Scatter(
                    x=_trade_nums,
                    y=low_path,
                    mode="lines",
                    name="5th pct",
                    line={"color": RED, "width": 1.2, "dash": "dot"},
                ))
                # 95th percentile upper bound
                _fig_mc.add_trace(go.Scatter(
                    x=_trade_nums,
                    y=high_path,
                    mode="lines",
                    name="95th pct",
                    line={"color": GREEN, "width": 1.2, "dash": "dot"},
                ))
                # Median path
                _fig_mc.add_trace(go.Scatter(
                    x=_trade_nums,
                    y=median_path,
                    mode="lines",
                    name="Median (50th pct)",
                    line={"color": BLUE, "width": 2.0},
                ))
                _fig_mc.add_hline(y=0, line_dash="dash", line_color=TEXT3, line_width=0.8)
                _fig_mc.update_layout(
                    **plotly_dark_layout(
                    title={"text": "Monte Carlo Simulation (1000 paths, 5th/95th confidence band)",
                           "font": {"size": 10, "color": TEXT3}},
                    height=300,
                    xaxis_title="Trade number",
                    yaxis_title="Cumulative P&L ($)",
                    legend={"x": 0, "y": 1, "bgcolor": "rgba(0,0,0,0)"},
                    margin={"l": 55, "r": 20, "t": 35, "b": 40},
                ))
                st.plotly_chart(_fig_mc, use_container_width=True)
                st.caption(f"Bootstrap simulation ({n_sims} iterations) testing sensitivity to trade order. "
                           "Blue = median path; shaded band = 5th–95th percentile range.")

                # Ruin probability metric
                st.metric(
                    "RUIN PROBABILITY",
                    f"{_p_ruin*100:.1f}%",
                    help=f"Fraction of {n_sims} paths whose final P&L puts bankroll below "
                         f"50% of the initial ${_init_bk:,.0f} capital "
                         f"(i.e. cumulative P&L < ${_ruin_threshold:,.2f}).",
                )
                if _p_ruin > 0.10:
                    st.warning(f"Ruin probability ({_p_ruin:.0%}) exceeds 10% — consider reducing position size.")
        except Exception as _mc_err:
            st.caption(f"Monte Carlo simulation unavailable: {_mc_err}")

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
            height=220, xaxis_title="", yaxis_title="Total P&L ($)",
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
                "STRATEGY": str(_strat_name).replace("_", " ").upper(),
                "TRADES": _n,
                "WIN RATE": f"{_wr * 100:.1f}%",
                "AVG P&L (c)": f"{_avg * 100:.2f}",
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
                st.success(f"Best by Sharpe: {_best_name} ({_best_sharpe:.2f})")

    # --- Interactive filter controls -----
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("#### TRADE LOG FILTERS")
    _fc1, _fc2, _fc3 = st.columns(3)
    with _fc1:
        fee_mult = st.slider(
            "Fee multiplier",
            min_value=0.5, max_value=2.0, value=1.0, step=0.1,
            key="p06_fee_mult",
        )
        st.caption("1.0 = standard Kalshi fees. >1.0 = stress test with higher fees.")
    with _fc2:
        min_edge_cents = st.slider(
            "Min net edge (¢)",
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
            "Strategies to include",
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
    st.caption("Simulated trades only — these are hypothetical entries generated by replaying historical order-book data. No real orders were placed.")
    if _filtered.empty:
        st.info("No trades match the current filter settings.")
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
                st.caption("Trade duration data not available in this dataset")
        else:
            st.caption("Trade duration data not available in this dataset")
    except Exception:
        st.caption("Trade duration data not available in this dataset")

    # --- Full Trade Log Export -----
    with st.expander("📋 Full Trade Log", expanded=False):
        if _filtered.empty:
            st.info("No trades match the current filter settings.")
        else:
            _log = _filtered.copy()

            # Build display columns
            _log_display = pd.DataFrame()
            _log_display["#"] = range(1, len(_log) + 1)

            # Date (ET)
            if "entry_ts" in _log.columns:
                _ts_col = pd.to_datetime(_log["entry_ts"], utc=True, errors="coerce")
                try:
                    _log_display["Date (ET)"] = _ts_col.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    _log_display["Date (ET)"] = _ts_col.dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                _log_display["Date (ET)"] = "--"

            _log_display["Ticker"] = _log.get("ticker", pd.Series(["--"] * len(_log))).fillna("--").values
            _log_display["Strategy"] = _log.get("strategy_type", pd.Series(["--"] * len(_log))).fillna("--").values

            _pnl_col = pd.to_numeric(_log.get("net_pnl", pd.Series([0.0] * len(_log))), errors="coerce").fillna(0)
            _gross_col = pd.to_numeric(_log.get("gross_pnl", _pnl_col), errors="coerce").fillna(0)

            _log_display["Gross (¢)"] = (_gross_col * 100).round(2).values
            _log_display["Net (¢)"] = (_pnl_col * 100).round(2).values
            _log_display["P&L ($)"] = _pnl_col.round(6).values
            _log_display["Classification"] = _log.get("classification", pd.Series(["--"] * len(_log))).fillna("--").values

            # Cumulative P&L column
            _log_display["Cumulative P&L ($)"] = _pnl_col.cumsum().round(6).values

            # Color-code P&L column via Styler
            def _color_pnl_row(val):
                try:
                    v = float(val)
                    return "color: #22c55e; font-weight: 600" if v > 0 else ("color: #ef4444; font-weight: 600" if v < 0 else "")
                except (TypeError, ValueError):
                    return ""

            _log_styler = _log_display.style.applymap(_color_pnl_row, subset=["P&L ($)", "Cumulative P&L ($)"])
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
                label="⬇️ Export Trade Log (all columns)",
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
        st.info("No runs available for comparison.")
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
            xaxis_title="# Trades",
            yaxis_title="Total P&L ($)",
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
            height=200, xaxis_title="", yaxis_title="Win Rate (%)",
        ))
        st.plotly_chart(fig2, use_container_width=True)

    # Summary table — ensure Win Rate shown as formatted percentage
    _runs_display = runs_df.copy()
    if "win_rate" in _runs_display.columns:
        _runs_display["Win Rate %"] = pd.to_numeric(_runs_display["win_rate"], errors="coerce").apply(
            lambda v: f"{v*100:.1f}%" if pd.notna(v) else "--"
        )
    elif "n_trades" in _runs_display.columns and "n_wins" in _runs_display.columns:
        _nt = pd.to_numeric(_runs_display["n_trades"], errors="coerce")
        _nw = pd.to_numeric(_runs_display["n_wins"], errors="coerce")
        _runs_display["Win Rate %"] = (_nw / _nt.replace(0, float("nan")) * 100).apply(
            lambda v: f"{v:.1f}%" if pd.notna(v) else "--"
        )
    st.dataframe(_runs_display, use_container_width=True, height=180, hide_index=True)


def _no_data(title: str, msg: str):
    from dashboard.styles import PANEL, BORDER, TEXT3
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};
padding:2rem;border-radius:3px;text-align:center;'>
<div style='font-family:JetBrains Mono,monospace;font-size:0.85rem;color:{TEXT3};'>
{title}
</div>
<div style='font-size:0.72rem;color:{TEXT3};margin-top:0.5rem;'>{msg}</div>
</div>""",
        unsafe_allow_html=True,
    )

