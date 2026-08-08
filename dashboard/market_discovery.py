"""
dashboard/market_discovery.py
==============================
Discovers Kalshi markets for live arb scanning.

Two modes:
  1. Synthesis REST API  — preferred; returns all markets with open_interest > 0
     (~42k tickers) together with event groupings needed by the ME/threshold
     scanners. Caches to disk; refreshes every SYNTHESIS_CACHE_TTL_H hours.
  2. Kalshi public REST API — fallback when no SYNTHESIS_SECRET_KEY is set.

Root-cause context: Synthesis WebSocket only streams data for tickers named
explicitly in the subscribe message (venue-only subscribe is a silent no-op,
verified via a live raw-frame trace on 2026-08-24).
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Synthesis-based discovery (primary)
# ---------------------------------------------------------------------------
_SYNTHESIS_BASE        = "https://synthesis.trade"
_SYNTHESIS_CACHE_PATH  = Path(__file__).parent.parent / "data" / "synthesis_markets_cache.json"
SYNTHESIS_CACHE_TTL_H  = 6        # hours between refreshes
_SYNTHESIS_MIN_OI      = 0.0      # open_interest threshold; 0 = all markets


def fetch_synthesis_markets(
    min_oi: float = _SYNTHESIS_MIN_OI,
    cache_ttl_hours: float = SYNTHESIS_CACHE_TTL_H,
    force_refresh: bool = False,
) -> Tuple[List[str], Dict[str, List[str]]]:
    """
    Return (tickers, event_groups) from Synthesis REST API.

    tickers      — flat list of all Kalshi market tickers for WS subscription
    event_groups — {event_id: [ticker, ...]} for ME / CE / threshold scanning

    Caches result to data/synthesis_markets_cache.json; uses cache if fresh.
    Falls back to empty event_groups on any error.
    """
    import requests as _req

    synthesis_key = os.environ.get("SYNTHESIS_SECRET_KEY", "").strip()
    if not synthesis_key:
        logger.warning("SYNTHESIS_SECRET_KEY not set — Synthesis discovery unavailable")
        return [], {}

    # --- Cache check --------------------------------------------------------
    if not force_refresh and _SYNTHESIS_CACHE_PATH.exists():
        age_h = (time.time() - _SYNTHESIS_CACHE_PATH.stat().st_mtime) / 3600
        if age_h < cache_ttl_hours:
            try:
                with open(_SYNTHESIS_CACHE_PATH) as fh:
                    cached = json.load(fh)
                tickers      = cached["tickers"]
                event_groups = cached["event_groups"]
                logger.info(
                    "Synthesis cache loaded: %d tickers, %d event groups (age %.1fh)",
                    len(tickers), len(event_groups), age_h,
                )
                return tickers, event_groups
            except Exception as exc:
                logger.warning("Synthesis cache load failed (%s) — re-fetching", exc)

    # --- Fetch from Synthesis REST ------------------------------------------
    headers = {"X-PROJECT-API-KEY": synthesis_key, "User-Agent": "kalshi-arb"}
    tickers: List[str]                    = []
    event_groups: Dict[str, List[str]]   = {}
    offset = 0
    total_events = 0

    logger.info("Fetching Synthesis Kalshi market list (this takes ~60-90s for full universe)…")
    while True:
        try:
            r = _req.get(
                f"{_SYNTHESIS_BASE}/api/v1/kalshi/markets?limit=250&offset={offset}",
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            batch = r.json().get("response", [])
        except Exception as exc:
            logger.warning("Synthesis fetch error at offset=%d: %s — stopping early", offset, exc)
            break

        if not batch:
            break

        for event_data in batch:
            ev        = event_data.get("event", {})
            event_id  = ev.get("event_id") or ev.get("series_id", "")
            for m in event_data.get("markets", []):
                ticker = m.get("market_id")
                if not ticker:
                    continue
                oi = float(m.get("open_interest") or 0)
                if oi < min_oi:
                    continue
                tickers.append(ticker)
                if event_id:
                    event_groups.setdefault(event_id, []).append(ticker)

        total_events += len(batch)
        offset += 250
        if offset % 2500 == 0:
            logger.info("  … fetched %d events, %d tickers so far", total_events, len(tickers))

    logger.info(
        "Synthesis fetch complete: %d tickers across %d event groups",
        len(tickers), len(event_groups),
    )

    # --- Persist cache ------------------------------------------------------
    try:
        _SYNTHESIS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SYNTHESIS_CACHE_PATH, "w") as fh:
            json.dump({"tickers": tickers, "event_groups": event_groups}, fh)
        logger.info("Synthesis market cache saved to %s", _SYNTHESIS_CACHE_PATH)
    except Exception as exc:
        logger.warning("Synthesis cache save failed: %s", exc)

    return tickers, event_groups


# ---------------------------------------------------------------------------
# Kalshi REST fallback (used when Synthesis key absent / as subscription base)
# ---------------------------------------------------------------------------
KALSHI_MARKETS_URL = "https://trading-api.kalshi.com/trade-api/v2/markets"

_JUNK_PREFIXES      = ("KXMVECROSSCATEGORY",)
_CLOSE_WINDOWS_DAYS = (7, 30, 90, 180, 365, 730)
MIN_VOLUME_FP       = 50_000


def _fetch_kalshi_page(max_close_ts: int, cursor: str | None = None) -> dict:
    url = f"{KALSHI_MARKETS_URL}?status=open&limit=1000&max_close_ts={max_close_ts}"
    if cursor:
        url += f"&cursor={cursor}"
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-dashboard"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _volume(m: dict) -> float:
    try:
        return float(m.get("volume_fp", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def fetch_liquid_tickers(limit: int = 400, pages_per_window: int = 2) -> List[str]:
    """
    Kalshi-based fallback: return up to `limit` open tickers sorted by volume,
    excluding synthetic shard markets. No auth required.
    """
    now  = int(time.time())
    seen: dict[str, dict] = {}

    try:
        for days in _CLOSE_WINDOWS_DAYS:
            max_ts = now + days * 86400
            cursor = None
            for _ in range(pages_per_window):
                data  = _fetch_kalshi_page(max_ts, cursor)
                batch = data.get("markets", [])
                for m in batch:
                    ticker = m.get("ticker", "")
                    if ticker and not ticker.startswith(_JUNK_PREFIXES):
                        seen[ticker] = m
                cursor = data.get("cursor")
                if not cursor or not batch:
                    break
    except Exception as exc:
        logger.warning("fetch_liquid_tickers: Kalshi REST call failed: %s", exc)
        if not seen:
            return []

    ranked   = sorted(seen.values(), key=_volume, reverse=True)
    filtered = [m for m in ranked if _volume(m) >= MIN_VOLUME_FP]
    tickers  = [m["ticker"] for m in filtered[:limit]]
    logger.info(
        "fetch_liquid_tickers: %d candidates, %d above volume_fp>=%d -> returning %d",
        len(seen), len(filtered), MIN_VOLUME_FP, len(tickers),
    )
    return tickers
