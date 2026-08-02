"""
data/kalshi_client.py
Thin, rate-limited HTTP client for the Kalshi Trade API v2.

Handles:
  - RSA-PSS authentication (for authenticated endpoints)
  - Cursor-based pagination
  - Exponential back-off on 429 / transient errors
  - Token-bucket rate limiting (read vs. write)

Public endpoints work without credentials; authenticated endpoints require
KALSHI_KEY_ID and KALSHI_PRIVKEY_PATH set in the environment / .env.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import math
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

logger = logging.getLogger(__name__)


# -- Token-bucket rate limiter --------------------------------------------------

class TokenBucket:
    """Simple token-bucket for rate limiting."""

    def __init__(self, rate: float):
        self._rate = rate
        self._tokens = rate
        self._last = time.monotonic()

    def consume(self, tokens: float = 1.0) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
        self._last = now
        if self._tokens < tokens:
            sleep_for = (tokens - self._tokens) / self._rate
            time.sleep(sleep_for)
            self._tokens = 0.0
        else:
            self._tokens -= tokens


# -- RSA signing ---------------------------------------------------------------

def _load_private_key(path: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Kalshi private key not found: {path}")
    with open(p, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _sign_request(key_id: str, private_key, method: str, path: str) -> Dict[str, str]:
    """Build Kalshi RSA-PSS auth headers."""
    ts_ms = str(int(time.time() * 1000))
    message = ts_ms + method.upper() + path
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
    }


# -- KalshiClient --------------------------------------------------------------

class KalshiClient:
    """
    HTTP client for Kalshi Trade API v2.

    Usage:
        client = KalshiClient()                    # public endpoints only
        client = KalshiClient(authenticated=True)  # requires env vars
    """

    BASE_URL = config.KALSHI_BASE_URL

    def __init__(self, authenticated: bool = False):
        self._authenticated = authenticated
        self._private_key = None

        if authenticated:
            if not config.KALSHI_KEY_ID or not config.KALSHI_PRIVKEY_PATH:
                raise ValueError(
                    "KALSHI_KEY_ID and KALSHI_PRIVKEY_PATH must be set for authenticated use."
                )
            self._private_key = _load_private_key(config.KALSHI_PRIVKEY_PATH)

        # HTTP session with connection pooling and basic retry for network errors
        self._session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("https://", adapter)
        self._session.headers.update({"Content-Type": "application/json"})

        # Rate limiters (conservative - Basic tier defaults)
        self._read_limiter  = TokenBucket(config.KALSHI_READ_RPS)
        self._write_limiter = TokenBucket(config.KALSHI_WRITE_RPS)

    # -- Core request method ----------------------------------------------------

    @retry(
        retry=retry_if_exception_type(requests.exceptions.HTTPError),
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        is_write = method.upper() in ("POST", "PUT", "DELETE", "PATCH")
        if is_write:
            self._write_limiter.consume()
        else:
            self._read_limiter.consume()

        headers: Dict[str, str] = {}
        if self._authenticated and self._private_key:
            headers.update(_sign_request(
                config.KALSHI_KEY_ID,
                self._private_key,
                method,
                "/trade-api/v2" + path,
            ))

        url = self.BASE_URL + path
        resp = self._session.request(
            method=method,
            url=url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=30,
        )

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "5"))
            logger.warning("Rate limited (429). Sleeping %.1fs.", retry_after)
            time.sleep(retry_after)
            resp.raise_for_status()  # triggers tenacity retry

        resp.raise_for_status()
        return resp.json()

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: Dict[str, Any]) -> Any:
        return self._request("POST", path, json_body=body)

    # -- Paginated iterator -----------------------------------------------------

    def paginate(
        self,
        path: str,
        result_key: str,
        params: Optional[Dict[str, Any]] = None,
        page_size: int = 200,
    ) -> Iterator[List[Dict[str, Any]]]:
        """
        Yield successive pages of results from a cursor-paginated endpoint.

        Args:
            path:        API path (e.g. "/markets")
            result_key:  Top-level JSON key containing the list (e.g. "markets")
            params:      Additional query parameters
            page_size:   Items per page (capped by API limits)
        """
        base_params = dict(params or {})
        base_params["limit"] = page_size
        cursor = None

        while True:
            if cursor:
                base_params["cursor"] = cursor
            data = self.get(path, params=base_params)

            # Guard: API may return None or a non-dict body on transient errors.
            if not isinstance(data, dict):
                break

            items: List = data.get(result_key, [])
            if items:
                yield items

            cursor = data.get("cursor")
            if not cursor:
                break

    # -- Public API methods -----------------------------------------------------

    def get_exchange_status(self) -> Dict[str, Any]:
        return self.get("/exchange/status")

    def get_markets(
        self,
        status: Optional[str] = None,
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
        tickers: Optional[List[str]] = None,
        limit: int = 1000,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if status:          params["status"] = status
        if series_ticker:   params["series_ticker"] = series_ticker
        if event_ticker:    params["event_ticker"] = event_ticker
        if tickers:         params["tickers"] = ",".join(tickers)
        if cursor:          params["cursor"] = cursor
        return self.get("/markets", params=params)

    def get_market(self, ticker: str) -> Dict[str, Any]:
        return self.get(f"/markets/{ticker}")

    def get_orderbook(self, ticker: str, depth: int = 10) -> Dict[str, Any]:
        return self.get(f"/markets/{ticker}/orderbook", params={"depth": depth})

    def get_candlesticks(
        self,
        tickers: List[str],
        period_interval: int,
        start_ts: int,
        end_ts: int,
    ) -> Dict[str, Any]:
        """Live candlestick endpoint (≈ last 3 months)."""
        return self.get(
            "/markets/candlesticks",
            params={
                "tickers": ",".join(tickers),
                "period_interval": period_interval,
                "start_ts": start_ts,
                "end_ts": end_ts,
            },
        )

    def get_trades(
        self,
        ticker: Optional[str] = None,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None,
        limit: int = 1000,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if ticker:  params["ticker"] = ticker
        if min_ts:  params["min_ts"] = min_ts
        if max_ts:  params["max_ts"] = max_ts
        if cursor:  params["cursor"] = cursor
        return self.get("/markets/trades", params=params)

    def get_events(
        self,
        status: Optional[str] = None,
        series_ticker: Optional[str] = None,
        with_nested_markets: bool = False,
        limit: int = 200,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if status:              params["status"] = status
        if series_ticker:       params["series_ticker"] = series_ticker
        if with_nested_markets: params["with_nested_markets"] = "true"
        if cursor:              params["cursor"] = cursor
        return self.get("/events", params=params)

    def get_event(self, event_ticker: str) -> Dict[str, Any]:
        return self.get(f"/events/{event_ticker}")

    def get_series(self) -> Dict[str, Any]:
        return self.get("/series")

    def get_fee_changes(self) -> Dict[str, Any]:
        return self.get("/series/fee_changes")

    # -- Historical endpoints ---------------------------------------------------

    def get_historical_cutoff(self) -> Dict[str, Any]:
        """Returns partition timestamps separating live from historical tier."""
        return self.get("/historical/cutoff")

    def get_historical_markets(
        self,
        limit: int = 1000,
        cursor: Optional[str] = None,
        status: Optional[str] = None,
        series_ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if cursor:        params["cursor"] = cursor
        if status:        params["status"] = status
        if series_ticker: params["series_ticker"] = series_ticker
        return self.get("/historical/markets", params=params)

    def get_historical_market(self, ticker: str) -> Dict[str, Any]:
        return self.get(f"/historical/markets/{ticker}")

    def get_historical_candlesticks(
        self,
        ticker: str,
        period_interval: int,
        start_ts: int,
        end_ts: int,
    ) -> Dict[str, Any]:
        """Historical candlesticks for a settled/archived market."""
        return self.get(
            f"/historical/markets/{ticker}/candlesticks",
            params={
                "start_ts": start_ts,
                "end_ts": end_ts,
                "period_interval": period_interval,
            },
        )

    def get_historical_trades(
        self,
        limit: int = 1000,
        cursor: Optional[str] = None,
        ticker: Optional[str] = None,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if cursor: params["cursor"] = cursor
        if ticker: params["ticker"] = ticker
        if min_ts: params["min_ts"] = min_ts
        if max_ts: params["max_ts"] = max_ts
        return self.get("/historical/trades", params=params)

    # -- Authenticated portfolio endpoints --------------------------------------

    def get_balance(self) -> Dict[str, Any]:
        self._require_auth()
        return self.get("/portfolio/balance")

    def get_positions(self) -> Dict[str, Any]:
        self._require_auth()
        return self.get("/portfolio/positions")

    def get_fills(
        self,
        ticker: Optional[str] = None,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None,
        limit: int = 200,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._require_auth()
        params: Dict[str, Any] = {"limit": limit}
        if ticker: params["ticker"] = ticker
        if min_ts: params["min_ts"] = min_ts
        if max_ts: params["max_ts"] = max_ts
        if cursor: params["cursor"] = cursor
        return self.get("/portfolio/fills", params=params)

    # -- Helpers ----------------------------------------------------------------

    def _require_auth(self) -> None:
        if not self._authenticated:
            raise RuntimeError("This endpoint requires authenticated=True.")

    def all_open_markets(self) -> Iterator[Dict[str, Any]]:
        """Yield every open market, one at a time (handles pagination)."""
        for page in self.paginate("/markets", "markets",
                                   params={"status": "open"}, page_size=1000):
            yield from page

    def all_historical_markets(self) -> Iterator[Dict[str, Any]]:
        """Yield every historical (settled/archived) market."""
        for page in self.paginate("/historical/markets", "markets", page_size=1000):
            yield from page

    def all_events(self, status: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        params = {}
        if status:
            params["status"] = status
        for page in self.paginate("/events", "events", params=params, page_size=200):
            yield from page
