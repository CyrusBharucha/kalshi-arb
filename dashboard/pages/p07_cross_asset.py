"""
dashboard/pages/p07_cross_asset.py — Cross-Asset Analysis

Sections:
1. Data-driven spread chart (Kalshi vs traditional market, requires DB)
2. Z-score chart for spread mean-reversion signal
3. BOC Rate Probability Calculator (live, requires no DB)
4. FX Implied Probability Calculator (live, requires no DB)
5. Methodology tabs (hypotheses, data sources, limitations)
"""
from __future__ import annotations
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.boc_panel import render_boc_panel
from dashboard.data_layer import (
    get_cross_asset_data, get_cross_asset_signals,
    get_top_markets_by_volume, get_market_price_history,
    get_external_market_prices, get_latest_external_prices,
)
from dashboard.styles import plotly_dark_layout, GREEN, RED, AMBER, BLUE, CYAN, TEXT, TEXT2, TEXT3, PANEL, BORDER


@st.cache_data(ttl=300)
def _fetch_rates_vix() -> dict:
    """Fetch live US 2Y, US 10Y, CAD 10Y yields and VIX from Yahoo Finance."""
    import requests
    tickers = {"^TNX": "us10y", "^IRX": "us2y", "^VIX": "vix"}
    result = {"us2y": 4.25, "us10y": 4.45, "cad10y": 3.85, "vix": 18.0}  # fallback
    try:
        for sym, key in tickers.items():
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1d"
            r = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                data = r.json()
                price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                result[key] = round(float(price), 2)
        # CAD 10Y
        url = "https://query1.finance.yahoo.com/v8/finance/chart/CA10YT%3DXX?interval=1d&range=1d"
        r = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json()
            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            result["cad10y"] = round(float(price), 2)
    except Exception:
        pass  # use fallbacks
    return result


_ASSETS = [
    ("CORRA",    "Canadian Overnight Repo Rate Average"),
    ("BOC_RATE", "Bank of Canada Policy Rate"),
    ("CAGB_2Y",  "Canadian 2-Year Government Bond"),
    ("CADUSD",   "CAD/USD Exchange Rate"),
    ("WTI_SPOT", "WTI Crude Oil (spot)"),
    ("CA_CPI",   "Canadian CPI YoY"),
    ("SP500",    "S&P 500 Index"),
    ("VIX",      "CBOE Volatility Index"),
]


# ---------------------------------------------------------------------------
# Live cross-asset signal scanner
# ---------------------------------------------------------------------------

def _parse_boc_ticker(ticker: str):
    """
Parse a KXBOC-{YYMM}-T{rate} ticker.
Returns (target_rate_decimal, date_str) or None on failure.
e.g. 'KXBOC-26SEP-T2.25' → (0.0225, '26SEP')
"""
    import re
    m = re.match(r"KXBOC-(\d{2}[A-Z]{3})-T([\d.]+)", ticker.upper())
    if not m:
        return None
    date_str  = m.group(1)           # e.g. '26SEP'
    rate_pct  = float(m.group(2))    # e.g. 2.25
    return rate_pct / 100.0, date_str


def _parse_fed_ticker(ticker: str):
    """
Parse a KXFED-{YYMM}-T{rate} ticker.
Returns (target_rate_decimal, date_str) or None on failure.
"""
    import re
    m = re.match(r"KXFED-(\d{2}[A-Z]{3})-T([\d.]+)", ticker.upper())
    if not m:
        return None
    date_str  = m.group(1)
    rate_pct  = float(m.group(2))
    return rate_pct / 100.0, date_str


def _days_to_month(yymm_str: str) -> int:
    """
Convert '26SEP' → days from today to the actual BOC/FOMC meeting date in that month.
Falls back to first of the month if no known meeting date is found.
Returns 30 as default fallback.
"""
    from datetime import datetime, timezone, date
    _MONTH = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    try:
        year  = 2000 + int(yymm_str[:2])
        month = _MONTH.get(yymm_str[2:5].upper(), 9)
        today = datetime.now(timezone.utc).date()
        # Use actual BOC meeting dates when available (more accurate for OIS model)
        try:
            from dashboard.boc_panel import BOC_MEETING_DATES
            matches = [d for d in BOC_MEETING_DATES if d.year == year and d.month == month]
            if matches:
                target = min(matches)
                return max(1, (target - today).days)
        except Exception:
            pass
        # Fall back to first of the month
        target = date(year, month, 1)
        return max(1, (target - today).days)
    except Exception:
        return 30


def _render_live_ca_signals(ext_prices: dict, rate_live: dict) -> None:
    """
Show live cross-asset relative value signals.

For each KXBOC-*/KXFED-* market in the live WS stream:
- Parse the target rate from the ticker
- Compute model probability using CORRA / Fed Funds OIS
- Compare to the live Kalshi mid
- Flag where |gap| >= 5%
"""
    if not rate_live:
        return

    try:
        from analysis.probability_engine import boc_rate_implied_probability
    except ImportError:
        if rate_live:
            st.caption("⚠ cross_asset.probability_engine not available — OIS model signals disabled. Install the cross-asset pipeline to enable live CE/OIS signals.")
        return

    # Current CORRA (used as OIS rate for BOC model)
    corra_pct = ext_prices.get("CORRA") or ext_prices.get("BOC_RATE")
    if not corra_pct:
        st.caption("⚠ CORRA/BOC rate unavailable from external data feed — OIS-implied probability signals disabled. Data is fetched from the Bank of Canada Valet API; check connectivity or wait for the next ingestion cycle.")
        return
    # Current Fed Funds OIS approximation (use SOFR or Fed effective rate)
    # No hardcoded fallback — if unavailable we skip KXFED rows rather than use stale data
    ff_pct = ext_prices.get("SOFR") or ext_prices.get("FED_FUNDS") or None

    rows = []
    for t, q in sorted(rate_live.items()):
        bid = q.yes_bid if q.yes_bid and q.yes_bid > 0 else None
        ask = q.yes_ask if q.yes_ask and q.yes_ask > 0 else None
        if not bid or not ask:
            continue
        mid = (bid + ask) / 2.0

        is_boc = t.upper().startswith("KXBOC")
        is_fed = t.upper().startswith("KXFED")
        parsed = _parse_boc_ticker(t) if is_boc else _parse_fed_ticker(t) if is_fed else None
        if not parsed:
            continue

        target_rate, date_str = parsed
        days_ahead = _days_to_month(date_str)

        if is_boc and corra_pct:
            ois_rate = float(corra_pct) / 100.0
        elif is_fed and ff_pct:
            ois_rate = float(ff_pct) / 100.0
        else:
            continue  # skip if we have no live OIS rate — never use stale fallback
        if ois_rate <= 0:
            continue

        try:
            # Determine direction: if target < OIS → "cut" market, else "hike"
            if target_rate < ois_rate - 0.001:
                direction = "cut"
            elif target_rate > ois_rate + 0.001:
                direction = "hike"
            else:
                direction = "hold"
            model_prob = boc_rate_implied_probability(
                current_ois_rate=ois_rate,
                meeting_date_days_ahead=days_ahead,
                target_rate=target_rate,
                direction=direction,
            )
        except Exception:
            continue

        # Skip signals where model has no resolution (< 1% or > 99%) —
        # single-meeting OIS model is not calibrated for large multi-meeting moves.
        if model_prob < 0.01 or model_prob > 0.99:
            continue

        gap = mid - model_prob
        flagged = abs(gap) >= 0.05

        # Confidence: how far model is from 50/50 (0% = pure coin-flip, 100% = certain)
        confidence = abs(model_prob - 0.5) * 200.0  # maps [0.5→0%, 0→/1→100%]

        if gap > 0.05:
            signal = "SELL YES (overprice)"
            sig_color = "#EF4444"
        elif gap < -0.05:
            signal = "BUY YES (underprice)"
            sig_color = "#22C55E"
        else:
            signal = "FAIR"
            sig_color = "#64748B"

        rows.append({
            "ticker":     t,
            "target":     f"{target_rate*100:.2f}%",
            "direction":  direction.upper(),
            "live_mid":   mid,
            "model_prob": model_prob,
            "confidence": confidence,
            "gap":        gap,
            "signal":     signal,
            "sig_color":  sig_color,
            "flagged":    flagged,
            "ois_used":   f"{ois_rate*100:.2f}%",
        })

    with st.expander("📖 How to Read OIS Signals"):
        st.markdown("""
    **Positive divergence (Kalshi > OIS):** Market implies a higher cut probability than OIS swap pricing.
    This may signal retail optimism or a pricing inefficiency — potential arb if divergence >10pp.

    **Negative divergence (Kalshi < OIS):** OIS markets are more dovish than Kalshi prediction markets.
    Institutional traders may be pricing in cuts that retail hasn't yet priced.

    **Divergence >10pp:** Statistically significant — consider taking a position on the lower-priced side.

    **Divergence <5pp:** Noise level — markets are in rough agreement; no actionable signal.
    """)

    if not rows:
        st.info("No OIS divergence signals above threshold. Market prices align with OIS-implied probabilities.")
        return

    # Save all rows before slider filter for fixed-threshold alerts and top divergence metric
    _all_rows_before_filter = list(rows)
    _top_div_pp = max(abs(r["gap"]) * 100 for r in _all_rows_before_filter) if _all_rows_before_filter else 0.0
    _n_above_10 = sum(1 for r in _all_rows_before_filter if abs(r["gap"]) * 100 > 10)
    _n_above_5  = sum(1 for r in _all_rows_before_filter if abs(r["gap"]) * 100 > 5)

    # Top Divergence metric (always based on full unfiltered scan)
    st.metric("Top Divergence", f"{_top_div_pp:.1f}pp", help="Maximum |Kalshi − OIS| across all scanned markets")

    # Fixed-threshold alerts (independent of slider position)
    if _n_above_10 > 0:
        st.error(f"🚨 High divergence detected: {_n_above_10} market(s) show >10pp Kalshi-OIS gap")
    elif _n_above_5 > 0:
        st.warning(f"⚡ Moderate divergence: {_n_above_5} market(s) show >5pp gap")

    # --- Divergence threshold slider -----
    threshold = st.slider(
        "Min divergence threshold (pp)", min_value=1, max_value=20, value=5, step=1,
        key="p07_min_div",
    )
    rows = [r for r in rows if abs(r["gap"]) * 100 >= threshold]
    st.caption(f"Showing signals with |Kalshi − OIS| ≥ {threshold}pp")

    if not rows:
        st.info(f"No signals meet the {threshold}pp divergence threshold.")
        return

    # Sort by CONF (= |gap|) descending
    rows.sort(key=lambda r: abs(r["gap"]), reverse=True)

    flagged_count = sum(1 for r in rows if r["flagged"])
    header_color  = AMBER if flagged_count > 0 else TEXT3

    rows_html = ""
    for r in rows:
        gap_str   = f"{r['gap']*100:+.1f}pp"
        mid_str   = f"{r['live_mid']*100:.1f}c"
        model_str = f"{r['model_prob']*100:.1f}c"
        conf_str  = f"{abs(r['gap'])*100:.1f}pp"
        # Confidence colour: green >= 10pp, amber >= 5pp, dim otherwise
        conf_col  = GREEN if abs(r["gap"]) >= 0.10 else (AMBER if abs(r["gap"]) >= 0.05 else TEXT3)
        flag_str  = "⚑" if r["flagged"] else ""
        sig_col   = r["sig_color"]
        sig_txt   = r["signal"]
        rows_html += (
            f"<tr>"
            f"<td style='color:{TEXT};font-family:JetBrains Mono,monospace;padding:4px 8px;font-size:0.7rem;'>{r['ticker']}</td>"
            f"<td style='color:{TEXT2};padding:4px 8px;font-size:0.7rem;'>{r['direction']}</td>"
            f"<td style='color:{TEXT2};padding:4px 8px;font-size:0.7rem;'>{r['target']}</td>"
            f"<td style='color:{CYAN};font-family:JetBrains Mono,monospace;padding:4px 8px;text-align:right;font-size:0.7rem;'>{mid_str}</td>"
            f"<td style='color:{BLUE};font-family:JetBrains Mono,monospace;padding:4px 8px;text-align:right;font-size:0.7rem;'>{model_str}</td>"
            f"<td style='color:{conf_col};font-family:JetBrains Mono,monospace;padding:4px 8px;text-align:right;font-size:0.7rem;'>{conf_str}</td>"
            f"<td style='color:{sig_col};font-family:JetBrains Mono,monospace;padding:4px 8px;text-align:right;font-weight:600;font-size:0.7rem;'>{gap_str}</td>"
            f"<td style='color:{sig_col};padding:4px 8px;font-size:0.7rem;'>{sig_txt}</td>"
            f"<td style='color:{AMBER};padding:4px 8px;font-size:0.7rem;'>{flag_str}</td>"
            f"</tr>"
        )

    try:
        ois_note = f"BOC OIS: {float(corra_pct):.2f}%" if corra_pct is not None else "BOC OIS: unavailable"
    except (TypeError, ValueError):
        ois_note = "BOC OIS: unavailable"
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:4px solid {header_color};
padding:0.75rem 1rem;border-radius:3px;margin-bottom:0.75rem;'>
<div style='font-size:0.62rem;letter-spacing:0.08em;color:{header_color};
text-transform:uppercase;margin-bottom:8px;'>
⚡ LIVE CROSS-ASSET SIGNALS — {len(rows)} markets · {flagged_count} flagged (|gap|≥5%)
</div>
<table style='width:100%;border-collapse:collapse;'>
<thead>
<tr style='border-bottom:1px solid {BORDER};'>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:left;padding:4px 8px;font-size:0.65rem;'>TICKER</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:left;padding:4px 8px;font-size:0.65rem;'>DIR</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:left;padding:4px 8px;font-size:0.65rem;'>TARGET</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:right;padding:4px 8px;font-size:0.65rem;'>LIVE MID</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:right;padding:4px 8px;font-size:0.65rem;'>OIS MODEL</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:right;padding:4px 8px;font-size:0.65rem;'>CONF</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:right;padding:4px 8px;font-size:0.65rem;'>GAP</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:left;padding:4px 8px;font-size:0.65rem;'>SIGNAL</th>
<th style='color:{TEXT3};padding:4px 8px;font-size:0.65rem;'>FLAG</th>
</tr>
</thead>
<tbody>{rows_html}</tbody>
</table>
<div style='font-size:0.62rem;color:{TEXT3};font-family:JetBrains Mono,monospace;
margin-top:8px;border-top:1px solid {BORDER};padding-top:6px;'>
Model: lognormal OIS implied prob · {ois_note} ·
Gap = Kalshi mid − OIS model · CONF = |Kalshi mid − OIS model| in percentage points ·
Flags at |gap| ≥ 5% · RELATIVE VALUE only — not executable cross-venue arb
</div>
</div>""",
        unsafe_allow_html=True,
    )


def _render_ce_sum_check(rate_live: dict) -> None:
    """Group KXFED-*/KXBOC-* by meeting date; YES mids should sum to ~$1 (CE markets)."""
    if not rate_live:
        return
    import re, collections
    groups: dict = collections.defaultdict(list)
    for ticker, q in rate_live.items():
        bid = q.yes_bid if q.yes_bid and q.yes_bid > 0 else None
        ask = q.yes_ask if q.yes_ask and q.yes_ask > 0 else None
        if bid is None or ask is None:
            continue
        mid = (bid + ask) / 2.0
        m = re.match(r"(KX(?:FED|BOC)-\d{2}[A-Z]{3})", ticker.upper())
        if m:
            groups[m.group(1)].append(mid)
    if not groups:
        return
    rows = []
    for meeting, mids in sorted(groups.items()):
        total = sum(mids)
        dev = total - 1.0
        flag = "CE ARB" if total < 0.98 else ("OVER" if total > 1.02 else "OK")
        rows.append({"MEETING": meeting, "OUTCOMES": len(mids),
                     "SUM OF MIDS ($)": f"{total:.3f}",
                     "DEV FROM $1": f"{dev*100:+.1f}c", "STATUS": flag})
    import pandas as _pd_ce
    _df_ce = _pd_ce.DataFrame(rows)
    _flagged = sum(1 for r in rows if r["STATUS"] != "OK")
    _col = RED if _flagged else GREEN
    st.markdown(
        f"<div style='font-size:0.62rem;letter-spacing:0.08em;color:{_col};"
        f"text-transform:uppercase;margin-bottom:4px;'>"
        f"{'🔴 ' if _flagged else ''}CE SUM CHECK — {len(rows)} meetings · {_flagged} flagged</div>",
        unsafe_allow_html=True,
    )
    st.dataframe(_df_ce, use_container_width=True, hide_index=True,
                 height=min(160, len(_df_ce) * 38 + 42))
    st.caption("CE markets: YES mids for all rate outcomes at the same meeting should sum to ~$1. "
               "< $0.98 = potential buy-all-YES arb. > $1.02 = book over-priced.")


def _render_yes_no_complement_spread(live_state) -> None:
    """
    For every live market with a valid two-sided quote, compute the implied
    YES + NO round-trip cost (yes_ask + no_ask_implied = yes_ask + 1 - yes_bid).
    Should be > 1.0 (bid-ask spread > 0). Values < 1.0 indicate a crossed/inverted
    market — a riskless two-leg arb on the SAME contract. Displays top 20 by lowest
    combined ask and flags any crossings.
    """
    try:
        quotes = live_state.snapshot_all()
    except Exception:
        return
    rows = []
    for ticker, q in quotes.items():
        bid = q.yes_bid if q.yes_bid and q.yes_bid > 0 else None
        ask = q.yes_ask if q.yes_ask and q.yes_ask > 0 else None
        if bid is None or ask is None:
            continue
        no_ask_implied = 1.0 - bid          # cost to buy NO = 1 - YES_bid
        combined = ask + no_ask_implied     # total cost to own both legs (should be >= 1.0)
        spread_c = round((combined - 1.0) * 100, 2)   # excess over par in cents
        crossed = combined < 1.0
        rows.append({"TICKER": ticker, "YES ASK": f"{ask*100:.0f}c",
                     "NO ASK (impl)": f"{no_ask_implied*100:.0f}c",
                     "COMBINED": f"{combined*100:.1f}c",
                     "EXCESS (c)": f"{spread_c:+.1f}",
                     "⚑": "CROSSED" if crossed else ""})
    if not rows:
        return
    rows.sort(key=lambda r: float(r["COMBINED"].rstrip("c")))
    import pandas as _pd_yn
    _df = _pd_yn.DataFrame(rows[:20])
    _flagged = sum(1 for r in rows if r["⚑"])
    _hdr_color = RED if _flagged else TEXT3
    st.markdown(
        f"<div style='font-size:0.62rem;letter-spacing:0.08em;color:{_hdr_color};"
        f"text-transform:uppercase;margin-bottom:4px;'>"
        f"{'🔴 ' if _flagged else ''}LIVE YES/NO COMPLEMENT SPREAD — "
        f"{len(rows)} markets · {_flagged} crossed (arb)</div>",
        unsafe_allow_html=True,
    )
    st.dataframe(_df, use_container_width=True, hide_index=True,
                 height=min(220, len(_df) * 38 + 42))
    st.caption(
        "Column guide — "
        "YES ASK: cost to buy YES (cents). "
        "NO ASK (impl): implied cost to buy NO = 1 − YES bid (cents). "
        "COMBINED: YES ask + NO ask (impl); should be ≥ 100c since both legs together must resolve to $1. "
        "EXCESS: combined − 100c; negative = crossed market (riskless two-leg arb on the same contract). "
        "Sorted by lowest COMBINED (cheapest round-trip). Top 20 markets shown."
    )


def render():
    st.markdown("""
<span style='font-size:1rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;'>
CROSS-ASSET RELATIVE VALUE
</span>
<div style='font-size:0.7rem;color:#64748B;letter-spacing:0.04em;margin-top:0.25rem;'>
Kalshi implied probability vs. externally derived probability.
These are RELATIVE VALUE signals, not arbitrage &mdash; the two legs settle on
different venues under different rules and cannot be locked against each other.
</div>
""", unsafe_allow_html=True)
    st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)

    from dashboard.data_layer import get_system_health as _gh
    _h = _gh()
    _is_sqlite_p07 = not _h.get("db_connected", False) and _h.get("db_mode") == "sqlite"


    # --- Latest external market prices (yfinance + BOC VALET) -----
    _ext_prices = get_latest_external_prices()

    # --- Macro regime indicator (top of page) -----
    _mr_btc = float(_ext_prices.get("BTC") or 0) if _ext_prices else 0.0
    _mr_vix = float(_ext_prices.get("VIX") or 0) if _ext_prices else 0.0
    _mr_tnx = float(_ext_prices.get("TNX") or 0) if _ext_prices else 0.0
    if _mr_btc > 50000 and _mr_vix > 0 and _mr_vix < 20:
        _macro_regime, _regime_color = "RISK-ON", "#22C55E"
        _regime_note = f"BTC ${_mr_btc:,.0f} (bullish) · VIX {_mr_vix:.1f} (low vol)"
    elif (_mr_btc > 0 and _mr_btc < 30000) or (_mr_vix > 25 and _mr_tnx > 4.5):
        _macro_regime, _regime_color = "RISK-OFF", "#EF4444"
        _regime_note = f"BTC ${_mr_btc:,.0f} · VIX {_mr_vix:.1f} (elevated) · US10Y {_mr_tnx:.2f}%"
    else:
        _macro_regime, _regime_color = "NEUTRAL", "#F59E0B"
        _regime_note = "Mixed signals — no dominant macro theme"
    st.markdown(
        f"<div style='background:{PANEL};border:1px solid {BORDER};border-left:4px solid {_regime_color};"
        f"padding:0.45rem 1rem;border-radius:3px;margin-bottom:0.75rem;display:flex;"
        f"align-items:center;gap:1.25rem;'>"
        f"<span style='font-size:0.58rem;letter-spacing:0.12em;color:#64748B;"
        f"text-transform:uppercase;font-family:Inter,sans-serif;'>MACRO REGIME</span>"
        f"<span style='font-family:JetBrains Mono,monospace;font-size:0.92rem;font-weight:700;"
        f"color:{_regime_color};'>{_macro_regime}</span>"
        f"<span style='font-size:0.65rem;color:#64748B;'>{_regime_note}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if _ext_prices:
        _ext_labels = {
            # Rates & bonds
            "CORRA": "CORRA", "BOC_RATE": "BOC RATE",
            "CAGB_2Y": "CA2Y", "CAGB_5Y": "CA5Y", "CAGB_10Y": "CA10Y",
            "TNX": "US10Y", "FVX": "US5Y",
            # FX
            "CADUSD": "CAD/USD", "USDCAD": "USD/CAD",
            # Equities
            "SP500": "S&P 500", "NASDAQ": "NASDAQ", "DJI": "DOW",
            "TSX": "TSX", "VIX": "VIX",
            # Commodities
            "WTI": "WTI OIL", "GOLD": "GOLD", "NATGAS": "NAT GAS",
            # Crypto
            "BTC": "BTC", "ETH": "ETH", "DOGE": "DOGE",
            "XRP": "XRP", "BNB": "BNB",
        }
        _yield_keys  = {"CORRA", "BOC_RATE", "CAGB_2Y", "CAGB_5Y", "CAGB_10Y", "TNX", "FVX"}
        _fx_keys     = {"CADUSD", "USDCAD"}
        _large_idx   = {"SP500", "NASDAQ", "DJI", "TSX", "BTC"}
        _small_keys  = {"DOGE", "XRP"}

        _ordered = [k for k in _ext_labels if k in _ext_prices]
        if _ordered:
            # Show in rows of 7
            _ROW = 7
            for _row_start in range(0, len(_ordered), _ROW):
                _row_keys = _ordered[_row_start:_row_start + _ROW]
                _cols_ext = st.columns(len(_row_keys))
                for _i, _k in enumerate(_row_keys):
                    _v = _ext_prices[_k]
                    _lbl = _ext_labels.get(_k, _k)
                    if _v is None:
                        _cols_ext[_i].metric(_lbl, "--")
                    elif _k in _yield_keys:
                        _cols_ext[_i].metric(_lbl, f"{float(_v):.2f}%")
                    elif _k in _fx_keys:
                        _cols_ext[_i].metric(_lbl, f"{float(_v):.4f}")
                    elif _k in _large_idx:
                        _cols_ext[_i].metric(_lbl, f"{float(_v):,.0f}")
                    elif _k in _small_keys:
                        _cols_ext[_i].metric(_lbl, f"{float(_v):.4f}")
                    else:
                        _cols_ext[_i].metric(_lbl, f"{float(_v):,.2f}")

    # --- Live WS quotes for BOC/FED rate markets -----
    _rate_live_p07: dict = {}
    try:
        from dashboard.live_state import get_live_state as _gls_p07ws
        _ws_p07 = _gls_p07ws()
        _ws_connected_p07 = _ws_p07.get_stats().get("connected", False)
        if _ws_connected_p07:
            _all_q_p07 = _ws_p07.snapshot_all()
            _rate_live_p07 = {
                t: q for t, q in _all_q_p07.items()
                if any(t.upper().startswith(pfx) for pfx in ("KXCB", "KXBOC", "KXFED", "KXCORR"))
                and not t.upper().startswith("KXFEDERALCHARGE")
            }
            if _rate_live_p07:
                _rows_ws = []
                for _t, _q in sorted(_rate_live_p07.items()):
                    _bid = _q.yes_bid if _q.yes_bid and _q.yes_bid > 0 else None
                    _ask = _q.yes_ask if _q.yes_ask and _q.yes_ask > 0 else None
                    _mid = round((_bid + _ask) / 2, 4) if _bid and _ask else None
                    _sprd = round(_ask - _bid, 4) if _bid and _ask else None
                    _age_s = int(getattr(_q, "age_seconds", 0))
                    if _age_s >= 3600:
                        _age_str = f"{_age_s // 3600}h{(_age_s % 3600) // 60}m"
                    elif _age_s >= 60:
                        _age_str = f"{_age_s // 60}m{_age_s % 60}s"
                    else:
                        _age_str = f"{_age_s}s"
                    _rows_ws.append({
                        "TICKER": _t,
                        "BID": f"{_bid*100:.0f}c" if _bid else "--",
                        "ASK": f"{_ask*100:.0f}c" if _ask else "--",
                        "SPREAD": f"{_sprd*100:.1f}c" if _sprd else "--",
                        "MID": f"{_mid*100:.1f}c" if _mid else "--",
                        "AGE": _age_str,
                    })
                if _rows_ws:
                    import pandas as _pd_p07ws
                    st.markdown(
                        f"<div style='font-size:0.62rem;letter-spacing:0.08em;color:#22C55E;"
                        f"text-transform:uppercase;margin-bottom:4px;'>● LIVE WS — BOC/FED RATE MARKET QUOTES "
                        f"({len(_rows_ws)} markets)</div>",
                        unsafe_allow_html=True,
                    )
                    st.dataframe(_pd_p07ws.DataFrame(_rows_ws), use_container_width=True, hide_index=True, height=min(200, len(_rows_ws) * 38 + 42))
            else:
                # WS connected but Synthesis has no KXBOC/KXFED/KXCB markets in the snapshot
                st.caption(
                    "WebSocket connected but no BOC/FED rate markets (KXBOC-*, KXFED-*, KXCB-*) "
                    "are present in the live quote stream. Rate market tickers will appear here once "
                    "Synthesis begins distributing them — this is normal when these markets are expired "
                    "or not yet listed for the upcoming meeting date."
                )
    except Exception:
        pass

    # --- CE sum check (KXFED-*/KXBOC-* per-meeting completeness) -----
    _render_ce_sum_check(_rate_live_p07)

    # --- Implied Fed path (next 3 FOMC meetings from KXFED markets) -----
    if _rate_live_p07:
        import re as _re_fed_path, collections as _col_fed_path
        _fed_path_groups: dict = _col_fed_path.defaultdict(list)
        for _fpt, _fpq in _rate_live_p07.items():
            if not _fpt.upper().startswith("KXFED"):
                continue
            _fpbid = _fpq.yes_bid if (_fpq.yes_bid or 0) > 0 else None
            _fpask = _fpq.yes_ask if (_fpq.yes_ask or 0) > 0 else None
            if not _fpbid or not _fpask:
                continue
            _fpmid = (_fpbid + _fpask) / 2.0
            _fpm = _re_fed_path.match(r"KXFED-(\d{2}[A-Z]{3})-T([\d.]+)", _fpt.upper())
            if _fpm:
                _fp_date, _fp_rate = _fpm.group(1), float(_fpm.group(2))
                _fed_path_groups[_fp_date].append((_fp_rate, _fpmid))
        if _fed_path_groups:
            # Sort meeting dates, take next 3 with positive days ahead
            _fp_sorted = sorted(
                [(_d, _days_to_month(_d), _items) for _d, _items in _fed_path_groups.items()],
                key=lambda x: x[1],
            )
            _fp_future = [x for x in _fp_sorted if x[1] >= 0][:3]
            if _fp_future:
                st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)
                st.markdown(
                    f"<div style='font-size:0.62rem;letter-spacing:0.08em;color:{AMBER};"
                    f"text-transform:uppercase;margin-bottom:0.5rem;'>📈 IMPLIED FED RATE PATH — NEXT {len(_fp_future)} FOMC MEETINGS</div>",
                    unsafe_allow_html=True,
                )
                _fp_cols = st.columns(len(_fp_future))
                for _fp_ci, (_fp_date, _fp_days, _fp_items) in enumerate(_fp_future):
                    # Implied rate = probability-weighted average of rate outcomes
                    _fp_total_mid = sum(m for _, m in _fp_items)
                    if _fp_total_mid > 0:
                        _fp_implied = sum(_r * _m for _r, _m in _fp_items) / _fp_total_mid
                    else:
                        _fp_implied = sum(_r for _r, _ in _fp_items) / len(_fp_items)
                    # Most probable outcome
                    _fp_top = max(_fp_items, key=lambda x: x[1])
                    with _fp_cols[_fp_ci]:
                        st.metric(
                            f"FOMC {_fp_date}",
                            f"{_fp_implied:.2f}%",
                            help=(
                                f"Probability-weighted implied Fed rate in {_fp_days}d. "
                                f"Most likely: {_fp_top[0]:.2f}% ({_fp_top[1]*100:.0f}c implied prob). "
                                f"{len(_fp_items)} outcomes tracked."
                            ),
                        )
                        st.caption(f"in {_fp_days}d · {len(_fp_items)} outcomes")
                st.caption(
                    "Implied Fed rate path derived from KXFED market mid-prices. "
                    "Rate = probability-weighted average of all outcome strikes per meeting."
                )

    # --- YES/NO complement spread (single-market bid-ask inversion check) -----
    try:
        from dashboard.live_state import get_live_state as _gls_yn
        _ws_yn = _gls_yn()
        if _ws_yn.get_stats().get("connected", False):
            _render_yes_no_complement_spread(_ws_yn)
    except Exception:
        pass

    # --- Crypto Market Context (KXBTC markets) -----
    try:
        from dashboard.live_state import get_live_state as _gls_btc
        _ws_btc = _gls_btc()
        if _ws_btc.get_stats().get("connected", False):
            _all_q_btc = _ws_btc.snapshot_all()
            _kxbtc_markets = {
                t: q for t, q in _all_q_btc.items()
                if t.upper().startswith("KXBTC")
            }
            st.markdown("<hr style='margin:0.5rem 0 0.75rem 0;'>", unsafe_allow_html=True)
            st.markdown(
                f"<div style='font-size:0.62rem;letter-spacing:0.08em;color:{CYAN};"
                f"text-transform:uppercase;margin-bottom:0.5rem;'>₿ CRYPTO MARKET CONTEXT</div>",
                unsafe_allow_html=True,
            )
            if _kxbtc_markets:
                # Build rows for the table
                import re as _re_btc
                _btc_rows = []
                for _t, _q in _kxbtc_markets.items():
                    _bid = _q.yes_bid if (_q.yes_bid or 0) > 0 else None
                    if _bid is None:
                        continue
                    # Try to parse BTC strike from ticker e.g. KXBTC-25DEC25-B50000
                    _strike = None
                    _strike_match = _re_btc.search(r"-B(\d+)", _t.upper())
                    if _strike_match:
                        _strike = int(_strike_match.group(1))
                    _btc_rows.append({
                        "ticker": _t,
                        "yes_bid": _bid,
                        "strike": _strike,
                    })
                # Top 3 by YES bid descending
                _btc_rows_sorted = sorted(_btc_rows, key=lambda r: r["yes_bid"], reverse=True)
                _top3 = _btc_rows_sorted[:3]
                if _top3:
                    import pandas as _pd_btc
                    _btc_table = []
                    for _r in _top3:
                        _implied_range = f">${_r['strike']:,}" if _r["strike"] else "N/A"
                        _btc_table.append({
                            "TICKER": _r["ticker"],
                            "YES BID": f"{_r['yes_bid']*100:.0f}c",
                            "IMPLIED BTC PRICE RANGE": _implied_range,
                        })
                    st.dataframe(_pd_btc.DataFrame(_btc_table), use_container_width=True,
                                 hide_index=True, height=min(160, len(_btc_table) * 38 + 42))

                    # BTC Implied Probability: YES bid of the lowest-strike KXBTC market
                    _with_strike = [r for r in _btc_rows if r["strike"] is not None]
                    if _with_strike:
                        _lowest = min(_with_strike, key=lambda r: r["strike"])
                        st.metric(
                            label=_lowest["ticker"],
                            value=f"{_lowest['yes_bid']*100:.0f}%",
                            help=f"YES bid = implied probability BTC > ${_lowest['strike']:,} by market expiry",
                        )
                else:
                    st.info("KXBTC markets found but no valid YES bids available.")

                st.info(
                    "Crypto vs Rates: High BTC prices often correlate with risk-on sentiment, "
                    "which can suppress cut probabilities in Fed funds markets."
                )
            else:
                st.info("No KXBTC markets currently in the live quote stream.")
    except Exception:
        pass

    # --- Historical signal accuracy tracking -----
    st.info("Historical signal accuracy tracking not yet available — signals are logged for future backtesting.")

    # --- Live cross-asset signals (OIS model vs Kalshi mid) -----
    _render_live_ca_signals(_ext_prices or {}, _rate_live_p07)

    # --- Cross-asset signals from SQLite (real BOC/FX signals) -----
    _sig_df, _sig_err = get_cross_asset_signals()
    if not _sig_df.empty:
        # Derive dynamic footnote values from signal data
        import json as _json
        _scan_date = "--"
        _age_days = 0
        _corra_val = "--"
        _cadusd_val = "--"
        if "scanned_at" in _sig_df.columns:
            try:
                _scan_date = str(_sig_df["scanned_at"].max())[:10]
                # Warn if signals are more than 24h stale
                from datetime import date as _date
                _age_days = ((_date.today() - _date.fromisoformat(_scan_date)).days
                             if _scan_date != "--" else 999)
            except Exception:
                _age_days = 0
        if "model_inputs" in _sig_df.columns:
            try:
                _mi = _json.loads(_sig_df["model_inputs"].iloc[0] or "{}")
                _corra_val = f"{float(_mi.get('corra', _mi.get('policy_rate', 0))):.2f}%"
                _cadusd_val = f"{float(_mi.get('cadusd', 0)):.4f}"
            except Exception:
                pass
        _col_map = {
            "ticker": "TICKER",
            "title": "TITLE",
            "kalshi_midpoint": "KALSHI MID",   # actual column name in cross_asset_model_spreads
            "kalshi_mid": "KALSHI MID",         # legacy alias kept for safety
            "model_prob": "MODEL PROB",
            "edge_pct": "EDGE %",
            "direction": "DIRECTION",
            "is_flagged": "FLAGGED",            # actual column name
            "flagged": "FLAGGED",               # legacy alias
        }
        _display_df = _sig_df.rename(columns={k: v for k, v in _col_map.items() if k in _sig_df.columns})
        if "TITLE" in _display_df.columns:
            _display_df["TITLE"] = _display_df["TITLE"].astype(str).str[:50]
        _rows_html = ""
        for _, _row in _display_df.iterrows():
            _edge_raw = _row.get("EDGE %", 0)
            _dir = str(_row.get("DIRECTION", ""))
            try:
                _edge_f = float(_edge_raw)
            except (ValueError, TypeError):
                _edge_f = 0.0
            if _dir == "BUY_YES" and _edge_f < 0:
                _edge_color = GREEN
            elif _dir == "SELL_YES" and _edge_f > 0:
                _edge_color = RED
            else:
                _edge_color = TEXT2
            _ticker_val = _row.get("TICKER", "--")
            _title_val = str(_row.get("TITLE", "--"))[:50]
            _kmid_val = _row.get("KALSHI MID", "--")
            _mprob_val = _row.get("MODEL PROB", "--")
            # is_flagged stored as 't'/'f' (SQLite) or True/False
            _flagged_raw = _row.get("FLAGGED", "")
            _flagged_val = "⚑ YES" if str(_flagged_raw).lower() in ("t", "true", "1", "yes") else ""
            try:
                _kmid_s = f"{float(_kmid_val)*100:.1f}c"
            except (ValueError, TypeError):
                _kmid_s = str(_kmid_val)
            try:
                _mprob_s = f"{float(_mprob_val)*100:.1f}c"
            except (ValueError, TypeError):
                _mprob_s = str(_mprob_val)
            try:
                _edge_s = f"{_edge_f:+.1f}%"
            except (ValueError, TypeError):
                _edge_s = str(_edge_raw)
            _rows_html += (
                f"<tr>"
                f"<td style='color:{TEXT};font-family:JetBrains Mono,monospace;padding:4px 8px;'>{_ticker_val}</td>"
                f"<td style='color:{TEXT2};padding:4px 8px;'>{_title_val}</td>"
                f"<td style='color:{CYAN};font-family:JetBrains Mono,monospace;padding:4px 8px;text-align:right;'>{_kmid_s}</td>"
                f"<td style='color:{BLUE};font-family:JetBrains Mono,monospace;padding:4px 8px;text-align:right;'>{_mprob_s}</td>"
                f"<td style='color:{_edge_color};font-family:JetBrains Mono,monospace;padding:4px 8px;text-align:right;font-weight:600;'>{_edge_s}</td>"
                f"<td style='color:{TEXT2};padding:4px 8px;'>{_dir}</td>"
                f"<td style='color:{AMBER if _flagged_val else TEXT3};padding:4px 8px;'>{_flagged_val}</td>"
                f"</tr>"
            )
        st.markdown(
            f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:4px solid {GREEN};
padding:0.75rem 1rem;border-radius:3px;margin-bottom:0.75rem;'>
<div style='font-size:0.62rem;letter-spacing:0.08em;color:{GREEN};
text-transform:uppercase;margin-bottom:8px;'>CROSS-ASSET SIGNALS</div>
<table style='width:100%;border-collapse:collapse;font-size:0.72rem;'>
<thead>
<tr style='border-bottom:1px solid {BORDER};'>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:left;padding:4px 8px;'>TICKER</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:left;padding:4px 8px;'>TITLE</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:right;padding:4px 8px;'>KALSHI MID</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:right;padding:4px 8px;'>MODEL PROB</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:right;padding:4px 8px;'>EDGE %</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:left;padding:4px 8px;'>DIRECTION</th>
<th style='color:{TEXT3};font-family:JetBrains Mono,monospace;text-align:left;padding:4px 8px;'>FLAGGED</th>
</tr>
</thead>
<tbody>{_rows_html}</tbody>
</table>
<div style='font-size:0.65rem;color:{AMBER if _age_days > 1 else TEXT3};font-family:JetBrains Mono,monospace;
margin-top:8px;border-top:1px solid {BORDER};padding-top:6px;'>
Signals from BOC rate probability model (CORRA={_corra_val}, CAD/USD={_cadusd_val}).
Scanned {_scan_date}{f" — ⚠ {_age_days}d stale. Signals are for reference; market prices may have moved." if _age_days > 1 else " — signals current."}.
</div>
</div>""",
            unsafe_allow_html=True,
        )
    elif _sig_df.empty:
        # Show this regardless of DB mode — signals require manual pipeline run
        # Try to get CORRA from live ext_prices first, then DB, then omit
        _fb_corra = None
        if _ext_prices:
            _raw_c = _ext_prices.get("CORRA") or _ext_prices.get("BOC_RATE")
            if _raw_c:
                _fb_corra = f"{float(_raw_c):.2f}"
        if not _fb_corra:
            try:
                import json as _json_fb
                try:
                    from dashboard.data_layer import _sqlite_conn as _gsc_fb
                except ImportError:
                    _gsc_fb = None
                _fbc = _gsc_fb() if _gsc_fb else None
                if _fbc:
                    _fbr = _fbc.execute(
                        "SELECT model_inputs FROM cross_asset_model_spreads "
                        "WHERE model_inputs IS NOT NULL LIMIT 1"
                    ).fetchone()
                    _fbc.close()
                    if _fbr:
                        _fb_mi = _json_fb.loads(_fbr[0] or "{}")
                        _fb_raw = _fb_mi.get("corra") or _fb_mi.get("policy_rate")
                        if _fb_raw is not None:
                            _fb_corra = f"{float(_fb_raw):.2f}"
            except Exception:
                pass
        _corra_note = f" (live CORRA: {_fb_corra}%)" if _fb_corra else ""
        _db_note = "Neon PostgreSQL connected" if not _is_sqlite_p07 else "local SQLite"
        st.markdown(
            f"""<div style='background:{PANEL};border:1px solid {AMBER};border-left:4px solid {AMBER};
padding:0.75rem 1rem;border-radius:3px;margin-bottom:0.75rem;'>
<div style='font-size:0.62rem;letter-spacing:0.08em;color:{AMBER};
text-transform:uppercase;margin-bottom:4px;'>CROSS-ASSET MODEL SIGNALS UNAVAILABLE</div>
<div style='font-size:0.72rem;color:{TEXT2};line-height:1.6;font-family:JetBrains Mono,monospace;'>
Cross-asset model spreads not yet populated ({_db_note}).<br>
Connect the cross-asset pipeline to enable model spread signals.<br>
The live signals panel above and the calculators below work without this table{_corra_note}.
</div>
</div>""",
            unsafe_allow_html=True,
        )

    # --- BOC Market Price History (from candlestick DB) -----
    with st.expander("📈 BOC MARKET PRICE HISTORY (candlestick data)", expanded=True):
        # Get KXCB% markets from top-volume list, or query directly
        _boc_tickers = []
        try:
            from dashboard.data_layer import _sqlite_conn as _gsc_p07
            _c_p07 = _gsc_p07()
            if _c_p07:
                # candlesticks.market_id IS the ticker — no JOIN needed
                _boc_rows = _c_p07.execute(
                    "SELECT DISTINCT market_id FROM candlesticks "
                    "WHERE (market_id LIKE 'KXCB%' OR market_id LIKE 'KXFED%') "
                    "AND market_id NOT LIKE 'KXFEDERALCHARGE%' "
                    "ORDER BY market_id"
                ).fetchall()
                _c_p07.close()
                _boc_tickers = [r[0] for r in _boc_rows if r[0]]
        except Exception:
            pass

        if _boc_tickers:
            _sel_boc = st.selectbox(
                "MARKET",
                _boc_tickers,
                key="p07_boc_ticker",
                help="BOC rate and Fed rate markets with candlestick data",
            )
            _boc_hist, _boc_err = get_market_price_history(_sel_boc, interval=60, limit=300)
            _required_cols = {"period_end_ts", "price_close", "yes_bid_close", "yes_ask_close"}
            _boc_cols_ok = _required_cols.issubset(_boc_hist.columns)
            if not _boc_hist.empty and _boc_cols_ok:
                _ts = pd.to_datetime(_boc_hist["period_end_ts"], utc=True, errors="coerce")
                _close = pd.to_numeric(_boc_hist["price_close"], errors="coerce")
                _bid = pd.to_numeric(_boc_hist["yes_bid_close"], errors="coerce")
                _ask = pd.to_numeric(_boc_hist["yes_ask_close"], errors="coerce")

                # Overlay model probability if this ticker appears in signals
                _model_line = None
                if not _sig_df.empty and "ticker" in _sig_df.columns and "model_prob" in _sig_df.columns:
                    _match = _sig_df[_sig_df["ticker"] == _sel_boc]
                    if not _match.empty:
                        try:
                            _model_line = float(_match["model_prob"].iloc[0])
                        except (ValueError, TypeError):
                            pass

                fig_boc = go.Figure()
                fig_boc.add_trace(go.Scatter(x=_ts, y=_ask, name="YES ASK",
                    line=dict(color=RED, width=1), opacity=0.7))
                fig_boc.add_trace(go.Scatter(x=_ts, y=_close, name="TRADE PRICE",
                    line=dict(color=GREEN, width=1.5)))
                fig_boc.add_trace(go.Scatter(x=_ts, y=_bid, name="YES BID",
                    line=dict(color=AMBER, width=1), opacity=0.7,
                    fill="tonexty", fillcolor="rgba(34,197,94,0.05)"))
                if _model_line is not None:
                    fig_boc.add_hline(
                        y=_model_line, line_color=CYAN, line_dash="dash", line_width=1.5,
                        annotation_text=f"MODEL {_model_line*100:.1f}c",
                        annotation_position="right",
                    )
                fig_boc.update_layout(**plotly_dark_layout(
                    title={"text": f"{_sel_boc} — 1h price history (CORRA model overlay)",
                           "font": {"size": 10, "color": TEXT3}},
                    height=300, xaxis_title="", yaxis_title="YES Prob",
                    yaxis=dict(range=[0, 1], tickformat=".2f"),
                    legend=dict(orientation="h", y=1.12),
                    margin={"l": 50, "r": 60, "t": 40, "b": 20},
                ))
                st.plotly_chart(fig_boc, use_container_width=True)
                st.caption(f"{len(_boc_hist):,} hourly candles · {_ts.min().strftime('%Y-%m-%d') if pd.notna(_ts.min()) else '?'} – {_ts.max().strftime('%Y-%m-%d') if pd.notna(_ts.max()) else '?'}")
            elif not _boc_hist.empty and not _boc_cols_ok:
                st.caption(f"Price data for {_sel_boc} is missing expected columns. Ensure the candlestick pipeline version is up to date.")
            else:
                st.caption(f"No hourly candles found for {_sel_boc}. Ensure the candlestick pipeline has run for this market.")
        else:
            st.caption("No KXCB%/KXFED% markets found in candlestick database.")

    # --- Live BOC reference data -----
    render_boc_panel(show_countdown=True)

    # --- Controls -----
    col1, col2, col3 = st.columns([3, 2, 1])

    with col1:
        market_id = st.text_input(
            "KALSHI MARKET ID",
            placeholder="e.g. KXBOC-26JAN-T4.25",
            help="Kalshi market ticker or market_id",
        )

    with col2:
        asset_labels = [f"{a} - {d}" for a, d in _ASSETS]
        sel_label = st.selectbox("TRADITIONAL ASSET", asset_labels)
        asset_code = _ASSETS[asset_labels.index(sel_label)][0]

    with col3:
        days = st.slider("DAYS", 7, 180, 60)

    if not market_id:
        _render_ext_market_chart()
        _info_panel()
        _render_calculators()
        return

    # --- Load data -----
    df, err = get_cross_asset_data(market_id, asset_code, days)

    if err or df.empty:
        _no_data_panel(market_id, asset_code, err)
        _render_calculators()
        return

    # --- Statistics row -----
    spread = df["spread"] if "spread" in df.columns else pd.Series()

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("OBSERVATIONS",  f"{len(df):,}")
    k2.metric("AVG SPREAD",    f"{spread.mean()*100:.2f}c" if len(spread) else "--")
    k3.metric("SPREAD STD",    f"{spread.std()*100:.2f}c"  if len(spread) else "--")
    k4.metric("MAX |SPREAD|",  f"{spread.abs().max()*100:.2f}c" if len(spread) else "--")
    _std = spread.std() if len(spread) > 1 else 0.0
    z = ((spread - spread.mean()) / _std) if _std and _std > 0 else pd.Series()
    k5.metric("CURRENT Z",     f"{float(z.iloc[-1]):.2f}" if len(z) and not pd.isna(z.iloc[-1]) else "--")

    # --- Main chart -----
    if "spread_ts" in df.columns:
        df["spread_ts"] = pd.to_datetime(df["spread_ts"], utc=True)

    fig = go.Figure()

    if "kalshi_probability" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["spread_ts"], y=df["kalshi_probability"] * 100,
            name="Kalshi implied (c)", mode="lines",
            line={"color": BLUE, "width": 1.5},
        ))

    if "trad_probability" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["spread_ts"], y=df["trad_probability"] * 100,
            name=f"{asset_code} OIS model (c)", mode="lines",
            line={"color": CYAN, "width": 1.5},
        ))

    if "spread" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["spread_ts"], y=df["spread"] * 100,
            name="Spread (Kalshi – Trad)", mode="lines",
            line={"color": AMBER, "width": 1.2, "dash": "dot"},
            yaxis="y2",
        ))

    fig.update_layout(
        **plotly_dark_layout(
        title={"text": f"KALSHI vs {asset_code}: {market_id}", "font": {"size": 10, "color": TEXT3}},
        height=380,
        xaxis_title="Date",
        yaxis={"title": "Probability (c)", "tickformat": ".0f"},
        yaxis2={
            "title": "Spread (c)", "overlaying": "y", "side": "right",
            "zeroline": True, "zerolinecolor": TEXT3, "zerolinewidth": 0.8,
            "gridcolor": "rgba(0,0,0,0)",
            "tickfont": {"family": "JetBrains Mono", "size": 10},
        },
        legend={"x": 0, "y": 1, "bgcolor": "rgba(0,0,0,0)"},
    ))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"Kalshi implied: live Kalshi contract mid-price (cents). "
        f"{asset_code} OIS model: probability derived from the overnight index swap (OIS) rate "
        f"using a lognormal model — what the rates market implies for the same outcome. "
        f"Spread = Kalshi − OIS model; persistent non-zero spread may indicate mispricing, "
        f"information lag, or liquidity premium."
    )

    # -------------------------------------------------------------- Z-score chart --------------------------------------------------------------
    if len(z) > 3:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=df["spread_ts"], y=z,
            mode="lines", name="Z-score",
            line={"color": AMBER, "width": 1.2},
            fill="tozeroy", fillcolor="rgba(245,158,11,0.06)",
        ))
        fig2.add_hline(y=2,  line_dash="dash", line_color=RED,   line_width=0.8,
                       annotation_text="+2", annotation_font_size=9)
        fig2.add_hline(y=-2, line_dash="dash", line_color=RED,   line_width=0.8,
                       annotation_text="-2", annotation_font_size=9)
        fig2.add_hline(y=0,  line_dash="solid", line_color=TEXT3, line_width=0.5)
        fig2.update_layout(
            **plotly_dark_layout(
            title={"text": "SPREAD Z-SCORE", "font": {"size": 10, "color": TEXT3}},
            height=200,
            xaxis_title="", yaxis_title="",
        ))
        st.plotly_chart(fig2, use_container_width=True)

    # --- Data table -----
    with st.expander("RAW DATA"):
        st.dataframe(df, use_container_width=True, height=300, hide_index=True)

    # --- Live probability calculators (always available) -----
    _render_calculators()

    # No auto-rerun here — cross-asset data updates on TTL (ext prices: 1h, signals: 5min).
    # Sleeping before rerun blocks Streamlit's server thread; removed.


def _render_calculators():
    """
Standalone probability calculators — require no DB, use only the
probability_engine math functions directly.
"""
    try:
        from analysis.probability_engine import (
            boc_rate_implied_probability,
            boc_rate_distribution,
            fx_implied_probability,
            compute_cross_market_spread,
        )
    except ImportError:
        st.info("Probability engine module not available. Connect the cross-asset pipeline to enable rate probability calculations.")
        return

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.65rem;letter-spacing:0.1em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.75rem;'>PROBABILITY CALCULATORS</div>",
        unsafe_allow_html=True,
    )

    # Derive CORRA default from DB if available, else current BOC rate (2.25%; holding since Jan 28 2026)
    _calc_ois_default = 2.25
    try:
        import json as _json_calc
        from dashboard.data_layer import _sqlite_conn as _gsc_calc
        _calcc = _gsc_calc()
        if _calcc:
            _calcr = _calcc.execute(
                "SELECT model_inputs FROM cross_asset_model_spreads "
                "WHERE model_inputs IS NOT NULL LIMIT 1"
            ).fetchone()
            _calcc.close()
            if _calcr:
                _calc_mi = _json_calc.loads(_calcr[0] or "{}")
                _calc_raw = _calc_mi.get("corra") or _calc_mi.get("policy_rate")
                if _calc_raw is not None:
                    _calc_ois_default = float(_calc_raw)  # already stored as percent (e.g. 2.25)
    except Exception:
        pass

    # Fetch live external prices inside the calculator so this function doesn't
    # depend on _ext_prices being visible in the caller's local scope.
    _calc_ext = get_latest_external_prices() or {}

    # Prefer live CORRA from the feed; fall back to DB; last resort 2.25% (current BOC rate)
    if _calc_ext:
        _live_corra = _calc_ext.get("CORRA") or _calc_ext.get("BOC_RATE")
        if _live_corra is not None:
            try:
                _calc_ois_default = float(_live_corra)
            except (TypeError, ValueError):
                pass  # keep DB / hardcoded fallback

    tab_boc, tab_fx = st.tabs(["BOC RATE PROBABILITY", "FX THRESHOLD PROBABILITY"])

    # ------------------------------------------------------------------ BOC tab
    with tab_boc:
        st.markdown(
            f"<div style='font-size:0.7rem;color:{TEXT2};margin-bottom:0.75rem;'>"
            "Estimate the implied probability of a specific BOC rate outcome using CORRA/OIS market data."
            "</div>",
            unsafe_allow_html=True,
        )

        # ---- VIX REGIME SECTION ----
        _live = _fetch_rates_vix()
        _vix_live = _live["vix"]  # Yahoo returns VIX as e.g. 18.0, not 0.18
        st.subheader("📊 VIX REGIME")
        st.metric("VIX (live)", f"{_vix_live:.1f}")
        if _vix_live < 15:
            st.success("LOW volatility regime · Kalshi options cheaply priced")
        elif _vix_live < 25:
            st.info("MEDIUM volatility regime · Kalshi options fairly priced")
        else:
            st.warning("HIGH volatility regime · Kalshi options elevated premium")
        st.caption("VIX regime affects option pricing models and arb opportunity frequency")
        st.markdown("<br>", unsafe_allow_html=True)
        # ---- END VIX REGIME SECTION ----

        # ---- YIELD CURVE SECTION ----
        st.markdown(
            f"<div style='font-size:0.62rem;letter-spacing:0.1em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.4rem;'>YIELD CURVE</div>",
            unsafe_allow_html=True,
        )
        _yc_2y = _live["us2y"]    # live US 2Y Treasury yield (%)
        _yc_10y = _live["us10y"]  # live US 10Y Treasury yield (%)
        _yc_cad_10y = _live["cad10y"]  # live CAD 10Y yield (%)
        _yc_spread_bps = (_yc_10y - _yc_2y) * 100  # in basis points
        # Curve shape indicator
        _yc_diff = _yc_10y - _yc_2y
        if abs(_yc_diff) <= 0.10:
            st.warning("Yield curve FLAT — 10Y and 2Y within 10bps")
            _yc_shape = "Flat"
        elif _yc_10y > _yc_2y:
            st.success("Yield curve NORMAL (upward sloping) — 10Y > 2Y")
            _yc_shape = "Normal (upward sloping)"
        else:
            st.error("Yield curve INVERTED — 2Y > 10Y (recession signal)")
            _yc_shape = "Inverted"
        _yc_c1, _yc_c2, _yc_c3 = st.columns(3)
        _yc_c1.metric("2Y TREASURY", f"{_yc_2y:.2f}%")
        _yc_c2.metric("10Y TREASURY", f"{_yc_10y:.2f}%")
        _yc_c3.metric("2Y-10Y SPREAD", f"{_yc_spread_bps:+.2f}bps")
        st.caption("Reference rates — live via Yahoo Finance (cached 5 min) · fallback to hardcoded values if unavailable")

        # --- Curve steepness color-coded metric -----
        _steep_bps = (_yc_10y - _yc_2y) * 100
        if _steep_bps > 100:
            _steep_label, _steep_color = f"STEEP", GREEN
        elif _steep_bps >= 0:
            _steep_label, _steep_color = f"FLAT", AMBER
        else:
            _steep_label, _steep_color = f"INVERTED", RED
        st.markdown(
            f"<div style='background:{PANEL};border:1px solid {BORDER};border-left:4px solid {_steep_color};"
            f"padding:0.35rem 0.75rem;border-radius:2px;margin:0.3rem 0 0.5rem 0;display:inline-block;'>"
            f"<span style='font-size:0.58rem;letter-spacing:0.08em;color:{TEXT3};text-transform:uppercase;"
            f"font-family:Inter,sans-serif;margin-right:0.5rem;'>CURVE STEEPNESS (10Y−2Y)</span>"
            f"<span style='font-family:JetBrains Mono,monospace;font-size:0.85rem;font-weight:700;"
            f"color:{_steep_color};'>{_steep_label} &nbsp; {_steep_bps:+.0f}bps</span>"
            f"<span style='font-size:0.62rem;color:{TEXT3};margin-left:0.75rem;'>"
            f"{'&gt;100bps = steep (risk-on)' if _steep_bps > 100 else '0–100bps = flat' if _steep_bps >= 0 else '&lt;0 = inverted (recession signal)'}"
            f"</span></div>",
            unsafe_allow_html=True,
        )
        # Yield curve explainer expander
        with st.expander("📖 Yield Curve & Prediction Markets"):
            st.markdown(f"""
**Curve shape:** {_yc_shape}

**Normal curve (10Y > 2Y):** Upward sloping yield curve signals a risk-on environment — investors
expect future growth and inflation. In this regime Kalshi prediction markets tend to see higher
activity in growth-sensitive categories (equities, commodities).

**Inverted curve (2Y > 10Y):** A classic recession-fear signal. Historically precedes economic
slowdowns. Kalshi recession-probability markets and Fed rate-cut markets typically see elevated
volume and tighter bid-ask spreads during inversion.

**Flat curve (within 10bps):** Transitional state — markets are uncertain about the growth
outlook. Prediction market liquidity may be thinner as traders wait for clearer macro signals.

**Impact on Kalshi arb:** A steep normal curve is associated with higher risk appetite, more
speculative flow, and potentially wider gross edges. Inversions may reduce market activity overall
but increase interest in rate-decision markets (KXFED-*, KXBOC-*).
""")
        # Rate Differential chart: US 10Y vs CAD 10Y
        import plotly.graph_objects as _go_yc
        _fig_yc = _go_yc.Figure(_go_yc.Bar(
            x=["US 10Y Treasury", "CAD 10Y Government Bond"],
            y=[_yc_10y, _yc_cad_10y],
            marker_color=[BLUE, CYAN],
            marker_line_width=0,
            text=[f"{_yc_10y:.2f}%", f"{_yc_cad_10y:.2f}%"],
            textposition="outside",
        ))
        _fig_yc.update_layout(**plotly_dark_layout(
            title={"text": "US 10Y vs CAD 10Y YIELD", "font": {"size": 10, "color": TEXT3}},
            height=220,
            xaxis_title="",
            yaxis_title="Yield (%)",
            margin={"l": 40, "r": 20, "t": 35, "b": 40},
            yaxis={"range": [0, max(_yc_10y, _yc_cad_10y) * 1.25]},
        ))
        _fig_yc.add_hline(
            y=0, line_color=TEXT3, line_width=0.8, line_dash="dot",
            annotation_text="No divergence (equal rates)",
            annotation_font_size=8, annotation_position="bottom right",
        )
        st.plotly_chart(_fig_yc, use_container_width=True)
        _yc_diff_bps = (_yc_10y - _yc_cad_10y) * 100
        st.caption(
            f"Rate differential: US 10Y − CAD 10Y = {_yc_diff_bps:+.0f}bps · "
            f"{'US yields higher → USD carry advantage' if _yc_diff_bps > 0 else 'CAD yields higher → CAD carry advantage' if _yc_diff_bps < 0 else 'No divergence'} · live via Yahoo Finance"
        )
        st.markdown("<br>", unsafe_allow_html=True)
        # ---- END YIELD CURVE SECTION ----

        with st.expander("📐 How the OIS model works", expanded=False):
            st.markdown("""
- **Overnight Index Swaps price future central bank rate decisions via forward rate agreements**
- **Kalshi markets on Fed/BOC rate decisions imply a probability distribution over rate outcomes**
- **OIS-implied probability = (forward rate - current rate) / (expected hike size)**
- **Divergence between OIS-implied and Kalshi market price = potential mispricing signal**
""")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            ois_rate = st.number_input(
                "OIS RATE (%)", min_value=0.0, max_value=20.0, value=_calc_ois_default, step=0.05,
                help=f"Current CORRA / OIS rate as a percentage (default: {_calc_ois_default:.2f}% from DB)",
                key="boc_ois",
            ) / 100.0
        with c2:
            target_rate = st.number_input(
                "TARGET RATE (%)", min_value=0.0, max_value=20.0, value=2.25, step=0.25,
                help="The specific BOC rate outcome to price (current BOC rate: 2.25%; holding since Jan 28 2026; next decision Sep 9 2026)", key="boc_target",
            ) / 100.0
        with c3:
            direction = st.selectbox("DIRECTION", ["cut", "hold", "hike"], key="boc_dir")
        with c4:
            n_meetings = st.slider("MEETINGS", 1, 8, 1, key="boc_meetings")

        try:
            p_boc = boc_rate_implied_probability(ois_rate, 30, target_rate, direction, n_meetings=n_meetings)
        except Exception as _boc_exc:
            st.warning(f"BOC probability model error: {_boc_exc}")
            p_boc = None
        if p_boc is None:
            st.warning("OIS rate data unavailable — cannot compute BOC implied probability.")
            p_boc = 0.5  # fallback so display renders; labels will show N/A

        # Kalshi comparison
        kalshi_c_boc = st.number_input(
            "KALSHI CONTRACT PRICE (c)", min_value=1, max_value=99, value=50, step=1,
            key="boc_kalshi_c",
            help="Current Kalshi YES price in cents for this rate outcome",
        )
        kalshi_p_boc = kalshi_c_boc / 100.0

        try:
            spread_res = compute_cross_market_spread(kalshi_p_boc, p_boc)
        except Exception:
            spread_res = {"spread": 0.0, "log_odds_diff": None, "significant_10pct": False, "significant_5pct": False}

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("OIS IMPLIED", f"{p_boc*100:.1f}c")
        k2.metric("KALSHI PRICE", f"{kalshi_c_boc}c")
        spread_c = spread_res["spread"] * 100
        k3.metric("SPREAD (K - OIS)", f"{spread_c:+.1f}c")
        k4.metric("LOG-ODDS DIFF", f"{spread_res['log_odds_diff']:.3f}" if spread_res["log_odds_diff"] is not None else "--")
        sig_label = "YES (>10c)" if spread_res["significant_10pct"] else ("MINOR (>5c)" if spread_res["significant_5pct"] else "NO")
        k5.metric("SIGNIFICANT", sig_label)

        # Full distribution
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:0.6rem;letter-spacing:0.08em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.25rem;'>RATE DISTRIBUTION (±100bps of OIS)</div>",
            unsafe_allow_html=True,
        )
        # Generate distribution spanning both ois_rate and target_rate
        import numpy as np
        _anchor = (ois_rate + target_rate) / 2.0  # centre window on midpoint of both
        base = round(_anchor / 0.0025) * 0.0025   # round to nearest 25bps
        possible_rates = [round(base + i * 0.0025, 4) for i in range(-8, 9)]
        # Ensure target_rate is in range; extend window if needed
        if target_rate < possible_rates[0]:
            possible_rates = [round(target_rate + i * 0.0025, 4) for i in range(17)]
        elif target_rate > possible_rates[-1]:
            possible_rates = [round(target_rate - 16 * 0.0025 + i * 0.0025, 4) for i in range(17)]
        try:
            dist = boc_rate_distribution(ois_rate, possible_rates)
        except Exception as _dist_exc:
            st.warning(f"Rate distribution error: {_dist_exc}")
            dist = {}

        _target_key = f"{target_rate*100:.2f}%"
        if dist:  # guard: only render chart if distribution computed successfully
            fig_dist = go.Figure(go.Bar(
                x=list(dist.keys()),
                y=[v * 100 for v in dist.values()],
                marker_color=[GREEN if k == _target_key else BLUE for k in dist.keys()],
            ))
            fig_dist.update_layout(
                **plotly_dark_layout(
                title={"text": "BOC RATE IMPLIED DISTRIBUTION", "font": {"size": 10, "color": TEXT3}},
                height=220,
                xaxis_title="Rate", yaxis_title="Probability (%)",
                margin={"l": 40, "r": 20, "t": 30, "b": 40},
            ))
            st.plotly_chart(fig_dist, use_container_width=True)

    # ------------------------------------------------------------------ FX tab
    with tab_fx:
        st.markdown(
            f"<div style='font-size:0.7rem;color:{TEXT2};margin-bottom:0.4rem;'>"
            "Log-normal (Black-Scholes digital) probability that CAD/USD will be above or below a threshold."
            "</div>"
            f"<div style='font-size:0.62rem;color:{TEXT3};font-family:JetBrains Mono,monospace;"
            f"margin-bottom:0.75rem;'>"
            "Methodology: Black-Scholes digital option formula (d2 term) applied to FX rate. "
            "Inputs: spot rate, annualized implied volatility, days to expiry. "
            "No OIS drift applied — model assumes risk-neutral drift of zero (FX forward ≈ spot). "
            "For short-dated contracts (&lt;90d) this is a reasonable approximation."
            "</div>",
            unsafe_allow_html=True,
        )
        with st.expander("📐 Black-Scholes Digital Option Methodology", expanded=False):
            st.markdown(
                "- A digital (binary) option pays $1 if the underlying ends above the strike\n"
                "- Price = N(d2) where d2 = (ln(S/K) + (r - σ²/2)T) / (σ√T)\n"
                "- Kalshi YES markets approximate digital call options on the underlying\n"
                "- Mispricing = |Kalshi YES price - BS digital price| > fee threshold"
            )
            st.latex(r"d_2 = \frac{\ln(S/K) + (r - \sigma^2/2)T}{\sigma\sqrt{T}}")

            st.markdown(
                f"<div style='font-size:0.62rem;letter-spacing:0.1em;color:{TEXT3};text-transform:uppercase;"
                f"margin-top:0.75rem;margin-bottom:0.4rem;'>INTERACTIVE BS DIGITAL CALCULATOR</div>",
                unsafe_allow_html=True,
            )
            _bs_c1, _bs_c2, _bs_c3, _bs_c4, _bs_c5 = st.columns(5)
            with _bs_c1:
                _bs_S = st.number_input("S (current price)", min_value=0.01, value=100.0, step=1.0, key="bs_S")
            with _bs_c2:
                _bs_K = st.number_input("K (strike)", min_value=0.01, value=100.0, step=1.0, key="bs_K")
            with _bs_c3:
                _bs_T = st.slider("T (years to expiry)", min_value=0.01, max_value=2.0, value=0.25, step=0.01, key="bs_T")
            with _bs_c4:
                _bs_r_pct = st.number_input("r (risk-free rate %)", min_value=0.0, max_value=20.0, value=4.25, step=0.25, key="bs_r")
            with _bs_c5:
                _bs_sigma_pct = st.slider("σ (volatility %)", min_value=1, max_value=100, value=20, key="bs_sigma")

            _bs_r = _bs_r_pct / 100.0
            _bs_sigma = _bs_sigma_pct / 100.0
            import math as _math_bs
            try:
                _bs_d2 = (_math_bs.log(_bs_S / _bs_K) + (_bs_r - 0.5 * _bs_sigma ** 2) * _bs_T) / (_bs_sigma * _math_bs.sqrt(_bs_T))
                try:
                    from scipy.stats import norm as _norm_bs
                    _bs_price = _norm_bs.cdf(_bs_d2)
                except ImportError:
                    import math as _m2
                    _bs_price = 1.0 / (1.0 + _m2.exp(-1.702 * _bs_d2))
                _bs_put_price = 1.0 - _bs_price
                _bsc1, _bsc2 = st.columns(2)
                _bsc1.metric(
                    "BS DIGITAL CALL (P > K)",
                    f"{_bs_price:.1%}",
                    help=f"N(d₂) = {_bs_price:.4f} · d₂ = {_bs_d2:.4f}",
                )
                _bsc2.metric(
                    "BS DIGITAL PUT (P < K)",
                    f"{_bs_put_price:.1%}",
                    help=f"1 − N(d₂) = {_bs_put_price:.4f} · sum = 100% ✓",
                )
                st.markdown(
                    f"<div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;color:{TEXT3};"
                    f"margin:0.25rem 0 0.5rem;'>d₂ = {_bs_d2:.4f} · call + put = {_bs_price + _bs_put_price:.3f}</div>",
                    unsafe_allow_html=True,
                )
                # Compare to current Kalshi YES price
                _bs_kalshi_c = st.number_input(
                    "Kalshi YES price (c) for comparison", min_value=1, max_value=99, value=50, step=1, key="bs_kalshi"
                )
                _bs_kalshi_p = _bs_kalshi_c / 100.0
                _bs_div_pp = (_bs_kalshi_p - _bs_price) * 100
                _bs_div_color = GREEN if abs(_bs_div_pp) < 5 else (AMBER if abs(_bs_div_pp) < 10 else RED)
                st.markdown(
                    f"<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;color:{_bs_div_color};'>"
                    f"Kalshi YES: {_bs_kalshi_c}c &nbsp;·&nbsp; BS model: {_bs_price:.1%} &nbsp;·&nbsp; "
                    f"Divergence: <b>{_bs_div_pp:+.1f}pp</b>"
                    f"{'  ⚡ Potential mispricing' if abs(_bs_div_pp) >= 5 else '  ✓ Within noise'}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            except (ValueError, ZeroDivisionError) as _bs_ex:
                st.warning(f"Cannot compute BS price: {_bs_ex}")

        f1, f2, f3, f4, f5 = st.columns(5)
        with f1:
            _live_cadusd = float(_calc_ext.get("CADUSD") or 0.735) if _calc_ext else 0.735
            spot = st.number_input("SPOT (CAD/USD)", min_value=0.5, max_value=2.0, value=_live_cadusd, step=0.001, format="%.3f", key="fx_spot")
        with f2:
            threshold = st.number_input("THRESHOLD", min_value=0.5, max_value=2.0, value=0.740, step=0.001, format="%.3f", key="fx_thresh")
        with f3:
            vol = st.number_input("ANNUAL VOL (%)", min_value=1.0, max_value=60.0, value=6.0, step=0.5, key="fx_vol") / 100.0
        with f4:
            days_fx = st.number_input("DAYS TO EXPIRY", min_value=1, max_value=365, value=30, key="fx_days")
        with f5:
            fx_dir = st.radio("DIRECTION", ["above", "below"], key="fx_dir")

        try:
            p_fx = fx_implied_probability(spot, threshold, vol, days_fx, fx_dir)
        except Exception as _fx_exc:
            st.warning(f"FX probability model error: {_fx_exc}")
            p_fx = 0.5

        kalshi_c_fx = st.number_input(
            "KALSHI CONTRACT PRICE (c)", min_value=1, max_value=99, value=50, step=1,
            key="fx_kalshi_c",
        )
        kalshi_p_fx = kalshi_c_fx / 100.0
        try:
            spread_res_fx = compute_cross_market_spread(kalshi_p_fx, p_fx)
        except Exception:
            spread_res_fx = {"spread": 0.0, "log_odds_diff": None, "significant_10pct": False, "significant_5pct": False}

        g1, g2, g3, g4 = st.columns(4)
        g1.metric("FX MODEL IMPLIED", f"{p_fx*100:.1f}c")
        g2.metric("KALSHI PRICE", f"{kalshi_c_fx}c")
        g3.metric("SPREAD (K - FX)", f"{spread_res_fx['spread']*100:+.1f}c")
        sig_label_fx = "YES (>10c)" if spread_res_fx["significant_10pct"] else ("MINOR (>5c)" if spread_res_fx["significant_5pct"] else "NO")
        g4.metric("SIGNIFICANT", sig_label_fx)

        # Sensitivity: probability as a function of spot
        import numpy as np
        spots = [spot * (0.85 + i * 0.01) for i in range(31)]
        try:
            probs_above = [fx_implied_probability(s, threshold, vol, days_fx, "above") for s in spots]
            probs_below = [fx_implied_probability(s, threshold, vol, days_fx, "below") for s in spots]
        except Exception as _sens_exc:
            st.warning(f"FX sensitivity chart error: {_sens_exc}")
            probs_above = probs_below = []

        fig_fx = go.Figure()
        fig_fx.add_trace(go.Scatter(x=[s for s in spots] if probs_above else [], y=[p * 100 for p in probs_above],
                                     name="P(above)", line={"color": GREEN, "width": 1.5}))
        fig_fx.add_trace(go.Scatter(x=[s for s in spots], y=[p * 100 for p in probs_below],
                                     name="P(below)", line={"color": RED, "width": 1.5}))
        fig_fx.add_vline(x=spot, line_dash="dash", line_color=AMBER, line_width=1,
                          annotation_text=f"Spot {spot:.3f}", annotation_font_size=9)
        fig_fx.add_vline(x=threshold, line_dash="dot", line_color=TEXT3, line_width=1,
                          annotation_text=f"Threshold {threshold:.3f}", annotation_font_size=9)
        fig_fx.update_layout(
            **plotly_dark_layout(
            title={"text": "FX PROBABILITY SENSITIVITY (vs SPOT)", "font": {"size": 10, "color": TEXT3}},
            height=230,
            xaxis_title="CAD/USD Spot", yaxis_title="Probability (%)",
            margin={"l": 40, "r": 20, "t": 30, "b": 30},
        ))
        st.plotly_chart(fig_fx, use_container_width=True)

        # ---- RATE DIFFERENTIAL MODEL ----
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:0.62rem;letter-spacing:0.1em;color:{TEXT3};text-transform:uppercase;margin-bottom:0.4rem;'>RATE DIFFERENTIAL MODEL</div>",
            unsafe_allow_html=True,
        )
        _boc_rate = 2.25
        _fed_rate = 4.25
        _rate_diff = _boc_rate - _fed_rate  # -2.00
        _rd_c1, _rd_c2, _rd_c3 = st.columns(3)
        _rd_c1.metric("BOC RATE", "2.25%")
        _rd_c2.metric("FED RATE", "4.25%")
        _rd_c3.metric("DIFFERENTIAL", f"{_rate_diff:+.2f}pp")
        if _rate_diff < 0:
            st.info("BOC rate below FED → CAD typically weakens relative to USD")
        st.caption("Rate differential is a key driver of CAD/USD spot rate over medium-term horizons")
        # ---- END RATE DIFFERENTIAL MODEL ----


def _render_ext_market_chart():
    """
Show external market data history chart (yfinance + BOC VALET).
Reads from ext_market_daily table.  Silently skips if no data yet.
"""
    with st.expander("EXTERNAL MARKET DATA (yfinance + BOC VALET)", expanded=True):
        _asset_choices = [
            "CORRA", "BOC_RATE", "CADUSD", "USDCAD",
            "SP500", "VIX", "TSX", "WTI", "GOLD", "TNX", "FVX",
            "CAGB_2Y", "CAGB_5Y", "CAGB_10Y",
        ]
        _col1, _col2 = st.columns([3, 1])
        with _col1:
            _sel_asset = st.selectbox(
                "ASSET", _asset_choices, key="ext_asset_sel",
                help="Data fetched via yfinance (FX/equity/commodity) or BOC VALET API (rates)"
            )
        with _col2:
            _ext_days = st.slider("DAYS", 30, 365, 180, key="ext_days_slider")

        _ext_df, _ext_err = get_external_market_prices([_sel_asset], days=_ext_days)
        if _ext_df.empty:
            st.info(
                f"No historical price data available for **{_sel_asset}** in the database yet. "
                f"The external market data pipeline populates this table with yfinance and BOC VALET data. "
                f"Connect the external market data pipeline to populate historical price data for this asset.",
                icon="📊",
            )
            return

        if "obs_date" not in _ext_df.columns or "close_val" not in _ext_df.columns:
            st.info(f"Price data for **{_sel_asset}** is missing expected columns (obs_date / close_val).")
            return
        _ts  = pd.to_datetime(_ext_df["obs_date"])
        _val = pd.to_numeric(_ext_df["close_val"], errors="coerce")

        _is_rate = _sel_asset in ("CORRA", "BOC_RATE", "CAGB_2Y", "CAGB_5Y", "CAGB_10Y", "TNX", "FVX")
        _y_label = "Rate (%)" if _is_rate else "Price"

        _fig_ext = go.Figure()
        _fig_ext.add_trace(go.Scatter(
            x=_ts, y=_val, name=_sel_asset,
            mode="lines",
            line={"color": CYAN, "width": 1.5},
        ))
        _fig_ext.update_layout(**plotly_dark_layout(
            title={"text": f"{_sel_asset} — {_ext_days}d history", "font": {"size": 10, "color": TEXT3}},
            height=260,
            xaxis_title="", yaxis_title=_y_label,
            margin={"l": 50, "r": 20, "t": 35, "b": 20},
        ))
        st.plotly_chart(_fig_ext, use_container_width=True)
        _src = _ext_df["source"].iloc[0] if "source" in _ext_df.columns and len(_ext_df) else "?"
        st.caption(f"{len(_ext_df):,} observations · source: {_src} · "
                   f"from {str(_ts.min())[:10]} to {str(_ts.max())[:10]}")


def _info_panel():
    st.markdown(
        f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:1.5rem;border-radius:3px;'>
<div style='font-size:0.65rem;letter-spacing:0.08em;text-transform:uppercase;
color:{TEXT3};margin-bottom:0.75rem;'>ABOUT THIS PAGE</div>
<div style='font-size:0.78rem;color:{TEXT2};font-family:JetBrains Mono,monospace;line-height:1.6;'>
Compares Kalshi contract implied probabilities against traditional market-derived
probabilities for the same underlying event.
<br><br>
<b>Data sources (all free, no API keys required)</b><br>
&middot; Bank of Canada VALET API (CORRA, policy rate, GoC bonds, CPI)<br>
&middot; yfinance (CAD/USD, S&amp;P 500, TSX, VIX, WTI, Gold, US Treasuries)<br>
&middot; Synthesis WebSocket (live Kalshi L2 orderbook, BBO quotes)<br>
<br>
<b>Interpretation</b><br>
A persistent non-zero spread between Kalshi and traditional markets may represent
a mispricing, information lag, liquidity premium, or jurisdiction-specific
restriction. Spreads typically narrow as event date approaches.
</div>
</div>""",
        unsafe_allow_html=True,
    )

    # Methodology tabs
    st.markdown("<br>", unsafe_allow_html=True)
    t1, t2, t3 = st.tabs(["HYPOTHESIS", "DATA SOURCES", "LIMITATIONS"])

    with t1:
        st.markdown("""
**H1:** Large Kalshi/traditional probability spreads mean-revert
**H2:** Kalshi incorporates Canadian macro information more slowly than rates markets
**H3:** Large spreads more likely when Kalshi liquidity is low
**H4:** Apparent arb disappears after fees and depth constraints
**H5:** Cross-market spreads widen around major macro releases
""")
    with t2:
        st.markdown("""
| Asset | Source | Update Frequency | API Key? |
|-------|--------|-----------------|---------|
| CORRA / Bank of Canada rate | BOC VALET API | Daily | None |
| Canadian government bonds (2Y/5Y/10Y) | BOC VALET API | Daily | None |
| Canadian CPI, unemployment | BOC VALET API | Monthly | None |
| CAD/USD, USD/CAD FX | yfinance | Daily | None |
| S&P 500, TSX Composite | yfinance | Daily | None |
| VIX (volatility index) | yfinance | Daily | None |
| WTI crude oil (front-month) | yfinance | Daily | None |
| Gold (front-month futures) | yfinance | Daily | None |
| US 5Y / 10Y Treasury yields | yfinance | Daily | None |
| Kalshi BBO quotes | Synthesis WebSocket | ~30s | Synthesis key |

External market data is refreshed automatically when the cross-asset pipeline is connected.
""")
    with t3:
        st.markdown("""
**Limitations:**
- Cross-asset spreads are a *relative value* signal — the two legs settle differently
- All data sourced from free public APIs (yfinance, BOC VALET)
- yfinance provides daily bars only (no intraday); BOC VALET is T+1 for most series
- The probability engine uses simplified OIS → rate models (not a full term-structure model)
- Spread charts require the cross-asset pipeline to be connected and running

Cross-asset analysis is a research component. No live trading signals are generated.
""")


def _no_data_panel(market_id: str, asset: str, err: str | None):
    from dashboard.data_layer import get_system_health as _gh
    _h = _gh()
    _is_sqlite = not _h.get("db_connected", False) and _h.get("db_mode") == "sqlite"

    if _is_sqlite:
        st.markdown(
            f"""<div style='background:{PANEL};border:1px solid {BORDER};border-left:3px solid {AMBER};
padding:1.25rem 1.5rem;border-radius:3px;'>
<div style='font-family:JetBrains Mono,monospace;font-size:0.82rem;color:{TEXT3};'>
NO SPREAD DATA
</div>
<div style='font-size:0.72rem;color:{TEXT2};margin-top:0.5rem;line-height:1.7;'>
No cross-asset spread data for <b>{market_id}</b>.<br>
The <code>cross_asset_spreads</code> table is populated by the live probability engine
(Synthesis WebSocket + PostgreSQL). The calculators below work without a database.
</div>
</div>""",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""<div style='background:{PANEL};border:1px solid {BORDER};padding:1.5rem;border-radius:3px;'>
<div style='font-family:JetBrains Mono,monospace;font-size:0.85rem;color:{TEXT3};'>
NO CORRELATION DATA
</div>
<div style='font-size:0.72rem;color:{TEXT2};margin-top:0.5rem;'>
No cross-asset data found for <b>{market_id}</b> vs <b>{asset}</b>.
{"The cross-asset pipeline has not recorded spread data for this pair yet." if err else ""}
</div>
<div style='font-size:0.7rem;color:{TEXT3};margin-top:0.75rem;font-family:JetBrains Mono,monospace;'>
Run the cross-asset pipeline to populate spread data for this market/asset pair,
then reload the dashboard.
</div>
</div>""",
            unsafe_allow_html=True,
        )

