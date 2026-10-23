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
    "Cash limits use an assumed capital (the shadow capital setting), not the real "
    "account: bids the real limits could not have funded are recorded as capped and "
    "left out, but the real balance and its timing would differ.",
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
def fill_scenarios(q0, level_min, traded_vol, through, count, through_vol=None) -> dict[str, float]:
    """Estimated filled contracts (lo / mid / hi), each capped to ``count``.

    ``q0`` is the queue ahead of us when we joined, ``level_min`` the smallest
    total size seen at our price level since (the queue can only shrink through
    cancels or trades), ``traded_vol`` the volume traded at our price while the
    episode was open, ``through`` whether the market traded through our price.

    A trade-through (2A-3 F4): ``hi`` is the full count (the level emptied, so a
    resting bid there would have been hit first); ``lo`` assumes nobody ahead of
    us cancelled, so only the volume beyond the queue ahead reached us:
    ``max(0, traded_vol + through_vol - q0)``, capped to the count; ``mid`` is
    halfway. ``through_vol`` None (episodes recorded before 2A-3) keeps the old
    full-count fill in all three scenarios.
    """
    count_f = max(0.0, _num(count))
    if count_f <= 0:
        return {"lo": 0.0, "mid": 0.0, "hi": 0.0}
    if through:
        full = count_f
        if through_vol is None:
            return {"lo": full, "mid": full, "hi": full}
        lo = _clamp(max(0.0, _num(traded_vol)) + max(0.0, _num(through_vol)) - max(0.0, _num(q0)), 0.0, full)
        return {"lo": lo, "mid": (lo + full) / 2.0, "hi": full}
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


def ep_fee_mode(ep: Mapping[str, Any]) -> str:
    """The fee mode an episode is scored with (F7): its recorded `fee_mode`, else the maker fee (conservative)."""
    m = ep.get("fee_mode")
    return m if m in FEE_MODES else "maker"


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
    """Maximum simultaneous cost of resting bids ("peak resting bids"; capped episodes were never placed)."""
    events: list[tuple[datetime, int, float]] = []
    for ep in episodes:
        if ep.get("capped") is True:
            continue
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


def _peak_held(scored: list[dict[str, Any]], now: datetime) -> float:
    """F5: maximum simultaneous cost of FILLED positions (central fill x price), each held from its fill time
    (`through_ts` for a trade-through, else `end_ts`) to settlement (`settled_ts`, else `close_time`)."""
    events: list[tuple[datetime, int, float]] = []
    for s in scored:
        amt = s["fill"]["mid"] * s["price"]
        if amt <= 0:
            continue
        ep = s["ep"]
        start = ((_as_dt(ep.get("through_ts")) if ep.get("through") else None) or _as_dt(ep.get("end_ts"))
                 or _as_dt(ep.get("join_ts")))
        if start is None:
            continue
        end = _as_dt(ep.get("settled_ts")) or _as_dt(ep.get("close_time")) or now
        if end < start:
            end = start
        events.append((start, 1, amt))
        events.append((end, 0, -amt))
    events.sort(key=lambda e: (e[0], e[1]))
    cur = peak = 0.0
    for _, _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def _first_fill_per_market(scored: list[dict[str, Any]], mode_key: str) -> dict[str, Any]:
    """F1 (additive): only the FIRST filled episode (earliest join) of each (ticker, side), central scenario.
    `mode_key` "pnl_s" (the scheduled fee, the headline) or "pnl_z" (zero fee)."""
    first: dict[tuple[str, str], dict[str, Any]] = {}
    for s in scored:
        if s["fill"]["mid"] <= 0:
            continue
        k = (str(s["ep"].get("ticker") or ""), str(s["ep"].get("side") or ""))
        j = _as_dt(s["ep"].get("join_ts")) or _tmin()
        cur = first.get(k)
        if cur is None or j < cur[0]:
            first[k] = (j, s)
    cost = sum(s["fill"]["mid"] * s["price"] for _, s in first.values())
    pnl = sum(s[mode_key]["mid"] for _, s in first.values())
    return {"cost": _r(cost), "pnl": _r(pnl), "roi": _r(pnl / cost, 6) if cost > 0 else None,
            "markets": len(first)}


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
        "pnl": {"lo": 0.0, "mid": 0.0, "hi": 0.0, "ci90": None},
        "roi": {"lo": None, "mid": None, "hi": None, "ci90": None},   # V3: no cost, no ROI
        "fill_rate": {"lo": 0.0, "mid": 0.0, "hi": 0.0},
        "peak_capital": 0.0,
        # Sprint 2A-3 (additive)
        "ci_reliable": False,                    # F3: the ci90 fields are null until the too-early gate passes
        "headline_status": "too_early",          # F2: "too_early" | "ok"
        "break_even_win_rate": None,             # F2: cost-weighted average price paid (central fills)
        "observed_win_rate": None,               # F2: share of central filled contracts that won
        "n_settled_fills": 0,                    # F2: settled episodes with a central fill > 0
        "peak_held_capital": 0.0,                # F5: peak cost of filled positions held to settlement
        "return_on_held": None,                  # F5: central P&L / peak_held_capital
        "first_fill_per_market": {"cost": 0.0, "pnl": 0.0, "roi": None, "markets": 0},   # F1 (additive)
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
            # Sprint 2A-3 (additive)
            "headline_status": "too_early",     # F2
            "capped_episodes": 0,               # F1: episodes the caps refused (never scored)
            "settled_days": 0,                  # F6: close days with a scored episode (filled or not)
        },
        "headline": _empty_headline(),
        "headline_zero_fee": _empty_headline(),
        "by_series": [],
        "by_cell": [],
        "daily": [],
        "fee_mode": "maker",
        "caveats": list(CAVEATS),
        "day_basis": "close_day",               # F6: a "day" is the UTC close (settle) day, not the event date
        "fee_modes": {m: 0 for m in FEE_MODES},  # F7: scored episodes per fee mode
        # hotfix (legacy episodes): the headline basis, and the legacy (pre-cap) episodes kept apart
        "methodology": 2,
        "legacy": None,
        "legacy_banner": None,
        # Sprint 2A-3 wave 2 (interval variants; empty while env SHADOW_VARIANTS is off)
        "variants_enabled": False,
        "variants": [],
        "variant_compare": [],
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
        "pnl": _range3(_sum3([_sched(r) for r in rows])),
    }


def _sched(s: Mapping[str, Any]) -> Mapping[str, float]:
    """The P&L row the headline uses: the episode's own fee mode (F7; maker when none is recorded)."""
    return s.get("pnl_s") or s["pnl"]["maker"]


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
    episodes (scored), filled_episodes (central fill > 0), cost / pnl (each episode's fee mode) /
    return per scenario."""
    days: dict[str, dict[str, Any]] = {}
    for s in scored:
        b = days.setdefault(s["day"], {"episodes": 0, "filled": 0, "cost": {sc: 0.0 for sc in SCENARIOS},
                                       "pnl": {sc: 0.0 for sc in SCENARIOS}})
        b["episodes"] += 1
        b["filled"] += int(s["fill"]["mid"] > 0)
        for sc in SCENARIOS:
            b["cost"][sc] += s["fill"][sc] * s["price"]
            b["pnl"][sc] += _sched(s)[sc]
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

    Only settled, uncapped episodes (with a result and a fill estimate) are
    scored; uncapped episodes count toward peak resting bids (`peak_capital`).
    Fee-adjusted figures (each episode's recorded `fee_mode`, maker when none)
    are the headline; zero-fee figures sit under ``headline_zero_fee``.
    ``passes`` (optional, any order) feeds the pass health fields.

    Sprint 2A-3: capped episodes are counted (`status.capped_episodes`) and
    never scored; `days_covered` and the too-early gate count only close days
    with a filled episode; the 90% intervals are null (`ci_reliable` false)
    while too early and resample only filled days.
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
    capped = 0
    for ep in all_eps:
        if ep.get("capped") is True:                  # F1: refused by a cap, never placed, never scored
            capped += 1
            continue
        if ep.get("result") not in ("yes", "no") or not ep.get("fill"):
            continue
        day = _settle_day(ep)
        if day is None:
            continue
        pnl = score_episode(ep)
        mode = ep_fee_mode(ep)
        scored.append({
            "ep": ep,
            "day": day,
            "fill": {sc: _num(ep["fill"].get(sc)) for sc in SCENARIOS},
            "pnl": pnl,
            "pnl_s": pnl[mode],                       # F7: the episode's own fee mode (the headline)
            "pnl_z": pnl["zero"],
            "mode": mode,
            "price": _num(ep.get("price")),
            "win": ep.get("result") == ep.get("side"),
        })
    out["status"]["capped_episodes"] = capped

    out["headline"]["peak_capital"] = _r(_peak_capital(all_eps, now_dt), 2)
    out["headline_zero_fee"]["peak_capital"] = out["headline"]["peak_capital"]
    if not scored:
        return out

    # per-day buckets (settlement / close day, UTC)
    by_day: dict[str, dict[str, Any]] = {}
    for s in scored:
        b = by_day.setdefault(s["day"], {
            "sched": {sc: 0.0 for sc in SCENARIOS},
            "zero": {sc: 0.0 for sc in SCENARIOS},
            "cost_mid": 0.0,
            "filled": 0,
        })
        for sc in SCENARIOS:
            b["sched"][sc] += s["pnl_s"][sc]
            b["zero"][sc] += s["pnl_z"][sc]
        b["cost_mid"] += s["fill"]["mid"] * s["price"]
        b["filled"] += int(s["fill"]["mid"] > 0)
    day_keys = sorted(by_day)
    filled_days = [k for k in day_keys if by_day[k]["filled"] > 0]       # F6: only days with a filled episode

    total_count = sum(_num(s["ep"].get("count")) for s in scored)
    fills = {sc: sum(s["fill"][sc] for s in scored) for sc in SCENARIOS}
    cost = {sc: sum(s["fill"][sc] * s["price"] for s in scored) for sc in SCENARIOS}

    pnl_sched = _sum3([s["pnl_s"] for s in scored])
    pnl_zero = _sum3([s["pnl_z"] for s in scored])

    central_fills = fills["mid"]
    filled = [s for s in scored if s["fill"]["mid"] > 0]
    # V2: one gate everywhere (the reliability note too): 7 close days with a fill and 30 FILLED EPISODES
    too_early = not (len(filled_days) >= MIN_SETTLED_DAYS and len(filled) >= MIN_FILLED_EPISODES)
    ci = None
    if not too_early:                                 # F3: no interval while too early; filled days only
        ci = _bootstrap(
            [{"pnl_maker": by_day[k]["sched"]["mid"], "pnl_zero": by_day[k]["zero"]["mid"],
              "cost": by_day[k]["cost_mid"]} for k in filled_days],
            seed, draws,
        )

    # F2: break-even vs observed win rate, over central filled contracts
    n_contracts = sum(s["fill"]["mid"] for s in filled)
    break_even = _r(cost["mid"] / n_contracts, 6) if n_contracts > 0 else None
    observed = _r(sum(s["fill"]["mid"] for s in filled if s["win"]) / n_contracts, 6) if n_contracts > 0 else None
    peak_held = _r(_peak_held(scored, now_dt), 2)
    status = "too_early" if too_early else "ok"

    fill_rate = {sc: (fills[sc] / total_count if total_count > 0 else 0.0) for sc in SCENARIOS}
    for name, pnl, ci_p, ci_r, key in (
            ("headline", pnl_sched, ci and ci["pnl_maker"], ci and ci["roi_maker"], "pnl_s"),
            ("headline_zero_fee", pnl_zero, ci and ci["pnl_zero"], ci and ci["roi_zero"], "pnl_z")):
        roi = _ret3(pnl, cost)                        # V3: a scenario with no cost has no ROI (null), not 0 %
        out[name] = {
            "pnl": {**_range3(pnl), "ci90": None if ci_p is None else [_r(ci_p[0]), _r(ci_p[1])]},
            "roi": {**roi, "ci90": None if ci_r is None else [_r(ci_r[0], 6), _r(ci_r[1], 6)]},
            "fill_rate": {sc: _r(fill_rate[sc], 6) for sc in SCENARIOS},
            "peak_capital": out["headline"]["peak_capital"],
            "ci_reliable": ci is not None,
            "headline_status": status,
            "break_even_win_rate": break_even,
            "observed_win_rate": observed,
            "n_settled_fills": len(filled),
            "peak_held_capital": peak_held,
            "return_on_held": _r(pnl["mid"] / peak_held, 6) if peak_held > 0 else None,
            "first_fill_per_market": _first_fill_per_market(scored, key),
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
            cum[sc] += by_day[k]["sched"][sc]
        band = _range3(cum)
        daily.append({"day": k, "cum_lo": band["lo"], "cum_mid": band["mid"], "cum_hi": band["hi"]})
    out["daily"] = daily

    modes = {m: sum(1 for s in scored if s["mode"] == m) for m in FEE_MODES}
    out["fee_modes"] = modes
    out["fee_mode"] = "maker" if modes["zero"] == 0 else "per_series"
    out["status"].update({
        "days_covered": len(filled_days),
        "settled_days": len(day_keys),
        "settled_episodes": len(scored),
        "central_fills": _r(central_fills),
        "too_early": too_early,
        "headline_status": status,
    })
    rows = by_event_day(scored)
    out["by_event_day"] = rows
    out["day_stats"] = day_stats(rows)
    out["reliability_note"] = reliability_note(out["day_stats"]["days_with_fills"],
                                               out["day_stats"]["filled_episodes"])
    return out


# --------------------------------------------------------------------------
# hotfix: legacy (methodology 1) episodes, stored before the 2A-3 caps
# --------------------------------------------------------------------------
LEGACY_BANNER = "legacy, uncapped, not comparable"
NO_CORRECTED_DATA = "no_corrected_data"
_LEGACY_HEADLINE_KEYS = ("pnl", "roi", "fill_rate", "peak_capital", "peak_held_capital", "first_fill_per_market",
                         "break_even_win_rate", "observed_win_rate", "n_settled_fills")


def legacy_block(episodes, now) -> dict[str, Any]:
    """The legacy episodes' own figures (no interval: they were never capped, so a range of their fills is not a
    comparable estimate). Same scoring as the headline, reported apart with the banner."""
    s = summarize(list(episodes or []), now, draws=0)
    out: dict[str, Any] = {"banner": LEGACY_BANNER, "episodes": len(list(episodes or []))}
    for name in ("headline", "headline_zero_fee"):
        h = {k: s[name][k] for k in _LEGACY_HEADLINE_KEYS}
        h["pnl"] = {k: h["pnl"][k] for k in SCENARIOS}
        h["roi"] = {k: h["roi"][k] for k in SCENARIOS}
        out[name] = h
    out["status"] = {k: s["status"][k] for k in ("settled_episodes", "settled_days", "days_covered",
                                                 "central_fills", "capped_episodes")}
    out["by_series"] = s["by_series"]
    out["daily"] = s["daily"]
    first = [d for d in (_as_dt(e.get("join_ts")) for e in episodes or []) if d is not None]
    out["from"] = _iso(min(first)) if first else None
    out["to"] = _iso(max(first)) if first else None
    return out


def mark_no_corrected_data(out: dict[str, Any]) -> None:
    """Only legacy episodes in the window: the headline has no corrected data, so its numbers are null."""
    for name in ("headline", "headline_zero_fee"):
        h = out[name]
        for k in ("pnl", "roi"):
            h[k] = {"lo": None, "mid": None, "hi": None, "ci90": None}
        h["fill_rate"] = {"lo": None, "mid": None, "hi": None}
        for k in ("peak_capital", "peak_held_capital", "return_on_held", "break_even_win_rate",
                  "observed_win_rate"):
            h[k] = None
        h["first_fill_per_market"] = {"cost": None, "pnl": None, "roi": None, "markets": 0}
        h["headline_status"] = NO_CORRECTED_DATA
        h["ci_reliable"] = False
    out["status"]["headline_status"] = NO_CORRECTED_DATA
    out["status"]["too_early"] = True


# --------------------------------------------------------------------------
# 2A-3 wave 2: interval variants (each cadence's own episodes; paired comparison with the baseline)
# --------------------------------------------------------------------------
BASELINE_INTERVAL = 10
COMPARE_TOO_EARLY_TEXT = ("Too early to compare cadences: both need {d} close days with a fill and {f} filled "
                          "episodes before the difference means much.")
COMPARE_NOTE = ("Paired by close day over {n} days with a fill in either cadence. Both cadences quote the same "
                "markets on the same days, so their results move together and the difference is noisy; read the "
                "interval, not the point. Each cadence has its own capped position, so a faster one is not simply "
                "more of the same bids.")


def _day_pnl(eps: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Per close day: central P&L at each episode's fee mode and central fill cost, the same episodes summarize()
    scores (uncapped, yes/no result, a fill estimate, a day)."""
    days: dict[str, dict[str, float]] = {}
    for ep in eps:
        if ep.get("capped") is True or ep.get("result") not in ("yes", "no") or not ep.get("fill"):
            continue
        day = _settle_day(ep)
        if day is None:
            continue
        f = _num(ep["fill"].get("mid"))
        d = days.setdefault(day, {"pnl": 0.0, "cost": 0.0, "filled": 0.0})
        d["pnl"] += score_episode(ep)[ep_fee_mode(ep)]["mid"]
        d["cost"] += f * _num(ep.get("price"))
        d["filled"] += 1.0 if f > 0 else 0.0
    return days


def _paired_ci(diffs: list[float], seed: int, draws: int) -> list[float] | None:
    """Day-clustered bootstrap of the summed paired difference: resample days with replacement."""
    n = len(diffs)
    if n == 0 or draws <= 0:
        return None
    rng = random.Random(seed)
    sums = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) for _ in range(draws))
    return [_r(_percentile(sums, 0.05)), _r(_percentile(sums, 0.95))]


def _variant_row(iv: int, s: Mapping[str, Any]) -> dict[str, Any]:
    h, st = s["headline"], s["status"]
    return {"interval": iv, "baseline": iv == BASELINE_INTERVAL,
            "pnl": {k: h["pnl"][k] for k in SCENARIOS}, "roi": {k: h["roi"][k] for k in SCENARIOS},
            "fill_rate": dict(h["fill_rate"]), "central_fills": st["central_fills"],
            "settled_episodes": st["settled_episodes"], "n_settled_fills": h["n_settled_fills"],
            "capped_episodes": st["capped_episodes"], "days_covered": st["days_covered"],
            "too_early": st["too_early"], "headline_status": st["headline_status"],
            "peak_held_capital": h["peak_held_capital"], "last_pass_ts": st["last_pass_ts"],
            "pass_ok_pct": st["pass_ok_pct"]}


def variant_blocks(episodes, now, intervals, *, passes=None, interval_of=None, seed: int = DEFAULT_SEED,
                   draws: int = DEFAULT_DRAWS) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(variants, variant_compare) for the baseline (10) and each variant cadence in `intervals`.

    `variants`: per cadence, its own headline figures (maker/scheduled fee), fills and fill rate, from the episodes
    tagged with that interval. `variant_compare`: per variant, the paired difference vs the baseline by close day
    (variant minus baseline central P&L, summed), a day-clustered bootstrap 90% interval of that sum (null while
    either side is too early), the ROI and fill-rate differences and a reliability note."""
    iv_of = interval_of or (lambda d: int((d or {}).get("interval") or BASELINE_INTERVAL))
    ivs = sorted({BASELINE_INTERVAL, *[int(i) for i in (intervals or [])]})
    groups: dict[int, list] = {i: [] for i in ivs}
    for ep in episodes or []:
        g = groups.get(iv_of(ep))
        if g is not None:
            g.append(ep)
    pgroups: dict[int, list] = {i: [] for i in ivs}
    for p in passes or []:
        g = pgroups.get(iv_of(p))
        if g is not None:
            g.append(p)
    sums = {i: summarize(groups[i], now, passes=pgroups[i], draws=0) for i in ivs}
    variants = [_variant_row(i, sums[i]) for i in ivs]
    base_days = _day_pnl(groups[BASELINE_INTERVAL])
    base = sums[BASELINE_INTERVAL]
    compare = []
    for i in ivs:
        if i == BASELINE_INTERVAL:
            continue
        var_days = _day_pnl(groups[i])
        days = sorted(d for d in set(base_days) | set(var_days)
                      if (base_days.get(d, {}).get("filled", 0) > 0 or var_days.get(d, {}).get("filled", 0) > 0))
        diffs = [var_days.get(d, {}).get("pnl", 0.0) - base_days.get(d, {}).get("pnl", 0.0) for d in days]
        too_early = bool(base["status"]["too_early"] or sums[i]["status"]["too_early"])
        ci = None if too_early else _paired_ci(diffs, seed, draws)
        hv, hb = sums[i]["headline"], base["headline"]
        compare.append({
            "interval": i, "vs": BASELINE_INTERVAL, "days": len(days),
            "pnl_diff": {"mid": _r(sum(diffs)), "ci90": ci},
            "roi_diff_mid": (None if hv["roi"]["mid"] is None or hb["roi"]["mid"] is None
                             else _r(hv["roi"]["mid"] - hb["roi"]["mid"], 6)),
            "fill_rate_diff_mid": _r(hv["fill_rate"]["mid"] - hb["fill_rate"]["mid"], 6),
            "too_early": too_early, "ci_reliable": ci is not None,
            "reliability_note": (COMPARE_TOO_EARLY_TEXT.format(d=MIN_SETTLED_DAYS, f=MIN_FILLED_EPISODES) if too_early
                                 else COMPARE_NOTE.format(n=len(days))),
        })
    return variants, compare


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
    "empty_arb", "arb_merge", "arb_view", "variant_blocks", "legacy_block", "mark_no_corrected_data",
]
