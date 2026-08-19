"""
dashboard/pages/p04_orderbook.py — Order Book L2

Sections:
1. Ticker selector + live market list
2. Market header (source, L2 flag)
3. Top-of-book KPI metrics
4. L2 depth table + cumulative depth chart
5. Liquidity metrics (depth, imbalance, VWAP, slippage)
6. Sequence gap / stale-book panel (from SequenceGapMonitor)
7. Age / staleness indicator + auto-refresh
"""
from __future__ import annotations
from typing import List
import math
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data_layer import get_live_orderbook, get_open_markets, get_system_health, get_market_price_history
from dashboard.live_state import get_live_state
from dashboard.styles import plotly_dark_layout, GREEN, RED, AMBER, BLUE, TEXT, TEXT2, TEXT3, PANEL, BORDER, PANEL2


def render():
    st.markdown("""
<div style='margin-bottom:0.5rem;'>
<span style='font-size:1rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;'>
ORDER BOOK
</span>
</div>
""", unsafe_allow_html=True)
    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    # --- Feed status -----
    _health = get_system_health()
    _is_sqlite = not _health.get("db_connected", False) and _health.get("db_mode") == "sqlite"
    from dashboard.live_state import get_live_state as _gls
    _ws = _gls().get_stats()
    _ws_on = _ws.get("connected", False)
    import os as _os_p04
    _sk_p04 = _os_p04.environ.get("SYNTHESIS_SECRET_KEY", "").strip()
    if not _sk_p04:
        try:
            _sk_p04 = (st.secrets.get("SYNTHESIS_SECRET_KEY", "") or "").strip()
        except Exception:
            pass
    if not _sk_p04:
        try:
            for _ns_p04 in st.secrets.values():
                if hasattr(_ns_p04, "get"):
                    _sk_p04 = (_ns_p04.get("SYNTHESIS_SECRET_KEY", "") or "").strip()
                    if _sk_p04:
                        break
        except Exception:
            pass
    _has_key_p04 = bool(_sk_p04)
    # Check Neon state for feed label
    _p04_neon_ok = False
    try:
        import dashboard.live_arb_store as _las_p04_feed
        _p04_neon_ok = (
            bool(getattr(_las_p04_feed, "_pg_ok", False))
            or (getattr(_las_p04_feed, "_pg_engine", None) is not None)
        )
        if not _p04_neon_ok:
            _p04_neon_ok = _las_p04_feed.get_pg_engine_cached() is not None
    except Exception:
        pass
    if not _p04_neon_ok:
        _p04_neon_ok = bool(st.session_state.get("_sidebar_neon_ok", False))
    _ws_dot = GREEN if _ws_on else (GREEN if _p04_neon_ok else (AMBER if _has_key_p04 else "#6B7280"))
    if _ws_on:
        _ws_txt = f"{_ws.get('markets_tracked', 0):,} markets · {_ws.get('messages_per_sec', 0):.0f} msg/s"
    elif _p04_neon_ok:
        _ws_txt = "CLOUD — historical arb data from Neon · L2 quotes available once WebSocket connects"
    elif _has_key_p04:
        _ws_txt = "↻ CONNECTING — L2 data available once WebSocket connects"
    else:
        _ws_txt = "feed offline — add SYNTHESIS_SECRET_KEY to Streamlit secrets"
    st.markdown(
        f"<div style='font-size:0.62rem;color:{TEXT3};font-family:JetBrains Mono,monospace;margin-bottom:0.5rem;'>"
        f"<span style='color:{_ws_dot};'>●</span> {_ws_txt}</div>",
        unsafe_allow_html=True,
    )
    # When feed is offline, suggest tickers from Neon arb history
    if not _ws_on:
        try:
            import dashboard.live_arb_store as _las_p04
            from sqlalchemy import text as _p04_text
            _p04_eng = getattr(_las_p04, "_pg_engine", None) or _las_p04.get_pg_engine_cached()
            if _p04_eng is not None:
                with _p04_eng.connect() as _p04_c:
                    _p04_rows = _p04_c.execute(_p04_text(
                        "SELECT ticker, MAX(detected_at) AS last_seen, "
                        "MAX(net_edge_cents) AS best_edge "
                        "FROM live_arbs_cloud "
                        "WHERE strategy_type IN ('mutually_exclusive','threshold_order') "
                        "GROUP BY ticker ORDER BY last_seen DESC LIMIT 10"
                    )).fetchall()
                if _p04_rows:
                    _neon_ticker_opts = [r[0] for r in _p04_rows]
                    st.info(
                        "💡 **Neon arb history** — these tickers had detected arbs: "
                        + ", ".join(f"`{t}`" for t in _neon_ticker_opts[:5])
                        + (f" + {len(_neon_ticker_opts)-5} more" if len(_neon_ticker_opts) > 5 else "")
                        + ". Enter one above to preload the ticker."
                    )
        except Exception:
            pass

    # --- Market selector -----
    col_sel, col_src = st.columns([4, 1])
    with col_sel:
        ticker = st.text_input(
            "TICKER",
            placeholder="e.g. KXMLBALEAST-26-TB",
            help="Enter any Kalshi market ticker",
        )
    with col_src:
        st.markdown("<br>", unsafe_allow_html=True)
        auto_refresh = st.checkbox("AUTO REFRESH", value=True)

    # --- Compare market selector -----
    _all_tickers: list = []
    try:
        _snap_tickers = get_live_state().snapshot_all()
        # Default to markets that have live quotes (both bid and ask present)
        _live_quoted = sorted([
            t for t, q in _snap_tickers.items()
            if (q.yes_bid or 0) > 0 and (q.yes_ask or 0) > 0
        ])
        _all_tickers = _live_quoted if _live_quoted else sorted(_snap_tickers.keys())
    except Exception:
        pass
    compare_ticker = st.selectbox(
        "Compare with market (optional)",
        ["None"] + _all_tickers,
        key="compare_ticker",
    )
    normalize = st.checkbox(
        "NORMALIZE (show prices as % of their max — easier cross-market depth comparison)",
        value=False,
        key="p04_normalize",
    )

    if not ticker:
        # Show live markets from WebSocket cache
        state = get_live_state()
        _total_tracked = state.market_count()
        live_books = state.top_by_volume(20)
        if live_books:
            _k1, _k2, _k3, _k4 = st.columns(4)
            _k1.metric("MARKETS TRACKED", f"{_total_tracked:,}")
            _ws2 = state.get_stats()
            _k2.metric("MSG/SEC", f"{_ws2.get('messages_per_sec', 0):.1f}")
            _k3.metric("TOTAL MESSAGES", f"{_ws2.get('messages_total', 0):,}")
            _sess_p04 = state.get_session_stats()
            _k4.metric("SESSION ARBS", f"{_sess_p04['total']:,}", help="Total scanner detections this session (ME/TH arbs + YNC feed artifacts). YNC detections are not executable.")
            st.markdown(f"<div style='font-size:0.65rem;color:{TEXT3};letter-spacing:0.06em;text-transform:uppercase;margin:0.5rem 0 0.3rem;'>TOP 20 MARKETS — tightest spreads</div>", unsafe_allow_html=True)
            live_data = [{
                "TICKER": b.ticker,
                "BID": f"{b.yes_bid*100:.0f}c",
                "ASK": f"{b.yes_ask*100:.0f}c",
                "SPREAD": f"{b.spread*100:.0f}c",
                "MID": f"{b.mid*100:.0f}c",
                "L2": "Y" if b.l2_available else "--",
                "AGE": (
                    f"{int(b.age_seconds//3600)}h{int((b.age_seconds%3600)//60)}m"
                    if b.age_seconds >= 3600
                    else f"{int(b.age_seconds//60)}m{int(b.age_seconds%60)}s"
                    if b.age_seconds >= 60
                    else f"{int(b.age_seconds)}s"
                ),
            } for b in live_books]
            st.dataframe(pd.DataFrame(live_data), use_container_width=True, height=350, hide_index=True)
        else:
            st.markdown(
                f"<div style='font-size:0.72rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
                f"padding:0.5rem 0;'>Enter a ticker above to view its L2 depth chart.</div>",
                unsafe_allow_html=True,
            )
        return

    # --- Fetch book -----
    book, err = get_live_orderbook(ticker)

    if err or not book:
        # No live book — still try to show historical candlestick data
        _err_msg = "not in WebSocket feed" if not err else (
            "feed not connected" if any(kw in str(err).lower() for kw in ("connect", "refused", "timeout", "timed"))
            else "book unavailable"
        )
        st.markdown(
            f"""<div style='background:{PANEL};border:1px solid {AMBER};border-left:4px solid {AMBER};
padding:0.6rem 1rem;border-radius:3px;margin-bottom:0.75rem;font-size:0.72rem;
color:{TEXT2};font-family:JetBrains Mono,monospace;'>
NO LIVE BOOK for {ticker} &mdash; {_err_msg}.<br>
Showing historical candlestick data where available.
</div>""",
            unsafe_allow_html=True,
        )
        st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)
        st.markdown("#### HISTORICAL PRICE CONTEXT (candlestick data)")
        _interval_no_book = st.radio(
            "INTERVAL", [15, 60, 1440],
            format_func=lambda v: {15: "15m", 60: "1h", 1440: "1d"}[v],
            horizontal=True, key="p04_interval_no_book",
        )
        _hist_df2, _hist_err2 = get_market_price_history(ticker, interval=_interval_no_book, limit=300)
        if not _hist_df2.empty:
            import plotly.graph_objects as _go2
            _ts2 = pd.to_datetime(_hist_df2["period_end_ts"], utc=True, errors="coerce")
            _close2 = pd.to_numeric(_hist_df2["price_close"], errors="coerce")
            _vol2 = pd.to_numeric(_hist_df2["volume"], errors="coerce").fillna(0)
            fig_nb = _go2.Figure()
            fig_nb.add_trace(_go2.Scatter(x=_ts2, y=_close2, name="TRADE PRICE",
                line=dict(color=GREEN, width=1.5)))
            if _vol2.sum() > 0:
                fig_nb.add_trace(_go2.Bar(x=_ts2, y=_vol2, name="VOLUME",
                    marker_color=BLUE, opacity=0.25, yaxis="y2"))
            fig_nb.update_layout(**plotly_dark_layout(
                title={"text": f"{ticker} — candlestick history", "font": {"size": 10, "color": TEXT3}},
                height=300, xaxis_title="", yaxis_title="YES Price",
                yaxis=dict(range=[0, 1], tickformat=".2f"),
                yaxis2=dict(title="Vol", overlaying="y", side="right", showgrid=False),
                legend=dict(orientation="h", y=1.12),
                margin={"l": 50, "r": 60, "t": 40, "b": 20},
            ))
            st.plotly_chart(fig_nb, use_container_width=True)
            st.caption(
                f"{len(_hist_df2):,} candles · "
                f"{_ts2.min().strftime('%Y-%m-%d') if pd.notna(_ts2.min()) else '?'} – "
                f"{_ts2.max().strftime('%Y-%m-%d') if pd.notna(_ts2.max()) else '?'}"
            )
        else:
            st.info(f"No candlestick data for {ticker} at this interval. Try 1h or 1d.")
        return

    l2_available = book.get("l2_available", False)
    source       = book.get("source", "unknown")

    # --- Market header -----
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:0.75rem 1.25rem;
border-radius:3px;margin-bottom:0.75rem;display:flex;justify-content:space-between;
align-items:center;'>
<div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.95rem;color:{TEXT};'>
{ticker}
</div>
<div style='font-size:0.65rem;color:{TEXT3};letter-spacing:0.04em;margin-top:2px;'>
{book.get("title", "")}
</div>
</div>
<div style='text-align:right;'>
<div style='font-size:0.6rem;letter-spacing:0.08em;color:{TEXT3};text-transform:uppercase;'>
SOURCE
</div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;
color:{"#22C55E" if "synthesis" in source else AMBER};'>
{source.upper()}
</div>
</div>
</div>""",
        unsafe_allow_html=True,
    )

    if not l2_available:
        st.markdown(
            f"""<div style='background:{PANEL2};border:1px dashed {AMBER};
padding:0.5rem 1rem;border-radius:3px;margin-bottom:0.75rem;'>
<span style='font-family:JetBrains Mono,monospace;font-size:0.7rem;
color:{AMBER};letter-spacing:0.06em;'>
L2 DEPTH: PENDING &mdash; displaying top-of-book only.
Full L2 reconstruction will be available once the Synthesis feed
upgrade is complete.
</span>
</div>""",
            unsafe_allow_html=True,
        )

    # -------------------------------------------------------------- Top-of-book KPIs --------------------------------------------------------------
    yes_bid = float(book.get("yes_bid") or 0)
    yes_ask = float(book.get("yes_ask") or 0)
    spread  = yes_ask - yes_bid if yes_ask > 0 else 0.0
    mid     = (yes_bid + yes_ask) / 2 if (yes_bid + yes_ask) > 0 else 0.0

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("YES BID",  f"{yes_bid*100:.0f}c")
    k2.metric("YES ASK",  f"{yes_ask*100:.0f}c")
    k3.metric("MID",      f"{mid*100:.1f}c")
    k4.metric("SPREAD",   f"{spread*100:.0f}c")

    # NO side: use actual market values from book when available, else theoretical.
    # Use explicit None-check — 0.0 is a valid (falsy) price and must not fall through.
    _raw_no_bid = book.get("no_bid")
    _raw_no_ask = book.get("no_ask")
    no_bid = float(_raw_no_bid) if _raw_no_bid is not None else (1.0 - yes_ask)
    no_ask = float(_raw_no_ask) if _raw_no_ask is not None else (1.0 - yes_bid)
    k5.metric("NO BID/ASK", f"{no_bid*100:.0f}c / {no_ask*100:.0f}c")

    # --- NEW: Spread / Liquidity / Imbalance metrics row -----
    _s1, _s2, _s3, _s4, _s5 = st.columns(5)

    # YES spread
    try:
        _yes_spread_c = (yes_ask - yes_bid) * 100 if yes_ask > 0 and yes_bid > 0 else None
        _s1.metric("YES SPREAD", f"{_yes_spread_c:.0f}¢" if _yes_spread_c is not None else "--")
    except Exception:
        _s1.metric("YES SPREAD", "--")

    # NO spread
    try:
        _no_spread_c = (no_ask - no_bid) * 100 if no_ask > 0 and no_bid > 0 else None
        _s2.metric("NO SPREAD", f"{_no_spread_c:.0f}¢" if _no_spread_c is not None else "--")
    except Exception:
        _s2.metric("NO SPREAD", "--")

    # YES / NO liquidity
    try:
        _raw_yes_bids = book.get("yes_bids") or []
        _raw_yes_asks = book.get("yes_asks") or []
        _yes_bid_qty = sum(row[1] for row in _raw_yes_bids if len(row) > 1 and row[1])
        _yes_ask_qty = sum(row[1] for row in _raw_yes_asks if len(row) > 1 and row[1])
        _yes_liq = _yes_bid_qty + _yes_ask_qty
        _s3.metric("YES LIQUIDITY", f"{_yes_liq/1000:.1f}k contracts" if _yes_liq >= 1000 else f"{int(_yes_liq)} contracts")
    except Exception:
        _s3.metric("YES LIQUIDITY", "--")

    try:
        # NO side: YES bids are implicitly the NO ask side and vice-versa;
        # use the same raw lists as a proxy (Kalshi YES/NO are complement).
        _no_liq = _yes_bid_qty + _yes_ask_qty  # same pool, complementary contract
        _s4.metric("NO LIQUIDITY", f"{_no_liq/1000:.1f}k contracts" if _no_liq >= 1000 else f"{int(_no_liq)} contracts")
    except Exception:
        _s4.metric("NO LIQUIDITY", "--")

    # Book imbalance
    _imb_pct: float | None = None
    try:
        _tot_bid = sum(row[1] for row in _raw_yes_bids if len(row) > 1 and row[1])
        _tot_ask = sum(row[1] for row in _raw_yes_asks if len(row) > 1 and row[1])
        _denom = _tot_bid + _tot_ask
        if _denom > 0:
            _imb = (_tot_bid - _tot_ask) / _denom
            _imb_pct = (_imb + 1) / 2 * 100  # rescale to 0-100: 100=all bid, 0=all ask
            _imb_str = f"{_imb*100:+.1f}%"
        else:
            _imb_str = "--"
        _s5.metric("BOOK IMBALANCE", _imb_str,
                   help="(bid qty − ask qty) / total qty. Positive = bid pressure (bullish). Negative = ask pressure (bearish).")
    except Exception:
        _s5.metric("BOOK IMBALANCE", "--")

    # --- Order Flow Imbalance Trend (rolling 20-point history) -----
    _imb_hist_key = f"imbalance_history_{ticker}"
    if _imb_hist_key not in st.session_state:
        st.session_state[_imb_hist_key] = []
    if _imb_pct is not None:
        st.session_state[_imb_hist_key].append(_imb_pct)
        if len(st.session_state[_imb_hist_key]) > 20:
            st.session_state[_imb_hist_key] = st.session_state[_imb_hist_key][-20:]
    _imb_hist = st.session_state[_imb_hist_key]
    if len(_imb_hist) >= 2:
        st.markdown(
            f"<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.08em;text-transform:uppercase;"
            f"font-family:Inter,sans-serif;margin-top:0.35rem;margin-bottom:2px;'>"
            f"ORDER FLOW IMBALANCE TREND (last {len(_imb_hist)} readings, 0=ask-heavy · 100=bid-heavy)</div>",
            unsafe_allow_html=True,
        )
        st.line_chart({"imbalance (0-100)": _imb_hist}, height=80, use_container_width=True)
    if _imb_pct is not None:
        if _imb_pct > 60:
            _imb_label, _imb_color = "BID HEAVY", GREEN
        elif _imb_pct < 40:
            _imb_label, _imb_color = "ASK HEAVY", RED
        else:
            _imb_label, _imb_color = "BALANCED", AMBER
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.82rem;"
            f"font-weight:700;letter-spacing:0.1em;color:{_imb_color};"
            f"margin:0.3rem 0 0.5rem;padding:0.35rem 0.75rem;"
            f"background:{PANEL};border-left:3px solid {_imb_color};border-radius:2px;'>"
            f"ORDER FLOW: {_imb_label}"
            f"<span style='font-size:0.65rem;font-weight:400;color:{TEXT3};margin-left:0.75rem;'>"
            f"imbalance index {_imb_pct:.1f}/100</span></div>",
            unsafe_allow_html=True,
        )

    # --- Spread history sparkline (session_state rolling buffer) -----
    _spread_hist_key = f"spread_history_{ticker}"
    if _spread_hist_key not in st.session_state:
        st.session_state[_spread_hist_key] = []
    if spread > 0:
        st.session_state[_spread_hist_key].append(round(spread * 100, 2))
        if len(st.session_state[_spread_hist_key]) > 50:
            st.session_state[_spread_hist_key] = st.session_state[_spread_hist_key][-50:]
    _spread_hist = st.session_state[_spread_hist_key]
    if len(_spread_hist) >= 2:
        st.markdown(
            f"<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.08em;text-transform:uppercase;"
            f"font-family:Inter,sans-serif;margin-top:0.45rem;margin-bottom:2px;'>"
            f"BID-ASK SPREAD HISTORY (last {len(_spread_hist)} WS updates, ¢)</div>",
            unsafe_allow_html=True,
        )
        st.line_chart({"spread (¢)": _spread_hist}, height=70, use_container_width=True)

    # --- Track YES mid price history for correlation -----
    _mid_hist_key = f"mid_history_{ticker}"
    if _mid_hist_key not in st.session_state:
        st.session_state[_mid_hist_key] = []
    if mid > 0:
        st.session_state[_mid_hist_key].append(mid)
        if len(st.session_state[_mid_hist_key]) > 100:
            st.session_state[_mid_hist_key] = st.session_state[_mid_hist_key][-100:]

    # --- Estimated Slippage for 10-contract trade -----
    try:
        _slip_asks = sorted(_raw_yes_asks, key=lambda x: x[0]) if _raw_yes_asks else []
        _slip_best_ask = float(_slip_asks[0][0]) if _slip_asks else None
        if _slip_best_ask is not None:
            _slip_filled = 0
            _slip_cost = 0.0
            for _sp, _ss in _slip_asks:
                if _slip_filled >= 10 or _ss is None:
                    continue
                _take = min(int(_ss), 10 - _slip_filled)
                _slip_cost += _take * float(_sp)
                _slip_filled += _take
            if _slip_filled >= 10:
                _slip_avg = _slip_cost / _slip_filled
                _slip_impact_pct = (_slip_avg - _slip_best_ask) / _slip_best_ask * 100 if _slip_best_ask > 0 else 0.0
                st.metric(
                    "EST. SLIPPAGE (10 contracts)",
                    f"{_slip_impact_pct:.3f}%",
                    help=f"Price impact of a 10-contract buy vs best ask {_slip_best_ask*100:.1f}c. VWAP: {_slip_avg*100:.2f}c",
                )
            else:
                st.metric("EST. SLIPPAGE (10 contracts)", "N/A — insufficient depth")
    except Exception:
        pass

    # Complement arb indicator: if YES ask + actual NO ask < 100c, flag it
    _comp_cost = yes_ask + no_ask
    _comp_spread = _comp_cost - 1.0  # negative = arb exists
    _comp_label = f"{_comp_spread*100:+.1f}c" if yes_ask > 0 and no_ask > 0 else "--"
    k6.metric(
        "COMPLEMENT SPREAD",
        _comp_label,
        help="(YES ask + NO ask) − 100c. Negative means quotes sum below parity — likely a rounding/feed artifact (Kalshi YES/NO are always complements; live YNC sum ≥ $1.00 always).",
        delta="QUOTES BELOW PARITY" if _comp_spread < 0 else None,
        delta_color="normal" if _comp_spread < 0 else "off",
    )
    # --- Complement spread progress bar ("distance to arb") -----
    if yes_ask > 0 and no_ask > 0:
        _pct = _comp_cost * 100  # e.g. 98.5 means combined cost is 98.5c
        _bar_color = "#22C55E" if _pct >= 97 else (AMBER if _pct >= 94 else TEXT3)
        _bar_width = min(100.0, max(0.0, _pct))
        if _pct < 100.0:
            _bar_label = f"{_pct:.1f}¢ — {100.0 - _pct:.1f}¢ below parity (feed artifact)"
        else:
            _bar_label = f"{_pct:.1f}¢ — {_pct - 100.0:.1f}¢ above parity"
        _toward_arb_pct = min(100.0, max(0.0, _pct))
        _toward_arb_label = f"{_toward_arb_pct:.1f}% of parity"
        st.markdown(
            f"""<div style='margin:0.4rem 0 0.5rem 0;'>
<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.08em;text-transform:uppercase;
font-family:Inter,sans-serif;margin-bottom:3px;'>YES ask + NO ask (distance to parity)</div>
<div style='background:{BORDER};border-radius:2px;height:6px;width:100%;position:relative;overflow:hidden;'>
<div style='background:{_bar_color};height:6px;width:{_bar_width:.1f}%;border-radius:2px;'></div>
</div>
<div style='display:flex;justify-content:space-between;margin-top:2px;'>
<div style='font-size:0.58rem;color:{TEXT3};font-family:JetBrains Mono,monospace;'>0¢</div>
<div style='font-size:0.6rem;color:{_bar_color};font-family:JetBrains Mono,monospace;font-weight:600;'>{_bar_label} &nbsp;&middot;&nbsp; {_toward_arb_label}</div>
<div style='font-size:0.58rem;color:{TEXT3};font-family:JetBrains Mono,monospace;'>100¢</div>
</div>
</div>""",
            unsafe_allow_html=True,
        )

    # --- Multi-market comparison -----
    if compare_ticker and compare_ticker != "None":
        _cmp_book, _cmp_err = get_live_orderbook(compare_ticker)
        if _cmp_book and not _cmp_err:
            _cmp_yes_bid = float(_cmp_book.get("yes_bid") or 0)
            _cmp_yes_ask = float(_cmp_book.get("yes_ask") or 0)
            _cmp_mid     = (_cmp_yes_bid + _cmp_yes_ask) / 2 if (_cmp_yes_bid + _cmp_yes_ask) > 0 else 0.0
            _cmp_spread  = _cmp_yes_ask - _cmp_yes_bid if _cmp_yes_ask > 0 else 0.0
            _raw_cmp_no_bid = _cmp_book.get("no_bid")
            _raw_cmp_no_ask = _cmp_book.get("no_ask")
            _cmp_no_bid  = float(_raw_cmp_no_bid) if _raw_cmp_no_bid is not None else (1.0 - _cmp_yes_ask)
            _cmp_no_ask  = float(_raw_cmp_no_ask) if _raw_cmp_no_ask is not None else (1.0 - _cmp_yes_bid)
            _cmp_raw_yes_bids = _cmp_book.get("yes_bids") or []
            _cmp_raw_yes_asks = _cmp_book.get("yes_asks") or []
            try:
                _cmp_bid_qty = sum(row[1] for row in _cmp_raw_yes_bids if len(row) > 1 and row[1])
                _cmp_ask_qty = sum(row[1] for row in _cmp_raw_yes_asks if len(row) > 1 and row[1])
                _cmp_denom   = _cmp_bid_qty + _cmp_ask_qty
                _cmp_imb_str = f"{((_cmp_bid_qty - _cmp_ask_qty) / _cmp_denom)*100:+.1f}%" if _cmp_denom > 0 else "--"
            except Exception:
                _cmp_imb_str = "--"

            st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)
            st.markdown(
                f"<div style='font-size:0.62rem;letter-spacing:0.1em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.4rem;'>MARKET COMPARISON</div>",
                unsafe_allow_html=True,
            )
            # Normalize: express prices as % of their combined max
            _norm_max = max(mid, _cmp_mid) if (mid > 0 or _cmp_mid > 0) else 1.0
            def _fmt_price(v_dec: float) -> str:
                if normalize and _norm_max > 0:
                    return f"{v_dec / _norm_max * 100:.1f}%"
                return f"{v_dec*100:.1f}c"

            # Mid price spread row
            _mid_diff = mid - _cmp_mid
            _comp_row = st.columns(3)
            _comp_row[0].metric(f"{ticker} YES MID", _fmt_price(mid))
            _comp_row[1].metric(f"{compare_ticker} YES MID", _fmt_price(_cmp_mid))
            _comp_row[2].metric("MID SPREAD (primary − compare)", f"{_mid_diff*100:+.1f}c")
            if normalize:
                st.caption("NORMALIZED — prices shown as % of the higher mid across both markets")

            # Side-by-side orderbook metrics
            _col_left, _col_right = st.columns(2)
            with _col_left:
                st.markdown(
                    f"<div style='font-size:0.62rem;letter-spacing:0.08em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.3rem;'>{ticker}</div>",
                    unsafe_allow_html=True,
                )
                _lc1, _lc2 = st.columns(2)
                _lc1.metric("YES BID", _fmt_price(yes_bid))
                _lc2.metric("YES ASK", _fmt_price(yes_ask))
                _lc3, _lc4 = st.columns(2)
                _lc3.metric("NO BID", _fmt_price(no_bid))
                _lc4.metric("NO ASK", _fmt_price(no_ask))
                _lc5, _lc6 = st.columns(2)
                _lc5.metric("SPREAD", f"{spread*100:.0f}c")
                try:
                    _lc6.metric("IMBALANCE", _imb_str)
                except NameError:
                    _lc6.metric("IMBALANCE", "--")
            with _col_right:
                st.markdown(
                    f"<div style='font-size:0.62rem;letter-spacing:0.08em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.3rem;'>{compare_ticker}</div>",
                    unsafe_allow_html=True,
                )
                _rc1, _rc2 = st.columns(2)
                _rc1.metric("YES BID", _fmt_price(_cmp_yes_bid))
                _rc2.metric("YES ASK", _fmt_price(_cmp_yes_ask))
                _rc3, _rc4 = st.columns(2)
                _rc3.metric("NO BID", _fmt_price(_cmp_no_bid))
                _rc4.metric("NO ASK", _fmt_price(_cmp_no_ask))
                _rc5, _rc6 = st.columns(2)
                _rc5.metric("SPREAD", f"{_cmp_spread*100:.0f}c")
                _rc6.metric("IMBALANCE", _cmp_imb_str)

            # --- Track compare market mid history for correlation -----
            _cmp_mid_hist_key = f"mid_history_{compare_ticker}"
            if _cmp_mid_hist_key not in st.session_state:
                st.session_state[_cmp_mid_hist_key] = []
            if _cmp_mid > 0:
                st.session_state[_cmp_mid_hist_key].append(_cmp_mid)
                if len(st.session_state[_cmp_mid_hist_key]) > 100:
                    st.session_state[_cmp_mid_hist_key] = st.session_state[_cmp_mid_hist_key][-100:]

            # --- Pearson correlation of YES prices -----
            _h1 = st.session_state.get(f"mid_history_{ticker}", [])
            _h2 = st.session_state.get(_cmp_mid_hist_key, [])
            _n_corr = min(len(_h1), len(_h2))
            if _n_corr >= 3:
                try:
                    import statistics as _stat
                    _x = _h1[-_n_corr:]
                    _y = _h2[-_n_corr:]
                    _mx, _my = sum(_x) / _n_corr, sum(_y) / _n_corr
                    _num = sum((_xi - _mx) * (_yi - _my) for _xi, _yi in zip(_x, _y))
                    _sx = (_stat.stdev(_x) * (_n_corr - 1) ** 0.5)
                    _sy = (_stat.stdev(_y) * (_n_corr - 1) ** 0.5)
                    _corr = _num / (_sx * _sy) if _sx > 0 and _sy > 0 else float("nan")
                    _corr_color = GREEN if abs(_corr) >= 0.7 else (AMBER if abs(_corr) >= 0.3 else TEXT3)
                    st.markdown(
                        f"<div style='margin:0.4rem 0;background:{PANEL};border:1px solid {BORDER};"
                        f"border-left:4px solid {_corr_color};padding:0.35rem 0.75rem;border-radius:2px;'>"
                        f"<span style='font-size:0.58rem;letter-spacing:0.08em;color:{TEXT3};"
                        f"text-transform:uppercase;font-family:Inter,sans-serif;margin-right:0.5rem;'>"
                        f"YES PRICE CORRELATION (last {_n_corr} obs)</span>"
                        f"<span style='font-family:JetBrains Mono,monospace;font-size:0.85rem;"
                        f"font-weight:700;color:{_corr_color};'>{_corr:+.3f}</span></div>",
                        unsafe_allow_html=True,
                    )
                except Exception:
                    pass
            st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)
        else:
            st.warning(f"Could not load orderbook for comparison market: {compare_ticker}")

    # --- Position Sizing expander -----
    with st.expander("💰 Position Sizing", expanded=False):
        _target_profit = st.number_input(
            "Target profit ($)", min_value=1, max_value=1000, value=10, key="p04_target_profit"
        )
        _compl_cost_cents = _comp_cost * 100  # e.g. 98.5
        _net_edge_cents = max(0.0, 100.0 - _compl_cost_cents)
        if _net_edge_cents <= 0 or yes_ask <= 0 or no_ask <= 0:
            st.info("No arb edge — position sizing N/A")
        else:
            _contracts_needed = _target_profit / (_net_edge_cents / 100)
            _pos1, _pos2 = st.columns(2)
            _pos1.metric("CONTRACTS NEEDED", f"{_contracts_needed:.0f}")
            _pos2.metric("TOTAL OUTLAY", f"${_contracts_needed * _compl_cost_cents / 100:.2f}")

    with st.expander("📈 What this spread means", expanded=False):
        st.markdown(
            "- **The complement spread = YES ask + NO ask.** For a fair market this equals exactly $1.00.\n"
            "- **Spread < $1.00** means quotes sum below parity — a feed/rounding artifact. Kalshi YES/NO are structurally complementary so live round-trip cost is always ≥ $1.00.\n"
            "- **Spread > $1.00** is normal. The market maker captures the bid-ask spread.\n"
            "- The YNC scanner runs and detects sub-$1.00 quotes; all detections are feed artifacts (not executable)."
        )

    if _comp_cost < 1.0:
        _comp_gross = (1.0 - _comp_cost) * 100
        st.caption(
            f"⚠ Quotes below parity: YES ask {yes_ask*100:.1f}c + NO ask {no_ask*100:.1f}c = {_comp_cost*100:.1f}c. "
            f"Apparent gap: {_comp_gross:.2f}c — this is a feed/rounding artifact. "
            f"Kalshi YES/NO are structurally complementary; live round-trip cost is always ≥ $1.00."
        )

    # -------------------------------------------------------------- L2 Depth table --------------------------------------------------------------
    yes_bids: List = book.get("yes_bids", [[yes_bid, None]])
    yes_asks: List = book.get("yes_asks", [[yes_ask, None]])

    if yes_bids or yes_asks:
        _render_depth_table(yes_bids, yes_asks)
        if l2_available:
            _render_depth_chart(yes_bids, yes_asks)
        else:
            st.caption("Depth chart, liquidity metrics, and slippage calculator require full L2 data (pending Synthesis feed upgrade).")

    # --- Liquidity metrics -----
    if l2_available and (yes_bids or yes_asks):
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown("#### LIQUIDITY METRICS")
        _render_liquidity_metrics(yes_bids, yes_asks, mid)

    # --- Sequence gap / stale-book panel -----
    _render_seq_panel(ticker)

    # --- Slippage calculator -----
    if l2_available and (yes_bids or yes_asks):
        st.markdown("<hr>", unsafe_allow_html=True)
        _render_slippage_calculator(yes_bids, yes_asks)

    # --- Age indicator -----
    age = float(book.get("age_seconds") or 0)
    ts  = book.get("ts", "")
    is_stale = book.get("is_stale", False)

    color = RED if is_stale else GREEN
    st.markdown(
        f"""<div style='font-size:0.62rem;font-family:JetBrains Mono,monospace;
color:{color};margin-top:0.5rem;letter-spacing:0.04em;'>
{'--  STALE --  ' if is_stale else '--- '}last update {
    f"{int(age//3600)}h{int((age%3600)//60)}m" if age >= 3600
    else f"{int(age//60)}m{int(age%60)}s" if age >= 60
    else f"{int(age)}s"
} ago &nbsp;&middot;&nbsp; {ts[:19]}
</div>""",
        unsafe_allow_html=True,
    )

    # --- Historical price context (from candlestick DB) -----
    st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)
    with st.expander("📈 HISTORICAL PRICE CONTEXT (candlestick data)", expanded=False):
        _hist_df, _hist_err = get_market_price_history(ticker, interval=60, limit=200)
        if not _hist_df.empty:
            _ts = pd.to_datetime(_hist_df["period_end_ts"], utc=True, errors="coerce")
            _close = pd.to_numeric(_hist_df["price_close"], errors="coerce")
            _bid = pd.to_numeric(_hist_df["yes_bid_close"], errors="coerce")
            _ask = pd.to_numeric(_hist_df["yes_ask_close"], errors="coerce")
            _vol = pd.to_numeric(_hist_df["volume"], errors="coerce").fillna(0)

            fig_hist = go.Figure()
            fig_hist.add_trace(go.Scatter(x=_ts, y=_ask, name="YES ASK",
                line=dict(color=RED, width=1), opacity=0.7))
            fig_hist.add_trace(go.Scatter(x=_ts, y=_close, name="TRADE PRICE",
                line=dict(color=GREEN, width=1.5)))
            fig_hist.add_trace(go.Scatter(x=_ts, y=_bid, name="YES BID",
                line=dict(color=AMBER, width=1), opacity=0.7,
                fill="tonexty", fillcolor="rgba(34,197,94,0.05)"))
            # Current live mid as a horizontal reference line
            fig_hist.add_hline(y=mid, line_color=BLUE, line_dash="dot", line_width=1,
                annotation_text=f"LIVE MID {mid*100:.0f}c", annotation_position="right")
            if _vol.sum() > 0:
                fig_hist.add_trace(go.Bar(x=_ts, y=_vol, name="VOLUME",
                    marker_color=BLUE, opacity=0.25, yaxis="y2"))
            fig_hist.update_layout(**plotly_dark_layout(
                title={"text": f"{ticker} — 1h OHLC (historical)", "font": {"size": 10, "color": TEXT3}},
                height=300,
                xaxis_title="", yaxis_title="YES Price",
                yaxis=dict(range=[0, 1], tickformat=".2f"),
                yaxis2=dict(title="Vol", overlaying="y", side="right", showgrid=False),
                legend=dict(orientation="h", y=1.12),
                margin={"l": 50, "r": 60, "t": 40, "b": 20},
            ))
            st.plotly_chart(fig_hist, use_container_width=True)
            st.caption(
                f"{len(_hist_df):,} hourly candles · "
                f"{_ts.min().strftime('%Y-%m-%d') if pd.notna(_ts.min()) else '?'} – "
                f"{_ts.max().strftime('%Y-%m-%d') if pd.notna(_ts.max()) else '?'}"
            )
        elif _hist_err:
            st.caption(f"No historical data available for {ticker}.")
        else:
            st.caption(f"No hourly candles found for {ticker}. Try another ticker or interval.")

    # --- Recent Trade History -----
    st.subheader("📜 Recent Trade History")
    _real_trades_found = False
    try:
        from dashboard.data_layer import _sqlite_conn as _gsc_p04
        _tc = _gsc_p04()
        if _tc:
            _trade_rows = _tc.execute(
                """
                SELECT created_at, price, side, count
                FROM trades
                WHERE market_id = ?
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (ticker,),
            ).fetchall()
            _tc.close()
            if _trade_rows:
                _real_trades_found = True
                import pytz as _pytz_p04
                _et_tz = _pytz_p04.timezone("America/New_York")
                _trade_records = []
                for _tr in _trade_rows:
                    _ts_raw, _price_raw, _side_raw, _qty_raw = _tr
                    try:
                        _ts_et = pd.to_datetime(_ts_raw, utc=True, errors="coerce").tz_convert(_et_tz).strftime("%H:%M:%S ET")
                    except Exception:
                        _ts_et = str(_ts_raw)[:19]
                    try:
                        _p_dec = float(_price_raw)
                        _p_c = _p_dec * 100 if _p_dec <= 1 else _p_dec
                    except Exception:
                        _p_c = None
                    try:
                        _qty = int(_qty_raw) if _qty_raw is not None else 0
                    except Exception:
                        _qty = 0
                    _val = round(_p_c / 100 * _qty, 2) if _p_c is not None and _qty else None
                    _trade_records.append({
                        "Time (ET)": _ts_et,
                        "Side": str(_side_raw or ""),
                        "Price (c)": f"{_p_c:.0f}c" if _p_c is not None else "--",
                        "Size": _qty,
                        "Value ($)": f"${_val:.2f}" if _val is not None else "--",
                    })
                _trade_df = pd.DataFrame(_trade_records)
                # Buy/Sell ratio metrics
                _buy_ct = sum(1 for r in _trade_records if "buy" in r["Side"].lower())
                _sell_ct = sum(1 for r in _trade_records if "sell" in r["Side"].lower())
                _avg_sz = sum(r["Size"] for r in _trade_records) / max(len(_trade_records), 1)
                _tm1, _tm2, _tm3 = st.columns(3)
                _tm1.metric("BUY TRADES", f"{_buy_ct}")
                _tm2.metric("SELL TRADES", f"{_sell_ct}")
                _tm3.metric("AVG TRADE SIZE", f"{_avg_sz:.1f} contracts")
                st.dataframe(_trade_df, use_container_width=True, hide_index=True, height=min(400, 36 + 35 * len(_trade_df)))
    except Exception:
        pass

    if not _real_trades_found:
        # Generate synthetic recent trades from orderbook (simulated fills at current bid/ask)
        st.warning("⚠️ Simulated trades — actual trade feed not available in demo mode")
        import random as _rnd_p04
        import datetime as _dt_p04
        _syn_trades = []
        _now_et = _dt_p04.datetime.now(_dt_p04.timezone.utc).astimezone(
            __import__("zoneinfo", fromlist=["ZoneInfo"]).ZoneInfo("America/New_York")
            if hasattr(__import__("zoneinfo", fromlist=["ZoneInfo"]), "ZoneInfo") else _dt_p04.timezone.utc
        )
        _rng = _rnd_p04.Random(int(yes_bid * 1000 + yes_ask * 1000))
        for _si in range(20):
            _offset_s = _si * _rng.randint(8, 45)
            _t_et = (_now_et - _dt_p04.timedelta(seconds=_offset_s)).strftime("%H:%M:%S ET")
            _is_buy = _rng.random() > 0.45
            _side = "yes_buy" if _is_buy else "yes_sell"
            _px_c = yes_ask * 100 if _is_buy else yes_bid * 100
            _px_c += _rng.uniform(-0.5, 0.5)
            _qty = _rng.randint(1, 50)
            _val = round(_px_c / 100 * _qty, 2)
            _syn_trades.append({
                "Time (ET)": _t_et,
                "Side": _side,
                "Price (c)": f"{_px_c:.0f}c",
                "Size": _qty,
                "Value ($)": f"${_val:.2f}",
            })
        _syn_df = pd.DataFrame(_syn_trades)
        _buy_ct_s = sum(1 for r in _syn_trades if "buy" in r["Side"])
        _sell_ct_s = len(_syn_trades) - _buy_ct_s
        _avg_sz_s = sum(r["Size"] for r in _syn_trades) / max(len(_syn_trades), 1)
        _sm1, _sm2, _sm3 = st.columns(3)
        _sm1.metric("BUY TRADES (sim)", f"{_buy_ct_s}")
        _sm2.metric("SELL TRADES (sim)", f"{_sell_ct_s}")
        _sm3.metric("AVG TRADE SIZE (sim)", f"{_avg_sz_s:.1f} contracts")
        st.dataframe(_syn_df, use_container_width=True, hide_index=True, height=min(400, 36 + 35 * len(_syn_df)))

    # --- PRICE IMPACT / VWAP TRADE SIMULATION -----
    st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.62rem;letter-spacing:0.1em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.4rem;'>VWAP TRADE SIMULATION</div>",
        unsafe_allow_html=True,
    )
    _pi_order_size = st.number_input(
        "Test order size (contracts)", min_value=1, max_value=100, value=10, key="p04_order_size"
    )

    _pi_asks = sorted(book.get("yes_asks", []), key=lambda x: x[0]) if book.get("yes_asks") else []
    _pi_bids = sorted(book.get("yes_bids", []), key=lambda x: x[0], reverse=True) if book.get("yes_bids") else []
    _pi_best_ask = yes_ask  # already computed above (decimal)
    _pi_best_bid = yes_bid

    _vwap_ask_cents: float | None = None
    _vwap_bid_cents: float | None = None

    _pi_col_buy, _pi_col_sell = st.columns(2)

    # --- Buy simulation: walk asks -----
    with _pi_col_buy:
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.08em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.3rem;'>BUY SIMULATION (walks asks)</div>",
            unsafe_allow_html=True,
        )
        if _pi_asks:
            _pi_filled = 0
            _pi_cost = 0.0
            for _pi_price, _pi_size in _pi_asks:
                if _pi_filled >= _pi_order_size or _pi_size is None:
                    continue
                _pi_take = min(int(_pi_size), _pi_order_size - _pi_filled)
                _pi_cost += _pi_take * float(_pi_price)
                _pi_filled += _pi_take
            if _pi_filled >= _pi_order_size:
                _vwap_ask_cents = (_pi_cost / _pi_filled) * 100
                _pi_impact = _vwap_ask_cents - (_pi_best_ask * 100)
                _bm1, _bm2 = st.columns(2)
                _bm1.metric(f"VWAP ({_pi_order_size} contracts)", f"{_vwap_ask_cents:.1f}¢",
                            help=f"Best ask: {_pi_best_ask*100:.1f}¢")
                _bm2.metric("PRICE IMPACT", f"{_pi_impact:.2f}¢",
                            delta=f"vs best ask {_pi_best_ask*100:.1f}¢",
                            delta_color="inverse")
            else:
                st.warning(f"Insufficient ask liquidity ({_pi_filled}/{_pi_order_size} filled)")
        else:
            st.warning("No ask levels available")

    # --- Sell simulation: walk bids -----
    with _pi_col_sell:
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.08em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.3rem;'>SELL SIMULATION (walks bids)</div>",
            unsafe_allow_html=True,
        )
        if _pi_bids:
            _ps_filled = 0
            _ps_proceeds = 0.0
            for _ps_price, _ps_size in _pi_bids:
                if _ps_filled >= _pi_order_size or _ps_size is None:
                    continue
                _ps_take = min(int(_ps_size), _pi_order_size - _ps_filled)
                _ps_proceeds += _ps_take * float(_ps_price)
                _ps_filled += _ps_take
            if _ps_filled >= _pi_order_size:
                _vwap_bid_cents = (_ps_proceeds / _ps_filled) * 100
                _ps_impact = (_pi_best_bid * 100) - _vwap_bid_cents
                _sm1, _sm2 = st.columns(2)
                _sm1.metric(f"VWAP ({_pi_order_size} contracts)", f"{_vwap_bid_cents:.1f}¢",
                            help=f"Best bid: {_pi_best_bid*100:.1f}¢")
                _sm2.metric("PRICE IMPACT", f"{_ps_impact:.2f}¢",
                            delta=f"vs best bid {_pi_best_bid*100:.1f}¢",
                            delta_color="inverse")
            else:
                st.warning(f"Insufficient bid liquidity ({_ps_filled}/{_pi_order_size} filled)")
        else:
            st.warning("No bid levels available")

    # --- Round-trip cost metrics -----
    if _vwap_ask_cents is not None and _vwap_bid_cents is not None:
        _rt_cost_dollars = (_vwap_ask_cents - _vwap_bid_cents) * _pi_order_size / 100
        _rt_breakeven_cents = _vwap_ask_cents - _vwap_bid_cents  # per contract in cents
        _rt1, _rt2 = st.columns(2)
        _rt1.metric(
            "ROUND-TRIP COST",
            f"${_rt_cost_dollars:.2f}",
            help=f"(VWAP ask − VWAP bid) × {_pi_order_size} contracts",
        )
        _rt2.metric(
            "BREAK-EVEN EDGE NEEDED",
            f"{_rt_breakeven_cents:.2f}¢/contract",
            help="Must earn this per contract to cover round-trip slippage",
        )
        if _rt_breakeven_cents > 4.0:
            st.info(f"⚠️ Wide spread: need >{_rt_breakeven_cents:.1f}¢ edge to profit on this size")

    _ws_live_p04 = _ws.get("connected", False)
    if auto_refresh and (not _is_sqlite or _ws_live_p04):
        import time as _t04
        _now04 = _t04.time()
        if st.session_state.get("_p04_next_refresh", 0) <= _now04:
            st.session_state["_p04_next_refresh"] = _now04 + 5
            st.rerun()


def _render_depth_table(bids: list, asks: list):
    """Render the L2 order book as a symmetric price ladder."""
    # Normalise --  bids descending, asks ascending
    bids_sorted = sorted(bids, key=lambda x: x[0], reverse=True)[:10]
    asks_sorted = sorted(asks, key=lambda x: x[0])[:10]

    max_depth = max(len(bids_sorted), len(asks_sorted))
    if max_depth == 0:
        return

    # Compute max size for bar scaling
    all_sizes = [s for _, s in bids_sorted + asks_sorted if s is not None]
    max_size  = max(all_sizes) if all_sizes else 1

    st.markdown(
        f"""<div style='font-size:0.62rem;letter-spacing:0.1em;color:{TEXT3};
text-transform:uppercase;margin:0.75rem 0 0.25rem 0;'>ORDER BOOK DEPTH</div>""",
        unsafe_allow_html=True,
    )

    # Build HTML table
    header = f"""
<table style='width:100%;border-collapse:collapse;font-family:JetBrains Mono,monospace;font-size:0.78rem;'>
<thead>
<tr style='color:{TEXT3};font-size:0.6rem;letter-spacing:0.08em;'>
<th style='text-align:right;padding:4px 12px;'>SIZE</th>
<th style='text-align:right;padding:4px 12px;'>BID c</th>
<th style='text-align:left; padding:4px 12px;'>ASK c</th>
<th style='text-align:left; padding:4px 12px;'>SIZE</th>
</tr>
</thead>
<tbody>
"""
    rows_html = ""
    for i in range(max_depth):
        bid_p = bid_s = ask_p = ask_s = None
        if i < len(bids_sorted):
            bid_p, bid_s = bids_sorted[i]
        if i < len(asks_sorted):
            ask_p, ask_s = asks_sorted[i]

        bid_bar  = _bar(bid_s, max_size, side="bid")  if bid_s else ""
        ask_bar  = _bar(ask_s, max_size, side="ask")  if ask_s else ""
        bid_p_s  = f"{float(bid_p)*100:.0f}" if bid_p is not None else ""
        ask_p_s  = f"{float(ask_p)*100:.0f}" if ask_p is not None else ""
        bid_s_s  = f"{int(bid_s):,}" if (bid_s is not None and bid_s > 0) else ""
        ask_s_s  = f"{int(ask_s):,}" if (ask_s is not None and ask_s > 0) else ""

        rows_html += f"""
<tr style='border-bottom:1px solid {BORDER};'>
<td style='text-align:right;padding:4px 12px;color:{GREEN};'>{bid_bar}{bid_s_s}</td>
<td style='text-align:right;padding:4px 12px;color:{GREEN};font-weight:500;'>{bid_p_s}</td>
<td style='text-align:left; padding:4px 12px;color:{RED};  font-weight:500;'>{ask_p_s}</td>
<td style='text-align:left; padding:4px 12px;color:{RED};  '>{ask_s_s}{ask_bar}</td>
</tr>
"""

    html = header + rows_html + "</tbody></table>"
    st.markdown(
        f"<div style='background:{PANEL};border:1px solid {BORDER};border-radius:3px;overflow-x:auto;'>{html}</div>",
        unsafe_allow_html=True,
    )


def _bar(size, max_size, side="bid") -> str:
    pct = min(int(size / max_size * 40), 40) if max_size else 0
    color = f"rgba(34,197,94,0.3)" if side == "bid" else "rgba(239,68,68,0.3)"
    border_color = "#22C55E" if side == "bid" else "#EF4444"
    direction = "right" if side == "bid" else "left"
    return f"<span style='display:inline-block;width:{pct}px;height:10px;background:{color};border-{direction}:2px solid {border_color};vertical-align:middle;margin-right:4px;'></span>"


def _render_depth_chart(bids: list, asks: list):
    """Proper cumulative depth chart: x=price (0-100¢), y=cumulative contracts."""
    # Guard: both sides empty → show informational message instead of crashing
    _bids_valid = [b for b in bids if len(b) > 1 and b[1]]
    _asks_valid = [a for a in asks if len(a) > 1 and a[1]]
    if not _bids_valid and not _asks_valid:
        from dashboard.styles import PANEL, BORDER, TEXT3 as _T3
        st.markdown(
            f"<div style='background:{PANEL};border:1px solid {BORDER};padding:1.5rem;"
            f"border-radius:3px;text-align:center;font-family:JetBrains Mono,monospace;"
            f"font-size:0.75rem;color:{_T3};'>NO DEPTH DATA — market has no L2 levels available</div>",
            unsafe_allow_html=True,
        )
        return

    bids_s = sorted(bids, key=lambda x: x[0], reverse=True)   # highest bid first
    asks_s = sorted(asks, key=lambda x: x[0])                  # lowest ask first

    # YES bids: cumulate right-to-left (from highest price downward)
    bid_prices = [b[0] * 100 for b in bids_s if b[1]]
    bid_sizes  = [b[1]       for b in bids_s if b[1]]
    # YES asks: cumulate left-to-right (from lowest price upward)
    ask_prices = [a[0] * 100 for a in asks_s if a[1]]
    ask_sizes  = [a[1]       for a in asks_s if a[1]]

    # Cumulative sums
    bid_cum, total = [], 0
    for s in bid_sizes:
        total += s
        bid_cum.append(total)
    ask_cum, total = [], 0
    for s in ask_sizes:
        total += s
        ask_cum.append(total)

    # Compute mid price for the vertical line
    _dc_best_bid = bid_prices[0] if bid_prices else None
    _dc_best_ask = ask_prices[0] if ask_prices else None
    _dc_mid = (_dc_best_bid + _dc_best_ask) / 2 if (_dc_best_bid and _dc_best_ask) else None

    # Determine if we have a real L2 ladder or just best bid/ask
    _has_ladder = len(bid_prices) > 1 or len(ask_prices) > 1

    fig = go.Figure()

    if _has_ladder:
        # Full cumulative depth chart
        if bid_prices:
            fig.add_trace(go.Scatter(
                x=bid_prices, y=bid_cum, mode="lines",
                name="YES bids (cumulative)",
                line={"color": GREEN, "width": 2},
                fill="tozeroy", fillcolor="rgba(34,197,94,0.15)",
            ))
        if ask_prices:
            fig.add_trace(go.Scatter(
                x=ask_prices, y=ask_cum, mode="lines",
                name="NO bids / YES asks (cumulative)",
                line={"color": RED, "width": 2},
                fill="tozeroy", fillcolor="rgba(239,68,68,0.15)",
            ))
        if _dc_mid is not None:
            fig.add_vline(
                x=_dc_mid, line_dash="dot", line_color="rgba(100,116,139,0.8)", line_width=1.5,
                annotation_text=f"MID {_dc_mid:.0f}¢", annotation_font_size=9,
                annotation_position="top",
            )
        _layout = plotly_dark_layout(
            title={"text": "CUMULATIVE DEPTH", "font": {"size": 10, "color": TEXT3}},
            height=220,
            xaxis_title="Price (¢)",
            yaxis_title="Cumulative Contracts",
            showlegend=True,
            margin={"l": 50, "r": 20, "t": 30, "b": 35},
            xaxis={"range": [0, 100]},
        )
    else:
        # Fallback: 4-bar chart showing YES bid/ask and NO bid/ask sizes
        _labels = ["YES BID", "YES ASK", "NO BID", "NO ASK"]
        _yes_bid_sz = bid_sizes[0] if bid_sizes else 0
        _yes_ask_sz = ask_sizes[0] if ask_sizes else 0
        _no_bid_sz  = ask_sizes[0] if ask_sizes else 0   # NO bid = YES ask side (complement)
        _no_ask_sz  = bid_sizes[0] if bid_sizes else 0   # NO ask = YES bid side
        _values = [_yes_bid_sz, _yes_ask_sz, _no_bid_sz, _no_ask_sz]
        _colors = [GREEN, RED, RED, GREEN]
        fig.add_trace(go.Bar(
            x=_labels, y=_values,
            marker_color=_colors,
            marker_line_width=0,
            name="Size at best price",
        ))
        _layout = plotly_dark_layout(
            title={"text": "TOP-OF-BOOK SIZES (no L2 ladder)", "font": {"size": 10, "color": TEXT3}},
            height=220,
            xaxis_title="",
            yaxis_title="Contracts",
            showlegend=False,
            margin={"l": 50, "r": 20, "t": 30, "b": 35},
        )

    fig.update_layout(**_layout)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Cumulative depth — area shows total contracts available at each price level. "
        "Green (YES bids) cumulates right-to-left; Red (YES asks / NO bids) cumulates left-to-right. "
        "Dotted line = current YES mid price."
    )


def _render_liquidity_metrics(bids: list, asks: list, mid: float):
    """Compute and display liquidity statistics."""
    bid_total = sum(b[1] for b in bids if len(b) > 1 and b[1])
    ask_total = sum(a[1] for a in asks if len(a) > 1 and a[1])
    imbalance  = (bid_total - ask_total) / max(bid_total + ask_total, 1)

    mid_c = mid * 100
    liquidity_1c  = (
        sum(b[1] for b in bids if b[1] and abs(b[0]*100 - mid_c) <= 1) +
        sum(a[1] for a in asks if a[1] and abs(a[0]*100 - mid_c) <= 1)
    )
    liquidity_2c  = (
        sum(b[1] for b in bids if b[1] and abs(b[0]*100 - mid_c) <= 2) +
        sum(a[1] for a in asks if a[1] and abs(a[0]*100 - mid_c) <= 2)
    )
    liquidity_5c  = (
        sum(b[1] for b in bids if b[1] and abs(b[0]*100 - mid_c) <= 5) +
        sum(a[1] for a in asks if a[1] and abs(a[0]*100 - mid_c) <= 5)
    )

    # VWAP bid side
    vwap_bid = (
        sum(b[0] * b[1] for b in bids if b[1]) / max(sum(b[1] for b in bids if b[1]), 1)
        if bids else 0
    )
    vwap_ask = (
        sum(a[0] * a[1] for a in asks if a[1]) / max(sum(a[1] for a in asks if a[1]), 1)
        if asks else 0
    )

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("BID DEPTH",      f"{int(bid_total):,}")
    m2.metric("ASK DEPTH",      f"{int(ask_total):,}")
    imb_pct = imbalance * 100
    m3.metric("IMBALANCE",      f"{imb_pct:+.1f}%")
    m4.metric("LIQ 1c",       f"{int(liquidity_1c):,}")
    m5.metric("LIQ 2c",       f"{int(liquidity_2c):,}")
    m6.metric("LIQ 5c",       f"{int(liquidity_5c):,}")

    m7, m8 = st.columns(2)
    m7.metric("VWAP BID",  f"{vwap_bid*100:.1f}c")
    m8.metric("VWAP ASK",  f"{vwap_ask*100:.1f}c")


def _render_seq_panel(ticker: str):
    """Display sequence gap statistics for this ticker from the live scanner."""
    try:
        from engine.live_scanner import LiveArbitrageScanner
        scanner = LiveArbitrageScanner._instance if hasattr(LiveArbitrageScanner, "_instance") else None
        if scanner is None:
            return

        mon = getattr(scanner, "_seq_monitor", None)
        if mon is None:
            return

        stats = mon.stats()
        is_stale = mon.is_stale(ticker)
        gap_count = mon._gap_counts.get(ticker, 0)
        total_gaps = stats.get("total_gaps", 0)
        stale_count = stats.get("stale_markets", 0)
        tracked = stats.get("markets_tracked", 0)

        stale_color = RED if is_stale else GREEN
        stale_label = "STALE" if is_stale else "LIVE"

        st.markdown(
            f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:0.65rem 1rem;
border-radius:3px;margin:0.75rem 0;'>
<div style='font-size:0.6rem;letter-spacing:0.1em;color:{TEXT3};text-transform:uppercase;
margin-bottom:0.5rem;'>SEQUENCE GAP MONITOR</div>
<div style='display:flex;gap:2rem;flex-wrap:wrap;'>
<div>
<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.06em;'>THIS TICKER</div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;color:{stale_color};'>{stale_label} &nbsp;({gap_count} gaps)</div>
</div>
<div>
<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.06em;'>TOTAL GAPS</div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;color:{TEXT};'>{total_gaps:,}</div>
</div>
<div>
<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.06em;'>STALE MARKETS</div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;color:{RED if stale_count else GREEN};'>{stale_count} / {tracked}</div>
</div>
</div>
</div>""",
            unsafe_allow_html=True,
        )
    except Exception:
        pass  # Panel is best-effort; don't break the page


def _render_slippage_calculator(bids: list, asks: list):
    """Interactive slippage cost estimator for a given trade size."""
    st.markdown(
        f"<div style='font-size:0.62rem;letter-spacing:0.1em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.5rem;'>SLIPPAGE CALCULATOR</div>",
        unsafe_allow_html=True,
    )
    col_s, col_d = st.columns([2, 3])
    with col_s:
        qty = st.number_input("CONTRACTS", min_value=1, max_value=50000, value=100, step=10, key="slippage_qty")
        side = st.radio("SIDE", ["BUY YES", "BUY NO"], horizontal=True, key="slippage_side")
        # Guard: qty must be positive (UI enforces min=1, but validate defensively)
        if qty <= 0:
            st.warning("Quantity must be at least 1 contract.")
            return

    with col_d:
        # Walk the book for the given qty
        if side == "BUY YES":
            levels = sorted(asks, key=lambda x: x[0])  # ascending by price
        else:
            # BUY NO = SELL YES = walk bid side descending, price = 1 - bid
            levels = [(1.0 - b[0], b[1]) for b in sorted(bids, key=lambda x: x[0], reverse=True)]

        filled = 0
        total_cost = 0.0
        rows = []
        for price, size in levels:
            if filled >= qty or size is None:
                continue
            take = min(int(size), qty - filled)
            total_cost += take * float(price)
            filled += take
            rows.append({"PRICE (c)": f"{float(price)*100:.0f}", "SIZE": f"{int(size):,}", "FILLED": f"{take:,}"})

        if filled > 0:
            avg_price = total_cost / filled
            tob_price = float(levels[0][0]) if levels else avg_price
            slippage_c = (avg_price - tob_price) * 100
            st.markdown(
                f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:0.65rem 1rem;border-radius:3px;
display:flex;gap:2rem;flex-wrap:wrap;'>
<div>
<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.06em;'>FILLED</div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.82rem;color:{GREEN};'>{filled:,} / {qty:,}</div>
</div>
<div>
<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.06em;'>AVG PRICE</div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.82rem;color:{TEXT};'>{avg_price*100:.2f}c</div>
</div>
<div>
<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.06em;'>SLIPPAGE</div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.82rem;color:{AMBER if slippage_c > 0 else GREEN};'>{slippage_c:+.2f}c</div>
</div>
<div>
<div style='font-size:0.58rem;color:{TEXT3};letter-spacing:0.06em;'>TOTAL COST</div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.82rem;color:{TEXT};'>${total_cost:.2f}</div>
</div>
</div>""",
                unsafe_allow_html=True,
            )
            if rows:
                with st.expander("EXECUTION TRACE"):
                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else:
            st.markdown(
                f"<div style='font-size:0.7rem;color:{AMBER};font-family:JetBrains Mono,monospace;'>INSUFFICIENT DEPTH for {qty:,} contracts</div>",
                unsafe_allow_html=True,
            )


def _placeholder_msg(msg: str):
    from dashboard.styles import PANEL, BORDER, TEXT3
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};
padding:2rem;border-radius:3px;text-align:center;'>
<div style='font-family:JetBrains Mono,monospace;font-size:0.8rem;color:{TEXT3};
letter-spacing:0.04em;'>
{msg}
</div>
</div>""",
        unsafe_allow_html=True,
    )


