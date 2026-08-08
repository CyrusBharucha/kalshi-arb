"""
dashboard/arb_logger.py
=======================
Persist every confirmed live-arb detection to a durable log.

Two backends (both attempted, both non-fatal on failure):
  1. JSONL flat file at /tmp/kalshi_arb_log_{YYYYMMDD}.jsonl
     — one JSON object per line, easy to load with pandas later.
  2. PostgreSQL arbitrage_opportunities table (when DATABASE_URL is set).

All I/O is thread-safe via a module-level lock.
Never raises — a logger must not break the scanner.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()
import tempfile as _tempfile
_LOG_DIR = Path(_tempfile.gettempdir())


def _today_path() -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _LOG_DIR / f"kalshi_arb_log_{date_str}.jsonl"


def _write_jsonl(opp: dict) -> None:
    path = _today_path()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(opp, default=str) + "\n")


def _write_db(opp: dict) -> None:
    """Insert into arbitrage_opportunities table. Skips silently if DB unavailable."""
    try:
        from database.repository import session_scope
        from database.models import ArbitrageOpportunity

        legs = opp.get("legs") or ([opp["ticker"]] if opp.get("ticker") else [])
        gross_c = float(opp.get("gross_edge_cents", 0)) / 100.0
        fees_c  = float(opp.get("fees_cents", 0)) / 100.0
        net_c   = float(opp.get("net_edge_cents", 0)) / 100.0
        qty     = opp.get("executable_contracts")

        row = ArbitrageOpportunity(
            opportunity_id=uuid.uuid4(),
            detected_at=datetime.fromtimestamp(
                opp.get("detected_at_ts", time.time()), tz=timezone.utc
            ),
            strategy_type=opp.get("strategy", "unknown"),
            classification="A",
            markets_involved=legs,
            prices_json={
                "yes_ask": opp.get("yes_ask"),
                "no_ask":  opp.get("no_ask"),
                "legs":    legs,
            },
            gross_edge=round(gross_c, 4) if gross_c else None,
            total_fees=round(fees_c, 4) if fees_c else None,
            net_edge=round(net_c, 4) if net_c else None,
            max_executable_contracts=float(qty) if qty else None,
            max_gross_profit=round(gross_c * float(qty), 4) if gross_c and qty else None,
            max_net_profit=round(net_c * float(qty), 4) if net_c and qty else None,
            status="open",
        )
        with session_scope() as sess:
            sess.add(row)
    except Exception as exc:
        logger.warning("arb_logger: PG write failed for %s: %s", opp.get("ticker", "unknown"), exc)


def log_opportunity(opp: dict) -> None:
    """
    Log a confirmed arb opportunity to JSONL and (if available) PostgreSQL.
    Call this right after state.add_opportunity() — never blocks the scanner.
    """
    with _lock:
        try:
            _write_jsonl(opp)
        except Exception as exc:
            logger.debug("arb_logger JSONL write failed: %s", exc)

        try:
            _write_db(opp)
        except Exception as exc:
            logger.debug("arb_logger DB write outer failed: %s", exc)


def load_today_log() -> list[dict]:
    """Load today's JSONL log for display in the dashboard."""
    path = _today_path()
    if not path.exists():
        logger.debug("arb_logger: JSONL log not found — may have been lost on container restart")
        return []
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception as exc:
        logger.warning("arb_logger load_today_log failed: %s", exc)
    return rows
