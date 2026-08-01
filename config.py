"""
config.py
Central configuration for the Kalshi Arbitrage Engine.
All secrets come from environment variables - never hardcode credentials.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present (development only)
load_dotenv(Path(__file__).parent / ".env")

# On Streamlit Cloud, secrets live in st.secrets — pull them into env vars
# so the rest of config.py (and the whole codebase) stays unchanged.
try:
    import streamlit as st
    _s = st.secrets
    for _key in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASS",
                 "KALSHI_KEY_ID", "KALSHI_PRIVKEY_PATH", "KALSHI_PRIVATE_KEY_PATH",
                 "FRED_API_KEY", "ALPHA_VANTAGE_KEY",
                 "SYNTHESIS_API_KEY", "SYNTHESIS_SECRET_KEY"):
        if _key in _s and not os.environ.get(_key):
            os.environ[_key] = str(_s[_key])
    # Also support a flat [postgres] table in secrets.toml
    if "postgres" in _s:
        _pg = _s["postgres"]
        for _k, _ek in (("host","DB_HOST"),("port","DB_PORT"),("dbname","DB_NAME"),
                        ("user","DB_USER"),("password","DB_PASS")):
            if _k in _pg and not os.environ.get(_ek):
                os.environ[_ek] = str(_pg[_k])
    # Support a single DATABASE_URL (Neon / Supabase / Railway style)
    if "DATABASE_URL" in _s and not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = str(_s["DATABASE_URL"])
except Exception:
    pass  # Not running under Streamlit — env vars already loaded from .env


# -- Kalshi API -----------------------------------------------------------------
KALSHI_BASE_URL = os.getenv("KALSHI_BASE_URL", "https://trading-api.kalshi.com/trade-api/v2")
KALSHI_WS_URL   = os.getenv("KALSHI_WS_URL",   "wss://trading-api.kalshi.com/trade-api/ws/v2")
KALSHI_KEY_ID   = os.getenv("KALSHI_KEY_ID", "")          # UUID from Kalshi dashboard
# Accept both env-var spellings for the private key path
KALSHI_PRIVKEY_PATH = (
    os.getenv("KALSHI_PRIVKEY_PATH", "")
    or os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
)

# Candlestick period options (minutes)
KALSHI_CANDLE_PERIODS = (1, 60, 1440)

# Rate-limit defaults (Basic tier - conservative)
KALSHI_READ_RPS  = int(os.getenv("KALSHI_READ_RPS",  "15"))   # stay under 20/s
KALSHI_WRITE_RPS = int(os.getenv("KALSHI_WRITE_RPS", "8"))    # stay under 10/s


# -- PostgreSQL -----------------------------------------------------------------
# Supports either a single DATABASE_URL (Neon / Supabase / Railway)
# OR individual DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASS env vars.
_raw_url = os.getenv("DATABASE_URL", "")
if _raw_url:
    # Neon returns postgres:// scheme; SQLAlchemy needs postgresql+psycopg2://
    _raw_url = _raw_url.replace("postgres://", "postgresql://", 1)
    if "+" not in _raw_url.split("://")[0]:
        _raw_url = _raw_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    DB_URL       = _raw_url
    DB_URL_ASYNC = _raw_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    # Parse individual pieces for code that uses them directly
    try:
        from urllib.parse import urlparse as _up
        _u = _up(_raw_url)
        DB_HOST = _u.hostname or "localhost"
        DB_PORT = _u.port or 5432
        DB_NAME = (_u.path or "/kalshi_arb").lstrip("/")
        DB_USER = _u.username or "postgres"
        DB_PASS = _u.password or ""
    except Exception:
        DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS = "localhost", 5432, "kalshi_arb", "postgres", ""
else:
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", "5432"))
    DB_NAME = os.getenv("DB_NAME", "kalshi_arb")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASS = os.getenv("DB_PASS", "")
    DB_URL = (
        f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    DB_URL_ASYNC = (
        f"postgresql+asyncpg://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )


# -- External Market Data -------------------------------------------------------
# Bank of Canada VALET API (free, no key required)
BOC_VALET_URL = "https://www.bankofcanada.ca/valet"

# FRED (Federal Reserve Economic Data) - free API key
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FRED_BASE_URL = "https://api.stlouisfed.org/fred"

# Alpha Vantage (FX, equities) - free tier available
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

# EIA (oil/energy data - free API key)
EIA_API_KEY = os.getenv("EIA_API_KEY", "")
EIA_BASE_URL = "https://api.eia.gov/v2"


# -- Execution Simulation -------------------------------------------------------
# Fee formula: ceil(0.07 * P * (1-P) * 100) / 100 per contract
# Cap: $0.035 per contract
FEE_ALPHA        = 0.07        # fee coefficient
FEE_CAP          = 0.035       # max taker fee per contract
MAKER_FEE_RATIO  = 0.25        # maker fee ≈ 25% of taker (rough)

# Slippage model defaults (conservative)
DEFAULT_SLIPPAGE_BPS = 5       # 5 basis points default slippage assumption
MIN_EXECUTABLE_CONTRACTS = 1   # minimum position size

# Arbitrage edge thresholds
MIN_GROSS_EDGE = 0.005         # $0.005 minimum gross edge to log as candidate
MIN_NET_EDGE   = 0.001         # $0.001 minimum net edge to flag as executable


# -- Data Collection ------------------------------------------------------------
SNAPSHOT_INTERVAL_S  = 30      # live snapshot polling interval (seconds)
ORDERBOOK_DEPTH      = 10      # levels to request per side
HISTORICAL_CANDLE_PERIOD = 60  # default candle period for historical ingestion (minutes)

# How far back to ingest on first run (days)
HISTORICAL_LOOKBACK_DAYS = 365


# -- Canadian Cross-Asset Focus -------------------------------------------------
# These Kalshi series/event tickers are our primary cross-asset targets.
# Update as Kalshi adds/changes contracts.
CANADIAN_TARGET_SERIES = [
    "KXBOC",          # Bank of Canada rate decisions (placeholder - verify ticker)
    "KXCADCPI",       # Canadian CPI (placeholder)
    "KXWTI",          # WTI crude oil (placeholder)
    "KXCADUSD",       # CAD/USD FX (placeholder)
]

# Canadian macro event types for event-study analysis
CANADIAN_MACRO_EVENTS = [
    "boc_rate_decision",
    "canada_cpi",
    "canada_employment",
    "canada_gdp",
    "wti_price",
]


# -- Logging --------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE  = os.getenv("LOG_FILE", "")   # empty = stdout only
