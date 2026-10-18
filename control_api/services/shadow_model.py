"""Pure estimate model for shadow mode.

No I/O, no clock reads, no global state: every function is deterministic for
its inputs. An "episode" is one hypothetical resting bid. This module turns an
episode's observed queue facts into pessimistic / central / optimistic fill
counts, scores them against the settlement result, and aggregates settled
episodes into the summary served by ``GET /api/shadow/summary``.

Everything here is an ESTIMATE of what a passive bid might have done. The
caveats list says what it cannot capture.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SCENARIOS = ("lo", "mid", "hi")
FEE_MODES = ("zero", "maker")
MAKER_FEE_RATE = 0.0175

MIN_SETTLED_DAYS = 7
MIN_CENTRAL_FILLS = 30
DEFAULT_SEED = 7
DEFAULT_DRAWS = 2000

CAVEATS = [
    "Our own orders would change the market: a real resting bid adds to the queue "
    "and can move other traders' behaviour, which this estimate ignores.",
    "The queue can change between checks in ways we cannot see (orders placed and "
    "cancelled between two looks), so queue position is approximate.",
    "No cash limit is applied in this version: every hypothetical bid is assumed "
    "affordable, so real results may differ when capital is short.",
    "Outages, rejected orders and missed passes are not modelled; a real bot would "
    "have gaps this estimate does not.",
    "Small sample: with few settled days the interval is wide, and a handful of "
    "outcomes can dominate the totals.",
]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _num(x: Any, default: float = 0.0) -> float:
    if x is None:
        return default
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if math.isnan(v):
        return default
    return v


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _as_dt(x: Any) -> datetime | None:
    """Datetimes are expected tz-aware UTC; ISO strings are tolerated."""
    if x is None:
        return None
    if isinstance(x, str):
        try:
            x = datetime.fromisoformat(x.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(x, datetime):
        return None
    if x.tzinfo is None:
        x = x.replace(tzinfo=timezone.utc)
    return x.astimezone(timezone.utc)


def _iso(x: Any) -> str | None:
    d = _as_dt(x)
    return d.isoformat() if d else None


def _r(x: float, nd: int = 4) -> float:
    v = round(float(x), nd)
    return 0.0 if v == 0 else v  # no "-0.0"


# --------------------------------------------------------------------------
# fills, fees, pnl
# --------------------------------------------------------------------------
def fill_scenarios(q0, level_min, traded_vol, through, count) -> dict[str, float]:
    """Estimated filled contracts (lo / mid / hi), each capped to ``count``.

    ``q0`` is the queue ahead of us when we joined, ``level_min`` the smallest
    total size seen at our price level since (the queue can only shrink through
    cancels or trades), ``traded_vol`` the volume traded at our price while the
    episode was open, ``through`` whether the market traded through our price
    (which fills a resting bid in full).
    """
    count_f = max(0.0, _num(count))
    if count_f <= 0:
        return {"lo": 0.0, "mid": 0.0, "hi": 0.0}
    if through:
        full = count_f
        return {"lo": full, "mid": full, "hi": full}
    q = max(0.0, _num(q0))
    lvl = q if level_min is None else max(0.0, _num(level_min))
    vol = max(0.0, _num(traded_vol))
    a_min = min(q, lvl)
    a_mid = (q + a_min) / 2.0
    return {
        "lo": _clamp(vol - q, 0.0, count_f),
        "mid": _clamp(vol - a_mid, 0.0, count_f),
        "hi": _clamp(vol - a_min, 0.0, count_f),
    }


def maker_fee(count, price, mode) -> float:
    """Fee in dollars for ``count`` contracts at ``price`` (dollars, 0..1)."""
    if mode == "zero":
        return 0.0
    if mode != "maker":
        raise ValueError(f"unknown fee mode: {mode!r}")
    c = max(0.0, _num(count))
    p = _num(price)
    raw = MAKER_FEE_RATE * c * p * (1.0 - p) * 100.0
    # round off float noise so an exact cent boundary does not tip up a cent
    return math.ceil(round(raw, 9)) / 100.0


def episode_pnl(fill, price, win, fee) -> float:
    """P&L in dollars: each filled contract pays 1 if it wins, costs ``price``."""
    return _num(fill) * ((1.0 if win else 0.0) - _num(price)) - _num(fee)


def score_episode(ep: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """P&L per fee mode and queue scenario for one settled episode."""
    result = ep.get("result")
    if result not in ("yes", "no"):
        raise ValueError("episode has no settlement result")
    fill = ep.get("fill")
    if not fill:
        raise ValueError("episode has no fill estimate")
    win = result == ep.get("side")
    price = _num(ep.get("price"))
    out: dict[str, dict[str, float]] = {}
    for mode in FEE_MODES:
        out[mode] = {}
        for sc in SCENARIOS:
            f = _num(fill.get(sc))
            fee = maker_fee(f, price, mode) if f > 0 else 0.0
            out[mode][sc] = episode_pnl(f, price, win, fee)
    return out


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------
def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _settle_day(ep: Mapping[str, Any]) -> str | None:
    d = _as_dt(ep.get("settled_ts")) or _as_dt(ep.get("close_time"))
    return d.date().isoformat() if d else None


def _peak_capital(episodes: Iterable[Mapping[str, Any]], now: datetime) -> float:
    """Maximum simultaneous cost of open episodes (no cash limit applied)."""
    events: list[tuple[datetime, int, float]] = []
    for ep in episodes:
        start = _as_dt(ep.get("join_ts"))
        if start is None:
            continue
        cost = _num(ep.get("cost"), _num(ep.get("count")) * _num(ep.get("price")))
        end = _as_dt(ep.get("end_ts")) or _as_dt(ep.get("close_time")) or now
        if end < start:
            end = start
        events.append((start, 1, cost))   # 1 = open
        events.append((end, 0, -cost))    # 0 = close, sorted first at equal times
    events.sort(key=lambda e: (e[0], e[1]))
    cur = peak = 0.0
    for _, _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def _range3(d: Mapping[str, float], nd: int = 4) -> dict[str, float]:
    """lo / mid / hi for a dollar figure.

    ``mid`` is the central queue scenario. A bid that settles as a loss loses
    MORE when it fills more, so the pessimistic-queue scenario is not always the
    lowest P&L. ``lo`` and ``hi`` are therefore the lowest and highest of the
    three scenarios, which keeps ``lo <= mid <= hi`` for charting.
    """
    vals = [d[sc] for sc in SCENARIOS]
    return {"lo": _r(min(vals), nd), "mid": _r(d["mid"], nd), "hi": _r(max(vals), nd)}


def _empty_headline() -> dict[str, Any]:
    return {
        "pnl": {"lo": 0.0, "mid": 0.0, "hi": 0.0, "ci90": [0.0, 0.0]},
        "roi": {"lo": 0.0, "mid": 0.0, "hi": 0.0, "ci90": [0.0, 0.0]},
        "fill_rate": {"lo": 0.0, "mid": 0.0, "hi": 0.0},
        "peak_capital": 0.0,
    }


def empty_summary(enabled: bool = False, standin: bool = False) -> dict[str, Any]:
    """The summary shape with zeroed numbers (shadow off, stand-in, no data)."""
    return {
        "enabled": bool(enabled),
        "standin": bool(standin),
        "status": {
            "days_covered": 0,
            "settled_episodes": 0,
            "central_fills": 0.0,
            "too_early": True,
            "last_pass_ts": None,
            "pass_ok_pct": None,
            "truncated": False,
        },
        "headline": _empty_headline(),
        "headline_zero_fee": _empty_headline(),
        "by_series": [],
        "by_cell": [],
        "daily": [],
        "fee_mode": "maker",
        "caveats": list(CAVEATS),
    }


def _bootstrap(days: list[dict[str, float]], seed: int, draws: int) -> dict[str, list[float]]:
    """Day-clustered bootstrap of the central scenario.

    Resamples whole settlement days (with replacement) so fills that settle the
    same day, which share weather or a game slate, stay together. Returns 90%
    intervals for maker pnl, zero-fee pnl, and the two ROIs.
    """
    keys = ("pnl_maker", "pnl_zero", "roi_maker", "roi_zero")
    n = len(days)
    if n == 0 or draws <= 0:
        return {k: [0.0, 0.0] for k in keys}
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {k: [] for k in keys}
    for _ in range(draws):
        pm = pz = cost = 0.0
        for _i in range(n):
            d = days[rng.randrange(n)]
            pm += d["pnl_maker"]
            pz += d["pnl_zero"]
            cost += d["cost"]
        samples["pnl_maker"].append(pm)
        samples["pnl_zero"].append(pz)
        samples["roi_maker"].append(pm / cost if cost > 0 else 0.0)
        samples["roi_zero"].append(pz / cost if cost > 0 else 0.0)
    out = {}
    for k, vals in samples.items():
        vals.sort()
        out[k] = [_percentile(vals, 0.05), _percentile(vals, 0.95)]
    return out


def _sum3(rows: list[Mapping[str, float]]) -> dict[str, float]:
    return {sc: sum(r[sc] for r in rows) for sc in SCENARIOS}


def _group_rows(scored: list[dict[str, Any]], keyfn) -> dict[Any, list[dict[str, Any]]]:
    groups: dict[Any, list[dict[str, Any]]] = {}
    for s in scored:
        groups.setdefault(keyfn(s["ep"]), []).append(s)
    return groups


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "episodes": len(rows),
        "fills_mid": _r(sum(r["fill"]["mid"] for r in rows)),
        "pnl": _range3(_sum3([r["pnl"]["maker"] for r in rows])),
    }


def summarize(episodes, now, *, seed: int = DEFAULT_SEED, draws: int = DEFAULT_DRAWS,
              passes=None, enabled: bool = True, standin: bool = False) -> dict[str, Any]:
    """Aggregate episodes into the ``GET /api/shadow/summary`` shape.

    Only settled episodes (with a result and a fill estimate) are scored;
    all episodes count toward peak capital. Fee-adjusted ("maker") figures are
    the headline; zero-fee figures sit under ``headline_zero_fee``. ``passes``
    (optional, newest first or any order) feeds the pass health fields.
    """
    now_dt = _as_dt(now) or datetime.now(timezone.utc)
    all_eps = list(episodes or [])
    out = empty_summary(enabled=enabled, standin=standin)

    # pass health
    plist = [p for p in (passes or []) if _as_dt(p.get("ts"))]
    if plist:
        plist.sort(key=lambda p: _as_dt(p["ts"]))
        out["status"]["last_pass_ts"] = _iso(plist[-1]["ts"])
        out["status"]["pass_ok_pct"] = _r(100.0 * sum(1 for p in plist if p.get("ok")) / len(plist), 1)
        out["status"]["truncated"] = any(bool(p.get("truncated")) for p in plist)

    scored: list[dict[str, Any]] = []
    for ep in all_eps:
        if ep.get("result") not in ("yes", "no") or not ep.get("fill"):
            continue
        day = _settle_day(ep)
        if day is None:
            continue
        scored.append({
            "ep": ep,
            "day": day,
            "fill": {sc: _num(ep["fill"].get(sc)) for sc in SCENARIOS},
            "pnl": score_episode(ep),
            "price": _num(ep.get("price")),
        })

    out["headline"]["peak_capital"] = _r(_peak_capital(all_eps, now_dt), 2)
    out["headline_zero_fee"]["peak_capital"] = out["headline"]["peak_capital"]
    if not scored:
        return out

    # per-day buckets (settlement day, UTC)
    by_day: dict[str, dict[str, Any]] = {}
    for s in scored:
        b = by_day.setdefault(s["day"], {
            "maker": {sc: 0.0 for sc in SCENARIOS},
            "zero": {sc: 0.0 for sc in SCENARIOS},
            "cost_mid": 0.0,
        })
        for sc in SCENARIOS:
            b["maker"][sc] += s["pnl"]["maker"][sc]
            b["zero"][sc] += s["pnl"]["zero"][sc]
        b["cost_mid"] += s["fill"]["mid"] * s["price"]
    day_keys = sorted(by_day)

    total_count = sum(_num(s["ep"].get("count")) for s in scored)
    fills = {sc: sum(s["fill"][sc] for s in scored) for sc in SCENARIOS}
    cost = {sc: sum(s["fill"][sc] * s["price"] for s in scored) for sc in SCENARIOS}

    def _roi(pnl: Mapping[str, float]) -> dict[str, float]:
        return {sc: (pnl[sc] / cost[sc] if cost[sc] > 0 else 0.0) for sc in SCENARIOS}

    pnl_maker = _sum3([s["pnl"]["maker"] for s in scored])
    pnl_zero = _sum3([s["pnl"]["zero"] for s in scored])

    ci = _bootstrap(
        [{"pnl_maker": by_day[k]["maker"]["mid"], "pnl_zero": by_day[k]["zero"]["mid"],
          "cost": by_day[k]["cost_mid"]} for k in day_keys],
        seed, draws,
    )

    fill_rate = {sc: (fills[sc] / total_count if total_count > 0 else 0.0) for sc in SCENARIOS}
    for name, pnl, ci_p, ci_r in (("headline", pnl_maker, ci["pnl_maker"], ci["roi_maker"]),
                                  ("headline_zero_fee", pnl_zero, ci["pnl_zero"], ci["roi_zero"])):
        roi = _roi(pnl)
        out[name] = {
            "pnl": {**_range3(pnl), "ci90": [_r(ci_p[0]), _r(ci_p[1])]},
            "roi": {**_range3(roi, 6), "ci90": [_r(ci_r[0], 6), _r(ci_r[1], 6)]},
            "fill_rate": {sc: _r(fill_rate[sc], 6) for sc in SCENARIOS},
            "peak_capital": out["headline"]["peak_capital"],
        }

    out["by_series"] = [
        {"series": k, **_group_summary(rows)}
        for k, rows in sorted(_group_rows(scored, lambda e: e.get("series") or "").items())
    ]
    cell_groups = _group_rows(scored, lambda e: tuple(e.get("cell") or ()))
    out["by_cell"] = [
        {"cell": list(k), **_group_summary(rows)}
        for k, rows in sorted(cell_groups.items(), key=lambda kv: [str(v) for v in kv[0]])
    ]

    cum = {sc: 0.0 for sc in SCENARIOS}
    daily = []
    for k in day_keys:
        for sc in SCENARIOS:
            cum[sc] += by_day[k]["maker"][sc]
        band = _range3(cum)
        daily.append({"day": k, "cum_lo": band["lo"], "cum_mid": band["mid"], "cum_hi": band["hi"]})
    out["daily"] = daily

    central_fills = fills["mid"]
    out["status"].update({
        "days_covered": len(day_keys),
        "settled_episodes": len(scored),
        "central_fills": _r(central_fills),
        "too_early": not (len(day_keys) >= MIN_SETTLED_DAYS and central_fills >= MIN_CENTRAL_FILLS),
    })
    return out


__all__ = [
    "CAVEATS", "fill_scenarios", "maker_fee", "episode_pnl", "score_episode",
    "summarize", "empty_summary",
]
