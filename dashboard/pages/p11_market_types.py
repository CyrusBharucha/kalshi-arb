"""dashboard/pages/p11_market_types.py -- Gate 0 Market Type Classifications"""
from __future__ import annotations

import streamlit as st
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

GREEN = "#22c55e"
AMBER = "#f59e0b"
RED   = "#ef4444"
BLUE  = "#3b82f6"
TEXT3 = "#64748b"


@st.cache_data(ttl=60)
def _load_classifications() -> pd.DataFrame:
    try:
        from dashboard.db import get_db_conn
        conn = get_db_conn()
        if conn is None:
            return pd.DataFrame()
        with conn.cursor() as c:
            c.execute("""
                SELECT series_prefix, market_type, ce_eligible, me_eligible,
                       status, auto_reason, title_example, market_count,
                       classified_by, first_seen_at, last_seen_at
                FROM event_series_classifications
                ORDER BY last_seen_at DESC
            """)
            cols = [d[0] for d in c.description]
            return pd.DataFrame(c.fetchall(), columns=cols)
    except Exception:
        return pd.DataFrame()


def _ce_dot(v: bool) -> str:
    return f"<span style='color:{GREEN};'>● CE</span>" if v else "<span style='color:#4b5563;'>○</span>"


def _me_dot(v: bool) -> str:
    return f"<span style='color:{BLUE};'>● ME</span>" if v else "<span style='color:#4b5563;'>○</span>"


def _type_pill(t: str) -> str:
    colors = {
        "binary_race":         GREEN,
        "multi_race_closed":   GREEN,
        "price_range_bins":    GREEN,
        "rate_decision_bins":  GREEN,
        "sports_match_winner": GREEN,
        "tournament_champion": GREEN,
        "award_nominees":      RED,
        "next_role":           RED,
        "who_will_general":    RED,
        "threshold_levels":    RED,
        "sports_spread_total": RED,
        "binary_event":        "#6b7280",
        "combo_market":        AMBER,
        "unknown":             AMBER,
    }
    c = colors.get(t, "#6b7280")
    return (
        f"<span style='background:{c}22;color:{c};border:1px solid {c}44;"
        f"border-radius:3px;padding:1px 5px;font-size:0.65rem;'>{t}</span>"
    )


def render():
    st.markdown(
        "<div style='font-size:1rem;font-weight:600;letter-spacing:0.06em;"
        "text-transform:uppercase;margin-bottom:0.4rem;'>Gate 0 — Market Type Classifications</div>"
        "<p style='font-size:0.72rem;color:#64748b;margin-bottom:1rem;'>"
        "Every Kalshi event series is classified by market type. CE/ME arb eligibility "
        "derives from the type. New unseen series are blocked (Gate 0) until classified. "
        "CE-eligible types: binary_race, tournament_champion, multi_race_closed.</p>",
        unsafe_allow_html=True,
    )

    df = _load_classifications()

    if df.empty:
        st.warning("Classification table unavailable — check DB connection.")
        return

    total     = len(df)
    ce_ok     = int(df["ce_eligible"].sum())
    unknown   = int((df["market_type"] == "unknown").sum())
    unclassed = int((df["status"] == "UNCLASSIFIED").sum())

    k1, k2, k3, k4 = st.columns(4)
    for col, label, val, color in [
        (k1, "SERIES CLASSIFIED", total,     "#E2E8F0"),
        (k2, "CE ELIGIBLE",       ce_ok,     GREEN),
        (k3, "UNKNOWN / BLOCKED", unknown,   AMBER if unknown else TEXT3),
        (k4, "UNCLASSIFIED",      unclassed, RED if unclassed else TEXT3),
    ]:
        col.markdown(
            f"<div style='background:#0F172A;border:1px solid #1E293B;border-radius:4px;"
            f"padding:0.6rem 0.8rem;'>"
            f"<div style='font-size:0.58rem;color:#64748b;text-transform:uppercase;"
            f"letter-spacing:0.06em;margin-bottom:2px;'>{label}</div>"
            f"<div style='font-size:1.3rem;font-weight:700;color:{color};font-family:JetBrains Mono,monospace;'>"
            f"{val}</div></div>",
            unsafe_allow_html=True,
        )

    if unclassed > 0:
        st.warning(
            f"⚠️ {unclassed} series are UNCLASSIFIED — Gate 0 will block CE/ME arbs for these until classified. "
            "Run `scripts/classify_event_series.py` to pick up new market types.",
            icon=None,
        )

    st.markdown("<div style='margin:0.75rem 0 0.5rem;'></div>", unsafe_allow_html=True)

    # Filters
    fc1, fc2, fc3 = st.columns([2, 2, 3])
    type_opts   = ["ALL"] + sorted(df["market_type"].dropna().unique().tolist())
    status_opts = ["ALL"] + sorted(df["status"].dropna().unique().tolist())
    sel_type    = fc1.selectbox("Market type", type_opts, key="p11_type")
    sel_status  = fc2.selectbox("Status", status_opts, key="p11_status")
    search      = fc3.text_input("Search prefix / title", "", key="p11_search")

    fdf = df.copy()
    if sel_type   != "ALL": fdf = fdf[fdf["market_type"] == sel_type]
    if sel_status != "ALL": fdf = fdf[fdf["status"] == sel_status]
    if search:
        mask = (
            fdf["series_prefix"].str.contains(search, case=False, na=False)
            | fdf["title_example"].fillna("").str.contains(search, case=False, na=False)
        )
        fdf = fdf[mask]

    st.markdown(
        f"<div style='font-size:0.68rem;color:{TEXT3};margin-bottom:0.5rem;'>"
        f"Showing {len(fdf):,} of {total:,} series</div>",
        unsafe_allow_html=True,
    )

    rows_html = ""
    for _, row in fdf.head(500).iterrows():
        rows_html += (
            f"<tr style='border-bottom:1px solid #1E293B;'>"
            f"<td style='font-family:JetBrains Mono,monospace;font-size:0.7rem;"
            f"padding:4px 8px;white-space:nowrap;'>{row['series_prefix']}</td>"
            f"<td style='padding:4px 8px;'>{_type_pill(str(row['market_type']))}</td>"
            f"<td style='text-align:center;padding:4px 8px;'>{_ce_dot(bool(row['ce_eligible']))}</td>"
            f"<td style='text-align:center;padding:4px 8px;'>{_me_dot(bool(row['me_eligible']))}</td>"
            f"<td style='color:#94a3b8;font-size:0.7rem;padding:4px 8px;max-width:300px;"
            f"overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>"
            f"{str(row['title_example'] or '')[:80]}</td>"
            f"<td style='color:{TEXT3};font-size:0.65rem;padding:4px 8px;"
            f"max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>"
            f"{str(row['auto_reason'] or '')}</td>"
            f"<td style='color:{TEXT3};font-size:0.65rem;padding:4px 8px;'>{row['classified_by']}</td>"
            f"</tr>"
        )

    st.markdown(
        f"<div style='overflow-x:auto;'>"
        f"<table style='width:100%;border-collapse:collapse;'>"
        f"<thead><tr style='border-bottom:2px solid #1E293B;'>"
        f"<th style='text-align:left;padding:4px 8px;font-size:0.62rem;color:#64748b;"
        f"font-weight:500;letter-spacing:0.05em;'>PREFIX</th>"
        f"<th style='text-align:left;padding:4px 8px;font-size:0.62rem;color:#64748b;"
        f"font-weight:500;letter-spacing:0.05em;'>TYPE</th>"
        f"<th style='text-align:center;padding:4px 8px;font-size:0.62rem;color:#64748b;"
        f"font-weight:500;letter-spacing:0.05em;'>CE</th>"
        f"<th style='text-align:center;padding:4px 8px;font-size:0.62rem;color:#64748b;"
        f"font-weight:500;letter-spacing:0.05em;'>ME</th>"
        f"<th style='text-align:left;padding:4px 8px;font-size:0.62rem;color:#64748b;"
        f"font-weight:500;letter-spacing:0.05em;'>TITLE</th>"
        f"<th style='text-align:left;padding:4px 8px;font-size:0.62rem;color:#64748b;"
        f"font-weight:500;letter-spacing:0.05em;'>REASON</th>"
        f"<th style='text-align:left;padding:4px 8px;font-size:0.62rem;color:#64748b;"
        f"font-weight:500;letter-spacing:0.05em;'>BY</th>"
        f"</tr></thead>"
        f"<tbody>"
        + rows_html
        + f"</tbody></table></div>",
        unsafe_allow_html=True,
    )

    if len(fdf) > 500:
        st.caption(f"Showing first 500 of {len(fdf):,}. Use filters to narrow.")

    # Type breakdown
    st.markdown(
        "<div style='margin:1.5rem 0 0.5rem;font-size:0.72rem;font-weight:600;"
        "letter-spacing:0.06em;text-transform:uppercase;color:#94A3B8;'>Type Breakdown</div>",
        unsafe_allow_html=True,
    )
    breakdown = (
        df.groupby("market_type")
        .agg(count=("series_prefix", "count"), ce=("ce_eligible", "sum"))
        .reset_index()
        .sort_values("count", ascending=False)
    )
    st.dataframe(breakdown, use_container_width=True, hide_index=True)
