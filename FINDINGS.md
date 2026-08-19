# Kalshi Arbitrage Research Findings

## Executive Summary

After scanning 14,000+ active markets across 6,000+ events on Kalshi, we found **zero exploitable arbitrage opportunities**. This document explains why, and what we learned about the platform's microstructure.

## Key Discoveries

### 1. Complement Arbs Are Structurally Impossible

**Complement arb**: Buy YES and NO on the same market for less than $1 total (guaranteed $1 payout).

**Finding**: These cannot exist at rest on Kalshi. The matching engine ensures that `YES_bid + NO_bid <= $1 - tick`, which means `YES_ask + NO_ask >= $1 + tick`. The minimum spread across 3,213 liquid markets is exactly $0.001 (one tick).

**How we confirmed this**: The orderbook API returns BID arrays (`yes_dollars` = YES buy orders, `no_dollars` = NO buy orders). Actual asks are derived: `best_YES_ask = 1 - max(NO_bid)`. We checked 2,000+ orderbooks and found zero crossed books.

### 2. Cross-Market Arbs Exist But Are Almost All Fake

**CE arb**: Buy YES on all legs of a mutually-exclusive event for less than $1 total.

**Finding**: We found 13 apparent CE arbs across ~5,600 ME events. However, 12 of 13 are on events where the listed outcomes are **not exhaustive**:

| Event | Apparent Edge | Why It's Fake |
|-------|--------------|---------------|
| 51st US State | +77.7c | Only 8 of infinite possibilities listed |
| Next Pope | +55.7c | 7 of thousands of cardinals listed |
| Goldman Sachs COO | +27.0c | 5 of many candidates listed |
| 2028 Presidential Matchup | +12.8c | 16 of many possible matchups |
| Putin/Zelenskyy Meeting Location | +9.0c | 9 of infinite locations listed |

**The 1 "validated" arb** (Trump AG Count, +1.8c) turned out to also be invalid: it covered outcomes 2-5 but had no "1 or fewer" or "6+" markets.

### 3. Near-Misses Exist on Validated Events

The closest real near-miss: **"Who will be Trump's next Secretary of State?"** — 5 outcomes including catch-all "No other person". Total YES asks = $0.955, gross edge = 4.5c, but **-3.5c after fees** (not profitable).

### 4. Why Arbs Are So Rare

1. **Matching engine**: Prevents single-market arbs entirely
2. **Market makers**: Efficiently price cross-market events, keeping sums near $1
3. **Fees**: Kalshi's fee structure (`min(3.5c, ceil(7% * P*(1-P) * 100) / 100)`) eats 2-7c on most trades, killing thin edges
4. **Non-exhaustive outcomes**: Most multi-outcome events don't cover all possibilities, making apparent CE arbs illusory

## Orderbook API Details

- **Endpoint**: `GET /markets/{ticker}/orderbook`
- **Response**: `orderbook_fp.yes_dollars` and `orderbook_fp.no_dollars` — arrays of `[price_string, qty_string]`
- **IMPORTANT**: These are BID books (buy orders), not ask books
  - YES side shows resting YES buy orders sorted low-to-high
  - NO side shows resting NO buy orders sorted low-to-high
  - Best YES ask = 1 - highest NO bid
  - Best NO ask = 1 - highest YES bid
- **Event listing prices** (`yes_ask_dollars`) often differ from orderbook-derived asks

## Exhaustiveness Classification

We classify events into four tiers:

1. **VALIDATED**: Contiguous numeric ranges with both upper AND lower bounds (e.g., GDP growth with "0% or Below" and "6% or Above" plus all ranges between)
2. **CATCHALL**: Has catch-all market ("Other", "None of the above") covering unlisted outcomes
3. **PARTIAL**: Has contiguous ranges but missing one or both bounds
4. **UNVALIDATED**: No proof of exhaustiveness — most "arbs" fall here and are fake

Only VALIDATED and CATCHALL events can have real CE arbs.

## Data

- `data/arb_log.jsonl` — Every real arb detected (validated exhaustive only)
- `data/scan_summary.jsonl` — Per-scan statistics
- `data/near_miss_log.jsonl` — Near-misses on validated events (sampled)

## Scanner Architecture

```
scripts/continuous_arb_scanner.py   — Main continuous scanner (runs every ~30s)
scripts/rest_scan_and_commit.py     — GitHub Actions scanner (runs every 5 min)
.github/workflows/scan_arbs.yml    — CI workflow
```

## Conclusion

Kalshi's market microstructure is well-designed to prevent arbitrage:
- The matching engine eliminates single-market arbs
- Market makers keep cross-market prices efficient
- Fees create a significant hurdle for thin edges
- Most apparent arbs are on non-exhaustive outcome sets

Real arbs, if they appear, would be fleeting (seconds) and small (<5c). Catching them would require real-time WebSocket feeds and sub-second execution — not REST polling.
