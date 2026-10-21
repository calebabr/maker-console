"""Account risk gate for arbitrage executions (pure functions; no I/O, no Kalshi, no store).

Definitions shared with the maker side:

* ``ceiling = max_open_frac * (cash + open_exposure)``: the existing open-exposure ceiling.
* a series (city) may hold at most ``series_cap_frac`` of the ceiling in open exposure; a position belongs to a
  series when its ticker starts with ``<SERIES>-``.
* total open exposure (maker and arbitrage together) plus the new set's cost may not exceed the ceiling.

Arbitrage adds two spend caps: the spend of one scan and the spend of one UTC day.

Every check fails closed: a missing, negative or non-finite input refuses the set.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

DEFAULT_CAPS = {"arb_scan_cap": 15.0, "arb_day_cap": 45.0, "arb_series_cap_frac": 0.35}


def series_of(ticker: Any) -> str:
    """Series ticker of a market or event ticker: everything before the first '-' ("" if not a string)."""
    if not isinstance(ticker, str) or not ticker:
        return ""
    return ticker.split("-", 1)[0]


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def exposure_by_series(positions: Iterable[Mapping[str, Any]] | None) -> dict[str, float]:
    """{series: sum |market_exposure_dollars|} over Kalshi market positions. Every position is counted (under its
    own series), so the values sum to the account's total open exposure. Raises ValueError on a malformed list,
    so a caller can fail closed."""
    if positions is None:
        raise ValueError("positions unreadable")
    out: dict[str, float] = {}
    for p in positions:
        if not isinstance(p, Mapping):
            raise ValueError("positions unreadable")
        x = _num(p.get("market_exposure_dollars") or 0)
        if x is None:
            raise ValueError("positions unreadable")
        if x == 0:
            continue
        s = series_of(p.get("ticker") or p.get("market_ticker"))
        out[s] = round(out.get(s, 0.0) + abs(x), 6)
    return out


def ceiling(cash: float, total_exposure: float, max_open_frac: float) -> float:
    return max(0.0, max_open_frac * (cash + total_exposure))


def check_arb(op_cost: Any, op_series: Any, positions_exposure_by_series: Mapping[str, Any] | None, cash: Any,
              max_open_frac: Any, scan_spent: Any, day_spent: Any,
              caps: Mapping[str, Any] | None) -> tuple[bool, str]:
    """(ok, reason). reason is "ok" when allowed, else a short plain-English why (no secrets, no keys)."""
    caps = {**DEFAULT_CAPS, **(caps or {})}
    cost, c, frac = _num(op_cost), _num(cash), _num(max_open_frac)
    scan, day = _num(scan_spent), _num(day_spent)
    scan_cap, day_cap = _num(caps.get("arb_scan_cap")), _num(caps.get("arb_day_cap"))
    ser_frac = _num(caps.get("arb_series_cap_frac"))
    if None in (cost, c, frac, scan, day, scan_cap, day_cap, ser_frac) or positions_exposure_by_series is None:
        return False, "bad input"
    if cost <= 0 or c < 0 or scan < 0 or day < 0 or not 0 < frac <= 1 or not 0 < ser_frac <= 1:
        return False, "bad input"
    by_series: dict[str, float] = {}
    for k, v in positions_exposure_by_series.items():
        x = _num(v)
        if x is None or x < 0:
            return False, "bad input"
        by_series[str(k)] = x
    series = op_series if isinstance(op_series, str) and op_series else ""
    if not series:
        return False, "bad input"
    total = sum(by_series.values())
    ceil = ceiling(c, total, frac)
    if cost > c + 1e-9:
        return False, f"cash {cost:.2f} > {c:.2f}"
    if total + cost > ceil + 1e-9:
        return False, f"total exposure {total:.2f} + {cost:.2f} > ceiling {ceil:.2f}"
    s_exp = by_series.get(series, 0.0)
    s_cap = ser_frac * ceil
    if s_exp + cost > s_cap + 1e-9:
        return False, f"series cap {series} {s_exp:.2f} + {cost:.2f} > {s_cap:.2f}"
    if scan + cost > scan_cap + 1e-9:
        return False, f"scan cap {scan:.2f} + {cost:.2f} > {scan_cap:.2f}"
    if day + cost > day_cap + 1e-9:
        return False, f"day cap {day:.2f} + {cost:.2f} > {day_cap:.2f}"
    return True, "ok"
