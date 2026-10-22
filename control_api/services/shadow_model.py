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
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

SCENARIOS = ("lo", "mid", "hi")
FEE_MODES = ("zero", "maker")
MAKER_FEE_RATE = 0.0175

MIN_SETTLED_DAYS = 7
MIN_CENTRAL_FILLS = 30
DEFAULT_SEED = 7
DEFAULT_DRAWS = 2000
# S2 (additive): per-day view and its reliability note. Same wording as the app's too-early state.
MIN_FILLED_EPISODES = 30
TOO_EARLY_TEXT = ("Too early to estimate. The range needs more days and settled episodes before the figures "
                  "mean much.")
# S3 (additive): arbitrage shadow scan, daily buckets kept inside the cached summary doc
ARB_RETENTION_DAYS = 30
ARB_MAX_EVENTS_PER_DAY = 300

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
        # additive (Sprint 2A-2 S2/S3): existing fields above are unchanged
        "by_event_day": [],
        "day_stats": _empty_day_stats(),
        "reliability_note": reliability_note(0, 0),
        "arb": empty_arb(),
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


# --------------------------------------------------------------------------
# S2: per-event-day returns (additive; the headline above is unchanged)
# --------------------------------------------------------------------------
def reliability_note(days_with_fills: int, filled_episodes: int) -> str | None:
    """Plain note while the per-day figures are too thin to read much into; None once both minimums are met."""
    if days_with_fills >= MIN_SETTLED_DAYS and filled_episodes >= MIN_FILLED_EPISODES:
        return None
    return (f"{TOO_EARLY_TEXT} So far: {days_with_fills} of {MIN_SETTLED_DAYS} days with a fill, "
            f"{filled_episodes} of {MIN_FILLED_EPISODES} filled episodes.")


def _empty_day_stats() -> dict[str, Any]:
    return {"event_days": 0, "days_with_fills": 0, "filled_episodes": 0, "mean_return_mid": None,
            "sd_return_mid": None, "worst_day": None, "best_day": None, "best_day_pnl_share": None,
            "top3_pnl_share": None}


def _ret3(pnl: Mapping[str, float], cost: Mapping[str, float]) -> dict[str, float | None]:
    """Return per scenario on that scenario's own cost; lo/hi are the min/max like the headline. None: no cost."""
    vals = {sc: (pnl[sc] / cost[sc] if cost[sc] > 0 else None) for sc in SCENARIOS}
    if vals["mid"] is None:
        return {"lo": None, "mid": None, "hi": None}
    have = [v for v in vals.values() if v is not None]
    return {"lo": _r(min(have), 6), "mid": _r(vals["mid"], 6), "hi": _r(max(have), 6)}


def by_event_day(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per settle day (UTC date of `settled_ts`, else `close_time`; the same key as `daily`), oldest first:
    episodes (scored), filled_episodes (central fill > 0), cost / pnl (maker fee) / return per scenario."""
    days: dict[str, dict[str, Any]] = {}
    for s in scored:
        b = days.setdefault(s["day"], {"episodes": 0, "filled": 0, "cost": {sc: 0.0 for sc in SCENARIOS},
                                       "pnl": {sc: 0.0 for sc in SCENARIOS}})
        b["episodes"] += 1
        b["filled"] += int(s["fill"]["mid"] > 0)
        for sc in SCENARIOS:
            b["cost"][sc] += s["fill"][sc] * s["price"]
            b["pnl"][sc] += s["pnl"]["maker"][sc]
    out = []
    for d in sorted(days):
        b = days[d]
        out.append({"day": d, "episodes": b["episodes"], "filled_episodes": b["filled"],
                    "cost": {sc: _r(b["cost"][sc]) for sc in SCENARIOS},
                    "pnl": _range3(b["pnl"]), "return": _ret3(b["pnl"], b["cost"])})
    return out


def day_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Spread and concentration of the per-day view. Mean / sample sd of the central daily return over days with a
    fill; worst / best day by central return; P&L shares from the single best day and the best three days (by
    central P&L), None unless the total central P&L is positive."""
    out = _empty_day_stats()
    out["event_days"] = len(rows)
    out["filled_episodes"] = sum(r["filled_episodes"] for r in rows)
    withret = [r for r in rows if r["return"]["mid"] is not None]
    out["days_with_fills"] = sum(1 for r in rows if r["filled_episodes"] > 0)
    if withret:
        rets = [r["return"]["mid"] for r in withret]
        out["mean_return_mid"] = _r(sum(rets) / len(rets), 6)
        if len(rets) >= 2:
            mu = sum(rets) / len(rets)
            out["sd_return_mid"] = _r(math.sqrt(sum((x - mu) ** 2 for x in rets) / (len(rets) - 1)), 6)
        lo = min(withret, key=lambda r: (r["return"]["mid"], r["day"]))
        hi = max(withret, key=lambda r: (r["return"]["mid"], r["day"]))
        out["worst_day"] = {"day": lo["day"], "return_mid": lo["return"]["mid"], "pnl_mid": lo["pnl"]["mid"]}
        out["best_day"] = {"day": hi["day"], "return_mid": hi["return"]["mid"], "pnl_mid": hi["pnl"]["mid"]}
    total = sum(r["pnl"]["mid"] for r in rows)
    if total > 0:
        ranked = sorted((r["pnl"]["mid"] for r in rows), reverse=True)
        out["best_day_pnl_share"] = _r(ranked[0] / total, 4)
        out["top3_pnl_share"] = _r(sum(ranked[:3]) / total, 4)
    return out


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
    rows = by_event_day(scored)
    out["by_event_day"] = rows
    out["day_stats"] = day_stats(rows)
    out["reliability_note"] = reliability_note(out["day_stats"]["days_with_fills"],
                                               out["day_stats"]["filled_episodes"])
    return out


# --------------------------------------------------------------------------
# S3: arbitrage shadow scan (daily buckets stored inside the cached summary doc)
# --------------------------------------------------------------------------
def empty_arb(enabled: bool = False) -> dict[str, Any]:
    """The stored block ({days, last_scan}) and the served view share this shape plus `enabled` / `rolling`."""
    return {"enabled": bool(enabled), "days": [],
            "rolling": {k: {**_arb_stats([], None, None), "clipped_to_period": False} for k in ("7d", "30d")},
            "last_scan": None}


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def arb_merge(block: Mapping[str, Any] | None, scan: Mapping[str, Any] | None, now: datetime) -> dict[str, Any]:
    """Fold one scan result into the stored block. Pure. Returns {days, last_scan}: one bucket per UTC day (last
    ARB_RETENTION_DAYS kept), each with scans / skipped / requests counters and its distinct events
    ({event, kind, first_seen, last_seen, seen, seen_yes, seen_no, profit, cost, contracts, legs, gate_ok}, the
    profit / cost / kind of the most profitable sighting). At most ARB_MAX_EVENTS_PER_DAY events per day."""
    now_dt = _as_dt(now) or datetime.now(timezone.utc)
    days = [dict(d, events=[dict(e) for e in (d.get("events") or []) if isinstance(e, dict)])
            for d in ((block or {}).get("days") or []) if isinstance(d, dict) and d.get("day")]
    last = (block or {}).get("last_scan")
    if scan:
        ts = _as_dt(scan.get("ts")) or now_dt
        key = ts.date().isoformat()
        day = next((d for d in days if d["day"] == key), None)
        if day is None:
            day = {"day": key, "scans": 0, "skipped": 0, "requests": 0, "dropped": 0, "events": []}
            days.append(day)
        day["scans"] = int(day.get("scans") or 0) + 1
        day["skipped"] = int(day.get("skipped") or 0) + int(bool(scan.get("skipped")))
        day["requests"] = int(day.get("requests") or 0) + int(_num(scan.get("requests")))
        by_event = {e.get("event"): e for e in day["events"]}
        iso_ts = ts.isoformat()
        for op in scan.get("opps") or []:
            ev = str(op.get("event") or "")
            if not ev:
                continue
            kind = "YES" if str(op.get("kind")).upper() == "YES" else "NO"
            e = by_event.get(ev)
            if e is None:
                if len(day["events"]) >= ARB_MAX_EVENTS_PER_DAY:
                    day["dropped"] = int(day.get("dropped") or 0) + 1
                    continue
                e = {"event": ev, "first_seen": iso_ts, "seen": 0, "seen_yes": 0, "seen_no": 0, "profit": None}
                day["events"].append(e)
                by_event[ev] = e
            e["last_seen"] = iso_ts
            e["seen"] = int(e.get("seen") or 0) + 1
            e["seen_yes" if kind == "YES" else "seen_no"] = int(e.get("seen_yes" if kind == "YES" else "seen_no")
                                                                or 0) + 1
            profit = _num(op.get("profit"))
            if e.get("profit") is None or profit > _num(e.get("profit")):
                e.update({"kind": kind, "profit": _r(profit), "cost": _r(_num(op.get("cost"))),
                          "contracts": _r(_num(op.get("contracts")), 2), "legs": int(_num(op.get("legs"))),
                          "gate_ok": bool(op.get("gate_ok"))})
        last = {k: scan.get(k) for k in ("ok", "skipped", "requests", "duration_s", "events_scanned")}
        last.update(ts=iso_ts, opportunities=len(scan.get("opps") or []))
    cutoff = (now_dt.date().toordinal() - ARB_RETENTION_DAYS + 1)
    days = sorted((d for d in days if _day_ord(d["day"]) >= cutoff), key=lambda d: d["day"])
    return {"days": days, "last_scan": last}


def _day_ord(day: str) -> int:
    try:
        return datetime.fromisoformat(day).date().toordinal()
    except (TypeError, ValueError):
        return 0


def _in(ts: Any, start: datetime | None, end: datetime | None) -> bool:
    t = _as_dt(ts)
    return t is not None and (start is None or t >= start) and (end is None or t < end)


def _arb_stats(events: list[Mapping[str, Any]], start: datetime | None, end: datetime | None) -> dict[str, Any]:
    durs = []
    for e in events:
        a, b = _as_dt(e.get("first_seen")), _as_dt(e.get("last_seen"))
        if a and b and int(_num(e.get("seen"))) >= 2:
            durs.append((b - a).total_seconds())
    n = len(events)
    return {"from": _iso(start), "to": _iso(end), "opportunities": n,
            "yes": sum(1 for e in events if e.get("kind") == "YES"),
            "no": sum(1 for e in events if e.get("kind") == "NO"),
            "profit": _r(sum(_num(e.get("profit")) for e in events)),
            "median_cost": None if not n else _r(_median([_num(e.get("cost")) for e in events])),
            "gate_pass_share": None if not n else _r(sum(1 for e in events if e.get("gate_ok")) / n, 4),
            "median_duration_s": None if not durs else _r(_median(durs), 1)}


def _merge_events(evs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Distinct events across day buckets (an event can span two UTC days): earliest first_seen, latest last_seen,
    summed sightings, the most profitable sighting's figures."""
    out: dict[str, dict[str, Any]] = {}
    for e in evs:
        k = str(e.get("event"))
        cur = out.get(k)
        if cur is None:
            out[k] = dict(e)
            continue
        cur["seen"] = int(_num(cur.get("seen"))) + int(_num(e.get("seen")))
        if (_as_dt(e.get("first_seen")) or _tmin()) < (_as_dt(cur.get("first_seen")) or _tmin()):
            cur["first_seen"] = e.get("first_seen")
        if (_as_dt(e.get("last_seen")) or _tmin()) > (_as_dt(cur.get("last_seen")) or _tmin()):
            cur["last_seen"] = e.get("last_seen")
        if _num(e.get("profit")) > _num(cur.get("profit")):
            for f in ("kind", "profit", "cost", "contracts", "legs", "gate_ok"):
                cur[f] = e.get(f)
    return list(out.values())


def _tmin() -> datetime:
    return datetime.min.replace(tzinfo=timezone.utc)


def arb_view(block: Mapping[str, Any] | None, now: datetime, *, enabled: bool,
             start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    """The served arbitrage section, clipped to [start, end) (a period; None = open). Events count when their
    last sighting falls inside the window. Per UTC day: opportunities (distinct events), yes / no, estimated profit
    after fees (sum of each event's best sighting), median set cost, gate_pass_share (share of events whose best
    set cost was within the arbitrage per-scan cap), median first-to-last-seen seconds (events seen twice or more),
    scans and skipped. `rolling` 7d / 30d: the same over distinct events, each window clipped to the period."""
    now_dt = _as_dt(now) or datetime.now(timezone.utc)
    stored = arb_merge(block, None, now_dt)
    out = empty_arb(enabled)
    out["last_scan"] = stored.get("last_scan")
    kept: list[dict[str, Any]] = []
    for d in stored["days"]:
        evs = [e for e in d["events"] if _in(e.get("last_seen"), start, end)]
        day_start = datetime.fromisoformat(d["day"]).replace(tzinfo=timezone.utc)
        if (end is not None and day_start >= end) or (start is not None and day_start + _ONE_DAY <= start):
            continue
        row = {"day": d["day"], **_arb_stats(evs, None, None), "scans": int(_num(d.get("scans"))),
               "skipped": int(_num(d.get("skipped"))), "dropped": int(_num(d.get("dropped")))}
        row.pop("from"), row.pop("to")
        out["days"].append(row)
        kept.extend(evs)
    top = now_dt if end is None else min(now_dt, end)
    for label, n in (("7d", 7), ("30d", 30)):
        lo = top - _days(n)
        clipped = start is not None and start > lo
        lo = max(lo, start) if start is not None else lo
        stats = _arb_stats([e for e in _merge_events(kept) if _in(e.get("last_seen"), lo, top + _EPS)], lo, top)
        stats["clipped_to_period"] = bool(clipped)
        out["rolling"][label] = stats
    return out


def _days(n: int) -> timedelta:
    return timedelta(days=n)


_ONE_DAY = timedelta(days=1)
_EPS = timedelta(seconds=1)          # an event seen exactly at `top` still counts


__all__ = [
    "CAVEATS", "fill_scenarios", "maker_fee", "episode_pnl", "score_episode",
    "summarize", "empty_summary", "by_event_day", "day_stats", "reliability_note",
    "empty_arb", "arb_merge", "arb_view",
]
