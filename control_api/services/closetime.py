"""Market close time (read-only telemetry, owner-approved 2026-10-13).

Records `close_time` (aware UTC) for the markets we quote, so analytics can compute hours-to-close:

* quote episodes: set when the episode is created (immutable afterwards: schemas.QUOTE_EPISODE_IMMUTABLE,
  MongoStore writes it with $setOnInsert). An episode created before its close time was known gets it later from
  the bounded backfill (store.set_quote_episode_close_time only writes while the field is missing);
* fills (maker and taker): via store.set_fill_fields;
* settlements: as given by Kalshi if a row carries close_time, else from the backfill.

Source, cheapest first:
1. a value already in hand: the doc itself, or market rows the runner already fetched (the arbitrage scan's
   GET /markets list is recorded through `RecordingPublic`), seeded into the cache at no extra cost;
2. otherwise ONE public GET /markets/{ticker} per (env, ticker), cached (LRU + TTL), at most `max_fetch` per run.

Kalshi field: `close_time` (when trading stops); `expected_expiration_time` is the fallback when close_time is
absent. Nothing here can place, change or cancel an order; failures are counted and never raised.
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

CACHE_MAX = 2048                 # (env, ticker) entries kept (LRU)
HIT_TTL_S = 6 * 3600             # a known close time is re-checked after 6 h (Kalshi can move it, rarely)
MISS_TTL_S = 15 * 60             # failed / empty lookups are not retried for 15 min (no hammering on errors)
MAX_FETCH_EPISODES = 6           # GET /markets/{t} per queue-position run (new episodes)
MAX_FETCH_BACKFILL = 10          # GET /markets/{t} per backfill run
FETCH_SPACING_S = 0.1            # pause between our own GETs (Kalshi public rate limit)
BACKFILL_DAYS = 7                # fills / settlements older than this are left alone
MAX_BACKFILL_WRITES = 200        # store writes per backfill run
CLIENT_TIMEOUT_S = 5.0           # S-31: the close-time public client: short timeout, no retries, so an
CLIENT_MAX_RETRIES = 0           # abandoned worker thread ends quickly
EXECUTOR_WORKERS = 2             # S-31: dedicated pool; never the default executor Stop's cancel uses
FIELD = "close_time"
# It goes into a URL path: no '/', '?', '%', no newline, and it must start with a letter or digit, so '.', '..'
# and dot-only names (path traversal segments) are rejected (S-31). Used with fullmatch.
_TICKER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{0,127}")
_MISS = object()


def to_utc(v: Any) -> datetime | None:
    """datetime | ISO string ('Z' ok) | epoch seconds -> aware UTC datetime (ms precision); else None."""
    try:
        if isinstance(v, bool) or v is None or v == "":
            return None
        if isinstance(v, datetime):
            d = v
        elif isinstance(v, (int, float)):
            d = datetime.fromtimestamp(float(v), timezone.utc)
        elif isinstance(v, str):
            d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        else:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        d = d.astimezone(timezone.utc)
        return d.replace(microsecond=(d.microsecond // 1000) * 1000)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def from_market(m: Any) -> datetime | None:
    """close_time of a Kalshi market row (close_time, else expected_expiration_time), or None."""
    if not isinstance(m, dict):
        return None
    return to_utc(m.get("close_time")) or to_utc(m.get("expected_expiration_time"))


def valid_ticker(t: Any) -> bool:
    return isinstance(t, str) and _TICKER.fullmatch(t) is not None


def doc_ticker(d: dict) -> str:
    return str(d.get("ticker") or d.get("market_ticker") or "")


class CloseTimeCache:
    """Thread-safe LRU of (env, ticker) -> close_time | miss, with separate TTLs for hits and misses."""

    def __init__(self, maxsize: int = CACHE_MAX, hit_ttl: float = HIT_TTL_S, miss_ttl: float = MISS_TTL_S,
                 clock: Callable[[], float] = time.monotonic):
        self.maxsize, self.hit_ttl, self.miss_ttl, self.clock = int(maxsize), float(hit_ttl), float(miss_ttl), clock
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self.fetches = 0             # GETs made (lifetime; for tests and status)
        self.errors = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._d)

    def _put(self, key: tuple[str, str], value: Any) -> None:
        ttl = self.miss_ttl if value is _MISS else self.hit_ttl
        with self._lock:
            self._d[key] = (value, self.clock() + ttl)
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def put(self, env: str, ticker: str, close: datetime | None) -> None:
        if valid_ticker(ticker):
            self._put((env, ticker), close if close is not None else _MISS)

    def seed(self, env: str, ticker: str, close: datetime | None) -> None:
        """A value already in hand (no call). Only real values are seeded; a None never overwrites anything."""
        if close is not None and valid_ticker(ticker):
            self._put((env, ticker), close)

    def lookup(self, env: str, ticker: str) -> tuple[bool, datetime | None]:
        """(known, value): known=False -> not cached or expired (a fetch may help); (True, None) -> a recent miss."""
        key = (env, ticker)
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                return False, None
            value, exp = hit
            if self.clock() >= exp:
                del self._d[key]
                return False, None
            self._d.move_to_end(key)
            return True, (None if value is _MISS else value)

    def get(self, env: str, ticker: str) -> datetime | None:
        return self.lookup(env, ticker)[1]

    def observe_markets(self, env: str, markets: Any) -> int:
        """Seed from market rows the runner already fetched. Returns the number seeded."""
        n = 0
        if not isinstance(markets, (list, tuple)):
            return 0
        for m in markets:
            c = from_market(m)
            t = m.get("ticker") if isinstance(m, dict) else None
            if c is not None and valid_ticker(t):
                self.seed(env, t, c)
                n += 1
        return n

    # ---- the only network part (sync; run it in a thread)
    def resolve(self, pub_factory: Callable[[str], Any] | None, wanted: Iterable[tuple[str, str]], *,
                max_fetch: int, deadline: float | None = None) -> dict[tuple[str, str], datetime]:
        """Close times for (env, ticker) pairs: cache first, then at most `max_fetch` GET /markets/{t} (stops early
        at `deadline`, a time.monotonic() value). Never raises. Unresolved pairs are simply absent."""
        out: dict[tuple[str, str], datetime] = {}
        todo: list[tuple[str, str]] = []
        for env, t in dict.fromkeys(wanted):          # de-duplicated, order kept
            if not valid_ticker(t):
                continue
            known, v = self.lookup(env, t)
            if known:
                if v is not None:
                    out[(env, t)] = v
            else:
                todo.append((env, t))
        if not todo or pub_factory is None or max_fetch <= 0:
            return out
        clients: dict[str, Any] = {}
        for i, (env, t) in enumerate(todo[:max_fetch]):
            if deadline is not None and time.monotonic() >= deadline:
                break
            if i:
                time.sleep(FETCH_SPACING_S)
            try:
                if env not in clients:
                    clients[env] = pub_factory(env)
                c = fetch_market_close(clients[env], t)
                self.fetches += 1
            except Exception:  # noqa: BLE001   (any failure: a short-lived miss, never raised)
                self.fetches += 1
                self.errors += 1
                c = None
            self.put(env, t, c)
            if c is not None:
                out[(env, t)] = c
        return out


def fetch_market_close(pub: Any, ticker: str) -> datetime | None:
    """One read-only GET /markets/{ticker}. Raises on API errors (the caller counts them)."""
    if pub is None or not valid_ticker(ticker):
        return None
    getter = getattr(pub, "get_market", None)
    if callable(getter):
        body = getter(ticker)
    else:
        body = pub.request("GET", f"/markets/{ticker}")
        body = body.get("market", body) if isinstance(body, dict) else body
    return from_market(body)


class RecordingPublic:
    """Wraps a public Kalshi client: get_markets results are passed through unchanged and their close times
    seeded into the cache (so markets the arbitrage scan already lists cost no extra call)."""

    def __init__(self, inner: Any, cache: CloseTimeCache, env: str):
        self._inner, self._cache, self._env = inner, cache, env

    def get_markets(self, *a, **kw):
        res = self._inner.get_markets(*a, **kw)
        try:
            self._cache.observe_markets(self._env, res)
        except Exception:  # noqa: BLE001   (recording must never affect the caller)
            pass
        return res

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def missing(docs: Iterable[dict]) -> list[dict]:
    """Docs without a usable close_time."""
    return [d for d in docs if isinstance(d, dict) and to_utc(d.get(FIELD)) is None]
