"""
execution/simulator.py
Realistic execution simulator.

Given an arbitrage opportunity, models:
  - Gross profit (pre-cost)
  - Taker/maker fees
  - Bid/ask spread costs (we already pay the spread by crossing it)
  - Slippage from shallow order books
  - Maximum executable quantity (limited by depth on shallowest leg)
  - Net profit after all costs

The key question we answer: is the edge EXECUTABLE after costs, or is it
purely a theoretical midpoint illusion?

Classification:
  A - net_edge > 0, confirmed depth at entry prices -> proven executable arb
  B - net_edge > 0 based on candlestick bid/ask OHLC, no depth confirmation
  C - gross_edge > 0 but net_edge <= 0 after costs -> D type
  D - spread/liquidity consumes all edge -> not actually executable
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from engine.fees import taker_fee_per_contract, compute_multi_leg_fees

logger = logging.getLogger(__name__)


# -- Data classes ---------------------------------------------------------------

@dataclass
class Leg:
    """Represents one side of a multi-leg trade."""
    market_id:   str
    side:        str          # 'yes' | 'no'
    action:      str          # 'buy' | 'sell'
    price:       float        # executable price (ask to buy, bid to sell)
    quantity:    float        # available at this price
    is_taker:    bool = True

    @property
    def fee(self) -> float:
        return taker_fee_per_contract(self.price) if self.is_taker else 0.0

    @property
    def cost_per_contract(self) -> float:
        """Net cash out (positive = cash out) per contract including fee."""
        if self.action == "buy":
            return self.price + self.fee
        else:   # sell
            return -(self.price - self.fee)   # negative = cash in


@dataclass
class ExecutionResult:
    """Output of the execution simulator."""
    strategy_type:       str
    classification:      str           # A | B | C | D | E
    markets_involved:    List[str]
    legs:                List[Leg]

    gross_edge:          float         # profit per contract set, pre-cost
    total_fees:          float         # total fee per contract set
    bid_ask_cost:        float         # spread already paid (embedded in executable price)
    estimated_slippage:  float         # additional slippage estimate
    net_edge:            float         # gross - fees - slippage

    max_executable:      float         # contracts limited by shallowest leg
    max_gross_profit:    float
    max_net_profit:      float

    prices_snapshot:     Dict[str, Any] = field(default_factory=dict)
    notes:               str = ""

    def is_profitable(self) -> bool:
        return self.net_edge > 0 and self.max_net_profit > 0

    def to_db_dict(self) -> Dict[str, Any]:
        return {
            "strategy_type":           self.strategy_type,
            "classification":          self.classification,
            "markets_involved":        self.markets_involved,
            "prices_json":             self.prices_snapshot,
            "gross_edge":              round(self.gross_edge, 6),
            "total_fees":              round(self.total_fees, 6),
            "estimated_slippage":      round(self.estimated_slippage, 6),
            "net_edge":                round(self.net_edge, 6),
            "max_executable_contracts": self.max_executable,
            "max_gross_profit":        round(self.max_gross_profit, 4),
            "max_net_profit":          round(self.max_net_profit, 4),
            "notes":                   self.notes,
        }


# -- Core simulator -------------------------------------------------------------

class ExecutionSimulator:
    """
    Evaluates whether a candidate arbitrage is genuinely executable.
    """

    # Slippage model: assume extra % of price for trades beyond order-book top
    SLIPPAGE_BPS_NO_DEPTH = 10   # 10 bps if no depth info available
    SLIPPAGE_BPS_WITH_DEPTH = 2  # 2 bps if depth data confirms quantity

    def simulate_complement_arb(
        self,
        market_id: str,
        yes_ask:   float,         # price to buy YES (taker)
        no_ask:    float,         # price to buy NO = 1 - yes_bid
        yes_depth: Optional[float] = None,   # available quantity at yes_ask
        no_depth:  Optional[float] = None,   # available quantity at no_ask
    ) -> ExecutionResult:
        """
        Strategy 1: Buy YES at ask + Buy NO at ask.
        If yes_ask + no_ask < 1.00, gross edge exists.
        Paying settlement returns $1.00.

        Gross edge = 1.00 - yes_ask - no_ask
        Total cost = yes_ask + no_ask + fee(yes_ask) + fee(no_ask)
        Net edge   = 1.00 - total_cost
        """
        if yes_ask <= 0 or no_ask <= 0:
            raise ValueError("Prices must be positive.")
        if yes_ask + no_ask >= 1.0:
            return self._no_edge("complement", market_id, yes_ask, no_ask)

        gross_edge = 1.0 - yes_ask - no_ask

        fee_yes = taker_fee_per_contract(yes_ask)
        fee_no  = taker_fee_per_contract(no_ask)
        total_fees = fee_yes + fee_no

        # Slippage
        has_depth = yes_depth is not None and no_depth is not None
        slip_bps  = self.SLIPPAGE_BPS_WITH_DEPTH if has_depth else self.SLIPPAGE_BPS_NO_DEPTH
        slippage  = (yes_ask + no_ask) * slip_bps / 10000

        net_edge = gross_edge - total_fees - slippage

        # Max executable = min available depth
        if has_depth:
            max_qty = min(yes_depth, no_depth)
            classification = "A" if net_edge > 0 else "D"
        else:
            max_qty = 10.0   # conservative assumption when no depth
            classification = "B" if net_edge > 0 else "D"

        legs = [
            Leg(market_id, "yes", "buy", yes_ask, max_qty),
            Leg(market_id, "no",  "buy", no_ask,  max_qty),
        ]

        return ExecutionResult(
            strategy_type    = "yes_no_complement",
            classification   = classification,
            markets_involved = [market_id],
            legs             = legs,
            gross_edge       = gross_edge,
            total_fees       = total_fees,
            bid_ask_cost     = 0.0,   # already embedded in ask price
            estimated_slippage = slippage,
            net_edge         = net_edge,
            max_executable   = max_qty,
            max_gross_profit = gross_edge * max_qty,
            max_net_profit   = net_edge * max_qty,
            prices_snapshot  = {
                market_id: {"yes_ask": yes_ask, "no_ask": no_ask,
                             "yes_depth": yes_depth, "no_depth": no_depth}
            },
            notes = f"Depth confirmed: {has_depth}",
        )

    def simulate_mutually_exclusive_arb(
        self,
        markets_prices: List[Dict[str, Any]],
        # Each dict: {"market_id": str, "yes_ask": float, "depth": Optional[float]}
    ) -> ExecutionResult:
        """
        Historical-scanner ME/CE arb: buy one YES in each outcome of an ME+CE set.

        Applies to Kalshi multi-outcome events where exactly one outcome resolves YES
        (mutually exclusive AND collectively exhaustive). Buying all YES contracts
        guarantees a $1.00 payout regardless of which outcome wins.

        Arb condition: Σ YES_asks < $1.00
        Gross edge:    $1.00 − Σ YES_asks

        Note: the live scanner (ws_bridge.py) also detects the complementary ME arb
        (buy all NOs) where Σ NO_asks < N−1; that payout is N−1 instead of $1.
        This function implements the YES-side strategy only.

        Args:
            markets_prices: list of {"market_id": str, "yes_ask": float, "depth": float|None}
        """
        if not markets_prices:
            raise ValueError("Need at least one market.")

        total_cost = sum(m["yes_ask"] for m in markets_prices)
        if total_cost >= 1.0:
            return self._no_edge_multi("mutually_exclusive", markets_prices)

        gross_edge = 1.0 - total_cost

        legs = []
        fees = 0.0
        min_depth = None
        for m in markets_prices:
            f = taker_fee_per_contract(m["yes_ask"])
            fees += f
            d = m.get("depth")
            if d is not None:
                min_depth = min(min_depth, d) if min_depth is not None else d
            legs.append(Leg(m["market_id"], "yes", "buy", m["yes_ask"],
                             d or 10.0))

        has_depth = min_depth is not None
        slip_bps  = self.SLIPPAGE_BPS_WITH_DEPTH if has_depth else self.SLIPPAGE_BPS_NO_DEPTH
        slippage  = total_cost * slip_bps / 10000
        net_edge  = gross_edge - fees - slippage

        max_qty = min_depth if has_depth else 10.0
        classification = ("A" if has_depth and net_edge > 0
                           else "B" if net_edge > 0 else "D")

        return ExecutionResult(
            strategy_type    = "mutually_exclusive",
            classification   = classification,
            markets_involved = [m["market_id"] for m in markets_prices],
            legs             = legs,
            gross_edge       = gross_edge,
            total_fees       = fees,
            bid_ask_cost     = 0.0,
            estimated_slippage = slippage,
            net_edge         = net_edge,
            max_executable   = max_qty,
            max_gross_profit = gross_edge * max_qty,
            max_net_profit   = net_edge * max_qty,
            prices_snapshot  = {m["market_id"]: m for m in markets_prices},
        )

    def simulate_threshold_violation(
        self,
        superset_market:  Dict[str, Any],
        subset_market:    Dict[str, Any],
        # Each: {"market_id": str, "yes_bid": float, "yes_ask": float, "depth": Optional[float]}
    ) -> ExecutionResult:
        """
        Strategy 3: Nested / logical violation.
        If P(superset) < P(subset) by executable prices:
          Buy YES on superset at its ask (cheap - should be more expensive)
          Sell YES on subset at its bid (expensive - should be cheaper)
          Net payoff: settled YES - settled YES = 0 in identical scenarios,
          but payoff > 0 when superset resolves but subset doesn't.

        This is a relative-value trade (Class C) unless the payoff is fully locked.
        In most cases this is NOT pure arbitrage - it is a conditional payoff.
        We classify it as C unless we can prove a locked payoff.
        """
        sup_ask = superset_market["yes_ask"]
        sub_bid = subset_market["yes_bid"]

        # Edge from selling expensive (sub) and buying cheap (sup)
        gross_edge = sub_bid - sup_ask  # positive if violation is executable

        if gross_edge <= 0:
            return self._no_edge_threshold(superset_market, subset_market)

        fee_sup = taker_fee_per_contract(sup_ask)
        fee_sub = taker_fee_per_contract(sub_bid)
        total_fees = fee_sup + fee_sub

        slippage = (sup_ask + sub_bid) * self.SLIPPAGE_BPS_NO_DEPTH / 10000
        net_edge = gross_edge - total_fees - slippage

        sup_depth = superset_market.get("depth")
        sub_depth = subset_market.get("depth")
        has_depth = sup_depth is not None and sub_depth is not None
        max_qty = min(sup_depth or 10.0, sub_depth or 10.0)

        # This is almost always a Class C (relative value) not A/B arb
        # because the payoff is not fully locked in (depends on settlement)
        classification = "C"

        legs = [
            Leg(superset_market["market_id"], "yes", "buy",  sup_ask, max_qty),
            Leg(subset_market["market_id"],   "yes", "sell", sub_bid, max_qty),
        ]

        return ExecutionResult(
            strategy_type    = "nested_logical",
            classification   = classification,
            markets_involved = [superset_market["market_id"], subset_market["market_id"]],
            legs             = legs,
            gross_edge       = gross_edge,
            total_fees       = total_fees,
            bid_ask_cost     = 0.0,
            estimated_slippage = slippage,
            net_edge         = net_edge,
            max_executable   = max_qty,
            max_gross_profit = gross_edge * max_qty,
            max_net_profit   = net_edge * max_qty,
            prices_snapshot  = {
                superset_market["market_id"]: superset_market,
                subset_market["market_id"]:   subset_market,
            },
            notes = "Class C: conditional payoff, not pure arbitrage.",
        )

    # -- Helpers ----------------------------------------------------------------

    def _no_edge(self, strategy: str, market_id: str, *args) -> ExecutionResult:
        return ExecutionResult(
            strategy_type="complement", classification="D",
            markets_involved=[market_id], legs=[],
            gross_edge=0.0, total_fees=0.0, bid_ask_cost=0.0,
            estimated_slippage=0.0, net_edge=0.0,
            max_executable=0.0, max_gross_profit=0.0, max_net_profit=0.0,
            notes="No gross edge after spread.",
        )

    def _no_edge_multi(self, strategy: str, markets_prices: list) -> ExecutionResult:
        return ExecutionResult(
            strategy_type=strategy, classification="D",
            markets_involved=[m["market_id"] for m in markets_prices], legs=[],
            gross_edge=0.0, total_fees=0.0, bid_ask_cost=0.0,
            estimated_slippage=0.0, net_edge=0.0,
            max_executable=0.0, max_gross_profit=0.0, max_net_profit=0.0,
            notes="Sum of ask prices >= 1.00 - no gross edge.",
        )

    def _no_edge_threshold(self, sup: dict, sub: dict) -> ExecutionResult:
        return ExecutionResult(
            strategy_type="nested_logical", classification="D",
            markets_involved=[sup["market_id"], sub["market_id"]], legs=[],
            gross_edge=0.0, total_fees=0.0, bid_ask_cost=0.0,
            estimated_slippage=0.0, net_edge=0.0,
            max_executable=0.0, max_gross_profit=0.0, max_net_profit=0.0,
            notes="No executable edge - prices consistent with logical ordering.",
        )
