"""
arbitrage/lifecycle.py
======================
Opportunity lifecycle manager for the Kalshi Arbitrage Engine.

Prevents duplicate inserts, closes stale opportunities, and computes
max_executable_contracts from L2 book depth — the three things the
naive scanner.py loop gets wrong.

Design
------
Each opportunity is fingerprinted as:
    (strategy_type, frozenset(markets_involved))

On every scanner cycle this module receives a fresh list of CURRENT
detections and the full set of OPEN opportunities from the DB. It:

  1. For each current detection:
       - If fingerprint already in DB (status=open) -> skip insert (dedup).
       - Otherwise -> insert as new open opportunity.

  2. For each DB open opportunity not seen in current detections:
       - Write closed_at = now, duration_seconds, status = 'expired'.

  3. Depth-based quantity calculation:
       - parse book_json from l2_snapshots to get full level stack
       - walk the book up to gross_edge / contract-price to find max qty

Usage
-----
    from engine.lifecycle import OpportunityLifecycle

    lc = OpportunityLifecycle(pg_engine)
    lc.reconcile(current_detections)   # call once per scan cycle

Each item in current_detections is a dict matching the columns of
arbitrage_opportunities, PLUS an optional "book_json" key (ignored
for storage but used for depth calculation).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from sqlalchemy import text

log = logging.getLogger(__name__)

# Kalshi taker fee schedule: fee = 0.07 * P * (1 - P), capped at $0.035/contract.
# Source: execution/fees.py (matches documented values at P=0.50 -> $0.0175).
_FEE_COEFF  = 0.07    # coefficient in 0.07 * P * (1-P)
_FEE_CAP    = 0.035   # $0.035 per contract max taker fee
_FEE_MIN    = 0.0001  # effective floor (formula is 0 at P=0 or 1)


def _kalshi_fee(price: float) -> float:
    """Approximate Kalshi taker fee for one contract at `price`."""
    if price is None or price <= 0 or price >= 1:
        return _FEE_MIN
    fee = _FEE_COEFF * price * (1.0 - price)
    return max(_FEE_MIN, min(_FEE_CAP, fee))


def _fingerprint(strategy_type: str, markets: List[str]) -> FrozenSet:
    return frozenset([strategy_type] + sorted(markets))


def _depth_qty_from_book(book_json_str: Optional[str], gross_edge: float) -> Optional[float]:
    """
    Walk the YES ask levels in the book JSON to find how many contracts
    can be bought before the ask price exceeds the break-even level.

    book_json is {"yes_bids": [[price, qty], ...], "yes_asks": [...], ...}
    For a complement arb: we buy YES at ask AND NO at ask (= 1 - YES bid).
    The binding constraint is whichever leg has less depth at profitable prices.

    Returns None if book_json is missing/invalid (caller uses conservative default).
    """
    if not book_json_str:
        return None
    try:
        book = json.loads(book_json_str) if isinstance(book_json_str, str) else book_json_str
    except (json.JSONDecodeError, TypeError):
        return None

    yes_asks = book.get("yes_asks") or []
    yes_bids = book.get("yes_bids") or []

    # Executable YES side: sum qty where ask <= (1 - gross_edge / 2)
    # (conservative: we need at least gross_edge net of both legs)
    breakeven_yes = 1.0 - gross_edge
    qty_yes = sum(float(q) for p, q in yes_asks if float(p) <= breakeven_yes) if yes_asks else 0.0

    # Executable NO side via YES bids: NO ask = 1 - YES bid
    # Buy NO at no_ask = 1 - yes_bid; profitable when yes_bid >= gross_edge / 2
    breakeven_bid = gross_edge
    qty_no = sum(float(q) for p, q in yes_bids if float(p) >= breakeven_bid) if yes_bids else 0.0

    if not qty_yes and not qty_no:
        return None

    # Binding leg = minimum; ignore zeros that mean "no depth on that side"
    sides = [q for q in [qty_yes, qty_no] if q > 0]
    return min(sides) if sides else None


class OpportunityLifecycle:
    """
    Reconciles a batch of current detections against the DB open set.
    Call reconcile() once per scanner iteration.
    """

    def __init__(self, pg_engine):
        self._engine = pg_engine

    # -- Public API -------------------------------------------------------------

    def reconcile(self, current_detections: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Main entry point.

        Args:
            current_detections: list of opportunity dicts from this scan cycle.
                Each dict must have: strategy_type, markets_involved, gross_edge,
                total_fees, estimated_slippage, net_edge, classification,
                prices_json. May also have book_json (used for depth calc,
                not stored directly).

        Returns:
            {"opened": N, "closed": N, "deduped": N}
        """
        if not current_detections:
            # Nothing detected — close everything still open in DB
            closed = self._close_stale(set())
            return {"opened": 0, "closed": closed, "deduped": 0}

        # Enrich each detection with depth-based qty
        for det in current_detections:
            self._enrich_qty(det)

        # Load open opportunities from DB
        open_db = self._load_open_opps()

        current_fps = {_fingerprint(d["strategy_type"], d["markets_involved"]): d
                       for d in current_detections}
        open_fps    = {_fingerprint(o["strategy_type"], list(o["markets_involved"])): o
                       for o in open_db}

        # New opportunities: detected now, not open in DB
        to_open = {fp: d for fp, d in current_fps.items() if fp not in open_fps}

        # Stale opportunities: open in DB, not detected now
        stale_fps = set(open_fps.keys()) - set(current_fps.keys())

        opened  = self._open_new(list(to_open.values()))
        closed  = self._close_stale(stale_fps, open_fps)
        deduped = len(current_fps) - len(to_open)

        log.info(
            "Lifecycle reconcile: %d detected, %d new opened, %d closed, %d deduped",
            len(current_detections), opened, closed, deduped,
        )
        return {"opened": opened, "closed": closed, "deduped": deduped}

    # -- Internal ---------------------------------------------------------------

    def _load_open_opps(self) -> List[Dict]:
        with self._engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT opportunity_id, strategy_type, markets_involved,
                       detected_at, gross_edge, net_edge
                FROM arbitrage_opportunities
                WHERE status = 'open'
            """)).fetchall()
        return [dict(r._mapping) for r in rows]

    def _enrich_qty(self, det: Dict[str, Any]) -> None:
        """Compute max_executable_contracts from book_json if available."""
        gross = det.get("gross_edge") or 0.0
        book_json = det.pop("book_json", None)   # consume, don't store

        qty = _depth_qty_from_book(book_json, gross)
        if qty is None:
            # No depth: use conservative default (10 contracts, Class B)
            qty = det.get("max_executable_contracts") or 10.0
            if not det.get("classification"):
                det["classification"] = "B"
        else:
            # Real L2 depth available -> always Class A regardless of prior value
            det["classification"] = "A"

        det["max_executable_contracts"] = qty
        net = det.get("net_edge") or 0.0
        det["max_gross_profit"] = round(gross * qty, 4)
        det["max_net_profit"]   = round(net   * qty, 4)

    def _open_new(self, detections: List[Dict]) -> int:
        if not detections:
            return 0
        now = datetime.now(timezone.utc)
        opened = 0
        with self._engine.begin() as conn:
            for det in detections:
                markets = det.get("markets_involved", [])
                prices  = det.get("prices_json", {})
                try:
                    conn.execute(text("""
                        INSERT INTO arbitrage_opportunities (
                            detected_at, strategy_type, classification,
                            markets_involved, prices_json,
                            gross_edge, total_fees, estimated_slippage, net_edge,
                            max_executable_contracts, max_gross_profit, max_net_profit,
                            status, notes
                        ) VALUES (
                            :detected_at, :strategy_type, :classification,
                            :markets_involved, :prices_json::jsonb,
                            :gross_edge, :total_fees, :estimated_slippage, :net_edge,
                            :max_executable_contracts, :max_gross_profit, :max_net_profit,
                            'open', :notes
                        )
                    """), {
                        "detected_at":              now,
                        "strategy_type":            det.get("strategy_type"),
                        "classification":           det.get("classification", "B"),
                        "markets_involved":         markets,
                        "prices_json":              json.dumps(prices),
                        "gross_edge":               det.get("gross_edge"),
                        "total_fees":               det.get("total_fees"),
                        "estimated_slippage":       det.get("estimated_slippage"),
                        "net_edge":                 det.get("net_edge"),
                        "max_executable_contracts": det.get("max_executable_contracts"),
                        "max_gross_profit":         det.get("max_gross_profit"),
                        "max_net_profit":           det.get("max_net_profit"),
                        "notes":                    det.get("notes"),
                    })
                    opened += 1
                except Exception as exc:
                    log.warning("Failed to insert opportunity: %s", exc)
        return opened

    def _close_stale(
        self,
        stale_fps: set,
        open_fps: Optional[Dict] = None,
    ) -> int:
        if not stale_fps:
            return 0
        if open_fps is None:
            open_fps = {_fingerprint(o["strategy_type"], list(o["markets_involved"])): o
                        for o in self._load_open_opps()}

        stale_ids = [open_fps[fp]["opportunity_id"]
                     for fp in stale_fps if fp in open_fps]
        if not stale_ids:
            return 0

        now = datetime.now(timezone.utc)
        closed = 0
        with self._engine.begin() as conn:
            for oid in stale_ids:
                try:
                    conn.execute(text("""
                        UPDATE arbitrage_opportunities
                        SET status           = 'expired',
                            closed_at        = :now,
                            duration_seconds = EXTRACT(EPOCH FROM (:now - detected_at))
                        WHERE opportunity_id = :oid
                          AND status = 'open'
                    """), {"now": now, "oid": str(oid)})
                    closed += 1
                except Exception as exc:
                    log.warning("Failed to close opportunity %s: %s", oid, exc)
        if closed:
            log.info("Closed %d stale opportunities", closed)
        return closed

    def close_settled(self, market_id: str, settlement_result: str) -> int:
        """
        Close all open opportunities involving a market that has settled.
        Called by settlement ingestion when a market resolves.

        settlement_result: 'yes' | 'no'
        """
        now = datetime.now(timezone.utc)
        with self._engine.begin() as conn:
            result = conn.execute(text("""
                UPDATE arbitrage_opportunities
                SET status             = 'settled',
                    closed_at          = :now,
                    duration_seconds   = EXTRACT(EPOCH FROM (:now - detected_at)),
                    settlement_outcome = :outcome
                WHERE status = 'open'
                  AND :mid = ANY(markets_involved)
                RETURNING opportunity_id
            """), {"now": now, "mid": market_id, "outcome": settlement_result})
            count = result.rowcount
        if count:
            log.info("Settled %d opportunities for market %s", count, market_id)
        return count
