"""
scripts/auto_refresh.py
========================
Long-running data refresh daemon.

Refreshes external market data (BOC VALET + yfinance) on a daily schedule
so CORRA, CAD/USD, WTI, and other cross-asset data stay current.

Run in background (keep terminal open or use Windows Task Scheduler):
  python scripts\auto_refresh.py

Stops with Ctrl+C.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent.parent / "logs" / "auto_refresh.log",
            encoding="utf-8",
        )
    ],
)
logger = logging.getLogger(__name__)

REFRESH_INTERVAL_HOURS = 24
SPREADS_INTERVAL_HOURS = 1


def run_external_refresh():
    logger.info("=== EXTERNAL MARKET DATA REFRESH ===")
    try:
        from analysis.market_data import run_refresh
        counts = run_refresh(days_back=365)
        total = sum(counts.values())
        logger.info("External refresh complete: %d rows across %d assets", total, len(counts))
    except Exception as exc:
        logger.error("External refresh failed: %s", exc)


def run_spreads_pipeline():
    logger.info("=== CROSS-ASSET SPREADS PIPELINE ===")
    try:
        from analysis.spreads_pipeline import run_pipeline
        counts = run_pipeline(days_back=30)
        logger.info("Spreads pipeline: %s", counts)
    except Exception as exc:
        logger.error("Spreads pipeline failed: %s", exc)


def main():
    # Ensure logs directory exists
    logs_dir = Path(__file__).parent.parent / "logs"
    logs_dir.mkdir(exist_ok=True)

    logger.info("Auto-refresh daemon started.")
    logger.info("  External data: every %dh", REFRESH_INTERVAL_HOURS)
    logger.info("  Spreads:        every %dh", SPREADS_INTERVAL_HOURS)
    logger.info("  Stop: Ctrl+C")

    last_external = 0.0
    last_spreads = 0.0

    # Run immediately on startup
    run_external_refresh()
    run_spreads_pipeline()
    last_external = time.monotonic()
    last_spreads = time.monotonic()

    while True:
        try:
            now = time.monotonic()
            if now - last_external >= REFRESH_INTERVAL_HOURS * 3600:
                run_external_refresh()
                last_external = now

            if now - last_spreads >= SPREADS_INTERVAL_HOURS * 3600:
                run_spreads_pipeline()
                last_spreads = now

            time.sleep(60)

        except KeyboardInterrupt:
            logger.info("Auto-refresh daemon stopped by user.")
            break
        except Exception as exc:
            logger.error("Daemon loop error: %s", exc)
            time.sleep(60)


if __name__ == "__main__":
    main()
