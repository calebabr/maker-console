"""Realised P&L from Kalshi fills + settlements (read-only; mirrors maker_monitor.fill_rows' per-fill math).

A fill's P&L is booked when its market settles: count x (win - price) - fee, where win is 1 if the market's
result equals the side bought. Windows (d7 / d30) are by settlement time. Maker fills (is_taker false) are the
maker strategy; taker fills are the arbitrage legs (and any manual taker trades, which can't be told apart).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fill_side(f: dict) -> str | None:
    s = f.get("outcome_side") or f.get("side")
    return s if s in ("yes", "no") else None


def fill_price(f: dict, side: str) -> float | None:
    p = _f(f.get(f"{side}_price_dollars"))
    if p is None:
        cents = _f(f.get(f"{side}_price"))
        p = cents / 100.0 if cents is not None else None
    return p


def fill_count(f: dict) -> float:
    c = _f(f.get("count_fp"))
    if c is None:
        c = _f(f.get("count"))
    return c or 0.0


def fill_pnl(f: dict, result: str) -> float | None:
    side = fill_side(f)
    if side is None or result not in ("yes", "no"):
        return None
    price = fill_price(f, side)
    if price is None:
        return None
    fee = _f(f.get("fee_cost")) or 0.0
    win = 1.0 if result == side else 0.0
    return fill_count(f) * (win - price) - fee


def windows(fills: Iterable[dict], settlements: Iterable[dict], now: datetime, *, taker: bool = False,
            env: str | None = None) -> dict | None:
    """{d7, d30, settled_fills} or None if no settled fill of that kind in the last 30 days."""
    res = {}
    for s in settlements:
        t, r, ts = s.get("ticker"), s.get("market_result") or s.get("result"), s.get("ts")
        if t and r in ("yes", "no") and isinstance(ts, datetime):
            res[t] = (r, ts)
    from .strategies import fill_label
    d7 = d30 = 0.0
    n = 0
    lo7, lo30 = now - timedelta(days=7), now - timedelta(days=30)
    want = "arb" if taker else "maker"
    for f in fills:
        src, strat = fill_label(f)                 # manual trades ("you") are never a bot statistic (Y1)
        if src != "bot" or strat != want:
            continue
        if env and f.get("env") and f.get("env") != env:
            continue
        hit = res.get(f.get("ticker") or f.get("market_ticker"))
        if not hit or hit[1] < lo30:
            continue
        p = fill_pnl(f, hit[0])
        if p is None:
            continue
        n += 1
        d30 += p
        if hit[1] >= lo7:
            d7 += p
    return {"d7": round(d7, 2), "d30": round(d30, 2), "settled_fills": n} if n else None


def realised_by_source(fills: Iterable[dict], settlements: Iterable[dict], *, since: datetime,
                       until: datetime | None = None, env: str | None = None) -> dict:
    """Realised P&L of fills whose market settled in [since, until), split by strategy and 'you':
    {maker: n|None, arb: n|None, you: n|None, total: n|None, bot: n|None, settled_fills: int}. A bucket with no
    settled fill is None; total = sum of the non-None buckets (None if all are None); bot = maker + arb + ..."""
    from .strategies import bucket_ids, fill_label
    res = {}
    for s in settlements:
        t, r, ts = s.get("ticker"), s.get("market_result") or s.get("result"), s.get("ts")
        if t and r in ("yes", "no") and isinstance(ts, datetime) and ts >= since and (until is None or ts < until):
            res[t] = r
    sums: dict[str, float] = {}
    n = 0
    for f in fills:
        if env and f.get("env") and f.get("env") != env:
            continue
        r = res.get(f.get("ticker") or f.get("market_ticker"))
        if r is None:
            continue
        p = fill_pnl(f, r)
        if p is None:
            continue
        src, strat = fill_label(f)
        key = "you" if src == "you" else (strat or "maker")
        sums[key] = sums.get(key, 0.0) + p
        n += 1
    out: dict = {k: (round(sums[k], 2) if k in sums else None) for k in bucket_ids()}
    for k in sums:                                  # a strategy id no longer in the registry still counts
        out.setdefault(k, round(sums[k], 2))
    bot = [v for k, v in sums.items() if k != "you"]
    out["bot"] = round(sum(bot), 2) if bot else None
    out["total"] = round(sum(sums.values()), 2) if sums else None
    out["settled_fills"] = n
    return out


FILL_FIELDS = ("fill_id", "ts", "ticker", "market_ticker", "side", "outcome_side", "yes_price_dollars",
               "no_price_dollars", "yes_price", "no_price", "count_fp", "count", "fee_cost", "is_taker", "env",
               "order_id", "source", "strategy", "action")


FILL_LOOKBACK = timedelta(days=35)


async def by_source_since(store, since: datetime, *, env: str | None = None, until: datetime | None = None
                          ) -> tuple[dict, list[dict], list[dict]]:
    """(realised_by_source, settlements, fills) for markets settled since `since`. Bounded, projected reads.
    Raises on store errors (callers wrap it)."""
    settlements = await store.list_settlements(since=since, limit=20000,
                                               fields=("ticker", "market_result", "result", "ts"))
    if not settlements:
        return realised_by_source([], [], since=since, until=until, env=env), [], []
    fills = await store.list_fills(since=since - FILL_LOOKBACK, limit=50000, fields=FILL_FIELDS)
    return realised_by_source(fills, settlements, since=since, until=until, env=env), settlements, fills


async def compute(store, now: datetime, *, taker: bool = False, env: str | None = None) -> dict | None:
    """P&L windows from the store. Raises on store errors (callers wrap it)."""
    settlements = await store.list_settlements(since=now - timedelta(days=30), limit=100000)
    if not settlements:
        return None
    fills = await store.list_fills(since=now - timedelta(days=60), limit=100000)
    return windows(fills, settlements, now, taker=taker, env=env)
