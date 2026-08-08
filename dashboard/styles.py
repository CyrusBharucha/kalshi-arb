"""
dashboard/styles.py
Institutional dark-theme CSS for the Kalshi Arbitrage Engine dashboard.
Injected via st.markdown(..., unsafe_allow_html=True) in app.py.
"""

from __future__ import annotations

# -- Palette tokens -------------------------------------------------------------
BG        = "#0B0F14"
BG2       = "#111820"
PANEL     = "#151D26"
PANEL2    = "#1A2332"
BORDER    = "#26313D"
BORDER2   = "#1E2A38"
TEXT      = "#F1F5F9"
TEXT2     = "#94A3B8"
TEXT3     = "#64748B"
GREEN     = "#22C55E"
GREEN_DIM = "#166534"
RED       = "#EF4444"
RED_DIM   = "#7F1D1D"
AMBER     = "#F59E0B"
AMBER_DIM = "#78350F"
BLUE      = "#3B82F6"
BLUE_DIM  = "#1E3A5F"
CYAN      = "#06B6D4"
PURPLE    = "#A855F7"


import functools as _functools


@_functools.lru_cache(maxsize=1)
def inject_css() -> str:
    """Return the full CSS block to inject into the Streamlit app. Memoised — computed once per process."""
    return f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@300;400;500&display=swap" rel="stylesheet">

<style>
/* -- Root & global ----------------------------------------------------------- */
html, body, [class*="css"] {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    color: {TEXT};
}}
.stApp {{
    background-color: {BG};
}}
section[data-testid="stSidebar"] {{
    background-color: {BG2};
    border-right: 1px solid {BORDER};
}}
/* Hide the close/collapse button completely */
[data-testid="stBaseButton-headerNoPadding"] {{
    display: none !important;
}}
/* collapsedControl handled below — shown only when sidebar is actually hidden */
section[data-testid="stSidebar"] .stMarkdown p {{
    color: {TEXT2};
    font-size: 0.72rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}}
/* Remove Streamlit top padding */
.block-container {{
    padding-top: 1rem;
    padding-bottom: 2rem;
    max-width: 1600px;
}}

/* -- Headings --------------------------------------------------------------- */
h1 {{
    font-size: 1.25rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.04em !important;
    text-transform: uppercase !important;
    color: {TEXT} !important;
    border-bottom: 1px solid {BORDER};
    padding-bottom: 0.5rem;
    margin-bottom: 1rem;
}}
h2 {{
    font-size: 0.95rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.06em !important;
    text-transform: uppercase !important;
    color: {TEXT2} !important;
}}
h3 {{
    font-size: 0.85rem !important;
    font-weight: 500 !important;
    color: {TEXT2} !important;
    letter-spacing: 0.04em !important;
}}

/* -- Metric cards ----------------------------------------------------------- */
[data-testid="metric-container"] {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 0.75rem 1rem;
}}
[data-testid="metric-container"] label {{
    font-family: 'Inter', sans-serif;
    font-size: 0.65rem !important;
    letter-spacing: 0.1em !important;
    text-transform: uppercase !important;
    color: {TEXT2} !important;
    font-weight: 500 !important;
}}
[data-testid="metric-container"] [data-testid="stMetricValue"] {{
    font-family: 'JetBrains Mono', 'Courier New', monospace !important;
    font-size: 1.4rem !important;
    font-weight: 400 !important;
    color: {TEXT} !important;
    letter-spacing: -0.02em;
}}
[data-testid="stMetricDelta"] {{
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.75rem !important;
}}

/* -- Dataframe / tables ----------------------------------------------------- */
[data-testid="stDataFrame"] {{
    border: 1px solid {BORDER};
    border-radius: 2px;
}}
.dataframe thead tr th {{
    background: {PANEL2} !important;
    color: {TEXT3} !important;
    font-size: 0.65rem !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
    font-family: 'Inter', sans-serif !important;
    border-bottom: 1px solid {BORDER} !important;
    padding: 6px 12px !important;
}}
.dataframe tbody tr td {{
    background: {PANEL} !important;
    color: {TEXT} !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.75rem !important;
    border-bottom: 1px solid {BORDER2} !important;
    padding: 5px 12px !important;
}}
.dataframe tbody tr:hover td {{
    background: {PANEL2} !important;
}}

/* -- Plotly chart backgrounds ----------------------------------------------- */
.js-plotly-plot .plotly .bg {{
    fill: {PANEL} !important;
}}

/* -- Sidebar radio / select ------------------------------------------------- */
/* Hide Streamlit's auto-generated multipage nav list only.
   In Streamlit ≥1.37 the stSidebarNav wrapper contains ALL sidebar content,
   so we must NOT hide the wrapper — only the nav items list inside it. */
[data-testid="stSidebarNavItems"] {{
    display: none !important;
}}
[data-testid="stSidebarNavSeparator"] {{
    display: none !important;
}}
div[role="radiogroup"] label {{
    font-size: 0.75rem !important;
    letter-spacing: 0.06em !important;
    text-transform: uppercase !important;
    color: {TEXT2} !important;
    padding: 0.3rem 0 !important;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}}
div[role="radiogroup"] label:has(input:checked) {{
    color: {TEXT} !important;
}}

/* -- Widget labels (STRATEGY, MIN NET EDGE, SORT BY, etc.) ------------------ */
[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"] p,
label[data-testid="stWidgetLabel"] {{
    color: {TEXT} !important;
    font-size: 0.68rem !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    opacity: 1 !important;
}}

/* -- Select boxes & inputs -------------------------------------------------- */
.stSelectbox > div > div,
.stTextInput > div > div > input,
.stNumberInput > div > div > input {{
    background-color: {PANEL} !important;
    border: 1px solid {BORDER} !important;
    color: {TEXT} !important;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem !important;
    border-radius: 2px !important;
}}

/* -- Buttons ---------------------------------------------------------------- */
.stButton > button {{
    background: {PANEL2};
    border: 1px solid {BORDER};
    color: {TEXT};
    font-size: 0.7rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    border-radius: 2px;
    padding: 0.35rem 0.8rem;
    transition: all 0.15s ease;
}}
.stButton > button:hover {{
    background: {BLUE_DIM};
    border-color: {BLUE};
    color: {TEXT};
}}

/* -- Dividers --------------------------------------------------------------- */
hr {{
    border: none;
    border-top: 1px solid {BORDER};
    margin: 0.75rem 0;
}}

/* -- Tabs ------------------------------------------------------------------- */
.stTabs [data-baseweb="tab-list"] {{
    background: transparent;
    gap: 0;
    border-bottom: 1px solid {BORDER};
}}
.stTabs [data-baseweb="tab"] {{
    background: transparent;
    color: {TEXT3};
    font-size: 0.7rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 0.4rem 1rem;
    border-bottom: 2px solid transparent;
    border-radius: 0;
}}
.stTabs [aria-selected="true"] {{
    background: transparent;
    color: {TEXT};
    border-bottom: 2px solid {BLUE};
}}
.stTabs [data-baseweb="tab-panel"] {{
    padding-top: 1rem;
}}

/* -- Expanders -------------------------------------------------------------- */
.streamlit-expanderHeader {{
    background: {PANEL} !important;
    border: 1px solid {BORDER} !important;
    border-radius: 2px !important;
    font-size: 0.75rem !important;
    letter-spacing: 0.06em !important;
    color: {TEXT2} !important;
}}
.streamlit-expanderContent {{
    background: {BG2} !important;
    border: 1px solid {BORDER} !important;
    border-top: none !important;
}}

/* -- Info / warning / error boxes ------------------------------------------ */
.stInfo {{
    background: {BLUE_DIM} !important;
    border: 1px solid {BLUE} !important;
    color: {TEXT} !important;
    font-size: 0.8rem !important;
    border-radius: 2px !important;
}}
.stWarning {{
    background: {AMBER_DIM} !important;
    border: 1px solid {AMBER} !important;
    font-size: 0.8rem !important;
    border-radius: 2px !important;
}}
.stError {{
    background: {RED_DIM} !important;
    border: 1px solid {RED} !important;
    font-size: 0.8rem !important;
    border-radius: 2px !important;
}}
.stSuccess {{
    background: {GREEN_DIM} !important;
    border: 1px solid {GREEN} !important;
    font-size: 0.8rem !important;
    border-radius: 2px !important;
}}

/* -- Custom panel class ----------------------------------------------------- */
.kae-panel {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 1rem 1.25rem;
    margin-bottom: 0.75rem;
}}
.kae-panel-title {{
    font-size: 0.6rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {TEXT3};
    margin-bottom: 0.5rem;
    font-weight: 500;
}}
.kae-mono {{
    font-family: 'JetBrains Mono', 'Courier New', monospace;
}}
.kae-label {{
    font-size: 0.6rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: {TEXT3};
    font-weight: 500;
}}
.kae-value {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.1rem;
    color: {TEXT};
    letter-spacing: -0.01em;
}}
.kae-green {{ color: {GREEN}; }}
.kae-red   {{ color: {RED}; }}
.kae-amber {{ color: {AMBER}; }}
.kae-blue  {{ color: {BLUE}; }}
.kae-dim   {{ color: {TEXT3}; }}

/* Opportunity row colours */
.opp-executable {{ background: rgba(34,197,94,0.06) !important; }}
.opp-stale      {{ background: rgba(245,158,11,0.05) !important; }}
.opp-negative   {{ background: rgba(239,68,68,0.05) !important; }}

/* Spinner override */
.stSpinner > div {{
    border-color: {BLUE} transparent transparent transparent !important;
}}

/* Code blocks */
code, pre {{
    font-family: 'JetBrains Mono', monospace !important;
    background: {PANEL2} !important;
    border: 1px solid {BORDER} !important;
    border-radius: 2px !important;
    color: {CYAN} !important;
    font-size: 0.78rem !important;
}}
pre {{
    padding: 0.75rem 1rem !important;
}}

/* Progress bars */
.stProgress > div > div > div > div {{
    background: {BLUE} !important;
}}

/* Sliders */
.stSlider > div > div > div > div {{
    background: {BLUE} !important;
}}
.stSlider > div > div > div {{
    background: {BORDER} !important;
}}

/* Checkbox / Toggle */
.stCheckbox label span, .stToggle label p, .stToggle label span {{
    color: {TEXT2} !important;
    font-size: 0.75rem !important;
    letter-spacing: 0.04em !important;
}}

/* Caption text */
.stCaption {{
    color: {TEXT3} !important;
    font-size: 0.7rem !important;
}}

/* Hide Streamlit branding */
#MainMenu {{visibility: hidden;}}
footer {{visibility: hidden;}}
header {{visibility: hidden;}}

/* -- Force sidebar permanently visible — never collapse ---------------------- */
section[data-testid="stSidebar"] {{
    transform: none !important;
    margin-left: 0 !important;
    min-width: 220px !important;
    width: 220px !important;
    display: flex !important;
    visibility: visible !important;
}}
/* Hide the collapsed-control re-open arrow (sidebar is always open) */
[data-testid="collapsedControl"] {{
    display: none !important;
}}
/* Hide ALL sidebar close/collapse buttons */
[data-testid="stBaseButton-headerNoPadding"],
button[data-testid="baseButton-header"],
[data-testid="stSidebar"] button[aria-label*="sidebar"],
[data-testid="stSidebar"] button[aria-label*="Close"],
[data-testid="stSidebar"] button[aria-label*="collapse"] {{
    display: none !important;
}}

/* -- Sidebar brand block ----------------------------------------------------- */
.kae-brand {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    font-weight: 500;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: {TEXT};
    padding: 1rem 0 0.25rem 0;
}}
.kae-brand-sub {{
    font-size: 0.55rem;
    letter-spacing: 0.08em;
    color: {TEXT3};
    text-transform: uppercase;
    margin-top: -2px;
    padding-bottom: 0.75rem;
    border-bottom: 1px solid {BORDER};
    margin-bottom: 0.75rem;
}}
.kae-nav-sep {{
    font-size: 0.55rem;
    letter-spacing: 0.12em;
    color: {TEXT3};
    text-transform: uppercase;
    padding: 0.4rem 0 0.2rem 0;
    border-top: 1px solid {BORDER};
    margin-top: 0.5rem;
}}
.kae-status-dot {{
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: middle;
}}
.dot-green  {{ background: {GREEN}; }}
.dot-red    {{ background: {RED}; }}
.dot-amber  {{ background: {AMBER}; }}
.dot-gray   {{ background: {TEXT3}; }}
.kae-status-row {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.62rem;
    letter-spacing: 0.04em;
    color: {TEXT2};
    padding: 2px 0;
    display: flex;
    align-items: center;
}}
.kae-status-label {{
    color: {TEXT3};
    width: 90px;
    display: inline-block;
}}
.kae-status-val {{
    color: {TEXT};
}}
.kae-depth-bar-bid {{
    display: inline-block;
    height: 12px;
    background: rgba(34,197,94,0.3);
    border-right: 2px solid {GREEN};
    vertical-align: middle;
}}
.kae-depth-bar-ask {{
    display: inline-block;
    height: 12px;
    background: rgba(239,68,68,0.3);
    border-left: 2px solid {RED};
    vertical-align: middle;
}}
</style>
"""


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into ``base``, returning a new dict."""
    out = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def plotly_dark_layout(**overrides) -> dict:
    """Standard dark Plotly layout dict - apply to all charts.

    Keyword overrides are deep-merged onto the defaults, so a caller can do::

        fig.update_layout(**plotly_dark_layout(yaxis={"title": "Edge"}))

    and keep the themed gridlines/tickfont while adding its own title. Passing
    the same key both as ``**plotly_dark_layout()`` and as a separate keyword to
    ``update_layout`` raises ``TypeError``; route it through here instead.
    """
    base = {
        "paper_bgcolor": PANEL,
        "plot_bgcolor":  PANEL,
        "font":          {"family": "Inter, sans-serif", "color": TEXT2, "size": 11},
        "xaxis": {
            "gridcolor": BORDER, "zerolinecolor": BORDER,
            "linecolor": BORDER, "tickfont": {"family": "JetBrains Mono", "size": 10},
        },
        "yaxis": {
            "gridcolor": BORDER, "zerolinecolor": BORDER,
            "linecolor": BORDER, "tickfont": {"family": "JetBrains Mono", "size": 10},
        },
        "legend": {
            "bgcolor": PANEL2, "bordercolor": BORDER, "borderwidth": 1,
            "font": {"size": 10},
        },
        "margin": {"l": 50, "r": 20, "t": 40, "b": 40},
        "hoverlabel": {
            "bgcolor": PANEL2, "bordercolor": BORDER,
            "font": {"family": "JetBrains Mono", "size": 11},
        },
    }
    return _deep_merge(base, overrides) if overrides else base
