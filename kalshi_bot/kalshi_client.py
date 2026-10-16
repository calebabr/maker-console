"""Minimal Kalshi Trade API v2 client: RSA-PSS request signing, retries, backoff.

Docs used (verified 2026-09-26):
  - Signing:      https://docs.kalshi.com/getting_started/api_keys
  - Environments: https://docs.kalshi.com/getting_started/api_environments
  - Create order: https://docs.kalshi.com/api-reference/orders/create-order-v2
  - Cancel order: https://docs.kalshi.com/api-reference/orders/cancel-order-v2
  - Orders:       https://docs.kalshi.com/api-reference/orders/get-orders
  - Balance:      https://docs.kalshi.com/api-reference/portfolio/get-balance
  - Positions:    https://docs.kalshi.com/api-reference/portfolio/get-positions
  - Rate limits:  https://docs.kalshi.com/getting_started/rate_limits

Signing: message = f"{timestamp_ms}{METHOD}{path}" where path INCLUDES the
"/trade-api/v2" prefix and EXCLUDES the query string. RSA-PSS, SHA-256, MGF1(SHA-256),
salt length = digest length. Signature is base64. Headers: KALSHI-ACCESS-KEY,
KALSHI-ACCESS-TIMESTAMP (ms), KALSHI-ACCESS-SIGNATURE.
"""
from __future__ import annotations

import base64
import email.utils
import logging
import os
import random
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

import requests

log = logging.getLogger("kalshi_client")

RETRY_STATUSES = {429, 500, 502, 503, 504}


class KalshiError(Exception):
    def __init__(self, status: int, body: Any, method: str = "", path: str = ""):
        super().__init__(f"{method} {path} -> HTTP {status}: {body}")
        self.status = status
        self.body = body


class CredentialsMissing(Exception):
    pass


def load_private_key_from_env():
    """Load the RSA (or Ed25519) private key from KALSHI_PRIVATE_KEY (PEM text)
    or KALSHI_PRIVATE_KEY_PATH (file). Returns None if neither is set."""
    from cryptography.hazmat.primitives import serialization

    pem = os.environ.get("KALSHI_PRIVATE_KEY")
    path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if pem:
        # Allow single-line secrets with literal "\n" (common in CI secret stores).
        data = pem.replace("\\n", "\n").encode()
    elif path:
        with open(os.path.expanduser(path), "rb") as f:
            data = f.read()
    else:
        return None
    return serialization.load_pem_private_key(data, password=None)


def sign_message(private_key, message: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    msg = message.encode("utf-8")
    if isinstance(private_key, Ed25519PrivateKey):
        sig = private_key.sign(msg)
    else:
        sig = private_key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
    return base64.b64encode(sig).decode("ascii")


class KalshiClient:
    def __init__(
        self,
        base_url: str,
        key_id: str | None = None,
        private_key=None,
        session: requests.Session | None = None,
        max_retries: int = 6,
        backoff_base: float = 0.5,
        backoff_cap: float = 20.0,
        timeout: float = 15.0,
        sleep=time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.path_prefix = urlsplit(self.base_url).path  # e.g. /trade-api/v2
        self.key_id = key_id
        self.private_key = private_key
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.timeout = timeout
        self._sleep = sleep
        self.clock_skew_seconds: float | None = None  # local - server

    @classmethod
    def from_env(cls, base_url: str, **kw) -> "KalshiClient":
        key_id = os.environ.get("KALSHI_API_KEY_ID") or None
        pk = load_private_key_from_env()
        return cls(base_url, key_id=key_id, private_key=pk, **kw)

    @property
    def has_credentials(self) -> bool:
        return bool(self.key_id and self.private_key is not None)

    # ---------------- low level ----------------
    def auth_headers(self, method: str, path: str, ts_ms: int | None = None) -> dict[str, str]:
        if not self.has_credentials:
            raise CredentialsMissing("KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY[_PATH] must be set")
        ts = str(ts_ms if ts_ms is not None else int(time.time() * 1000))
        sign_path = path.split("?", 1)[0]
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sign_message(self.private_key, ts + method.upper() + sign_path),
        }

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(self.backoff_cap, float(retry_after))
            except ValueError:
                pass
        return min(self.backoff_cap, self.backoff_base * (2 ** attempt)) * (0.5 + random.random() / 2)

    def _record_skew(self, resp) -> None:
        date = resp.headers.get("Date") if hasattr(resp, "headers") else None
        if not date:
            return
        try:
            server = email.utils.parsedate_to_datetime(date).timestamp()
            self.clock_skew_seconds = time.time() - server
        except (TypeError, ValueError):
            pass

    def request(self, method: str, endpoint: str, *, params: dict | None = None,
                json_body: dict | None = None, auth: bool = False) -> Any:
        method = method.upper()
        endpoint = "/" + endpoint.lstrip("/")
        path = self.path_prefix + endpoint
        url = self.base_url + endpoint
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urlencode(clean, doseq=True)
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            headers = {"Accept": "application/json"}
            if json_body is not None:
                headers["Content-Type"] = "application/json"
            if auth:
                headers.update(self.auth_headers(method, path))  # fresh timestamp per attempt
            try:
                resp = self.session.request(method, url, headers=headers, json=json_body, timeout=self.timeout)
            except requests.RequestException as e:
                last_exc = e
                if attempt >= self.max_retries:
                    raise
                # POSTs are safe to retry only because orders carry a client_order_id
                delay = self._backoff(attempt, None)
                log.warning("network error %s on %s %s; retry in %.1fs", e, method, endpoint, delay)
                self._sleep(delay)
                continue
            self._record_skew(resp)
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                delay = self._backoff(attempt, resp.headers.get("Retry-After"))
                log.warning("HTTP %s on %s %s; retry in %.1fs", resp.status_code, method, endpoint, delay)
                self._sleep(delay)
                continue
            try:
                body = resp.json() if resp.content else {}
            except ValueError:
                body = resp.text
            if resp.status_code >= 400:
                raise KalshiError(resp.status_code, body, method, endpoint)
            return body
        raise last_exc or RuntimeError("unreachable")

    # ---------------- public market data ----------------
    def get_markets(self, **params) -> list[dict]:
        out, cursor = [], None
        while True:
            q = dict(params, limit=params.get("limit", 200), cursor=cursor)
            body = self.request("GET", "/markets", params=q)
            out.extend(body.get("markets", []))
            cursor = body.get("cursor")
            if not cursor:
                return out

    def get_orderbook(self, ticker: str, depth: int | None = None) -> dict:
        return self.request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    def get_exchange_status(self) -> dict:
        return self.request("GET", "/exchange/status")

    # ---------------- authenticated portfolio ----------------
    def get_balance(self) -> dict:
        return self.request("GET", "/portfolio/balance", auth=True)

    def get_positions(self, **params) -> dict:
        return self.request("GET", "/portfolio/positions", params=params, auth=True)

    def get_market(self, ticker: str) -> dict:
        body = self.request("GET", f"/markets/{ticker}")
        return body.get("market", body)

    def get_fills(self, **params) -> list[dict]:
        """All fills (recent + historical archive), newest first. Read-only."""
        out = []
        for path in ("/portfolio/fills", "/historical/fills"):
            cursor = None
            while True:
                try:
                    body = self.request("GET", path, params=dict(params, limit=1000, cursor=cursor), auth=True)
                except KalshiError as e:
                    if path == "/historical/fills" and e.status in (400, 404):
                        break                                   # no archive access/needed
                    raise
                out.extend(body.get("fills", []))
                cursor = body.get("cursor")
                if not cursor or not body.get("fills"):
                    break
        seen, uniq = set(), []
        for f in out:
            fid = f.get("fill_id") or f.get("trade_id")
            if fid not in seen:
                seen.add(fid); uniq.append(f)
        return uniq

    def get_orders(self, **params) -> list[dict]:
        out, cursor = [], None
        while True:
            body = self.request("GET", "/portfolio/orders", params=dict(params, cursor=cursor), auth=True)
            out.extend(body.get("orders", []))
            cursor = body.get("cursor")
            if not cursor:
                return out

    def create_order(self, *, ticker: str, side: str, count: str, price: str, client_order_id: str,
                     time_in_force: str = "immediate_or_cancel",
                     self_trade_prevention_type: str = "taker_at_cross",
                     expiration_time: int | None = None, post_only: bool = False) -> dict:
        """V2 create order: POST /portfolio/events/orders.
        side: "bid" = buy YES at `price`; "ask" = sell YES at `price` (== buy NO at 1-price).
        count/price are fixed-point strings ("3.00", "0.4500")."""
        if side not in {"bid", "ask"}:
            raise ValueError("side must be 'bid' or 'ask'")
        body: dict[str, Any] = {
            "ticker": ticker,
            "side": side,
            "count": count,
            "price": price,
            "time_in_force": time_in_force,
            "self_trade_prevention_type": self_trade_prevention_type,
            "client_order_id": client_order_id,
            "post_only": post_only,
        }
        if expiration_time is not None:
            body["expiration_time"] = int(expiration_time)
        return self.request("POST", "/portfolio/events/orders", json_body=body, auth=True)

    def cancel_order(self, order_id: str, market_ticker: str | None = None) -> dict:
        params = {"market_ticker": market_ticker, "exchange_index": -1} if market_ticker else None
        return self.request("DELETE", f"/portfolio/events/orders/{order_id}", params=params, auth=True)
