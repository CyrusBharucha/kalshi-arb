"""dashboard/pages/p03_markets.py --  Market Explorer"""
from __future__ import annotations
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data_layer import get_open_markets
from dashboard.styles import GREEN, RED, AMBER, BLUE, TEXT, TEXT2, TEXT3, PANEL, BORDER


_CATEGORIES = [
    "All", "Elections", "Crypto", "Sports", "Finance/Markets",
    "Weather", "Bank of Canada", "Canadian Dollar", "Commodities",
    "Politics", "Other",
]


def render():
    st.markdown("""
<span style='font-size:1rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;'>
MARKET EXPLORER
</span>
""", unsafe_allow_html=True)
    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    # --- Data source note -----
    from dashboard.data_layer import get_system_health
    from dashboard.live_state import get_live_state as _gls
    _health = get_system_health()
    _ws_live = _gls().get_stats().get("connected", False)
    _is_sqlite = not _health.get("db_connected", False) and _health.get("db_mode") == "sqlite"
    _db_live = _health.get("db_connected", False)
    import os as _os_p03
    _sk_p03 = _os_p03.environ.get("SYNTHESIS_SECRET_KEY", "").strip()
    if not _sk_p03:
        try:
            _sk_p03 = (st.secrets.get("SYNTHESIS_SECRET_KEY", "") or "").strip()
        except Exception:
            pass
    if not _sk_p03:
        try:
            for _ns_p03 in st.secrets.values():
                if hasattr(_ns_p03, "get"):
                    _sk_p03 = (_ns_p03.get("SYNTHESIS_SECRET_KEY", "") or "").strip()
                    if _sk_p03:
                        break
        except Exception:
            pass
    _has_key_p03 = bool(_sk_p03)
    # Check Neon state for source label
    _p03_neon_ok = False
    try:
        import dashboard.live_arb_store as _las_p03
        _p03_neon_ok = (
            bool(getattr(_las_p03, "_pg_ok", False))
            or (getattr(_las_p03, "_pg_engine", None) is not None)
        )
        if not _p03_neon_ok:
            _p03_neon_ok = _las_p03.get_pg_engine_cached() is not None
    except Exception:
        pass
    if not _p03_neon_ok:
        _p03_neon_ok = bool(st.session_state.get("_sidebar_neon_ok", False))
    if _ws_live:
        _src_dot, _src_txt = GREEN, "LIVE"
    elif _db_live or _p03_neon_ok:
        _src_dot, _src_txt = GREEN, "CLOUD"
    elif _has_key_p03:
        _src_dot, _src_txt = AMBER, "↻ CONNECTING"
    else:
        _src_dot, _src_txt = "#6B7280", "offline"
    st.markdown(
        f"<div style='font-size:0.62rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
        f"margin-bottom:0.5rem;'><span style='color:{_src_dot};'>●</span> {_src_txt}</div>",
        unsafe_allow_html=True,
    )

    # --- Refresh control -----
    try:
        import time as _time
        if "p03_last_render_ts" not in st.session_state:
            st.session_state["p03_last_render_ts"] = _time.time()
        _age_secs = int(_time.time() - st.session_state["p03_last_render_ts"])
        _age_label = (
            f"{_age_secs // 3600}h {(_age_secs % 3600) // 60}m" if _age_secs >= 3600
            else f"{_age_secs // 60}m {_age_secs % 60}s" if _age_secs >= 60
            else f"{_age_secs}s"
        )
        if st.button("🔄 Refresh quotes", key="p03_refresh"):
            st.session_state["p03_last_render_ts"] = _time.time()
            st.rerun()
        st.caption(f"Last refreshed: {_age_label} ago")
    except Exception:
        pass

    # --- Filters -----
    f1, f2, f3, f4, f5 = st.columns([2, 1.5, 1, 1, 1])

    with f1:
        search = st.text_input("SEARCH", placeholder="ticker or title…", label_visibility="visible", key="p03_search")

    with f2:
        if _is_sqlite:
            # Populate from actual DB values
            try:
                try:
                    from dashboard.data_layer import _sqlite_conn as _gsc
                except ImportError:
                    _gsc = None
                _sc_tmp = _gsc() if _gsc is not None else None
                if _sc_tmp:
                    _cat_rows = _sc_tmp.execute(
                        "SELECT DISTINCT category FROM markets WHERE category IS NOT NULL ORDER BY category"
                    ).fetchall()
                    _sc_tmp.close()
                    _db_cats = ["All"] + [r[0] for r in _cat_rows if r[0]]
                else:
                    _db_cats = ["All"]
            except Exception:
                _db_cats = ["All"]
            category = st.selectbox("CATEGORY", _db_cats if len(_db_cats) > 1 else ["All"], key="p03_category")
        else:
            category = st.selectbox("CATEGORY", _CATEGORIES, key="p03_category")
        cat_arg  = None if category.startswith("All") else category

    with f3:
        canadian = st.checkbox("CANADIAN ONLY", value=False, key="p03_canadian")

    with f4:
        has_arb = st.checkbox("DETECTED ONLY", value=False, key="p03_has_arb")

    with f5:
        min_vol = st.number_input("MIN VOLUME", value=0, step=100, min_value=0, key="p03_min_vol")

    # --- Load (single call — prefixes derived from result below) -----
    df, err = get_open_markets(
        category_filter=cat_arg,
        canadian_only=canadian,
        min_volume=float(min_vol),
        limit=10000,
    )

    # --- Category prefix filter pills (derived from loaded data) -----
    try:
        if not df.empty and "ticker" in df.columns:
            _prefixes = df["ticker"].dropna().apply(lambda t: str(t).split("-")[0]).unique()
            sorted_prefixes = sorted(set(_prefixes))
        else:
            sorted_prefixes = []
        _selected_cats = st.multiselect(
            "Event categories", options=sorted_prefixes, default=[], key="p03_cat_filter"
        )
    except Exception:
        _selected_cats = []
        sorted_prefixes = []

    if err and df.empty:
        # Fallback: show live WS markets
        from dashboard.live_state import get_live_state
        state = get_live_state()
        all_quotes = state.snapshot_all()
        if all_quotes:
            rows = []
            for ticker, q in all_quotes.items():
                if search and search.lower() not in ticker.lower():
                    continue
                rows.append({
                    "ticker": ticker,
                    "yes_bid": round(q.yes_bid, 4),
                    "yes_ask": round(q.yes_ask, 4),
                    "spread": round(q.spread, 4),
                    "mid": round(q.mid, 4),
                    "l2_depth": len(q.yes_bids or []) + len(q.yes_asks or []),
                    "age": (
                        f"{int(q.age_seconds//3600)}h{int((q.age_seconds%3600)//60)}m"
                        if q.age_seconds >= 3600
                        else f"{int(q.age_seconds//60)}m{int(q.age_seconds%60)}s"
                        if q.age_seconds >= 60
                        else f"{int(q.age_seconds)}s"
                    ),
                })
            if rows:
                live_df = pd.DataFrame(rows).sort_values("spread")
                st.metric("LIVE MARKETS", f"{len(live_df):,}")
                st.dataframe(live_df, use_container_width=True, height=600, hide_index=True)
                st.caption("Live data from Synthesis WebSocket. Full market details available when Neon cloud is connected.")
                return

        # Check if Neon is reachable — direct row count is most reliable
        _neon_ok_p03 = False
        try:
            from dashboard.data_layer import get_live_arbs_cloud_stats as _p03_neon_probe
            if (_p03_neon_probe().get("total_count", 0) or 0) > 0:
                _neon_ok_p03 = True
        except Exception:
            pass
        if not _neon_ok_p03:
            try:
                import dashboard.live_arb_store as _las_p03
                _neon_ok_p03 = (
                    bool(getattr(_las_p03, "_pg_ok", False))
                    or (getattr(_las_p03, "_pg_engine", None) is not None)
                    or (_las_p03.get_pg_engine_cached() is not None)
                )
            except Exception:
                pass
        if not _neon_ok_p03:
            _neon_ok_p03 = bool(st.session_state.get("_sidebar_neon_ok", False))
        if _neon_ok_p03:
            # Show recent arb tickers from Neon as proxy for active markets
            try:
                import dashboard.live_arb_store as _las_p03b
                from sqlalchemy import text as _p03_text
                _p03_eng = getattr(_las_p03b, "_pg_engine", None) or _las_p03b.get_pg_engine_cached()
                if _p03_eng is not None:
                    with _p03_eng.connect() as _p03_c:
                        _p03_rows = _p03_c.execute(_p03_text(
                            "SELECT ticker, MAX(detected_at) AS last_seen, "
                            "MAX(net_edge_cents) AS best_edge, "
                            "string_agg(DISTINCT strategy_type, ', ') AS strategies "
                            "FROM live_arbs_cloud "
                            "WHERE strategy_type != 'collectively_exhaustive' "
                            "GROUP BY ticker ORDER BY last_seen DESC LIMIT 20"
                        )).fetchall()
                    if _p03_rows:
                        st.markdown(
                            f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                            f"color:{TEXT3};margin-bottom:0.4rem;'>NEON CLOUD — RECENTLY DETECTED ARB MARKETS</div>",
                            unsafe_allow_html=True,
                        )
                        import pandas as _pd_p03
                        _p03_df = _pd_p03.DataFrame(
                            _p03_rows, columns=["Ticker", "Last Detected", "Best Edge (¢)", "Strategies"]
                        )
                        _p03_df["Best Edge (¢)"] = _p03_df["Best Edge (¢)"].apply(
                            lambda v: f"{float(v):.2f}¢" if v else "--"
                        )
                        _p03_df["Last Detected"] = _p03_df["Last Detected"].apply(
                            lambda v: str(v)[:16] if v else "--"
                        )
                        st.dataframe(_p03_df, use_container_width=True, hide_index=True)
                        st.caption(
                            "Live market data requires the Synthesis WebSocket feed. "
                            "Showing historical arbs from Neon cloud as a proxy — ME/TH strategies are actionable."
                        )
                        return
            except Exception:
                pass
            st.info("Markets table not yet populated in the cloud DB. Historical arb data is available in Arb History.")
        else:
            st.markdown(
                f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:1rem;
border-radius:3px;color:{TEXT3};font-size:0.75rem;font-family:JetBrains Mono,monospace;'>
Feed connecting — market quotes will appear once Synthesis WebSocket is live.
</div>""",
                unsafe_allow_html=True,
            )
        return

    if df.empty:
        st.info("No markets match the current filters.")
        return

    # --- Apply local filters -----
    if search:
        _ticker_mask = df["ticker"].str.contains(search, case=False, na=False) if "ticker" in df.columns else pd.Series([False] * len(df))
        _title_mask  = df["title"].str.contains(search, case=False, na=False)  if "title"  in df.columns else pd.Series([False] * len(df))
        mask = _ticker_mask | _title_mask
        df = df[mask]

    if has_arb and "has_arb" in df.columns:
        df = df[df["has_arb"].astype(bool)]

    # --- Apply category prefix filter -----
    try:
        if _selected_cats and "ticker" in df.columns:
            _cat_mask = df["ticker"].apply(
                lambda t: any(str(t).startswith(pfx) for pfx in _selected_cats)
            )
            df = df[_cat_mask]
            st.caption(f"{len(df):,} markets in selected categories")
    except Exception:
        pass

    # --- Merge live WS bid/ask into DB data (when WS connected) -----
    if _ws_live and "ticker" in df.columns:
        _live_q = _gls().snapshot_all()
        if _live_q:
            def _ws_bid(t):
                _q = _live_q.get(t)
                return f"{_q.yes_bid*100:.0f}c" if _q and _q.yes_bid > 0 else None

            def _ws_ask(t):
                _q = _live_q.get(t)
                return f"{_q.yes_ask*100:.0f}c" if _q and _q.yes_ask > 0 else None

            def _ws_spread(t):
                _q = _live_q.get(t)
                if _q and _q.yes_bid > 0 and _q.yes_ask > 0:
                    return f"{(_q.yes_ask - _q.yes_bid)*100:.0f}c"
                return None

            def _ws_compl_arb(t):
                _q = _live_q.get(t)
                if _q and _q.yes_ask > 0 and _q.no_ask > 0:
                    return round(_q.yes_ask + _q.no_ask, 4)
                return None

            df = df.copy()
            df["_live_bid"] = df["ticker"].apply(_ws_bid)
            df["_live_ask"] = df["ticker"].apply(_ws_ask)
            df["_live_spread"] = df["ticker"].apply(_ws_spread)
            df["_compl_arb"] = df["ticker"].apply(_ws_compl_arb)

    # --- Complement arb for DB-only path (derive no_ask = 1 - yes_bid) -----
    if "_compl_arb" not in df.columns and "yes_ask" in df.columns and "yes_bid" in df.columns:
        _ya = pd.to_numeric(df["yes_ask"], errors="coerce")
        _yb = pd.to_numeric(df["yes_bid"], errors="coerce")
        _no_ask_derived = 1.0 - _yb
        _combo = (_ya + _no_ask_derived).round(4)
        df = df.copy()
        # Store actual combined value where both legs are available, else None
        df["_compl_arb"] = _combo.where(_ya.notna() & _yb.notna(), None)

    # --- ARB SCORE: max(0, 1 - (yes_ask + no_ask)); positive = below parity (feed/rounding artifact) -----
    if "_compl_arb" in df.columns:
        _ca_n = pd.to_numeric(df["_compl_arb"], errors="coerce")
        df["_arb_score"] = (1.0 - _ca_n).clip(lower=0).where(_ca_n.notna(), None)

    # --- SORT BY selectbox -----
    _sort_options = ["ARB SCORE ↓", "VOLUME ↓", "SPREAD ↑", "TICKER A-Z"]
    _sort_by = st.selectbox("SORT BY", _sort_options, key="p03_sort_by", label_visibility="visible")
    st.caption(
        "ARB SCORE: max(0, (1.00 − (yes_ask + no_ask)) × 100) expressed in cents. "
        "A score of +5c means the live quotes sum to 95¢ — 5¢ below parity. "
        "Zero means both legs sum to ≥ $1.00 (at or above parity — normal). "
        "Note: live YES+NO always sums ≥ $1.00 structurally; any score >0 reflects a feed/rounding artifact, not an executable opportunity."
    )
    if _sort_by == "ARB SCORE ↓" and "_arb_score" in df.columns:
        df = df.sort_values("_arb_score", ascending=False, na_position="last")
    elif _sort_by == "VOLUME ↓" and "volume" in df.columns:
        df = df.sort_values("volume", ascending=False, na_position="last")
    elif _sort_by == "SPREAD ↑" and "spread" in df.columns:
        df = df.sort_values("spread", ascending=True, na_position="last")
    elif _sort_by == "TICKER A-Z" and "ticker" in df.columns:
        df = df.sort_values("ticker", ascending=True, na_position="last")

    # --- Format display -----
    display = df.copy()

    # Live bid/ask/spread from WS — show as LIVE BID / LIVE ASK / LIVE SPR columns
    if "_live_bid" in display.columns:
        display["LIVE BID"] = display["_live_bid"].fillna("--")
        display["LIVE ASK"] = display["_live_ask"].fillna("--")
        display["LIVE SPR"] = display["_live_spread"].fillna("--") if "_live_spread" in display.columns else "--"
        display = display.drop(columns=[c for c in ["_live_bid", "_live_ask", "_live_spread"] if c in display.columns])

    if "yes_bid" in display.columns:
        display["BID"] = display["yes_bid"].apply(lambda v: f"{float(v)*100:.0f}c" if pd.notna(v) else "--")
    if "yes_ask" in display.columns:
        display["ASK"] = display["yes_ask"].apply(lambda v: f"{float(v)*100:.0f}c" if pd.notna(v) else "--")
    if "spread" in display.columns:
        display["SPREAD"] = display["spread"].apply(lambda v: f"{float(v)*100:.0f}c" if pd.notna(v) else "--")
    if "volume" in display.columns:
        display["VOLUME"] = display["volume"].apply(
            lambda v: f"{int(v):,}" if pd.notna(v) and v else "--"
        )
    if "last_price" in display.columns:
        display["LAST"] = display["last_price"].apply(lambda v: f"{float(v)*100:.0f}c" if pd.notna(v) else "--")
    if "close_time" in display.columns:
        _exp = pd.to_datetime(display["close_time"], utc=True, errors="coerce")
        display["EXPIRES"] = _exp.dt.strftime("%Y-%m-%d").where(_exp.notna(), "--")
    if "canadian_relevance" in display.columns:
        display["CA"] = display["canadian_relevance"].apply(
            lambda v: "✓" if pd.notna(v) and int(float(v)) >= 1 else ""
        )
    if "relationship_count" in display.columns:
        display["RELS"] = display["relationship_count"].apply(
            lambda v: str(int(float(v))) if pd.notna(v) and int(float(v)) > 0 else ""
        )
    if "has_arb" in display.columns:
        display["DETECTED"] = display["has_arb"].apply(lambda v: "✓" if v else "")
    if "_compl_arb" in display.columns:
        if _ws_live:
            # WS is live: yes_ask + no_ask from live quotes; < 1.0 is a feed/rounding artifact (YNC not executable)
            def _fmt_compl_arb(v):
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return ""
                try:
                    fv = float(v)
                    return f"✓ {fv:.2f}" if fv < 1.0 else f"{fv:.2f}"
                except (TypeError, ValueError):
                    return "✓" if v else ""
            display["BELOW PARITY?"] = display["_compl_arb"].apply(_fmt_compl_arb)
        else:
            # WS offline: value is yes_ask + (1 - yes_bid) = round-trip bid-ask spread,
            # NOT a complement arb check. Show as-is with no checkmark.
            def _fmt_round_trip(v):
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return ""
                try:
                    return f"{float(v):.2f}"
                except (TypeError, ValueError):
                    return ""
            display["ROUND-TRIP COST"] = display["_compl_arb"].apply(_fmt_round_trip)
        display = display.drop(columns=["_compl_arb"])

    if "_arb_score" in display.columns:
        def _fmt_arb_score(v):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return "--"
            try:
                fv = float(v)
                return f"+{fv*100:.1f}c" if fv > 0 else "--"
            except (TypeError, ValueError):
                return "--"
        display["ARB SCORE"] = display["_arb_score"].apply(_fmt_arb_score)
        display = display.drop(columns=["_arb_score"])

    # --- KPI row -----
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("SHOWN", f"{len(df):,}")
    # Spread/volume/bid-ask columns only exist in live DB (not snapshot)
    if _is_sqlite:
        m2.metric("WITH SPREAD", "--")
    else:
        m2.metric("WITH SPREAD", f"{df['spread'].notna().sum():,}" if "spread" in df.columns else "--")
    # CANADIAN — use column if present; derive from title keywords in snapshot
    if "canadian_relevance" in df.columns:
        _ca_count = (pd.to_numeric(df["canadian_relevance"], errors="coerce").fillna(0) >= 1).sum()
        m3.metric("CANADIAN", f"{int(_ca_count):,}")
    elif not df.empty and "title" in df.columns:
        _ticker_ca = df["ticker"].str.contains("KXBOC|KXCB|KXCAD", case=False, na=False) if "ticker" in df.columns else pd.Series(False, index=df.index)
        _ca_mask = (
            _ticker_ca |
            df["title"].str.contains("Canada|Canadian|Bank of Canada|CORRA|BOC", case=False, na=False)
        )
        m3.metric("CANADIAN", f"{int(_ca_mask.sum()):,}*", help="*Estimated from title/ticker keywords")
    else:
        m3.metric("CANADIAN", "--")
    # HAS ARB — use column if present (live DB); derive from arb_opportunities in snapshot
    if "has_arb" in df.columns:
        m4.metric("LIVE ARB", f"{int(df['has_arb'].fillna(False).sum()):,}",
                  help="Markets flagged by scanner (ME/TH actionable; YNC records are feed artifacts — live YES+NO always ≥ $1.00).")
    elif _is_sqlite:
        _n_arb_markets = 0
        try:
            import json as _jm4
            try:
                from dashboard.data_layer import _sqlite_conn as _gsc_m4
            except ImportError:
                _gsc_m4 = None
            _mc4 = _gsc_m4() if _gsc_m4 is not None else None
            if _mc4:
                _arb_rows = _mc4.execute(
                    "SELECT markets_involved FROM arbitrage_opportunities WHERE markets_involved IS NOT NULL "
                    "AND strategy_type != 'collectively_exhaustive'"
                ).fetchall()
                _mc4.close()
                _arb_tickers: set = set()
                for _ar in _arb_rows:
                    try:
                        _raw = str(_ar[0]).strip()
                        # Try JSON first, then PostgreSQL {T1,T2} array notation
                        if _raw.startswith("["):
                            _tlist = _jm4.loads(_raw)
                        elif _raw.startswith("{") and _raw.endswith("}"):
                            _tlist = [t.strip() for t in _raw[1:-1].split(",") if t.strip()]
                        else:
                            _tlist = [_raw] if _raw else []
                        _arb_tickers.update(_tlist)
                    except Exception:
                        pass
                _n_arb_markets = len(_arb_tickers)
        except Exception:
            pass
        m4.metric("HIST DETECT", f"{_n_arb_markets:,}",
                  help="Markets appearing in any historical detection (reference DB — ME/TH arbs + YNC feed artifacts).")
    else:
        m4.metric("LIVE ARB", "--")

    st.markdown("<br>", unsafe_allow_html=True)

    if _is_sqlite:
        st.markdown(
            f"<div style='font-size:0.65rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
            f"margin-bottom:0.4rem;'>Live bid/ask/spread available on the ORDER BOOK page. "
            f"Historical candle data shown below.</div>",
            unsafe_allow_html=True,
        )
        # Show candlestick markets as an extra explorer — these are NOT in the markets table
        # (candlesticks.market_id = ticker like CONTROLH-2026-D, not the SHARD UUIDs above)
        try:
            from dashboard.data_layer import get_top_markets_by_volume as _gtmv
            _cs_df, _cs_err = _gtmv(limit=50)
            # Apply search filter to candlestick markets (same search box as main table)
            if not _cs_df.empty and search and "ticker" in _cs_df.columns:
                _cs_df = _cs_df[_cs_df["ticker"].str.contains(search, case=False, na=False)]
            if not _cs_df.empty:
                st.markdown("<hr style='margin:0.75rem 0;'>", unsafe_allow_html=True)
                st.markdown(
                    f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                    f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
                    f"CANDLESTICK MARKETS ({len(_cs_df)} — historical price data available)</div>",
                    unsafe_allow_html=True,
                )
                _cs_show = _cs_df.copy()
                if "total_volume" in _cs_show.columns:
                    _cs_show["total_volume"] = pd.to_numeric(
                        _cs_show["total_volume"], errors="coerce"
                    ).apply(lambda v: f"{int(v):,}" if pd.notna(v) else "--")
                if "avg_price" in _cs_show.columns:
                    _cs_show["avg_price"] = pd.to_numeric(
                        _cs_show["avg_price"], errors="coerce"
                    ).apply(lambda v: f"{v:.3f}" if pd.notna(v) else "--")
                _cs_cols = [c for c in ["ticker", "category", "total_volume", "candle_count", "avg_price", "first_candle", "last_candle"] if c in _cs_show.columns]
                st.dataframe(
                    _cs_show[_cs_cols].rename(columns={
                        "ticker": "TICKER", "category": "CAT",
                        "total_volume": "VOLUME", "candle_count": "CANDLES",
                        "avg_price": "AVG PRICE", "first_candle": "FIRST", "last_candle": "LAST",
                    }),
                    use_container_width=True, height=350, hide_index=True,
                )
                st.markdown(
                    f"<div style='font-size:0.62rem;color:{TEXT3};font-family:JetBrains Mono,monospace;'>"
                    f"Enter any TICKER above in p04 ORDER BOOK to see its price history chart.</div>",
                    unsafe_allow_html=True,
                )
        except Exception:
            pass

    # In SQLite mode the DB BID/ASK/SPREAD columns are always "--" (no live quotes stored).
    # Drop them so users don't see a column full of dashes; the note above already explains why.
    _db_quote_cols = ["BID", "ASK", "SPREAD", "LAST"] if _is_sqlite else []

    show_cols = [c for c in
                 ["ticker", "event_ticker", "category", "title",
                  "LIVE BID", "LIVE ASK", "LIVE SPR", "LAST", "BID", "ASK", "SPREAD", "VOLUME",
                  "ARB SCORE", "RELS", "CA", "DETECTED", "BELOW PARITY?", "ROUND-TRIP COST", "EXPIRES"]
                 if c in display.columns and c not in _db_quote_cols]

    # --- Group by selectbox -----
    _group_by = st.selectbox("Group by", ["None", "Event", "Strategy type"], key="p03_group_by")
    if _group_by == "Event" and "ticker" in df.columns:
        try:
            _grp_df = df.copy()
            _grp_df["_event_group"] = _grp_df["ticker"].apply(
                lambda t: "-".join(str(t).split("-")[:2]) if t else "--"
            )
            _arb_col = "_arb_score" if "_arb_score" in _grp_df.columns else None
            _spread_col = "spread" if "spread" in _grp_df.columns else None
            _oi_col = "open_interest" if "open_interest" in _grp_df.columns else None
            _ask_col = "yes_ask" if "yes_ask" in _grp_df.columns else None
            _agg: dict = {"ticker": "count"}
            if _arb_col:
                _agg[_arb_col] = "mean"
            if _spread_col:
                _agg[_spread_col] = "min"
            if _oi_col:
                _agg[_oi_col] = "sum"
            if _ask_col:
                _agg[_ask_col] = "min"
            _summary = _grp_df.groupby("_event_group").agg(_agg).reset_index()
            _rename_map = {"_event_group": "EVENT", "ticker": "MARKET COUNT"}
            if _arb_col:
                _rename_map[_arb_col] = "AVG ARB SCORE"
            if _spread_col:
                _rename_map[_spread_col] = "BEST SPREAD"
            if _oi_col:
                _rename_map[_oi_col] = "TOTAL OI"
            if _ask_col:
                _rename_map[_ask_col] = "BEST YES ASK"
            _summary = _summary.rename(columns=_rename_map)
            if "AVG ARB SCORE" in _summary.columns:
                _summary["AVG ARB SCORE"] = _summary["AVG ARB SCORE"].apply(
                    lambda v: f"+{float(v)*100:.1f}c" if pd.notna(v) and float(v) > 0 else "--"
                )
            if "BEST SPREAD" in _summary.columns:
                _summary["BEST SPREAD"] = _summary["BEST SPREAD"].apply(
                    lambda v: f"{float(v)*100:.0f}c" if pd.notna(v) else "--"
                )
            if "TOTAL OI" in _summary.columns:
                _summary["TOTAL OI"] = _summary["TOTAL OI"].apply(
                    lambda v: f"{int(float(v)):,}" if pd.notna(v) and float(v) > 0 else "--"
                )
            if "BEST YES ASK" in _summary.columns:
                _summary["BEST YES ASK"] = _summary["BEST YES ASK"].apply(
                    lambda v: f"{float(v)*100:.0f}c" if pd.notna(v) else "--"
                )
            _summary = _summary.sort_values("MARKET COUNT", ascending=False)
            st.markdown(
                f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
                f"GROUPED BY EVENT ({len(_summary)} events)</div>",
                unsafe_allow_html=True,
            )

            # --- Most active event metric -----
            if not _summary.empty:
                _most_active_event = _summary.iloc[0]["EVENT"]
                _most_active_count = int(_summary.iloc[0]["MARKET COUNT"])
                st.metric("MOST ACTIVE EVENT", f"{_most_active_event}",
                          delta=f"{_most_active_count} markets",
                          help="Event with the most markets tracked in the current filter")

            # --- Markets per event bar chart -----
            try:
                _chart_df = _summary.head(30).copy()
                _fig_evt = go.Figure(go.Bar(
                    x=_chart_df["MARKET COUNT"],
                    y=_chart_df["EVENT"],
                    orientation="h",
                    marker_color=BLUE,
                    marker_line_width=0,
                    text=_chart_df["MARKET COUNT"].astype(str),
                    textposition="outside",
                ))
                _fig_evt.update_layout(
                    margin=dict(l=10, r=40, t=10, b=10),
                    height=max(200, min(500, len(_chart_df) * 22 + 40)),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=9)),
                    yaxis=dict(showgrid=False, tickfont=dict(size=9), autorange="reversed"),
                    showlegend=False,
                    font=dict(family="JetBrains Mono, monospace", size=9),
                    title=dict(text="MARKETS PER EVENT (top 30)", font=dict(size=10, color=TEXT3)),
                )
                st.plotly_chart(_fig_evt, use_container_width=True, config={"displayModeBar": False})
            except Exception:
                pass

            # --- Accordion expanders per event -----
            st.markdown(
                f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                f"color:{TEXT3};font-family:Inter,sans-serif;margin:0.5rem 0 0.3rem 0;'>"
                f"EVENT DETAIL ({len(_summary)} events)</div>",
                unsafe_allow_html=True,
            )
            _evt_search = st.text_input(
                "Filter events", placeholder="event name…",
                label_visibility="visible", key="p03_evt_search",
            )
            _summary_filtered = _summary[
                _summary["EVENT"].str.contains(_evt_search, case=False, na=False)
            ] if _evt_search else _summary
            for _, _evt_row in _summary_filtered.iterrows():
                _evt_name = _evt_row["EVENT"]
                _evt_count = int(_evt_row["MARKET COUNT"])
                _is_complex = _evt_count > 5
                _badge_str = " 🔶 complex event" if _is_complex else ""
                _expander_label = f"{_evt_name}  ({_evt_count} markets){_badge_str}"
                with st.expander(_expander_label, expanded=False):
                    _evt_markets = _grp_df[_grp_df["_event_group"] == _evt_name].copy()
                    _evt_cols = [c for c in ["ticker", "yes_bid", "yes_ask", "open_interest"] if c in _evt_markets.columns]
                    if _evt_cols:
                        _evt_show = _evt_markets[_evt_cols].copy()
                        if "yes_bid" in _evt_show.columns:
                            _evt_show["YES BID"] = _evt_show["yes_bid"].apply(
                                lambda v: f"{float(v)*100:.0f}c" if pd.notna(v) else "--"
                            )
                            _evt_show = _evt_show.drop(columns=["yes_bid"])
                        if "yes_ask" in _evt_show.columns:
                            _evt_show["YES ASK"] = _evt_show["yes_ask"].apply(
                                lambda v: f"{float(v)*100:.0f}c" if pd.notna(v) else "--"
                            )
                            _evt_show = _evt_show.drop(columns=["yes_ask"])
                        if "open_interest" in _evt_show.columns:
                            _evt_show["OPEN INT"] = _evt_show["open_interest"].apply(
                                lambda v: f"{int(float(v)):,}" if pd.notna(v) and float(v) > 0 else "--"
                            )
                            _evt_show = _evt_show.drop(columns=["open_interest"])
                        _evt_show = _evt_show.rename(columns={"ticker": "TICKER"})
                        # Summary line: total OI + best YES ask across legs
                        _total_oi_evt = _evt_markets["open_interest"].apply(pd.to_numeric, errors="coerce").sum() if "open_interest" in _evt_markets.columns else None
                        _best_ask_evt = _evt_markets["yes_ask"].apply(pd.to_numeric, errors="coerce").min() if "yes_ask" in _evt_markets.columns else None
                        _oi_str = f"{int(_total_oi_evt):,}" if _total_oi_evt and pd.notna(_total_oi_evt) else "--"
                        _ask_str = f"{_best_ask_evt*100:.0f}c" if _best_ask_evt and pd.notna(_best_ask_evt) else "--"
                        st.caption(f"Total OI across legs: {_oi_str} · Best YES ask: {_ask_str}")
                        st.dataframe(_evt_show, use_container_width=True,
                                     height=min(300, _evt_count * 35 + 38), hide_index=True)
                    else:
                        _tickers_in_evt = _evt_markets["ticker"].tolist() if "ticker" in _evt_markets.columns else []
                        st.caption(", ".join(str(t) for t in _tickers_in_evt[:20]))
        except Exception:
            st.caption("Group by Event unavailable for the current data.")

    _n_shown = len(display)
    _n_total_loaded = len(df)
    st.caption(f"Showing {_n_shown:,} of {_n_total_loaded:,} markets"
               + (" (filtered)" if search or has_arb or cat_arg or min_vol > 0 else ""))

    _tbl_height = min(600, max(200, _n_shown * 35 + 38))
    _display_renamed = display[show_cols].rename(columns={
        "ticker": "TICKER",
        "event_ticker": "EVENT",
        "category": "CATEGORY",
        "title": "TITLE",
    })
    st.dataframe(
        _display_renamed,
        use_container_width=True,
        height=_tbl_height,
        hide_index=True,
    )

    # Download CSV button
    _csv_bytes = _display_renamed.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇ Download CSV",
        data=_csv_bytes,
        file_name="kalshi_markets.csv",
        mime="text/csv",
        key="p03_download_csv",
    )

    # --- Market comparison table -----
    # Build a side-by-side comparison of visible markets, sorted by complement_spread ascending
    _cmp_rows = []
    for _, _cr in df.iterrows():
        _t = _cr.get("ticker", "")
        _yb = _cr.get("yes_bid")
        _ya = _cr.get("yes_ask")
        _as = _cr.get("_arb_score") if "_arb_score" in df.columns else None
        _ca = _cr.get("_compl_arb") if "_compl_arb" in df.columns else None
        try:
            _yb_f = float(_yb) if _yb is not None and pd.notna(_yb) else None
            _ya_f = float(_ya) if _ya is not None and pd.notna(_ya) else None
        except (TypeError, ValueError):
            _yb_f, _ya_f = None, None
        try:
            _no_bid_f = round(1.0 - _ya_f, 4) if _ya_f is not None else None
        except TypeError:
            _no_bid_f = None
        try:
            _compl_sp = round((_ya_f or 0) + (1.0 - (_yb_f or 0)), 4) if _ya_f is not None and _yb_f is not None else None
        except TypeError:
            _compl_sp = None
        try:
            _as_f = float(_as) if _as is not None and pd.notna(_as) else None
        except (TypeError, ValueError):
            _as_f = None
        _cmp_rows.append({
            "TICKER": _t,
            "YES BID": f"{_yb_f*100:.0f}c" if _yb_f is not None else "--",
            "NO BID": f"{_no_bid_f*100:.0f}c" if _no_bid_f is not None else "--",
            "COMPL SPREAD": round(_compl_sp, 4) if _compl_sp is not None else None,
            "ARB SCORE": f"+{_as_f*100:.1f}c" if _as_f is not None and _as_f > 0 else "--",
        })
    if _cmp_rows:
        _cmp_df = pd.DataFrame(_cmp_rows)
        _cmp_df_sorted = _cmp_df.dropna(subset=["COMPL SPREAD"]).sort_values("COMPL SPREAD", ascending=True)
        _cmp_rest = _cmp_df[_cmp_df["COMPL SPREAD"].isna()]
        _cmp_df_final = pd.concat([_cmp_df_sorted, _cmp_rest], ignore_index=True)
        # Format COMPL SPREAD after sorting
        _cmp_df_final["COMPL SPREAD"] = _cmp_df_final["COMPL SPREAD"].apply(
            lambda v: f"{v:.4f}" if v is not None and pd.notna(v) else "--"
        )
        if len(_cmp_df_final) > 1:
            st.markdown("<hr style='margin:0.75rem 0;'>", unsafe_allow_html=True)
            st.markdown(
                f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
                f"MARKET COMPARISON ({len(_cmp_df_final)}) — sorted by complement spread ascending</div>",
                unsafe_allow_html=True,
            )
            _cmp_height = min(400, max(150, len(_cmp_df_final) * 35 + 38))
            st.dataframe(_cmp_df_final, use_container_width=True, height=_cmp_height, hide_index=True)

    # -------------------------------------------------------------- Market detail on click (via selectbox) --------------------------------------------------------------
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("#### MARKET DETAIL")
    if "ticker" in df.columns:
        # Sort so non-SHARD tickers come first (SHARD = KXMVECROSSCATEGORY UUIDs)
        _tickers_sorted = sorted(
            df["ticker"].tolist()[:500],
            key=lambda t: (1 if "SHARD" in str(t).upper() else 0, str(t)),
        )
        sel_ticker = st.selectbox(
            "SELECT MARKET",
            ["--"] + _tickers_sorted,
            label_visibility="collapsed",
        )
        if sel_ticker and sel_ticker != "--":
            _render_market_detail(df, sel_ticker)


def _render_market_detail(df: pd.DataFrame, ticker: str):
    from dashboard.data_layer import get_system_health as _gsh_detail
    _h_det = _gsh_detail()
    _is_sqlite_det = not _h_det.get("db_connected", False) and _h_det.get("db_mode") == "sqlite"

    row = df[df["ticker"] == ticker].iloc[0]

    yes_bid = row.get("yes_bid")
    yes_ask = row.get("yes_ask")
    last    = row.get("last_price")
    vol     = row.get("volume")
    oi      = row.get("open_interest")
    spread  = row.get("spread")
    rel_cnt = row.get("relationship_count") or 0
    has_arb = row.get("has_arb", False)

    # --- Data freshness indicator -----
    _snap_ts = row.get("snapshot_ts") or row.get("updated_at") or row.get("last_updated")
    _snap_age_s = None
    if _snap_ts is not None:
        try:
            _ts_parsed = pd.to_datetime(_snap_ts, utc=True, errors="coerce")
            if pd.notna(_ts_parsed):
                _now_utc = pd.Timestamp.utcnow().tz_localize("UTC")
                if _ts_parsed.tzinfo is None:
                    _ts_parsed = _ts_parsed.tz_localize("UTC")
                _snap_age_s = (_now_utc - _ts_parsed).total_seconds()
        except Exception:
            _snap_age_s = None

    if _snap_age_s is not None:
        _fresh_color = RED if _snap_age_s > 300 else (AMBER if _snap_age_s > 60 else GREEN)
        _age_str = (
            f"{int(_snap_age_s//3600)}h{int((_snap_age_s%3600)//60)}m" if _snap_age_s >= 3600
            else f"{int(_snap_age_s//60)}m{int(_snap_age_s%60)}s" if _snap_age_s >= 60
            else f"{int(_snap_age_s)}s"
        )
        _fresh_label = f"SNAPSHOT: {_age_str} ago"
    else:
        _fresh_color = TEXT3
        _fresh_label = "SNAPSHOT: --"

    st.markdown(
        f"<div style='font-size:0.6rem;font-family:JetBrains Mono,monospace;"
        f"color:{_fresh_color};letter-spacing:0.06em;margin-bottom:0.5rem;'>&#9679; {_fresh_label}</div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("YES BID",      f"{float(yes_bid)*100:.0f}c" if yes_bid is not None and pd.notna(yes_bid) else "--")
    c2.metric("YES ASK",      f"{float(yes_ask)*100:.0f}c" if yes_ask is not None and pd.notna(yes_ask) else "--")
    c3.metric("LAST",         f"{float(last)*100:.0f}c" if last is not None and pd.notna(last) else "--")
    c4.metric("VOLUME",       f"{int(float(vol)):,}" if vol is not None and pd.notna(vol) else "--")
    c5.metric("OPEN INT",     f"{int(float(oi)):,}" if oi is not None and pd.notna(oi) and float(oi) > 0 else "--")

    # --- ARB SCORE for this market -----
    _det_yb = float(yes_bid) if yes_bid is not None and pd.notna(yes_bid) else None
    _det_ya = float(yes_ask) if yes_ask is not None and pd.notna(yes_ask) else None
    _det_sp = float(spread) if spread is not None and pd.notna(spread) else None
    _det_vol = float(vol) if vol is not None and pd.notna(vol) else None
    _det_compl_sp = round(_det_ya + (1.0 - _det_yb), 4) if _det_ya is not None and _det_yb is not None else None
    _det_arb_score = max(0.0, 1.0 - _det_compl_sp) if _det_compl_sp is not None else None

    # Query historical arbs from DB to boost score (cached)
    _hist_arb_count = 0
    _hist_avg_edge = None
    try:
        from dashboard.data_layer import get_ticker_arb_history as _gtah
        _hist_data = _gtah(ticker)
        _hist_arb_count = _hist_data.get("count", 0)
        _hist_avg_edge = _hist_data.get("avg_edge")
    except Exception:
        pass

    # Boost ARB SCORE based on historical arbs
    _hist_boost = min(20, _hist_arb_count * 2) / 100.0  # convert pts to decimal (same scale as _det_arb_score)
    _boosted_arb_score = (_det_arb_score or 0.0) + _hist_boost

    _arb_score_str = f"+{_boosted_arb_score*100:.1f}c" if _boosted_arb_score > 0 else "--"
    st.metric("ARB SCORE", _arb_score_str, help="max(0, 1 - (yes_ask + no_ask)) × 100 + historical arb boost (up to +20pts). Positive = quotes below parity (feed/rounding artifact — Kalshi YES/NO always sum ≥ $1.00 live).")
    st.caption("ARB SCORE = complement gap (0-40pts) + volume rank (0-30pts) + spread tightness (0-30pts) · max=100")

    # Historical Arb Count metric and status message
    st.metric("Historical Detection Count", f"{_hist_arb_count:,}",
              help="Scanner detections for this ticker in the last 30 days (from arbs table; YNC records are feed artifacts).")
    if _hist_arb_count > 0:
        _edge_str = f" · avg edge {_hist_avg_edge:.1f}¢" if _hist_avg_edge is not None else ""
        st.caption(f"📋 {_hist_arb_count} historical scanner detections{_edge_str} (ME/TH arbs are actionable; YNC feed artifacts inflate avg edge)")
    else:
        st.info("No detections for this market yet")

    # --- Spread Quality Score (bid/ask spread as market tightness proxy) -----
    try:
        _mh_bid = float(yes_bid) if yes_bid is not None and pd.notna(yes_bid) else None
        _mh_ask = float(yes_ask) if yes_ask is not None and pd.notna(yes_ask) else None
        if _mh_bid is not None and _mh_ask is not None and _mh_ask > 0:
            _spread_pct = (_mh_ask - _mh_bid) / _mh_ask
            _spread_score = max(0.0, min(1.0, 1.0 - _spread_pct / 0.20))
            _spread_pct_disp = f"{_spread_pct * 100:.1f}%"
            _spread_label = "Tight" if _spread_score > 0.7 else ("Fair" if _spread_score >= 0.4 else "Wide")
            st.metric(
                "Spread Quality",
                f"{int(_spread_score * 100)}/100",
                delta=_spread_label,
                help=(
                    "Bid/ask spread quality score 0–100 based on YES leg spread. "
                    f"Spread = (ask − bid) / ask = {_spread_pct_disp}. "
                    "Score = max(0, 1 − spread_pct/0.20). "
                    "≥70 = Tight, 40–69 = Fair, <40 = Wide."
                ),
            )
            st.progress(_spread_score)
    except Exception:
        pass

    # --- 2-column OI + Volume layout -----
    _oi_col, _vol_col = st.columns(2)

    # ---- LEFT: Open Interest metrics -----
    with _oi_col:
        try:
            _oi_val = None
            if "open_interest" in row.index and pd.notna(row.get("open_interest")) and float(row.get("open_interest")) > 0:
                _oi_val = float(row.get("open_interest"))
            elif "volume" in row.index and pd.notna(row.get("volume")) and float(row.get("volume")) > 0:
                _oi_val = float(row.get("volume"))

            # OI Percentile: rank among all snapshot markets
            _oi_pct_label = ""
            try:
                from dashboard.live_state import get_live_state as _gls_oi
                _snap_all_oi = _gls_oi().snapshot_all()
                if _snap_all_oi and _oi_val is not None:
                    _all_oi_vals = [
                        getattr(q, "open_interest", None) or getattr(q, "volume", None) or 0
                        for q in _snap_all_oi.values()
                    ]
                    _all_oi_vals = [float(v) for v in _all_oi_vals if v]
                    if _all_oi_vals:
                        _rank = sum(1 for v in _all_oi_vals if v <= _oi_val) / len(_all_oi_vals)
                        _pct_int = int(_rank * 100)
                        # "Top X%" = 100 - percentile rank
                        _top_pct = max(1, 100 - _pct_int)
                        _oi_pct_label = f"Top {_top_pct}% by OI"
            except Exception:
                pass

            # OI Trend: rolling session history
            _oi_trend_label = "➡️ Stable"
            if _oi_val is not None:
                _oi_key = f"oi_history_{ticker}"
                _oi_hist = st.session_state.get(_oi_key, [])
                _oi_hist.append(_oi_val)
                if len(_oi_hist) > 20:
                    _oi_hist = _oi_hist[-20:]
                st.session_state[_oi_key] = _oi_hist
                if len(_oi_hist) >= 3:
                    _oh3 = _oi_hist[-3:]
                    if _oh3[2] > _oh3[1] > _oh3[0]:
                        _oi_trend_label = "📈 Rising"
                    elif _oh3[2] < _oh3[1] < _oh3[0]:
                        _oi_trend_label = "📉 Falling"

            if _oi_val is not None:
                _oi_max_ref = 10000.0
                _oi_rel = min(1.0, _oi_val / _oi_max_ref)
                st.metric("OPEN INTEREST", f"{int(_oi_val):,} contracts")
                st.progress(_oi_rel)
                _oi_pct_of_max = int(_oi_rel * 100)
                _oi_detail = f"{_oi_pct_of_max}% of reference max (10k)"
                if _oi_pct_label:
                    _oi_detail += f" · {_oi_pct_label}"
                st.caption(_oi_detail)
                st.caption(f"OI Trend: {_oi_trend_label} (last {len(st.session_state.get(f'oi_history_{ticker}', []))} readings)")
            else:
                st.caption("Open interest data not available")

            # Liquidity Score: OI + spread + volume → 0-100 (tradability focus)
            try:
                _liq_oi_score = min(40.0, (_oi_val / 10000.0) * 40.0) if _oi_val else 0.0
                _liq_sp_score = max(0.0, 30.0 - (_det_sp or 0.10) * 300.0) if _det_sp is not None else 0.0
                _liq_vol_score = 0.0
                if _det_vol is not None and _det_vol > 0:
                    import math as _math_liq
                    _liq_vol_score = min(30.0, _math_liq.log10(max(1.0, _det_vol)) / 5.0 * 30.0)
                _liq_total = int(_liq_oi_score + _liq_sp_score + _liq_vol_score)
                _liq_label = "High" if _liq_total >= 70 else ("Medium" if _liq_total >= 40 else "Low")
                st.metric("LIQUIDITY SCORE", f"{_liq_total}/100", delta=_liq_label,
                          help="0-100 tradability score: OI weight (40pts) + spread tightness (30pts) + volume (30pts). Higher = easier to trade.")
            except Exception:
                pass
        except Exception:
            st.caption("Open interest data not available")

    # ---- RIGHT: Volume metrics -----
    with _vol_col:
        try:
            _vol24_val = row.get("volume_24h") or row.get("volume")
            if _vol24_val is not None and pd.notna(_vol24_val):
                _vol24_int = int(float(_vol24_val))
                st.metric("24H VOLUME", f"{_vol24_int:,} contracts")

                # Volume trend sparkline
                _vol_key = f"vol_history_{ticker}"
                _vol_hist = st.session_state.get(_vol_key, [])
                _vol_hist.append(_vol24_int)
                if len(_vol_hist) > 20:
                    _vol_hist = _vol_hist[-20:]
                st.session_state[_vol_key] = _vol_hist

                if len(_vol_hist) >= 2:
                    import pandas as _pd_spark
                    st.line_chart(_pd_spark.DataFrame({"Volume": _vol_hist}), height=80)
                    if len(_vol_hist) >= 3:
                        _v3 = _vol_hist[-3:]
                        if _v3[2] > _v3[1] > _v3[0]:
                            _vol_trend_label = "📈 Rising"
                        elif _v3[2] < _v3[1] < _v3[0]:
                            _vol_trend_label = "📉 Falling"
                        else:
                            _vol_trend_label = "➡️ Stable"
                    else:
                        _vol_trend_label = "➡️ Stable"
                    st.caption(f"Volume Trend: {_vol_trend_label} (last {len(_vol_hist)} readings this session)")
        except Exception:
            pass

    with st.expander("Score breakdown", expanded=False):
        # Build sub-score components
        _breakdown_names: list[str] = []
        _breakdown_vals: list[float] = []

        # 1. Complement spread proximity (0-40 pts): how close is yes_ask + no_ask to $1?
        if _det_compl_sp is not None:
            # Score 0-40 based on proximity: perfect=1.00 → 40pts, 1.10 → 0pts
            _cs_score = max(0.0, min(40.0, (1.10 - _det_compl_sp) / 0.10 * 40.0))
            _breakdown_names.append(f"complement_spread ({_det_compl_sp:.4f})")
            _breakdown_vals.append(round(_cs_score, 1))
        else:
            _breakdown_names.append("complement_spread (n/a)")
            _breakdown_vals.append(0.0)

        # 2. Volume score (0-30 pts)
        if _det_vol is not None and _det_vol > 0:
            import math as _math
            _vol_score = min(30.0, _math.log10(max(1.0, _det_vol)) / 5.0 * 30.0)
            _breakdown_names.append(f"volume_score ({int(_det_vol):,})")
            _breakdown_vals.append(round(_vol_score, 1))
        else:
            _breakdown_names.append("volume_score (n/a)")
            _breakdown_vals.append(0.0)

        # 3. Liquidity / spread tightness (0-30 pts)
        if _det_sp is not None and _det_sp >= 0:
            _liq_score = max(0.0, 30.0 - _det_sp * 300.0)  # 0¢ spread → 30, 10¢ → 0
            _breakdown_names.append(f"liquidity_score (spr={_det_sp*100:.1f}c)")
            _breakdown_vals.append(round(_liq_score, 1))
        else:
            _breakdown_names.append("liquidity_score (n/a)")
            _breakdown_vals.append(0.0)

        if any(v > 0 for v in _breakdown_vals):
            _fig_bd = go.Figure(go.Bar(
                x=_breakdown_vals,
                y=_breakdown_names,
                orientation="h",
                marker_color=["#4ade80", "#60a5fa", "#f59e0b"],
                text=[f"{v:.1f}" for v in _breakdown_vals],
                textposition="outside",
            ))
            _fig_bd.update_layout(
                margin=dict(l=10, r=40, t=10, b=10),
                height=160,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(range=[0, 45], showgrid=False, zeroline=False, tickfont=dict(size=10)),
                yaxis=dict(showgrid=False, tickfont=dict(size=10)),
                showlegend=False,
                font=dict(family="JetBrains Mono, monospace", size=10),
            )
            st.plotly_chart(_fig_bd, use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("Score = complement spread proximity + volume percentile + liquidity depth")

    # --- Market health score -----
    health_score, health_components = _compute_market_health(
        yes_bid=yes_bid, yes_ask=yes_ask, spread=spread,
        volume=vol, open_interest=oi, rel_count=rel_cnt, has_arb=has_arb,
        sqlite_mode=_is_sqlite_det,
    )
    health_color = GREEN if health_score >= 70 else (AMBER if health_score >= 40 else RED)

    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};
padding:0.75rem 1rem;border-radius:3px;margin-top:0.75rem;
display:flex;gap:2rem;align-items:center;'>
<div style='min-width:6rem;'>
<div style='font-size:0.55rem;letter-spacing:0.1em;color:{TEXT3};
text-transform:uppercase;font-family:Inter,sans-serif;'>
HEALTH SCORE
</div>
<div style='font-family:JetBrains Mono,monospace;font-size:1.5rem;
color:{health_color};font-weight:700;'>{health_score}</div>
<div style='font-size:0.55rem;color:{TEXT3};'>/ 100</div>
</div>
<div style='flex:1;display:flex;flex-wrap:wrap;gap:0.5rem;'>
{''.join(
f"<span style='font-size:0.6rem;background:{BORDER};padding:2px 6px;border-radius:2px;"
f"font-family:JetBrains Mono,monospace;color:{TEXT2};'>{c}</span>"
for c in health_components
)}
</div>
</div>""",
        unsafe_allow_html=True,
    )
    st.caption(
        "Health score 0–100: BID/ASK present (25 pts) + spread tightness (25 pts: ≤2¢=25, ≤5¢=15, ≤10¢=8) "
        "+ volume (25 pts: ≥10k=25, ≥1k=15, ≥100=8) + relationship count (15 pts) + live arb flag (10 pts bonus). "
        "≥70 = healthy (green), 40–69 = moderate (amber), <40 = thin/illiquid (red)."
    )

    # --- RELATED MARKETS section -----
    try:
        _event_prefix = str(ticker).split("-")[0] if ticker else ""
        if _event_prefix:
            _related = df[
                (df["ticker"].str.startswith(_event_prefix, na=False)) &
                (df["ticker"] != ticker)
            ].copy()
            if not _related.empty:
                _rel_rows = []
                for _, _rr in _related.iterrows():
                    _rt = _rr.get("ticker", "")
                    _ryb = _rr.get("yes_bid")
                    _rya = _rr.get("yes_ask")
                    try:
                        _ryb_f = float(_ryb) if _ryb is not None and pd.notna(_ryb) else None
                        _rya_f = float(_rya) if _rya is not None and pd.notna(_rya) else None
                        _rnob_f = round(1.0 - _rya_f, 4) if _rya_f is not None else None
                        _rcompl = round(_rya_f + (1.0 - _ryb_f), 4) if _rya_f is not None and _ryb_f is not None else None
                    except (TypeError, ValueError):
                        _ryb_f = _rya_f = _rnob_f = _rcompl = None
                    _rel_rows.append({
                        "TICKER": _rt,
                        "YES BID": f"{_ryb_f*100:.0f}c" if _ryb_f is not None else "--",
                        "NO BID": f"{_rnob_f*100:.0f}c" if _rnob_f is not None else "--",
                        "COMPL COST": _rcompl,
                    })
                _rel_df = pd.DataFrame(_rel_rows)
                _rel_df_sorted = _rel_df.dropna(subset=["COMPL COST"]).sort_values("COMPL COST").head(5)
                _rel_rest = _rel_df[_rel_df["COMPL COST"].isna()].head(max(0, 5 - len(_rel_df_sorted)))
                _rel_final = pd.concat([_rel_df_sorted, _rel_rest], ignore_index=True)
                _rel_final["COMPL COST"] = _rel_final["COMPL COST"].apply(
                    lambda v: f"{v:.4f}" if v is not None and pd.notna(v) else "--"
                )
                if not _rel_final.empty:
                    st.markdown("<hr style='margin:0.75rem 0;'>", unsafe_allow_html=True)
                    st.markdown(
                        f"<div style='font-size:0.6rem;letter-spacing:0.1em;text-transform:uppercase;"
                        f"color:{TEXT3};font-family:Inter,sans-serif;margin-bottom:0.4rem;'>"
                        f"RELATED MARKETS ({len(_rel_final)} shown)</div>",
                        unsafe_allow_html=True,
                    )
                    st.dataframe(_rel_final, use_container_width=True, hide_index=True)
                    st.caption("Other markets in the same event — CE (collectively exhaustive) scanning currently disabled")
    except Exception:
        pass

    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};
padding:0.75rem 1rem;border-radius:3px;margin-top:0.5rem;'>
<div style='font-size:0.6rem;letter-spacing:0.08em;color:{TEXT3};text-transform:uppercase;
font-family:Inter,sans-serif;'>TITLE</div>
<div style='font-family:JetBrains Mono,monospace;font-size:0.82rem;
color:{TEXT};margin-top:2px;'>
{row.get("title", "--")}
</div>
<div style='margin-top:8px;font-size:0.6rem;letter-spacing:0.08em;
color:{TEXT3};text-transform:uppercase;'>
{row.get("event_ticker") or ""} &nbsp;&middot;&nbsp;
{row.get("category") or ""} &nbsp;&middot;&nbsp;
Expires: {str(row.get("close_time") or "")[:10] or "--"} &nbsp;&middot;&nbsp;
Relationships: {int(float(rel_cnt)) if pd.notna(rel_cnt) else 0}
</div>
</div>""",
        unsafe_allow_html=True,
    )


def _compute_market_health(
    yes_bid, yes_ask, spread, volume, open_interest, rel_count, has_arb,
    sqlite_mode: bool = False,
):
    """
Compute a 0-100 market health score from available market data.
Returns (score, [component_labels]).
"""
    score = 0
    components = []

    # 1. Has bid/ask (25pts)
    if pd.notna(yes_bid) and pd.notna(yes_ask) and float(yes_bid) > 0 and float(yes_ask) > 0:
        score += 25
        components.append("BID/ASK ✓")
    else:
        components.append("NO QUOTE ✗")

    # 2. Tight spread (25pts)
    if pd.notna(spread):
        sp = float(spread)
        if sp <= 0.02:
            score += 25
            components.append(f"TIGHT {sp*100:.0f}c")
        elif sp <= 0.05:
            score += 15
            components.append(f"MODERATE {sp*100:.0f}c")
        elif sp <= 0.10:
            score += 8
            components.append(f"WIDE {sp*100:.0f}c")
        else:
            components.append(f"V-WIDE {sp*100:.0f}c")

    # 3. Volume (25pts)
    if pd.notna(volume) and volume:
        v = float(volume)
        if v >= 10000:
            score += 25
            components.append(f"VOL {int(v):,}")
        elif v >= 1000:
            score += 15
            components.append(f"VOL {int(v):,}")
        elif v >= 100:
            score += 8
            components.append(f"LOW-VOL {int(v):,}")
        else:
            components.append(f"MIN-VOL {int(v):,}")

    # 4. Has relationships (15pts)
    rc = int(float(rel_count)) if pd.notna(rel_count) else 0
    if rc > 0:
        score += 15
        components.append(f"RELS:{rc}")

    # 5. Has live arb (10pts bonus — not always good, just notable)
    if has_arb:
        score += 10
        components.append("ARB ACTIVE")

    return min(100, score), components


