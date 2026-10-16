"""Queue-position telemetry (read-only): where in line is each maker bid, and which fills come from the front.

No bot code is involved. Everything comes from the journal rows maker_strategy already writes plus read-only
Kalshi calls made here:

* `quote` row (written right after create_order): ticker, side bought, price, count, utc, "live order_id=..."
  -> a new EPISODE (one maker order resting at one price, keyed by order_id);
* `cancel` row (the strategy's own cancel): [ticker, book_side, order_price] -> the episode ends. If a new quote on
  the same ticker and book side at a DIFFERENT price follows within REPRICE_WINDOW, the outcome is `repriced`
  (re-quote moved the price: queue priority reset), otherwise `cancelled` (left its cell / budget);
* Stop's cancel (runner._cancel_resting records the order ids) -> `stopped`;
* gone from the resting orders and fully filled -> `filled`; gone otherwise -> `expired` (market closed, or a
  cancel the journal didn't see).

Per pass (capped, see constants) the runner reads: resting orders, recent fills, Kalshi's own queue position for
our resting orders (GET /portfolio/orders/queue_positions; optional, any failure is ignored), the order book for
tickers with new or resting episodes, and public trades since the oldest join on each ticker.

ESTIMATOR (contracts ahead of us at our price, "queue_ahead"). Cancellations by orders ahead of us cannot be seen,
so only upper bounds are observable from public data:

    join_ahead     = Kalshi queue_position at the first read (join_source "kalshi"), else
                     size at our level in the book read just after the pass - our remaining size (join_source "book";
                     biased UP, because orders that joined behind us in the meantime are counted too)
    traded         = volume of trades at exactly our price, on our side (taker_side opposite ours when present),
                     since we joined
    through        = some trade since we joined printed at a price WORSE for our side (our level was exhausted)
    nc  (no-cancel estimate) = 0 if through else max(0, join_ahead - traded)     -- upper bound #1
    hi  (upper bound)        = min(nc, max(0, level_now - our_remaining))         -- upper bound #2 is everyone else
                                                                                     at our price, ahead or behind
    lo  (lower bound)        = 0 (anyone ahead may have cancelled), or Kalshi's exact queue_position when known
    kq                       = Kalshi's queue_position at this observation (exact; null if unavailable)

At a fill: upper_at_fill = 0 if a trade-through printed before the fill, else max(0, join_ahead - traded before
the fill [our own fills' trade ids excluded]). front_fill = upper_at_fill < 1 contract ("price held, we were at the
front"). The fill's trade_through flag comes from fill_quality (services/fillquality.py).

Queue buckets (queue_ahead_at_join, contracts): "0" (< 1), "1-50", "51-200", "201-1000", ">1000".
"""
from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from . import fillquality as fq
from . import pnl as pnlsvc

DAILY_NEW_CAP = 1200            # new episodes tracked per UTC day (bounds Atlas M0 storage; see schemas)
MAX_OPEN = 300                  # open episodes loaded per run
OPEN_MAX_AGE = timedelta(days=3)
GONE_GRACE = timedelta(minutes=40)   # without a fresh fills read, wait for the 30-min fills sync before "expired"
REPRICE_WINDOW = timedelta(seconds=180)
MAX_BOOK_READS = 12             # GET /markets/{t}/orderbook per run
MAX_TRADE_TICKERS = 8           # tickers whose public trades are read per run
MAX_PAGES = 3                   # pages (1000 trades) per ticker
MAX_QPOS_CALLS = 2              # GET /portfolio/orders/queue_positions per run
QPOS_TICKERS_PER_CALL = 20
FILLS_MAX_DAYS = 3
MAX_OBS = 4                     # observations kept per episode (plus last_obs and obs_count)
MAX_FILL_IDS = 20
PRICE_TOL = 5e-5
BUCKETS = ("0", "1-50", "51-200", "201-1000", ">1000")
OUTCOMES = ("filled", "repriced", "cancelled", "stopped", "expired")
AGG_FIELDS = ("env", "series", "status", "outcome", "queue_ahead_at_join", "filled_count", "fill_ids",
              "front_fill")                     # all aggregate() reads (projection for the per-pass refresh)
_OID = re.compile(r"order_id=([A-Za-z0-9_.:\-]+)")
_BOOK_SIDE = {"bid": "bid", "yes": "bid", "ask": "ask", "no": "ask"}


# ---------------------------------------------------------------- small helpers
def _f(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    return pnlsvc._f(v)


def _ts(v: Any) -> datetime | None:
    return fq._ts(v)


def dicts(xs: Any) -> list[dict]:
    """The dict rows of a Kalshi array; anything else (None, a non-list, non-dict rows) is skipped."""
    return [x for x in xs if isinstance(x, dict)] if isinstance(xs, (list, tuple)) else []


def _body(b: Any) -> dict:
    return b if isinstance(b, dict) else {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def series_of(ticker: str, series_list: Iterable[str]) -> str:
    for s in series_list:
        if ticker.startswith(s + "-"):
            return s
    return ticker.split("-")[0]


def bucket(x: float) -> str:
    if x < 1:
        return "0"
    if x <= 50:
        return "1-50"
    if x <= 200:
        return "51-200"
    if x <= 1000:
        return "201-1000"
    return ">1000"


def _p4(x: Any) -> str | None:
    v = _f(x)
    return None if v is None else f"{v:.4f}"


# ---------------------------------------------------------------- journal -> episodes
def episode_from_quote(row: dict, env: str, series_list: Iterable[str]) -> dict | None:
    """New episode from a journal `quote` row, or None if the row can't be read (no order id etc.)."""
    m = _OID.search(str(row.get("note") or ""))
    side = row.get("side")
    price, count, ts = _f(row.get("price")), _f(row.get("count")), _ts(row.get("utc"))
    ticker = str(row.get("ticker") or "")
    if not m or side not in ("yes", "no") or price is None or count is None or ts is None or not ticker:
        return None
    return {"order_id": m.group(1), "ts": ts, "env": env, "series": series_of(ticker, series_list),
            "ticker": ticker, "side": side, "book_side": "bid" if side == "yes" else "ask", "price": price,
            "order_price": price if side == "yes" else round(1.0 - price, 4), "count": count,
            "status": "open", "outcome": None, "queue_ahead_at_join": None, "join_source": None,
            "join_level_size": None, "kalshi_queue_at_join": None, "last_obs": None, "obs": [], "obs_count": 0,
            "filled_count": 0.0, "fill_ids": [], "first_fill_utc": None, "upper_at_fill": None,
            "front_fill": None, "front_eval": False, "gone_utc": None, "closed_utc": None, "updated_utc": ts}


def cancel_key(row: dict) -> tuple[str, str, str] | None:
    """(ticker, book_side, price 4dp) from a journal `cancel` row's note (json [ticker, side, price])."""
    try:
        k = json.loads(row.get("note") or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(k, list) or len(k) != 3:
        return None
    bs, p = _BOOK_SIDE.get(str(k[1])), _p4(k[2])
    if not k[0] or bs is None or p is None:
        return None
    return str(k[0]), bs, p


def _matches(ep: dict, key: tuple[str, str, str]) -> bool:
    t, bs, p = key
    return ep.get("ticker") == t and ep.get("book_side") == bs and p in (_p4(ep.get("order_price")),
                                                                        _p4(ep.get("price")))


def close(ep: dict, outcome: str, now: datetime) -> dict:
    ep["status"], ep["outcome"], ep["closed_utc"], ep["updated_utc"] = "closed", outcome, now, now
    return ep


def apply_journal(rows: list[dict], open_eps: dict[str, dict], *, env: str, series_list: Iterable[str],
                  allowance: int, now: datetime | None = None) -> tuple[list[dict], list[dict], int]:
    """Process journal rows in order. Adds new episodes to open_eps (up to `allowance`), removes the ones a
    `cancel` row ends. Returns (new, closed, dropped). Rows of other events are ignored."""
    now = now or _now()
    series_list = list(series_list)
    new: list[dict] = []
    closed: list[dict] = []
    dropped = 0
    for i, row in enumerate(rows):
        ev = row.get("event")
        if ev == "quote":
            ep = episode_from_quote(row, env, series_list)
            if ep is None or ep["order_id"] in open_eps:
                continue
            if len(new) >= allowance:
                dropped += 1
                continue
            open_eps[ep["order_id"]] = ep
            new.append(ep)
        elif ev == "cancel":
            key = cancel_key(row)
            if key is None:
                continue
            cands = sorted((e for e in open_eps.values() if e.get("env") == env and _matches(e, key)),
                           key=lambda e: e["ts"])
            if not cands:
                continue
            ep = cands[0]
            t_cancel = _ts(row.get("utc")) or now
            repriced = False
            for later in rows[i + 1:]:
                lt = _ts(later.get("utc"))
                if lt is not None and lt - t_cancel > REPRICE_WINDOW:
                    break
                if later.get("event") == "quote" and later.get("ticker") == ep["ticker"] \
                        and later.get("side") == ep["side"] and _p4(later.get("price")) != _p4(ep["price"]):
                    repriced = True
                    break
            open_eps.pop(ep["order_id"], None)
            closed.append(close(ep, "repriced" if repriced else "cancelled", t_cancel))
    return new, closed, dropped


# ---------------------------------------------------------------- order book / trades math
def level_size(book: Any, side: str, price: float) -> float | None:
    """Resting size at `price` on the bids for `side` ("yes"/"no"); 0 if the level is empty; None if unreadable.
    Accepts orderbook_fp ({yes_dollars: [[price, qty]], ...}) or the legacy cents book ({yes: [[cents, qty]]})."""
    if not isinstance(book, dict):
        return None
    lv = book.get(f"{side}_dollars")
    scale = 1.0
    if lv is None and side in book:
        lv, scale = book.get(side), 0.01
    if lv is None:
        return 0.0
    if not isinstance(lv, (list, tuple)):
        return None
    total = 0.0
    for row in lv:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            return None                      # unreadable book: unknown, never "empty" (0 would fake a front fill)
        p, q = _f(row[0]), _f(row[1])
        if p is None or q is None:
            return None
        if abs(p * scale - price) < PRICE_TOL:
            total += q
    return total


def trade_count(t: dict) -> float:
    c = _f(t.get("count_fp"))
    if c is None:
        c = _f(t.get("count"))
    return c or 0.0


def scan_trades(trades: Iterable[dict], side: str, price: float, since: datetime, until: datetime | None = None,
                exclude: Iterable[str] = ()) -> tuple[float, bool]:
    """(volume traded at exactly our price on our side, any trade-through) for trades in [since, until].
    A trade at our price counts only if it hit the bids on our side: taker_side opposite to ours (YES bids are hit
    by takers buying NO and vice versa); trades without taker_side count. `exclude`: trade ids (our own fills)."""
    ex = set(exclude)
    vol, through = 0.0, False
    hitter = "no" if side == "yes" else "yes"
    for t in dicts(list(trades) if not isinstance(trades, (list, tuple)) else trades):
        tt, yp = _ts(t.get("created_time") or t.get("ts")), fq.trade_yes_price(t)
        if tt is None or yp is None or tt < since or (until is not None and tt > until):
            continue
        if t.get("trade_id") is not None and str(t.get("trade_id")) in ex:
            continue
        q = yp if side == "yes" else 1.0 - yp
        if q < price - PRICE_TOL:
            through = True
        elif abs(q - price) < PRICE_TOL and t.get("taker_side") in (None, "", hitter):
            vol += trade_count(t)
    return vol, through


def bounds(join_ahead: float | None, traded: float | None, through: bool, level_now: float | None,
           remaining: float | None, kq: float | None = None) -> dict:
    """{nc, hi, lo} for one observation (see the module docstring)."""
    nc = None
    if through:
        nc = 0.0
    elif join_ahead is not None and traded is not None:
        nc = max(0.0, join_ahead - traded)
    cands = [x for x in (nc,) if x is not None]
    if level_now is not None:
        cands.append(max(0.0, level_now - (remaining or 0.0)))
    hi = min(cands) if cands else None
    lo = 0.0 if hi is not None else None
    if kq is not None:
        lo = kq if hi is None else min(kq, hi)
    return {"nc": None if nc is None else round(nc, 2), "hi": None if hi is None else round(hi, 2),
            "lo": None if lo is None else round(lo, 2)}


def front_at_fill(ep: dict, trades: list[dict], own_trade_ids: Iterable[str]) -> tuple[float | None, bool | None]:
    """(upper_at_fill, front_fill) from the trades between join and the first fill."""
    join, first = _f(ep.get("queue_ahead_at_join")), _ts(ep.get("first_fill_utc"))
    joined = _ts(ep.get("ts"))
    if join is None or first is None or joined is None:
        return None, None
    vol, through = scan_trades(trades, ep["side"], float(ep["price"]), joined, first, exclude=own_trade_ids)
    upper = 0.0 if through else max(0.0, join - vol)
    return round(upper, 2), upper < 1.0


# ---------------------------------------------------------------- read-only Kalshi calls (sync; run in a thread)
def _qpos_value(row: dict) -> float | None:
    v = _f(row.get("queue_position_fp"))
    return v if v is not None else _f(row.get("queue_position"))


def collect_account(client: Any, tickers: list[str], fills_since: datetime) -> dict:
    """Resting orders, fills since `fills_since` and Kalshi queue positions. Never raises; a failed part is None
    (resting/fills) or {} (qpos). `calls` counts logical requests (client-side pagination not included)."""
    out: dict = {"resting": None, "fills": None, "qpos": {}, "calls": 0, "errors": []}
    if client is None:
        return out
    try:
        out["calls"] += 1
        out["resting"] = [o for o in dicts(client.get_orders(status="resting"))
                          if str(o.get("client_order_id", "")).startswith("mm-")]
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"orders: {type(e).__name__}")
    try:
        out["calls"] += 1
        out["fills"] = dicts(client.get_fills(min_ts=int(fills_since.timestamp())))
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"fills: {type(e).__name__}")
    req = getattr(client, "request", None)
    if callable(req) and tickers:
        for i in range(0, min(len(tickers), MAX_QPOS_CALLS * QPOS_TICKERS_PER_CALL), QPOS_TICKERS_PER_CALL):
            chunk = tickers[i:i + QPOS_TICKERS_PER_CALL]
            try:
                out["calls"] += 1
                body = req("GET", "/portfolio/orders/queue_positions",
                           params={"market_tickers": ",".join(chunk)}, auth=True)
                for r in dicts(_body(body).get("queue_positions")):
                    v = _qpos_value(r)
                    if r.get("order_id") and v is not None:
                        out["qpos"][str(r["order_id"])] = v
            except Exception as e:  # noqa: BLE001   (endpoint optional: the estimator works without it)
                out["errors"].append(f"queue_positions: {type(e).__name__}")
                break
    return out


def fetch_trades(pub: Any, ticker: str, since: datetime) -> tuple[list[dict], bool]:
    """Public trades for one ticker since `since` (bounded pagination). (trades, truncated). Raises on errors."""
    out: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        body = pub.request("GET", "/markets/trades", params={"ticker": ticker, "min_ts": int(since.timestamp()),
                                                              "limit": 1000, "cursor": cursor})
        body = _body(body)
        page = dicts(body.get("trades"))
        out.extend(page)
        cursor = body.get("cursor")
        if not cursor or not page:
            return out, False
    return out, True


def collect_public(pub: Any, book_tickers: list[str], trade_specs: list[tuple[str, datetime]]) -> dict:
    """Order books and public trades within the caps. Never raises."""
    out: dict = {"books": {}, "trades": {}, "truncated": set(), "calls": 0, "errors": []}
    if pub is None:
        return out
    for t in book_tickers[:MAX_BOOK_READS]:
        try:
            out["calls"] += 1
            ob = _body(pub.get_orderbook(t))
            book = ob.get("orderbook_fp") or ob.get("orderbook") or {}
            out["books"][t] = book if isinstance(book, dict) else {}
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"orderbook: {type(e).__name__}")
    for t, since in trade_specs[:MAX_TRADE_TICKERS]:
        try:
            trades, trunc = fetch_trades(pub, t, since)
            out["calls"] += 1
            out["trades"][t] = trades
            if trunc:
                out["truncated"].add(t)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"trades: {type(e).__name__}")
    return out


# ---------------------------------------------------------------- applying observations
def _remaining(o: dict) -> float | None:
    v = _f(o.get("remaining_count_fp"))
    return v if v is not None else _f(o.get("remaining_count"))


def apply_account(eps: list[dict], data: dict, *, now: datetime, new_ids: set[str], stop_ids: set[str],
                  last_stop_utc: datetime | None, store_fills: list[dict] | None = None) -> None:
    """Fills, Kalshi queue positions and closing from the resting-orders read. Mutates the episodes."""
    fills = data.get("fills")
    fresh_fills = fills is not None
    if fills is None:
        fills = store_fills or []
    by_order: dict[str, list[dict]] = {}
    for f in dicts(fills):
        if f.get("is_taker") or not f.get("order_id"):
            continue
        by_order.setdefault(str(f["order_id"]), []).append(f)
    resting = data.get("resting")
    rest_by_id = {str(o.get("order_id")): o for o in dicts(resting)}
    qpos = data.get("qpos") or {}
    for ep in eps:
        oid = ep["order_id"]
        fl = sorted(by_order.get(oid, []), key=lambda f: _ts(f.get("ts") or f.get("created_time"))
                    or datetime.max.replace(tzinfo=timezone.utc))
        if fl:
            ep["filled_count"] = round(sum(pnlsvc.fill_count(f) for f in fl), 2)
            ep["fill_ids"] = [str(f.get("fill_id") or f.get("trade_id")) for f in fl][:MAX_FILL_IDS]
            ep["_trade_ids"] = [str(f["trade_id"]) for f in fl if f.get("trade_id")]
            ep["first_fill_utc"] = _ts(fl[0].get("ts") or fl[0].get("created_time"))
        kq = qpos.get(oid)
        if kq is not None:
            ep["_kq"] = kq
            if oid in new_ids and ep.get("queue_ahead_at_join") is None:
                ep["queue_ahead_at_join"], ep["join_source"], ep["kalshi_queue_at_join"] = kq, "kalshi", kq
        if oid in rest_by_id:
            ep["_remaining"] = _remaining(rest_by_id[oid])
            ep["gone_utc"] = None
            continue
        if resting is None:
            if _ts(ep.get("ts")) and now - ep["ts"] > OPEN_MAX_AGE:
                close(ep, "expired", now)
            continue
        # no longer resting
        if ep.get("filled_count", 0) >= float(ep["count"]) - 0.005:
            close(ep, "filled", now)
        elif oid in stop_ids or (last_stop_utc is not None and last_stop_utc >= ep["ts"]):
            close(ep, "stopped", now)
        elif fresh_fills:
            close(ep, "expired", now)
        else:
            ep["gone_utc"] = ep.get("gone_utc") or now
            if now - ep["gone_utc"] > GONE_GRACE:
                close(ep, "expired", now)


def plan_public(eps: list[dict], new_ids: set[str]) -> tuple[list[str], list[tuple[str, datetime]]]:
    """Which tickers get a book read and a trades read this run (priority order, before the caps)."""
    def last_seen(e: dict) -> datetime:
        lo = e.get("last_obs") or {}
        return _ts(lo.get("ts")) or _ts(e.get("ts")) or datetime.min.replace(tzinfo=timezone.utc)

    resting = [e for e in eps if e.get("status") == "open"]
    books: list[str] = []
    for e in sorted((e for e in resting if e["order_id"] in new_ids), key=lambda e: e["ts"]):
        if e["ticker"] not in books:
            books.append(e["ticker"])
    for e in sorted(resting, key=last_seen):
        if e["ticker"] not in books:
            books.append(e["ticker"])
    since: dict[str, datetime] = {}
    order: list[str] = []
    need_front = [e for e in eps if e.get("first_fill_utc") and not e.get("front_eval")
                  and e.get("queue_ahead_at_join") is not None]
    for e in sorted(need_front, key=lambda e: e["ts"]) + sorted(resting, key=last_seen):
        t = e["ticker"]
        if t not in since:
            order.append(t)
            since[t] = e["ts"]
        else:
            since[t] = min(since[t], e["ts"])
    for e in eps:                                   # widen the window to the oldest episode on each chosen ticker
        if e["ticker"] in since and e.get("status") == "open":
            since[e["ticker"]] = min(since[e["ticker"]], e["ts"])
    return books, [(t, since[t]) for t in order]


def apply_public(eps: list[dict], data: dict, *, now: datetime, new_ids: set[str]) -> None:
    """Join measurement from the book, one observation per resting episode, front_fill at the first fill."""
    books, trades = data.get("books") or {}, data.get("trades") or {}
    for ep in eps:
        t, side, price = ep["ticker"], ep["side"], float(ep["price"])
        book = books.get(t)
        level = level_size(book, side, price) if book is not None else None
        rem = ep.get("_remaining")
        if rem is None:
            rem = max(0.0, float(ep["count"]) - float(ep.get("filled_count") or 0))
        if ep["order_id"] in new_ids and ep.get("queue_ahead_at_join") is None and level is not None:
            ep["queue_ahead_at_join"] = round(max(0.0, level - rem), 2)
            ep["join_source"] = "book"
        if ep["order_id"] in new_ids and level is not None:
            ep["join_level_size"] = level
        tr = trades.get(t)
        if ep.get("first_fill_utc") and not ep.get("front_eval") and tr is not None \
                and ep.get("queue_ahead_at_join") is not None:
            ep["upper_at_fill"], ep["front_fill"] = front_at_fill(ep, tr, ep.get("_trade_ids") or [])
            ep["front_eval"] = True
        if ep.get("status") != "open":
            continue
        traded, through = (None, False)
        if tr is not None:
            traded, through = scan_trades(tr, side, price, ep["ts"])
        kq = ep.get("_kq")
        if level is None and tr is None and kq is None:
            continue
        b = bounds(_f(ep.get("queue_ahead_at_join")), traded, through, level, rem, kq)
        obs = {"ts": now, "level": level, "traded": None if traded is None else round(traded, 2),
               "through": through, **b, "kq": kq, "rem": rem}
        ep["last_obs"] = obs
        ep["obs"] = (list(ep.get("obs") or []) + [obs])[-MAX_OBS:]
        ep["obs_count"] = int(ep.get("obs_count") or 0) + 1
        ep["updated_utc"] = now


def strip_private(ep: dict) -> dict:
    return {k: v for k, v in ep.items() if not k.startswith("_")}


# ---------------------------------------------------------------- aggregate (the agreed queue_stats shape)
def aggregate(eps: Iterable[dict], fills_by_id: dict[str, dict], *, env: str | None = None,
              now: datetime | None = None) -> dict:
    """queue_stats = {episodes, fills, median_queue_ahead_at_join, requote_reset_share, front_fill_share,
    by_queue: [{bucket, episodes, fills, fill_rate, trade_through_share}], updated_utc}."""
    rows = [e for e in eps if env is None or e.get("env") == env]
    filled = [e for e in rows if (_f(e.get("filled_count")) or 0) > 0]
    closed = [e for e in rows if e.get("status") == "closed" and e.get("outcome")]
    joins = [float(e["queue_ahead_at_join"]) for e in rows if _f(e.get("queue_ahead_at_join")) is not None]
    fronts = [bool(e["front_fill"]) for e in filled if e.get("front_fill") is not None]

    def tt(e: dict) -> bool | None:
        ids = e.get("fill_ids") or []
        fqd = (fills_by_id.get(str(ids[0])) or {}).get(fq.FIELD) if ids else None
        return fqd.get("trade_through") if isinstance(fqd, dict) and "trade_through" in fqd else None

    by_queue = []
    for b in BUCKETS:
        es = [e for e in rows if _f(e.get("queue_ahead_at_join")) is not None
              and bucket(float(e["queue_ahead_at_join"])) == b]
        fs = [e for e in es if (_f(e.get("filled_count")) or 0) > 0]
        tts = [x for x in (tt(e) for e in fs) if x is not None]
        by_queue.append({"bucket": b, "episodes": len(es), "fills": len(fs),
                         "fill_rate": round(len(fs) / len(es), 4) if es else None,
                         "trade_through_share": round(sum(tts) / len(tts), 4) if tts else None})
    return {"episodes": len(rows), "fills": len(filled),
            "median_queue_ahead_at_join": round(statistics.median(joins), 2) if joins else None,
            "requote_reset_share": (round(sum(1 for e in closed if e["outcome"] == "repriced") / len(closed), 4)
                                    if closed else None),
            "front_fill_share": round(sum(fronts) / len(fronts), 4) if fronts else None,
            "by_queue": by_queue,
            "updated_utc": (now or _now()).isoformat()}
