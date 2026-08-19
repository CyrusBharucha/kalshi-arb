"""
cross_asset/arb_scanner.py
===========================
Cross-asset arbitrage scanner.

Compares Kalshi implied probabilities to model-derived probabilities from:
  - BOC CORRA / policy rate (for rate-cut/hike prediction markets)
  - CAD/USD FX (for currency markets)
  - WTI crude (for oil/energy markets)
  - VIX (for volatility markets)

Architecture:
    1. Fetch live cross-asset data (BOC VALET + yfinance) — no API keys needed
    2. Fetch live Kalshi market prices from PostgreSQL (populated by Synthesis WS or historical pull)
    3. Build model-implied probabilities via simple Bayesian / threshold models
    4. Compute spread = kalshi_implied - model_implied
    5. Flag opportunities: |spread| > threshold and kalshi_volume > min_volume
    6. Write to cross_asset_spreads table + emit live signals

Usage:
    python cross_asset/arb_scanner.py             # single scan then exit
    python cross_asset/arb_scanner.py --loop 60   # loop every 60 seconds
    python cross_asset/arb_scanner.py --history   # back-fill historical spreads from DB

Models implemented:
    - BOCRateCutModel   : implied probability of BOC rate cut by next meeting
    - FXMomentumModel   : implied probability of CAD strengthening vs USD
    - VIXRegimeModel    : implied probability of low-vol regime (VIX < threshold)
"""
from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
load_dotenv()

from analysis.live_data import get_boc_rates, get_fx_rates, get_equity_data, get_boc_history

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MIN_EDGE_PCT        = 3.0   # minimum |kalshi - model| spread to flag (in pct points)
MIN_VOLUME          = 10    # minimum contracts traded in the volume window
VOLUME_WINDOW_DAYS  = 90    # trailing window used to derive volume / last price
HISTORY_DAYS        = 365   # how far back to back-fill historical spreads

# The trade table lags real time (the historical pull backfills in batches), so
# the volume window is anchored to the newest trade actually present rather than
# to NOW(). Anchoring on NOW() silently returns zero markets whenever ingestion
# is behind, which looks identical to "no opportunities exist".
_ANCHOR_SQL = "SELECT MAX(trade_ts) FROM trades"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_conn():
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL", "")
    db_url = db_url.replace("postgresql+psycopg2://", "postgresql://")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set in .env")
    return psycopg2.connect(db_url)


#: Model-scan output table.
#:
#: Deliberately separate from ``cross_asset_spreads``: that table is the
#: probability_engine's Kalshi-vs-traditional-*instrument* spread time series
#: (see database/models.py::CrossAssetSpread), keyed by (spread_ts, market_id,
#: asset). This one records *model*-derived probabilities keyed by model_name,
#: which is a different entity with different columns.
SPREADS_TABLE = "cross_asset_model_spreads"


def _ensure_schema(conn):
    """Create the model-scan output table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SPREADS_TABLE} (
                id              BIGSERIAL PRIMARY KEY,
                scanned_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ticker          TEXT        NOT NULL,
                series_ticker   TEXT,
                title           TEXT,
                -- Kalshi side
                kalshi_yes_bid  NUMERIC(6,4),
                kalshi_yes_ask  NUMERIC(6,4),
                kalshi_midpoint NUMERIC(6,4),
                kalshi_volume   BIGINT,
                -- Model side
                model_name      TEXT,
                model_prob      NUMERIC(6,4),
                model_inputs    JSONB,
                -- Spread
                edge_pct        NUMERIC(8,4),   -- (kalshi_mid - model_prob) * 100
                direction       TEXT,           -- 'SELL_YES' | 'BUY_YES' | 'FLAT'
                -- Signal
                is_flagged      BOOLEAN NOT NULL DEFAULT FALSE,
                price_source    TEXT,           -- 'l2_book' | 'last_trade'
                notes           TEXT
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_cams_ticker     ON {SPREADS_TABLE}(ticker)
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_cams_scanned_at ON {SPREADS_TABLE}(scanned_at DESC)
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_cams_flagged    ON {SPREADS_TABLE}(is_flagged, scanned_at DESC) WHERE is_flagged
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_cams_model      ON {SPREADS_TABLE}(model_name, scanned_at DESC)
        """)
    conn.commit()
    log.info("%s table verified.", SPREADS_TABLE)


# ---------------------------------------------------------------------------
# Model definitions
# ---------------------------------------------------------------------------

@dataclass
class ModelResult:
    model_name:   str
    model_prob:   float          # 0.0 - 1.0 implied probability of YES
    model_inputs: Dict[str, Any] = field(default_factory=dict)
    confidence:   float = 0.5    # 0-1 confidence in the model estimate


class BOCRateCutModel:
    """
    Estimate P(BOC cuts rate at next meeting) from:
    - Current CORRA vs policy rate spread (deviation signals market stress)
    - 3-month trend in CORRA (falling = cut expected)
    - CAD/USD (strengthening CAD = BOC less likely to cut)
    - BOC policy rate level (higher rates = more room to cut = higher prior)
    """
    name = "boc_rate_cut"

    # Target keywords in market titles (case-insensitive)
    TITLE_KEYWORDS = [
        "bank of canada", "boc", "rate cut", "rate hike", "policy rate",
        "overnight rate", "corra", "interest rate canada",
    ]

    @classmethod
    def matches_market(cls, title: str) -> bool:
        return _title_matches(title, cls.TITLE_KEYWORDS)

    @classmethod
    def compute(cls, boc: dict, fx: dict, eq: dict, market: dict) -> Optional[ModelResult]:
        corra       = boc.get("corra")
        policy_rate = boc.get("policy_rate")
        cadusd      = fx.get("cadusd")

        if corra is None or policy_rate is None:
            return None

        # --- Inputs ---
        spread_bps      = (corra - policy_rate) * 10_000   # CORRA - policy (normally < 0 when cut expected)
        cadusd_chg      = fx.get("cadusd_chg") or 0.0      # positive = CAD strengthening

        # --- Simple logistic prior ---
        # Higher policy rate → higher base cut probability (room to cut)
        base_prob = min(max(policy_rate / 0.05, 0.10), 0.80)   # 10-80% depending on level

        # Large negative spread (CORRA < policy) → market expects cut
        spread_adj = -spread_bps / 100.0 * 0.15   # 10bps below = +1.5%

        # CAD weakening → BOC less likely to cut (inflation concern)
        fx_adj = -cadusd_chg * 200.0   # -2% CADUSD move = +4% cut prob

        prob = max(0.02, min(0.98, base_prob + spread_adj + fx_adj))

        # Adjust for title clues about direction
        title = market.get("title", "").lower()
        if "hike" in title or "raise" in title:
            prob = 1.0 - prob   # invert for hike markets

        inputs = {
            "corra":         round(corra * 100, 4),
            "policy_rate":   round(policy_rate * 100, 4),
            "spread_bps":    round(spread_bps, 2),
            "cadusd":        cadusd,
            "cadusd_chg":    cadusd_chg,
            "base_prob":     round(base_prob, 4),
            "spread_adj":    round(spread_adj, 4),
            "fx_adj":        round(fx_adj, 4),
        }
        return ModelResult(cls.name, prob, inputs, confidence=0.55)


class FXMomentumModel:
    """
    Estimate P(CAD strengthens vs USD) from:
    - 1-day FX momentum
    - BOC / Fed rate differential
    - WTI crude (Canada oil export proxy)
    """
    name = "fx_momentum"

    # Deliberately narrow: bare "fx" and "currency" pulled in unrelated markets
    # (e.g. central-bank *digital currency* legislation), so they are excluded.
    TITLE_KEYWORDS = ["cad", "canadian dollar", "usd/cad", "cadusd", "loonie",
                      "exchange rate", "dollar"]

    @classmethod
    def matches_market(cls, title: str) -> bool:
        return _title_matches(title, cls.TITLE_KEYWORDS)

    @classmethod
    def compute(cls, boc: dict, fx: dict, eq: dict, market: dict) -> Optional[ModelResult]:
        cadusd = fx.get("cadusd")
        if cadusd is None:
            return None

        cadusd_chg = fx.get("cadusd_chg") or 0.0
        wti        = eq.get("wti")
        wti_chg    = eq.get("wti_1d_chg_pct") or 0.0

        # Momentum model: prior 50/50 + adjust for momentum
        prob = 0.50
        prob += cadusd_chg * 300.0     # +1% CAD move = +30% next-day prob
        prob += wti_chg / 100.0 * 0.25 # +10% oil = +2.5% CAD strengthening

        # BOC rate differential
        corra       = boc.get("corra") or 0.0
        policy_rate = boc.get("policy_rate") or 0.0
        prob += (policy_rate - 0.0425) * 2.0   # vs assumed 4.25% Fed rate

        prob = max(0.02, min(0.98, prob))

        # Invert if title says USD strengthens
        title = market.get("title", "").lower()
        if "usd strengthen" in title or "cad weaken" in title:
            prob = 1.0 - prob

        inputs = {
            "cadusd":      cadusd,
            "cadusd_chg":  cadusd_chg,
            "wti":         wti,
            "wti_chg_pct": wti_chg,
            "policy_rate": policy_rate,
        }
        return ModelResult(cls.name, prob, inputs, confidence=0.45)


class VIXRegimeModel:
    """
    Estimate P(low-vol regime continues / VIX below threshold) from VIX level + momentum.
    """
    name = "vix_regime"

    TITLE_KEYWORDS = ["vix", "volatility", "vol regime", "fear index"]

    @classmethod
    def matches_market(cls, title: str) -> bool:
        return _title_matches(title, cls.TITLE_KEYWORDS)

    @classmethod
    def compute(cls, boc: dict, fx: dict, eq: dict, market: dict) -> Optional[ModelResult]:
        vix     = eq.get("vix")
        vix_chg = eq.get("vix_1d_chg_pct") or 0.0
        if vix is None:
            return None

        # P(VIX < 20) as proxy for "low vol"
        prob = max(0.02, min(0.98, 1.0 - (vix - 12) / 30.0))
        prob -= vix_chg / 100.0 * 0.5   # rising VIX = lower prob
        prob = max(0.02, min(0.98, prob))  # re-clamp after vix_chg adjustment

        # Parse threshold from title if present
        title = market.get("title", "")
        import re
        m = re.search(r"(\d+\.?\d*)", title)
        if m:
            threshold = float(m.group(1))
            # Recalibrate to that threshold (guard against zero to avoid ZeroDivisionError)
            if threshold > 0:
                prob = max(0.02, min(0.98, 1.0 - (vix - threshold * 0.6) / threshold))

        inputs = {"vix": vix, "vix_1d_chg_pct": vix_chg}
        return ModelResult(cls.name, prob, inputs, confidence=0.40)


ALL_MODELS = [BOCRateCutModel, FXMomentumModel, VIXRegimeModel]


# ---------------------------------------------------------------------------
# Kalshi market loader
# ---------------------------------------------------------------------------

import re as _re


def _title_matches(title: str, keywords: List[str]) -> bool:
    """Whole-word keyword match against a market title.

    Plain substring matching produces silent false positives on short tickers:
    "cad" matches "de-CAD-e", "boc" matches "ro-BOC-s". Every keyword is
    therefore anchored to word boundaries, matching the SQL filter in
    ``_keyword_sql`` so the DB pre-filter and the Python check agree.
    """
    if not title:
        return False
    t = title.lower()
    return any(_re.search(rf"\b{_re.escape(kw)}\b", t) for kw in keywords)


def _keyword_sql(keywords: List[str], column: str = "m.title") -> Tuple[str, List[str]]:
    """Build a whole-word, case-insensitive POSIX-regex filter for ``keywords``.

    Postgres spells word boundaries ``\\m`` (start) and ``\\M`` (end); this is
    the SQL twin of ``_title_matches`` and must stay in step with it.
    """
    conditions = " OR ".join(f"{column} ~* %s" for _ in keywords)
    params = [rf"\m{_re.escape(kw)}\M" for kw in keywords]
    return conditions, params


def _trade_anchor(conn) -> Optional[datetime]:
    """Newest trade timestamp present, used as the reference "now" for windows."""
    with conn.cursor() as cur:
        cur.execute(_ANCHOR_SQL)
        row = cur.fetchone()
    return row[0] if row else None


def _load_kalshi_markets(conn, keywords: List[str]) -> List[dict]:
    """Load open Kalshi markets whose title matches any keyword.

    The ``markets`` table carries no price or volume columns (see
    database/schema.sql), so both are derived from the ``trades`` table:
    ``volume`` is contracts traded over the trailing ``VOLUME_WINDOW_DAYS``, and
    ``last_price`` is the most recent trade price. ``last_price`` is only a
    fallback for markets with no live L2 book -- a last trade is not a quote,
    so the source is recorded on every row it produces.
    """
    if not keywords:
        return []
    conditions, params = _keyword_sql(keywords)

    anchor = _trade_anchor(conn)
    if anchor is None:
        log.warning("trades table is empty -- no Kalshi prices available")
        return []
    window_start = anchor - timedelta(days=VOLUME_WINDOW_DAYS)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"""
            WITH matched AS (
                SELECT m.market_id, m.ticker, m.series_ticker, m.title
                FROM markets m
                WHERE ({conditions})
                  AND m.close_time > NOW()
                  AND m.status = 'active'
            ),
            activity AS (
                SELECT
                    t.market_id,
                    SUM(t.quantity)                                     AS volume,
                    (ARRAY_AGG(t.price ORDER BY t.trade_ts DESC))[1]    AS last_price,
                    MAX(t.trade_ts)                                     AS last_trade_ts
                FROM trades t
                JOIN matched mm ON mm.market_id = t.market_id
                WHERE t.trade_ts >= %s
                GROUP BY t.market_id
            )
            SELECT
                mt.market_id, mt.ticker, mt.series_ticker, mt.title,
                a.volume, a.last_price, a.last_trade_ts
            FROM matched mt
            JOIN activity a ON a.market_id = mt.market_id
            WHERE a.volume >= %s
            ORDER BY a.volume DESC
            LIMIT 500
        """, params + [window_start, MIN_VOLUME])
        return [dict(r) for r in cur.fetchall()]


def _load_market_prices(conn, market_ids: List[str]) -> Dict[str, dict]:
    """Reconstruct the best YES bid/ask from the latest L2 snapshot per market.

    Kalshi order books store *bids only* on each side, so the best YES ask is
    the complement of the best NO bid: ``yes_ask = 1 - no_bid``. Returns a dict
    keyed by ``market_id``; markets with no snapshot are simply absent, and the
    caller falls back to the last trade price.
    """
    if not market_ids:
        return {}
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT ON (market_id, side)
                market_id, side, price, quantity, snapshot_ts
            FROM order_book_snapshots
            WHERE market_id = ANY(%s)
              AND level_rank = 1
            ORDER BY market_id, side, snapshot_ts DESC
        """, (list(market_ids),))
        rows = cur.fetchall()

    books: Dict[str, dict] = {}
    for r in rows:
        entry = books.setdefault(r["market_id"], {})
        side = (r["side"] or "").strip().lower()
        price = float(r["price"]) if r["price"] is not None else None
        if side == "yes":
            entry["best_bid"] = price
        elif side == "no" and price is not None:
            entry["best_ask"] = 1.0 - price
        ts = r["snapshot_ts"]
        if ts is not None and (entry.get("snapshot_ts") is None or ts > entry["snapshot_ts"]):
            entry["snapshot_ts"] = ts
    return books


# ---------------------------------------------------------------------------
# Scanner core
# ---------------------------------------------------------------------------

@dataclass
class SpreadRecord:
    ticker:          str
    series_ticker:   Optional[str]
    title:           Optional[str]
    kalshi_yes_bid:  Optional[float]
    kalshi_yes_ask:  Optional[float]
    kalshi_midpoint: Optional[float]
    kalshi_volume:   Optional[int]
    model_name:      str
    model_prob:      float
    model_inputs:    dict
    edge_pct:        float
    direction:       str
    is_flagged:      bool
    price_source:    str = "last_trade"
    notes:           str = ""


def scan_once(conn) -> List[SpreadRecord]:
    """Run one full scan and return SpreadRecord list."""
    from analysis.live_data import get_all_live
    live = get_all_live()
    boc  = live["boc"]
    fx   = live["fx"]
    eq   = live["equity"]

    log.info("Live data: CORRA=%.4f policy=%.4f CADUSD=%.4f VIX=%.1f",
             boc.get("corra") or 0, boc.get("policy_rate") or 0,
             fx.get("cadusd") or 0, eq.get("vix") or 0)

    results: List[SpreadRecord] = []

    for ModelClass in ALL_MODELS:
        keywords = ModelClass.TITLE_KEYWORDS
        markets  = _load_kalshi_markets(conn, keywords)
        log.info("[%s] Found %d matching Kalshi markets", ModelClass.name, len(markets))

        prices = _load_market_prices(conn, [m["market_id"] for m in markets])

        for mkt in markets:
            ticker = mkt["ticker"]
            book = prices.get(mkt["market_id"], {})

            # Prefer a real quote. Fall back to the last trade price only when
            # no L2 book exists, and record which was used -- a last trade is
            # not a quote and the two must never be presented as equivalent.
            yes_bid = book.get("best_bid")
            yes_ask = book.get("best_ask")
            if yes_bid is not None and yes_ask is not None:
                midpoint = (yes_bid + yes_ask) / 2.0
                price_source = "l2_book"
            else:
                last = mkt.get("last_price")
                if last is None:
                    continue
                midpoint = float(last)
                yes_bid = yes_ask = None
                price_source = "last_trade"

            result = ModelClass.compute(boc, fx, eq, mkt)
            if result is None:
                continue

            edge_pct  = (midpoint - result.model_prob) * 100.0
            direction = "FLAT"
            if edge_pct > MIN_EDGE_PCT:
                direction = "SELL_YES"   # kalshi says YES too expensive → sell YES
            elif edge_pct < -MIN_EDGE_PCT:
                direction = "BUY_YES"    # kalshi says YES too cheap → buy YES

            is_flagged = abs(edge_pct) >= MIN_EDGE_PCT and (mkt.get("volume") or 0) >= MIN_VOLUME

            rec = SpreadRecord(
                ticker=ticker,
                series_ticker=mkt.get("series_ticker"),
                title=mkt.get("title"),
                kalshi_yes_bid=yes_bid,
                kalshi_yes_ask=yes_ask,
                kalshi_midpoint=midpoint,
                kalshi_volume=int(mkt["volume"]) if mkt.get("volume") is not None else None,
                price_source=price_source,
                model_name=result.model_name,
                model_prob=result.model_prob,
                model_inputs=result.model_inputs,
                edge_pct=round(edge_pct, 4),
                direction=direction,
                is_flagged=is_flagged,
                notes=f"confidence={result.confidence:.2f}",
            )
            results.append(rec)

    return results


def _persist_results(conn, records: List[SpreadRecord]):
    """Bulk insert scan results into the model-scan table."""
    if not records:
        return
    import json
    rows = [(
        r.ticker, r.series_ticker, r.title,
        r.kalshi_yes_bid, r.kalshi_yes_ask, r.kalshi_midpoint, r.kalshi_volume,
        r.model_name, r.model_prob, json.dumps(r.model_inputs),
        r.edge_pct, r.direction, r.is_flagged, r.price_source, r.notes,
    ) for r in records]

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, f"""
            INSERT INTO {SPREADS_TABLE}
                (ticker, series_ticker, title,
                 kalshi_yes_bid, kalshi_yes_ask, kalshi_midpoint, kalshi_volume,
                 model_name, model_prob, model_inputs,
                 edge_pct, direction, is_flagged, price_source, notes)
            VALUES %s
        """, rows, page_size=500)
    conn.commit()
    log.info("Persisted %d spread records.", len(records))


def _print_opportunities(records: List[SpreadRecord]):
    flagged = [r for r in records if r.is_flagged]
    if not flagged:
        print("No opportunities above threshold.")
        return
    print(f"\n{'='*80}")
    print(f"  CROSS-ASSET ARB OPPORTUNITIES  ({len(flagged)} flagged of {len(records)} scanned)")
    print(f"{'='*80}")
    for r in sorted(flagged, key=lambda x: abs(x.edge_pct), reverse=True):
        sign = "+" if r.edge_pct > 0 else ""
        print(f"  [{r.direction:<10}] {r.ticker:<40} edge={sign}{r.edge_pct:.1f}pp  model={r.model_name}")
        print(f"             kalshi_mid={r.kalshi_midpoint:.3f}  model_prob={r.model_prob:.3f}  vol={r.kalshi_volume}")
        print(f"             {r.title}")
        print()


# ---------------------------------------------------------------------------
# Historical back-fill
# ---------------------------------------------------------------------------

def _val(row, column: str) -> Optional[float]:
    """Read one column from a history row as a float, mapping NaN/missing to None."""
    if column not in row.index:
        return None
    value = row[column]
    return None if pd.isna(value) else float(value)


def _historical_frames() -> Tuple[Any, Any]:
    """Load the point-in-time BOC and market history parquet frames.

    Raises if either is missing: the back-fill must use the cross-asset values
    that were observable on each date. Substituting today's live values would
    inject look-ahead bias and make every historical edge fictitious.
    """
    from analysis.historical_data import load_boc_history, load_market_history

    boc_df = load_boc_history()
    mkt_df = load_market_history()
    if boc_df.empty or mkt_df.empty:
        raise RuntimeError(
            "Historical back-fill needs cross_asset/data/*.parquet. "
            "Run `python run.py cross_asset_history` first -- the back-fill "
            "will not substitute live values for historical ones."
        )
    return boc_df, mkt_df


def historical_backfill(conn, days_back: int = HISTORY_DAYS):
    """Back-fill model spreads from historical Kalshi trades.

    For each date, the Kalshi side is the day's last traded price per market
    (from the ``trades`` table) and the cross-asset side is that same date's
    BOC/FX/equity values from the history parquet -- strictly point-in-time on
    both legs. Dates missing from either source are skipped rather than
    filled in.

    Kalshi prices here are last trades, not quotes, so the resulting edges are
    research signals and are recorded with ``price_source='last_trade'``. They
    are not executable arbitrage.
    """
    log.info("Starting historical back-fill for %d days...", days_back)

    boc_df, mkt_df = _historical_frames()
    start_date = (datetime.now(timezone.utc) - timedelta(days=days_back)).date()

    keywords = sorted({kw for M in ALL_MODELS for kw in M.TITLE_KEYWORDS})
    conditions, params = _keyword_sql(keywords)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"""
            WITH matched AS (
                SELECT m.market_id, m.ticker, m.series_ticker, m.title
                FROM markets m
                WHERE ({conditions})
            )
            SELECT
                DATE(t.trade_ts)                                     AS trade_date,
                mt.ticker, mt.series_ticker, mt.title,
                SUM(t.quantity)                                      AS volume,
                (ARRAY_AGG(t.price ORDER BY t.trade_ts DESC))[1]     AS last_price
            FROM trades t
            JOIN matched mt ON mt.market_id = t.market_id
            WHERE t.trade_ts >= %s
            GROUP BY trade_date, mt.ticker, mt.series_ticker, mt.title
            HAVING SUM(t.quantity) >= %s
            ORDER BY trade_date
        """, params + [start_date, MIN_VOLUME])
        daily_rows = cur.fetchall()

    log.info("Loaded %d market-days of historical Kalshi trade prices", len(daily_rows))

    by_date: Dict[Any, List[dict]] = {}
    for row in daily_rows:
        by_date.setdefault(row["trade_date"], []).append(dict(row))

    total = 0
    for trade_date in sorted(by_date):
        ts = pd.Timestamp(trade_date)
        if ts not in boc_df.index or ts not in mkt_df.index:
            continue  # market holiday / no observable cross-asset print

        b, m = boc_df.loc[ts], mkt_df.loc[ts]
        boc = {"corra": _val(b, "CORRA"), "policy_rate": _val(b, "POLICY_RATE"),
               "gbond_2y": _val(b, "CA_2Y_YIELD"), "gbond_5y": _val(b, "CA_5Y_YIELD"),
               "gbond_10y": _val(b, "CA_10Y_YIELD")}
        fx  = {"cadusd": _val(m, "CADUSD"),
               "usdcad": (1.0 / _val(m, "CADUSD")) if _val(m, "CADUSD") else None}
        eq  = {"tsx": _val(m, "TSX"), "sp500": _val(m, "SP500"),
               "vix": _val(m, "VIX"), "wti": _val(m, "WTI")}

        records: List[SpreadRecord] = []
        for row in by_date[trade_date]:
            mkt = {"ticker": row["ticker"], "series_ticker": row["series_ticker"],
                   "title": row["title"] or "", "volume": int(row["volume"] or 0)}
            midpoint = float(row["last_price"])

            for ModelClass in ALL_MODELS:
                if not ModelClass.matches_market(mkt["title"]):
                    continue
                result = ModelClass.compute(boc, fx, eq, mkt)
                if not result:
                    continue
                edge_pct = (midpoint - result.model_prob) * 100.0
                direction = "FLAT"
                if edge_pct > MIN_EDGE_PCT:
                    direction = "SELL_YES"
                elif edge_pct < -MIN_EDGE_PCT:
                    direction = "BUY_YES"
                records.append(SpreadRecord(
                    ticker=mkt["ticker"],
                    series_ticker=mkt["series_ticker"],
                    title=mkt["title"],
                    kalshi_yes_bid=None,
                    kalshi_yes_ask=None,
                    kalshi_midpoint=midpoint,
                    kalshi_volume=mkt["volume"],
                    model_name=result.model_name,
                    model_prob=result.model_prob,
                    model_inputs=result.model_inputs,
                    edge_pct=round(edge_pct, 4),
                    direction=direction,
                    is_flagged=abs(edge_pct) >= MIN_EDGE_PCT,
                    price_source="last_trade",
                    notes=f"historical date={trade_date}",
                ))

        if records:
            _persist_results(conn, records)
            total += len(records)

    log.info("Historical back-fill complete: %d records across %d dates.",
             total, len(by_date))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    parser = argparse.ArgumentParser(description="Cross-asset arb scanner")
    parser.add_argument("--loop",    type=int, default=0,  help="Loop interval in seconds (0 = run once)")
    parser.add_argument("--history", action="store_true",  help="Run historical back-fill")
    parser.add_argument("--days",    type=int, default=HISTORY_DAYS, help="Days back for back-fill")
    args = parser.parse_args()

    conn = _get_conn()
    _ensure_schema(conn)

    if args.history:
        historical_backfill(conn, args.days)
        conn.close()
        return

    while True:
        t0 = time.monotonic()
        try:
            records = scan_once(conn)
            _persist_results(conn, records)
            _print_opportunities(records)
        except Exception as e:
            log.error("Scan failed: %s", e, exc_info=True)

        if args.loop <= 0:
            break
        elapsed = time.monotonic() - t0
        sleep_s = max(0, args.loop - elapsed)
        log.info("Sleeping %.0fs until next scan...", sleep_s)
        time.sleep(sleep_s)

    conn.close()


if __name__ == "__main__":
    main()
