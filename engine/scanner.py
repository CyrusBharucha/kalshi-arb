"""
arbitrage/scanner.py
Live arbitrage scanner - orchestrates all strategies, saves to DB.

Runs on a polling interval, pulling fresh prices and scanning all open markets.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from feeds.kalshi_client import KalshiClient
from database.repository import (
    session_scope, insert_opportunity, get_all_open_markets,
    get_event_market_matrix, insert_snapshot, get_engine,
)
from engine.yes_no import scan_complement_arb
from engine.mutually_exclusive import scan_me_arb, analyze_event_probability_sum
from engine.nested_contracts import scan_all_nested_violations
from engine.lifecycle import OpportunityLifecycle

logger = logging.getLogger(__name__)


class ArbitrageScanner:
    """
    Continuous arbitrage scanner.
    Fetches live market data and runs all detection strategies.

    Lifecycle tracking via OpportunityLifecycle ensures:
    - Duplicate windows are never double-inserted (same market/strategy pair).
    - Open opportunities are marked expired when their edge disappears.
    - max_executable_contracts is derived from L2 book depth when available.
    """

    def __init__(self, client: Optional[KalshiClient] = None):
        self.client    = client or KalshiClient(authenticated=False)
        self._lifecycle = OpportunityLifecycle(get_engine())

    def run_once(self) -> Dict[str, Any]:
        """
        Single scan cycle.
        Returns summary stats dict.
        """
        logger.info("=== Arbitrage scan cycle: %s ===",
                     datetime.now(timezone.utc).isoformat())
        t0 = time.monotonic()

        # 1. Pull all open markets with fresh prices
        markets_with_prices = self._fetch_live_prices()
        logger.info("Fetched prices for %d markets", len(markets_with_prices))

        # 2. Snapshot prices to DB
        self._save_snapshots(markets_with_prices)

        # 3. Group by event for ME/nested analysis
        events_markets = self._group_by_event(markets_with_prices)
        logger.info("Grouped into %d events", len(events_markets))

        # 4. Strategy 1: Complement
        complement_opps = scan_complement_arb(markets_with_prices)
        logger.info("Strategy 1 (complement): %d candidates", len(complement_opps))

        # 5. Strategy 2: ME / CE
        me_opps = scan_me_arb(events_markets)
        logger.info("Strategy 2 (ME/CE): %d candidates", len(me_opps))

        # 6. Strategy 3: Nested
        nested_opps = scan_all_nested_violations(events_markets)
        logger.info("Strategy 3 (nested): %d candidates", len(nested_opps))

        # 7. Lifecycle reconcile: dedup, open new, close stale
        all_opps = complement_opps + me_opps + nested_opps
        candidates = [o for o in all_opps if o.get("net_edge", 0) > config.MIN_NET_EDGE]
        lc_result  = self._lifecycle.reconcile(candidates)
        saved      = lc_result["opened"]

        elapsed = time.monotonic() - t0
        summary = {
            "timestamp":         datetime.now(timezone.utc).isoformat(),
            "markets_scanned":   len(markets_with_prices),
            "events_scanned":    len(events_markets),
            "complement_opps":   len(complement_opps),
            "me_opps":           len(me_opps),
            "nested_opps":       len(nested_opps),
            "total_opps_saved":  saved,
            "opps_closed":       lc_result["closed"],
            "opps_deduped":      lc_result["deduped"],
            "scan_seconds":      round(elapsed, 2),
        }
        logger.info("Scan complete: %s", summary)
        return summary

    def run_continuous(self, interval_s: int = 60) -> None:
        """Run continuous scanning loop."""
        logger.info("Starting continuous scan (interval=%ds)", interval_s)
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                logger.info("Scanner stopped.")
                break
            except Exception as exc:
                logger.error("Scan cycle error: %s", exc, exc_info=True)
            time.sleep(interval_s)

    # -- Internal helpers -------------------------------------------------------

    def _fetch_live_prices(self) -> List[Dict[str, Any]]:
        """
        Pull all open markets with their current prices.
        Uses the Kalshi REST API; order books are collected separately via WebSocket.
        """
        markets = []
        try:
            for raw_market in self.client.all_open_markets():
                market = {
                    "market_id":    raw_market.get("ticker", ""),
                    "ticker":       raw_market.get("ticker", ""),
                    "event_ticker": raw_market.get("event_ticker", ""),
                    "series_ticker": raw_market.get("series_ticker"),
                    "title":        raw_market.get("title", ""),
                    "category":     raw_market.get("category"),
                    "floor_strike": _safe_float(raw_market.get("floor_strike")),
                    "cap_strike":   _safe_float(raw_market.get("cap_strike")),
                    "yes_bid":      _safe_float(raw_market.get("yes_bid")),
                    "yes_ask":      _safe_float(raw_market.get("yes_ask")),
                    "no_bid":       _safe_float(raw_market.get("no_bid")),
                    "no_ask":       _safe_float(raw_market.get("no_ask")),
                    "last_price":   _safe_float(raw_market.get("last_price")),
                    "volume":       _safe_float(raw_market.get("volume")),
                    "open_interest": _safe_float(raw_market.get("open_interest")),
                }

                # Derive NO ask from YES bid if not present
                if market["no_ask"] is None and market["yes_bid"] is not None:
                    market["no_ask"] = round(1.0 - market["yes_bid"], 6)
                if market["no_bid"] is None and market["yes_ask"] is not None:
                    market["no_bid"] = round(1.0 - market["yes_ask"], 6)

                markets.append(market)

        except Exception as exc:
            logger.error("Error fetching live prices: %s", exc)

        return markets

    def _save_snapshots(self, markets: List[Dict[str, Any]]) -> None:
        ts = datetime.now(timezone.utc)
        with session_scope() as s:
            for m in markets:
                if m.get("yes_bid") is None and m.get("yes_ask") is None:
                    continue
                snap = {
                    "snapshot_ts":  ts,
                    "market_id":    m["market_id"],
                    "yes_bid":      m.get("yes_bid"),
                    "yes_ask":      m.get("yes_ask"),
                    "no_bid":       m.get("no_bid"),
                    "no_ask":       m.get("no_ask"),
                    "last_price":   m.get("last_price"),
                    "volume":       m.get("volume"),
                    "open_interest": m.get("open_interest"),
                    "source":       "api_poll",
                }
                insert_snapshot(s, snap)

    def _group_by_event(
        self,
        markets: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, list] = {}
        for m in markets:
            et = m.get("event_ticker", "unknown")
            grouped.setdefault(et, []).append(m)
        return grouped


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# -- Entry point ----------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    scanner = ArbitrageScanner()
    scanner.run_continuous(interval_s=60)
