"""
cross_asset/live_feed.py
=========================
Background daemon that polls live cross-asset data every 5 minutes and writes
results to the `cross_asset_live` table in PostgreSQL (created on first run).

Sources (all free / no API key required for core data):
  - Bank of Canada VALET API: CORRA, policy rate
  - Yahoo Finance: CADUSD, VIX, WTI crude

Usage:
    # Start as a background daemon
    python cross_asset/live_feed.py

    # Import and use get_latest() from dashboard / analysis code
    from analysis.live_feed import get_latest

    data = get_latest()
    # {"corra": 0.0275, "policy_rate": 0.0275, "cadusd": 0.731, ...}
"""

from __future__ import annotations

import logging
import os
import sys
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

# Allow import from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

# Poll interval
_POLL_INTERVAL_S = 300  # 5 minutes

# Thread-safe in-memory cache for get_latest()
_cache_lock = threading.Lock()
_latest_data: Dict[str, Any] = {}
_latest_ts: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Data fetching helpers (reuse cross_asset/live_data.py where possible)
# ---------------------------------------------------------------------------

#: BOC VALET series IDs, verified against the live API. These must match the
#: IDs used by cross_asset/historical_data.py so the live panel and the history
#: describe the same series. Values arrive as percents and are stored as
#: decimals (2.25 -> 0.0225).
_BOC_SERIES = {
    "corra":       "AVG.INTWO",
    "policy_rate": "V39079",
    "ca_2y":       "BD.CDN.2YR.DQ.YLD",
    "ca_5y":       "BD.CDN.5YR.DQ.YLD",
    "ca_10y":      "BD.CDN.10YR.DQ.YLD",
}

#: yfinance tickers for the FX / equity / commodity side of the panel.
_YF_TICKERS = {
    # FX
    "cadusd":  "CADUSD=X",
    "usdcad":  "USDCAD=X",
    # Equities
    "tsx":     "^GSPTSE",
    "sp500":   "^GSPC",
    "nasdaq":  "^IXIC",
    "dji":     "^DJI",
    "vix":     "^VIX",
    # Commodities
    "wti":     "CL=F",
    "natgas":  "NG=F",
    "gold":    "GC=F",
    # US rates
    "tnx":     "^TNX",
    "fvx":     "^FVX",
    # Crypto (real-time on yfinance)
    "btc":     "BTC-USD",
    "eth":     "ETH-USD",
    "doge":    "DOGE-USD",
    "xrp":     "XRP-USD",
    "bnb":     "BNB-USD",
}


def _fetch_boc_series(metric: str, series_id: str, result: Dict[str, Any]) -> None:
    """Fetch one BOC VALET series into ``result`` as a decimal rate."""
    import requests

    resp = requests.get(
        f"https://www.bankofcanada.ca/valet/observations/{series_id}/json",
        params={"recent": 1},
        timeout=10,
    )
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    if not obs:
        return
    for key, value in obs[-1].items():
        if key == "d":
            result.setdefault("boc_date", value)
            continue
        raw = value.get("v") if isinstance(value, dict) else value
        try:
            result[metric] = float(raw) / 100.0  # percent -> decimal
        except (TypeError, ValueError):
            pass


def _fetch_live_snapshot() -> Dict[str, Any]:
    """
    Fetch the latest cross-asset rates.
    Returns a flat dict of {metric_name: value}.
    Falls back gracefully on any single-source failure.
    """
    result: Dict[str, Any] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    # -- BOC VALET: CORRA, policy rate, and the 2y/5y/10y curve --
    for metric, series_id in _BOC_SERIES.items():
        try:
            _fetch_boc_series(metric, series_id, result)
        except Exception as exc:
            logger.warning("BOC %s (%s) fetch failed: %s", metric, series_id, exc)

    # -- yfinance: FX, equity indices, volatility, crude --
    try:
        import yfinance as yf
        for name, ticker in _YF_TICKERS.items():
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period="2d", interval="1d", auto_adjust=True)
                if not hist.empty:
                    result[name] = float(hist["Close"].iloc[-1])
                    if len(hist) >= 2:
                        prev = float(hist["Close"].iloc[-2])
                        curr = float(hist["Close"].iloc[-1])
                        result[f"{name}_1d_chg"] = curr - prev
                        result[f"{name}_1d_pct"] = (curr - prev) / prev if prev else 0.0
            except Exception as exc:
                logger.warning("yfinance %s failed: %s", ticker, exc)
    except ImportError:
        logger.warning("yfinance not installed — skipping FX/equity data")

    return result


# ---------------------------------------------------------------------------
# PostgreSQL persistence
# ---------------------------------------------------------------------------

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cross_asset_live (
    id          BIGSERIAL   PRIMARY KEY,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metric      TEXT        NOT NULL,
    value       DOUBLE PRECISION,
    UNIQUE (fetched_at, metric)
);
CREATE INDEX IF NOT EXISTS idx_cal_metric_ts
    ON cross_asset_live (metric, fetched_at DESC);
"""


def _ensure_table(engine) -> None:
    from sqlalchemy import text
    with engine.begin() as conn:
        for stmt in _TABLE_DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))


def _write_to_db(engine, snapshot: Dict[str, Any]) -> None:
    from sqlalchemy import text
    fetched_at = snapshot.get("fetched_at", datetime.now(timezone.utc).isoformat())
    rows = [
        {"fetched_at": fetched_at, "metric": k, "value": v}
        for k, v in snapshot.items()
        if k not in ("fetched_at", "boc_date") and isinstance(v, (int, float))
    ]
    if not rows:
        return
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO cross_asset_live (fetched_at, metric, value)
                VALUES (:fetched_at, :metric, :value)
                ON CONFLICT (fetched_at, metric) DO NOTHING
            """),
            rows,
        )
    logger.info("Wrote %d cross-asset metrics to DB", len(rows))


def _get_engine():
    """Lazy-init SQLAlchemy engine from DATABASE_URL."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        logger.warning("DATABASE_URL not set — DB writes disabled")
        return None

    try:
        from sqlalchemy import create_engine
        engine = create_engine(url, pool_pre_ping=True, pool_size=2)
        return engine
    except Exception as exc:
        logger.warning("Could not create DB engine: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API: get_latest()
# ---------------------------------------------------------------------------

def get_latest() -> Dict[str, Any]:
    """
    Return the most recently fetched cross-asset snapshot.
    Thread-safe — can be called from Streamlit without locking.
    If the daemon has not run yet, fetches once synchronously.
    """
    with _cache_lock:
        if _latest_data:
            return dict(_latest_data)

    # First call — fetch synchronously
    logger.info("get_latest(): no cache yet, fetching synchronously...")
    snapshot = _fetch_live_snapshot()
    with _cache_lock:
        _latest_data.update(snapshot)
        global _latest_ts
        _latest_ts = datetime.now(timezone.utc)
    return dict(snapshot)


def get_latest_ts() -> Optional[datetime]:
    """Return the UTC timestamp of the most recent successful fetch."""
    with _cache_lock:
        return _latest_ts


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------

_stop_event = threading.Event()


def _poll_loop(engine=None, interval_s: int = _POLL_INTERVAL_S) -> None:
    """Main daemon loop. Runs until _stop_event is set."""
    global _latest_ts

    logger.info("Cross-asset live feed started (poll every %ds)", interval_s)

    while not _stop_event.is_set():
        try:
            snapshot = _fetch_live_snapshot()
            with _cache_lock:
                _latest_data.clear()
                _latest_data.update(snapshot)
                _latest_ts = datetime.now(timezone.utc)

            logger.info(
                "Cross-asset snapshot: CORRA=%.4f  CADUSD=%.4f  SP500=%.0f  BTC=%.0f  VIX=%.2f",
                snapshot.get("corra", float("nan")),
                snapshot.get("cadusd", float("nan")),
                snapshot.get("sp500", float("nan")),
                snapshot.get("btc", float("nan")),
                snapshot.get("vix", float("nan")),
            )

            if engine is not None:
                try:
                    _write_to_db(engine, snapshot)
                except Exception as exc:
                    logger.warning("DB write failed: %s", exc)

        except Exception as exc:
            logger.error("Poll loop error: %s", exc, exc_info=True)

        _stop_event.wait(interval_s)

    logger.info("Cross-asset live feed stopped.")


def start_daemon(interval_s: int = _POLL_INTERVAL_S) -> threading.Thread:
    """
    Start the polling daemon as a background daemon thread.
    Returns the thread object. Calling code need not join it.
    """
    engine = _get_engine()
    if engine is not None:
        try:
            _ensure_table(engine)
        except Exception as exc:
            logger.warning("Could not create cross_asset_live table: %s", exc)

    t = threading.Thread(
        target=_poll_loop,
        kwargs={"engine": engine, "interval_s": interval_s},
        daemon=True,
        name="cross-asset-feed",
    )
    t.start()
    logger.info("Cross-asset daemon thread started (tid=%d)", t.ident or 0)
    return t


def stop_daemon() -> None:
    """Signal the daemon to stop."""
    _stop_event.set()


# ---------------------------------------------------------------------------
# CLI entry point (run as a blocking process)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )

    def _handle_signal(sig, frame):
        logger.info("Received signal %d — stopping.", sig)
        stop_daemon()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    engine = _get_engine()
    if engine is not None:
        try:
            _ensure_table(engine)
            logger.info("cross_asset_live table ready.")
        except Exception as exc:
            logger.warning("Table setup failed: %s", exc)
    else:
        logger.warning("Running without database — data will only be held in memory.")

    # Run in main thread (blocking)
    _poll_loop(engine=engine, interval_s=_POLL_INTERVAL_S)
