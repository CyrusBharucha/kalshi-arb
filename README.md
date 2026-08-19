# Kalshi Arbitrage Engine

Real-time arbitrage detection for Kalshi prediction markets, built as a personal research project.

**[Live Dashboard](https://kalshi-arb-tva5pkwy8zavn4qjcwrg9c.streamlit.app/)**

---

## What this is

Kalshi is a regulated US prediction market where contracts resolve at $1 (YES wins) or $0 (NO wins). Every contract has a YES side and a NO side. In an efficient market, YES ask + NO ask should always equal $1.00 after fees. When they don't, you can buy both sides and lock in a guaranteed profit regardless of how the contract resolves.

This project connects to Kalshi's live orderbook feed, scans for those mispricings on a 1-second cycle, and logs confirmed opportunities to a cloud database. No trade execution. This is a detection and research tool.

---

## Strategy

Three scanners run simultaneously on a 1-second cycle:

**YES/NO Complement (YNC):** when `yes_ask + no_ask < $1.00` after fees on the same contract, buying both sides locks in a guaranteed profit. In practice Kalshi prices YES and NO as complements (NO ask = 1 − YES bid), so the sum ≥ $1.00 always — YNC detections are feed/rounding artifacts.

**Mutually Exclusive (ME):** when the sum of NO asks across all outcomes of one event is below N−1 (where N is the leg count), buying all NO legs costs less than the guaranteed $1 payout when exactly one outcome resolves YES.

**Threshold Order (TH):** when a superset YES leg and a subset NO leg are mispriced relative to each other (monotonicity violation), buying both locks in a risk-free edge.

**CE (Collectively Exhaustive):** currently disabled pending false-positive review. Would detect when the sum of YES asks across all event outcomes is below $1.00.

Kalshi charges a fee per leg:

```
fee = min($0.035, ceil(0.07 * P * (1 - P) * 100) / 100)
```

where P is the price in dollars. Net edge must clear 2 cents after both legs' fees to qualify. Detections then pass through a price floor, an L2 depth check to confirm resting quantity at the stated prices, and a 5-minute dedup window to avoid re-alerting on a stale book.

---

## What the dashboard shows

Eleven pages covering live arb detections, historical opportunity log, full Kalshi market browser, orderbook depth and imbalance, Canadian rate market analysis, cross-asset context, backtesting, research methodology, and system health. The scanner runs as a background WebSocket thread and writes to a cloud Postgres database that the dashboard reads from.

---

## What makes this hard

The main challenge is not finding the arbs, it is filtering out the false ones. Stale quotes, thin books, and pre-settlement price dislocations all look like arbs superficially. A lot of the engineering in this project is the validation layer: freshness checks, depth walks, and dedup logic that separates a real opportunity from a bad quote.

---

## Stack

| Layer | Tech |
|-------|------|
| Live feed | Synthesis WebSocket (Kalshi L2), ~3,400 msg/sec |
| Scanner | Python background thread, 1-second cycle |
| Storage | Neon PostgreSQL (cloud) + SQLite (local fallback) |
| Dashboard | Streamlit, 11 pages |
