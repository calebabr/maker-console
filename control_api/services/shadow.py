"""Shadow mode: record what the installed strategy WOULD bid on production markets, using public reads only.

Nothing here can place an order. Three independent layers make sure of it:
  1. `ReadOnlyMarketData` wraps a market-data client by composition and exposes only four read methods
     (`markets`, `orderbook`, `market`, `trades`). It has no generic request method and no account or order methods.
  2. `GetOnlySession` (a requests.Session) refuses every non-GET request, every host other than the configured
     public API host, every path outside the four public market-data paths, and any request carrying credentials.
  3. `build_market_data` builds the client with no key id and no private key, and `ReadOnlyMarketData` refuses a
     client that has any credentials.

The strategy logic is NOT here: one cycle calls the installed strategy module's own pure `desired_quotes` (the
public stand-in returns an empty list) and only records, per hypothetical bid ("episode"), the queue facts the
estimate model (services/shadow_model.py) needs. `run_cycle` is synchronous (it runs on the runner's own one-worker
shadow pool) and returns the documents to write; it never touches the store, the process environment or any lock.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit

import requests

from . import botfiles
from . import closetime as ctsvc
from . import fillquality as fq
from . import queuepos as qp
from . import shadow_model as sm

# ---- limits (docs/SHADOW_CONTRACT.md)
SHADOW_BUDGET_S = 60                 # one whole cycle (store reads + the worker thread); a timeout writes nothing
SHADOW_MAX_BOOKS = 150               # order books read per cycle
SHADOW_MAX_TRADE_READS = 40          # ended episodes whose public trades are read per cycle
SHADOW_DAILY_NEW_CAP = 1500          # new episodes per UTC day (bounds Atlas M0 storage)
SHADOW_MAX_SETTLE_READS = 40         # market lookups for settlement per cycle
SHADOW_MAX_MARKET_PAGES = 5          # pages of GET /markets per series
SHADOW_MAX_TRADE_PAGES = 3           # pages of GET /markets/trades per episode
SHADOW_STALE_AFTER = timedelta(hours=1)   # an open episode not observed for this long ends "stale"
SHADOW_MAX_TRADE_ATTEMPTS = 3        # SH-3: a trade read failing this many times gives up (fill unknown)
SHADOW_VOID_AFTER = timedelta(days=7)     # SH-1: closed this long with no yes/no result -> "void"
SHADOW_SUMMARY_MAX_ROWS = 20_000     # SH-2: settled episodes loaded for one summary (newest kept)
SHADOW_SUMMARY_MAX_OPEN = 3_000
SHADOW_SUMMARY_EVERY = timedelta(minutes=30)   # SH-2: cached summary refresh (sooner when something settled)
# SH-2: the only episode fields summarize() reads (a projection keeps memory and Atlas transfer small)
SUMMARY_FIELDS = ("result", "fill", "price", "count", "cost", "side", "settled_ts", "join_ts", "end_ts",
                  "close_time", "series", "cell")
SETTLED_STATUSES = ("settled", "finalized", "determined")
SHADOW_SUMMARY_DAYS = 30
SHADOW_STORE_TIMEOUT_S = 10.0
# S3: the arbitrage shadow scan (off unless env SHADOW_ARB=true). At most one per shadow cycle, after the episode work;
# it stops itself at its own request and time budgets and never raises ShadowDeadline (the cycle's episodes are kept).
SHADOW_ARB_ENV = "SHADOW_ARB"
SHADOW_ARB_MAX_REQUESTS = 60         # market-list pages + order books per scan
SHADOW_ARB_BUDGET_S = 15.0           # wall time per scan (also never past the cycle deadline)
SHADOW_ARB_MAX_EVENTS = 40           # events scanned per scan
CLIENT_TIMEOUT_S = 8.0
CLIENT_MAX_RETRIES = 1
SHADOW_ARB_READ_RESERVE_S = CLIENT_TIMEOUT_S * (CLIENT_MAX_RETRIES + 1)   # L1: worst case of one read
DEADLINE_MARGIN_S = 5.0              # the worker stops itself this long before the cycle budget runs out

END_REASONS = ("cancel", "closed", "shadow_off", "stale")
_SEG = r"[A-Za-z0-9][A-Za-z0-9._\-]{0,127}"     # one ticker path segment (never "." or "..")
_CRED_HEADERS = ("authorization", "kalshi-access-key", "kalshi-access-signature", "kalshi-access-timestamp")


class ShadowDeadline(RuntimeError):
    """The worker ran past its deadline; the cycle's partial result is discarded."""


# ================================================================ layer 2: the session
class GetOnlySession(requests.Session):
    """A requests.Session that can only GET the public market-data paths of one host. Checked in both `request`
    and `send` (redirects go through `send` too). Raises PermissionError otherwise."""

    def __init__(self, base_url: str):
        super().__init__()
        u = urlsplit(base_url)
        self._scheme = u.scheme.lower()
        self._netloc = u.netloc.lower()
        prefix = re.escape(u.path.rstrip("/"))
        self._allowed = re.compile(rf"^{prefix}/markets(?:/trades|/{_SEG}|/{_SEG}/orderbook)?$")
        self.trust_env = False          # no proxy or netrc credentials picked up from the environment

    def _check(self, method: Any, url: Any) -> None:
        if str(method or "").upper() != "GET":
            raise PermissionError("shadow: only GET requests are allowed")
        u = urlsplit(str(url))
        if u.scheme.lower() != self._scheme or u.netloc.lower() != self._netloc:
            raise PermissionError("shadow: host not allowed")
        if "%" in u.path or "\\" in u.path or not self._allowed.fullmatch(u.path):
            raise PermissionError("shadow: path not allowed")

    def request(self, method, url, *args, **kwargs):  # noqa: D401
        self._check(method, url)
        return super().request(method, url, *args, **kwargs)

    def send(self, request, **kwargs):
        self._check(request.method, request.url)
        for h in request.headers:
            if str(h).lower() in _CRED_HEADERS:
                raise PermissionError("shadow: credentials are not allowed")
        return super().send(request, **kwargs)


# ================================================================ layer 1: the wrapper
def _body(b: Any) -> dict:
    return b if isinstance(b, dict) else {}


class ReadOnlyMarketData:
    """Public market data only. Holds the client privately; exposes four read methods and nothing else."""

    __slots__ = ("_client",)

    def __init__(self, client: Any):
        if (getattr(client, "has_credentials", False) or getattr(client, "key_id", None)
                or getattr(client, "private_key", None) is not None):
            raise PermissionError("shadow: the market-data client must have no credentials")
        self._client = client

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        return _body(self._client.request("GET", endpoint, params=params))

    @staticmethod
    def _ticker(t: Any) -> str:
        if not ctsvc.valid_ticker(t):
            raise ValueError("shadow: invalid ticker")
        return t

    def markets(self, series: str, status: str, min_close_ts: int | None = None) -> tuple[list[dict], bool]:
        """(markets of one series, truncated). Bounded pagination."""
        self._ticker(series)
        out: list[dict] = []
        cursor = None
        for _ in range(SHADOW_MAX_MARKET_PAGES):
            body = self._get("/markets", {"series_ticker": series, "status": status, "min_close_ts": min_close_ts,
                                          "limit": 200, "cursor": cursor})
            out.extend(qp.dicts(body.get("markets")))
            cursor = body.get("cursor")
            if not cursor:
                return out, False
        return out, True

    def orderbook(self, ticker: str, depth: int | None = None) -> dict:
        body = self._get(f"/markets/{self._ticker(ticker)}/orderbook", {"depth": depth})
        book = body.get("orderbook_fp") or body.get("orderbook") or {}
        return book if isinstance(book, dict) else {}

    def market(self, ticker: str) -> dict:
        body = self._get(f"/markets/{self._ticker(ticker)}")
        m = body.get("market", body)
        return m if isinstance(m, dict) else {}

    def trades(self, ticker: str, min_ts: int, max_ts: int) -> tuple[list[dict], bool]:
        """(public trades of one market in [min_ts, max_ts] (unix seconds), truncated). Bounded pagination."""
        self._ticker(ticker)
        out: list[dict] = []
        cursor = None
        for _ in range(SHADOW_MAX_TRADE_PAGES):
            body = self._get("/markets/trades", {"ticker": ticker, "min_ts": int(min_ts), "max_ts": int(max_ts),
                                                 "limit": 1000, "cursor": cursor})
            page = qp.dicts(body.get("trades"))
            out.extend(page)
            cursor = body.get("cursor")
            if not cursor or not page:
                return out, False
        return out, True


# ================================================================ layer 3: the factory
def build_market_data(base_url: str | None = None, *, adapter: Any = None) -> ReadOnlyMarketData:
    """The production public market-data client: no key id, no private key, GET-only session, short timeout.
    `adapter` (tests): a requests transport adapter mounted on the session, so the whole real chain is exercised
    without the network."""
    botfiles.ensure_bot_path()
    from config import BASE_URLS
    from kalshi_client import KalshiClient
    base = (base_url or BASE_URLS["prod"]).rstrip("/")
    session = GetOnlySession(base)
    if adapter is not None:
        session.mount(f"{urlsplit(base).scheme}://", adapter)
    client = KalshiClient(base, key_id=None, private_key=None, session=session,
                          max_retries=CLIENT_MAX_RETRIES, timeout=CLIENT_TIMEOUT_S)
    return ReadOnlyMarketData(client)


def default_shadow_factory() -> ReadOnlyMarketData:
    return build_market_data()


# ================================================================ one cycle
@dataclass
class CycleInput:
    series: list[str]
    run_budget: float
    max_order: float
    open_eps: list[dict] = field(default_factory=list)
    ended_unread: list[dict] = field(default_factory=list)     # up to SHADOW_MAX_TRADE_READS + 1 (oldest first)
    unsettled: list[dict] = field(default_factory=list)        # result null and close_time <= now (oldest first)
    new_today: int = 0
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    standin: bool = False
    # S3: None = no arbitrage scan this cycle; else {"series", "max_contracts", "min_profit", "scan_cap"}
    arb: dict | None = None


@dataclass
class CycleResult:
    upserts: list[dict]
    pass_doc: dict
    arb: dict | None = None          # S3: the arbitrage scan result (None when it did not run)


def ep_id_for(ticker: str, book_side: str, order_price: float, join_ts: datetime) -> str:
    return hashlib.sha256(f"{ticker}|{book_side}|{float(order_price):.4f}|{join_ts.isoformat()}".encode()).hexdigest()


def key_of(ticker: Any, book_side: Any, order_price: Any) -> tuple[str, str, str] | None:
    p = qp._f(order_price)
    if not ticker or book_side not in ("bid", "ask") or p is None:
        return None
    return str(ticker), str(book_side), f"{p:.4f}"


def _dt(v: Any) -> datetime | None:
    return ctsvc.to_utc(v)


def _err(errors: list[str], e: BaseException | str) -> None:
    """Record an error by exception type name only (messages can echo URLs or bodies)."""
    name = e if isinstance(e, str) else type(e).__name__
    if len(errors) < 50:
        errors.append(name)


def first_through(trades: Iterable[dict], side: str, price: float, since: datetime,
                  until: datetime | None) -> datetime | None:
    """Time of the first trade below our price on our side (a trade-through) in [since, until], or None."""
    first = None
    for t in qp.dicts(list(trades)):
        tt, yp = fq._ts(t.get("created_time") or t.get("ts")), fq.trade_yes_price(t)
        if tt is None or yp is None or tt < since or (until is not None and tt > until):
            continue
        q = yp if side == "yes" else 1.0 - yp
        if q < price - qp.PRICE_TOL and (first is None or tt < first):
            first = tt
    return first


def _quote_fields(q: Any) -> dict | None:
    """The plain fields of one strategy Quote, or None if it is malformed."""
    try:
        ticker, side, book_side = getattr(q, "ticker"), getattr(q, "side"), getattr(q, "book_side")
        price, count, order_price = float(q.price), float(q.count), float(q.order_price)
        cell = list(getattr(q, "cell", ()) or ())
    except (AttributeError, TypeError, ValueError):
        return None
    if not ctsvc.valid_ticker(ticker) or side not in ("yes", "no") or book_side not in ("bid", "ask"):
        return None
    if not (0 < price < 1) or not (0 < order_price < 1) or count <= 0:
        return None
    return {"ticker": ticker, "side": side, "book_side": book_side, "price": price, "count": count,
            "order_price": order_price, "cell": cell}


def _end(ep: dict, reason: str, at: datetime) -> dict:
    ep["end_ts"], ep["end_reason"] = at, reason
    return ep


def _maybe_score(ep: dict) -> None:
    if ep.get("result") in ("yes", "no") and ep.get("fill"):
        try:
            ep["pnl"] = sm.score_episode(ep)
        except (ValueError, TypeError):
            ep["pnl"] = None


def run_cycle(md: Any, strategy: Any, inp: CycleInput, *, deadline: float | None = None) -> CycleResult:
    """One shadow cycle against `md` (ReadOnlyMarketData or a test fake). Synchronous; network reads only.
    Raises ShadowDeadline past `deadline` (time.monotonic()), so the caller discards a partial result."""
    t0 = time.monotonic()
    now = inp.now
    errors: list[str] = []
    truncated = False
    updates: dict[str, dict] = {}

    def check_deadline() -> None:
        if deadline is not None and time.monotonic() > deadline:
            raise ShadowDeadline("shadow cycle past its deadline")

    def cur(ep: dict) -> dict:
        eid = ep["ep_id"]
        if eid not in updates:
            updates[eid] = dict(ep)
        return updates[eid]

    series = [str(s).strip() for s in inp.series if str(s).strip()]
    n = max(1, len(series))
    per_series = max(0.0, float(inp.run_budget)) / n
    order_cap = min(float(inp.max_order), per_series)

    # ---- 1) markets, books and the strategy's desired quotes, per series
    books_read = n_markets = n_quotes = new_eps = ended_eps = 0
    listed: set[str] = set()                 # series whose market list is complete
    observed: set[str] = set()               # tickers whose book was read AND whose series' quotes were computed
    open_tickers: set[str] = set()
    books: dict[str, dict] = {}
    market_close: dict[str, datetime | None] = {}
    desired: dict[tuple[str, str, str], dict] = {}
    for s in series:
        check_deadline()
        try:
            mkts, trunc = md.markets(s, "open")
        except Exception as e:  # noqa: BLE001
            _err(errors, e)
            continue
        mkts = [m for m in qp.dicts(mkts) if ctsvc.valid_ticker(m.get("ticker"))]
        truncated |= bool(trunc)
        if not trunc:
            listed.add(s)
        n_markets += len(mkts)
        usable: list[dict] = []
        for m in mkts:
            t = m["ticker"]
            open_tickers.add(t)
            market_close[t] = ctsvc.from_market(m)
        for m in mkts:
            if books_read >= SHADOW_MAX_BOOKS:
                truncated = True
                break
            check_deadline()
            try:
                books[m["ticker"]] = md.orderbook(m["ticker"])
                books_read += 1
                usable.append(m)
            except Exception as e:  # noqa: BLE001
                _err(errors, e)
        if not usable:
            continue
        sbooks = {m["ticker"]: books[m["ticker"]] for m in usable}
        try:
            want = list(strategy.desired_quotes(usable, sbooks, now, order_cap, per_series) or [])
        except Exception as e:  # noqa: BLE001   (the installed strategy failed: leave this series' episodes alone)
            _err(errors, e)
            continue
        observed.update(sbooks)
        for q in want:
            f = _quote_fields(q)
            if f is None or f["ticker"] not in sbooks:
                _err(errors, "BadQuote")
                continue
            k = key_of(f["ticker"], f["book_side"], f["order_price"])
            if k is not None and k not in desired:
                f["series"] = s
                desired[k] = f
        n_quotes += len(want)

    # ---- 2) diff the desired keys against the open episodes
    by_key: dict[tuple[str, str, str], dict] = {}
    for ep in inp.open_eps:
        check_deadline()
        k = key_of(ep.get("ticker"), ep.get("book_side"), ep.get("order_price"))
        t = str(ep.get("ticker") or "")
        close = _dt(ep.get("close_time"))
        last = _dt(ep.get("last_seen_ts")) or _dt(ep.get("join_ts")) or now
        if k is None or k in by_key:                           # unreadable or a duplicate: end it
            _end(cur(ep), "stale", last); ended_eps += 1
            continue
        if (close is not None and close <= now) or (ep.get("series") in listed and t not in open_tickers):
            _end(cur(ep), "closed", min(now, close) if close else now); ended_eps += 1
            continue
        if ep.get("series") not in series or now - last > SHADOW_STALE_AFTER:
            _end(cur(ep), "stale", last); ended_eps += 1
            continue
        if t in observed:
            if k in desired:
                by_key[k] = ep
                f = desired[k]
                level = qp.level_size(books.get(t), f["side"], f["price"])
                c = cur(ep)
                c["last_seen_ts"] = now
                if level is not None:
                    lm = qp._f(c.get("level_min"))
                    c["level_min"] = level if lm is None else min(lm, level)
                    c["level_last"] = level
                    c["n_obs"] = int(c.get("n_obs") or 0) + 1
            else:
                _end(cur(ep), "cancel", now); ended_eps += 1
        # else: not observed this cycle (a failed read or truncation): untouched

    # ---- 3) new episodes (at the back of the queue: the whole level is ahead of us)
    for k, f in desired.items():
        if k in by_key:
            continue
        if inp.new_today + new_eps >= SHADOW_DAILY_NEW_CAP:
            truncated = True
            break
        level = qp.level_size(books.get(f["ticker"]), f["side"], f["price"])
        if level is None:
            _err(errors, "BookUnreadable")
            continue
        eid = ep_id_for(f["ticker"], f["book_side"], f["order_price"], now)
        updates[eid] = {
            "ep_id": eid, "env": "prod", "ticker": f["ticker"], "series": f["series"], "side": f["side"],
            "book_side": f["book_side"], "price": f["price"], "order_price": f["order_price"], "count": f["count"],
            "cost": round(f["count"] * f["price"], 4), "cell": f["cell"], "close_time": market_close.get(f["ticker"]),
            "join_ts": now, "last_seen_ts": now, "end_ts": None, "end_reason": None, "q0": level,
            "level_min": level, "level_last": level, "n_obs": 1, "trades_read": False, "traded_vol": 0.0,
            "through": False, "through_ts": None, "truncated": False, "fill": None, "result": None,
            "settled_ts": None, "pnl": None, "standin": bool(inp.standin), "ts": now}
        new_eps += 1

    # ---- 4) public trades, once per ended episode
    trade_reads = 0
    if len(inp.ended_unread) > SHADOW_MAX_TRADE_READS:
        truncated = True
    for ep in inp.ended_unread[:SHADOW_MAX_TRADE_READS]:
        check_deadline()
        join, end = _dt(ep.get("join_ts")), _dt(ep.get("end_ts"))
        side, price = ep.get("side"), qp._f(ep.get("price"))
        if join is None or end is None or side not in ("yes", "no") or price is None:
            c = cur(ep)
            c["trades_read"], c["fill"] = True, None
            continue
        try:
            trades, trunc = md.trades(ep["ticker"], int(join.timestamp()), int(end.timestamp()) + 1)
        except Exception as e:  # noqa: BLE001   (retried next cycle, up to SHADOW_MAX_TRADE_ATTEMPTS)
            _err(errors, e)
            c = cur(ep)
            c["trade_attempts"] = int(c.get("trade_attempts") or 0) + 1
            if c["trade_attempts"] >= SHADOW_MAX_TRADE_ATTEMPTS:         # SH-3: give up, never block the queue
                c.update({"trades_read": True, "fill": None, "truncated": True, "trade_failed": True})
            continue
        trade_reads += 1
        vol, through = qp.scan_trades(trades, side, price, join, end)
        c = cur(ep)
        c.update({"trades_read": True, "traded_vol": round(vol, 4), "through": bool(through),
                  "through_ts": first_through(trades, side, price, join, end) if through else None,
                  "truncated": bool(trunc),
                  "fill": sm.fill_scenarios(c.get("q0"), c.get("level_min"), vol, through, c.get("count"))})
        truncated |= bool(trunc)
        _maybe_score(c)

    # ---- 5) settlement from the market's result
    settled = voided = 0
    groups: dict[str, list[dict]] = {}
    for ep in inp.unsettled:
        if ctsvc.valid_ticker(ep.get("ticker")):
            groups.setdefault(ep["ticker"], []).append(ep)
    if len(groups) > SHADOW_MAX_SETTLE_READS:
        truncated = True
    for t in list(groups)[:SHADOW_MAX_SETTLE_READS]:
        check_deadline()
        for ep in groups[t]:                                    # SH-1: rotate the queue, even on a failed read
            cur(ep)["settle_checked_ts"] = now
        try:
            m = md.market(t)
        except Exception as e:  # noqa: BLE001
            _err(errors, e)
            continue
        res = str(m.get("result") or "").lower()
        if res not in ("yes", "no"):
            closes = [d for d in (_dt(e.get("close_time")) for e in groups[t]) if d is not None]
            final = str(m.get("status") or "").lower() in SETTLED_STATUSES
            if not (final or (closes and now - max(closes) > SHADOW_VOID_AFTER)):
                continue                                        # not resolved yet: checked again later
            res = "void"                                        # SH-1: voided / never resolved: out of the estimate
        for ep in groups[t]:
            c = cur(ep)
            close = _dt(c.get("close_time"))
            c["result"] = res
            c["settled_ts"] = _dt(m.get("settlement_ts")) or close or now
            if c.get("end_ts") is None:
                _end(c, "closed", min(now, close) if close else now); ended_eps += 1
            if res == "void":
                c["pnl"] = None
                voided += 1
            else:
                _maybe_score(c)
                settled += 1

    pass_doc = {"ts": now, "ok": not errors, "duration_s": round(time.monotonic() - t0, 3), "markets": n_markets,
                "books": books_read, "quotes": n_quotes, "new_eps": new_eps, "ended_eps": ended_eps,
                "trade_reads": trade_reads, "settled": settled, "voided": voided, "truncated": bool(truncated),
                "errors": list(dict.fromkeys(errors))}
    arb = None
    if inp.arb:                                                 # S3: last, own budgets, never raises
        arb = arb_scan(md, inp.arb, now, deadline=deadline)
        pass_doc["arb_opps"] = len(arb["opps"])
        pass_doc["arb_skipped"] = arb["skipped"]
    return CycleResult(upserts=list(updates.values()), pass_doc=pass_doc, arb=arb)


# ================================================================ S3: arbitrage shadow scan (read only)
def arb_enabled() -> bool:
    """Env SHADOW_ARB (default off), read at each cycle so it can be measured and switched without a deploy of
    code (an env change on Render restarts the service)."""
    return os.getenv(SHADOW_ARB_ENV, "false").strip().lower() == "true"


def arb_input(arb_settings: dict) -> dict:
    """The CycleInput.arb block from the effective arbitrage settings (services/arb.current)."""
    return {"series": [str(s) for s in (arb_settings.get("arb_series") or [])],
            "max_contracts": float(arb_settings["arb_max_contracts"]),
            "min_profit": float(arb_settings["arb_min_profit"]),
            "scan_cap": float(arb_settings["arb_scan_cap"])}


def arb_scan(md: Any, cfg: dict, now: datetime, *, deadline: float | None = None, scanner: Any = None) -> dict:
    """One read-only arbitrage scan over production public data. Uses only `md.markets` / `md.orderbook` (the
    GET-allowlisted, credential-less client) and the installed scanner's pure `best_opportunity`; it never calls the
    scanner's scan or order functions and writes no file. Stops at SHADOW_ARB_MAX_REQUESTS, SHADOW_ARB_BUDGET_S or
    the cycle deadline (whichever first) and says so in `skipped`. Never raises."""
    t0 = time.monotonic()
    stop_at = t0 + SHADOW_ARB_BUDGET_S
    if deadline is not None:
        # L1: a read may start only if even a worst-case slow read (timeout x attempts) ends before the cycle
        # deadline, so the scan can never push the cycle past its budget and lose that cycle's episodes.
        stop_at = min(stop_at, deadline - SHADOW_ARB_READ_RESERVE_S)
    out = {"ts": now, "ok": True, "skipped": None, "requests": 0, "events_scanned": 0, "opps": [], "errors": []}

    def budget(n: int) -> str | None:
        if time.monotonic() > stop_at:
            return "time_budget"
        if out["requests"] + n > SHADOW_ARB_MAX_REQUESTS:
            return "request_budget"
        return None

    class _Stop(Exception):
        pass

    try:
        if scanner is None:
            from . import arb as arbsvc
            scanner = arbsvc.scanner()
        best = scanner.best_opportunity
        max_c, min_p, cap = float(cfg["max_contracts"]), float(cfg["min_profit"]), float(cfg["scan_cap"])
        series = list(cfg.get("series") or [])
        if series:                                   # rotate the start so a budget-capped scan covers every series
            k = int(now.timestamp() // 60) % len(series)
            series = series[k:] + series[:k]
        for s in series:
            why = budget(1)
            if why:
                out["skipped"] = why
                break
            try:
                mkts, trunc = md.markets(s, "open")
            except Exception as e:  # noqa: BLE001
                out["requests"] += 1
                _err(out["errors"], e)
                continue
            out["requests"] += SHADOW_MAX_MARKET_PAGES if trunc else 1
            events: dict[str, list[str]] = {}
            for m in qp.dicts(mkts):
                ev, t = m.get("event_ticker"), m.get("ticker")
                if ctsvc.valid_ticker(ev) and ctsvc.valid_ticker(t):
                    events.setdefault(ev, []).append(t)
            for ev, tickers in events.items():
                if out["events_scanned"] >= SHADOW_ARB_MAX_EVENTS:
                    out["skipped"] = "event_budget"
                    break
                why = budget(len(tickers))
                if why:
                    out["skipped"] = why
                    break
                books: dict[str, dict] = {}
                try:
                    for t in tickers:
                        why = budget(1)                 # L1: checked before EVERY book read, not once per event
                        if why:
                            out["skipped"] = why
                            raise _Stop
                        out["requests"] += 1
                        books[t] = md.orderbook(t)
                except _Stop:
                    break                               # a partly read event is dropped (it needs every leg)
                except Exception as e:  # noqa: BLE001   (an event needs every leg's book)
                    _err(out["errors"], e)
                    continue
                out["events_scanned"] += 1
                op = best(ev, books, max_c)
                if op is not None and float(op.profit) >= min_p:
                    cost = float(op.cost)
                    out["opps"].append({"event": str(op.event), "kind": str(op.kind), "profit": float(op.profit),
                                        "cost": cost, "contracts": float(op.contracts),
                                        "legs": len(list(op.legs or [])), "gate_ok": cost <= cap})
            if out["skipped"]:
                break
    except Exception as e:  # noqa: BLE001   (a scanner or settings problem: record it, keep the cycle)
        _err(out["errors"], e)
        out["skipped"] = out["skipped"] or "error"
    out["ok"] = not out["errors"]
    out["duration_s"] = round(time.monotonic() - t0, 3)
    out["errors"] = list(dict.fromkeys(out["errors"]))
    return out


async def persist_arb(store: Any, scan: dict, now: datetime, timeout: float = SHADOW_STORE_TIMEOUT_S) -> None:
    """Fold one scan into the cached summary doc's `arb` block (read, merge, replace). Raises on store errors."""
    doc = await asyncio.wait_for(store.get_shadow_summary(), timeout)
    doc = dict(doc) if isinstance(doc, dict) else sm.empty_summary(enabled=True)
    doc["arb"] = sm.arb_merge(doc.get("arb"), scan, now)
    await asyncio.wait_for(store.set_shadow_summary(doc), timeout)


async def carry_arb(store: Any, summary: dict, now: datetime, timeout: float = SHADOW_STORE_TIMEOUT_S) -> dict:
    """Copy the stored `arb` block into a freshly computed summary (summarize() does not know it), so a summary
    refresh never drops the arbitrage buckets. A failed read keeps the empty block."""
    try:
        doc = await asyncio.wait_for(store.get_shadow_summary(), timeout)
    except Exception:  # noqa: BLE001
        doc = None
    summary["arb"] = sm.arb_merge((doc or {}).get("arb") if isinstance(doc, dict) else None, None, now)
    return summary


def end_all(open_eps: Iterable[dict], reason: str, now: datetime) -> list[dict]:
    """End every open episode (shadow turned off): end_ts = when it was last observed."""
    out = []
    for ep in open_eps:
        if ep.get("ep_id") and ep.get("end_ts") is None:
            d = dict(ep)
            out.append(_end(d, reason, _dt(d.get("last_seen_ts")) or _dt(d.get("join_ts")) or now))
    return out


def day_start(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
