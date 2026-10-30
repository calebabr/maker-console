"""Trading periods and cash-flow events (Sprint 2A-1 section 1).

A period is a named stretch of time the owner marks (for example "Demo Period 2"). Period 1 is IMPLICIT: id "p1",
"Demo Period 1", from the beginning of data to the first marker. Each stored marker starts the next period, which
ends where the following marker starts (the last one is "current"). Markers are append-only.

Attribution is by FILL date: a position filled in one period counts toward that period for ever, even if it settles
in a later one. Open positions at a marker stay with the period of their fill. Deposits and withdrawals are shown
beside the figures and are NEVER netted into P&L.

Pure functions over plain store docs plus a few small async helpers that read the store. Services raise
ValueError; the routes turn it into HTTP errors.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from . import pnl as pnlsvc
from .strategies import bucket_ids, fill_label

P1_ID = "p1"
P1_NAME = "Demo Period 1"
NAME_MAX = 60
NOTE_MAX = 200
AMOUNT_MAX = 100_000.0
MIN_MARKER_GAP = timedelta(minutes=5)          # typo guard between two markers
FUTURE_SLACK = timedelta(minutes=5)
EARLIEST_FLOW_TS = datetime(2020, 1, 1, tzinfo=timezone.utc)
RETENTION = timedelta(days=180)                # how far back fills/settlements are kept
FILL_LIMIT = 50_000
SETTLEMENT_LIMIT = 20_000
SOURCES = ("bot", "you", "all")
FLOW_KINDS = ("deposit", "withdrawal")
EQUITY_CHANGE_LABEL = ("Account equity change excluding deposits and withdrawals. It counts every settlement that "
                       "landed in the period, including positions filled in an EARLIER period, plus unrealised "
                       "moves, so it can differ from the period's P&L. The by-fill-date `pnl` is the period's P&L.")
ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def utc(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)
    if isinstance(v, str):
        try:
            return utc(datetime.fromisoformat(v.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def iso(v: Any) -> str | None:
    d = utc(v)
    return d.isoformat() if d else None


# ---------------------------------------------------------------- input cleaning
def clean_text(raw: Any, field: str, *, maxlen: int, required: bool = False) -> str | None:
    """One line of plain text: control / bidi characters removed, whitespace collapsed."""
    if raw is None:
        if required:
            raise ValueError(f"{field}: required")
        return None
    if not isinstance(raw, str):
        raise ValueError(f"{field}: must be a string")
    s = " ".join(_CTRL_RE.sub(" ", raw).split())
    if required and not s:
        raise ValueError(f"{field}: required")
    if len(s) > maxlen:
        raise ValueError(f"{field}: at most {maxlen} characters")
    return s or None


def validate_marker(body: Any, markers: list[dict], now: datetime) -> dict:
    """{name, note, started_at} for a new marker. ValueError(list[str]) on bad input; ValueError("too_soon") if the
    last marker is younger than MIN_MARKER_GAP."""
    if not isinstance(body, dict) or set(body) - {"name", "note"}:
        raise ValueError(["body: must be {\"name\": \"...\", \"note\": \"...\"}"])
    errs: list[str] = []
    out: dict = {}
    for key, maxlen, req in (("name", NAME_MAX, True), ("note", NOTE_MAX, False)):
        try:
            out[key] = clean_text(body.get(key), key, maxlen=maxlen, required=req)
        except ValueError as e:
            errs.append(str(e))
    if errs:
        raise ValueError(errs)
    last = max((utc(m.get("started_at")) for m in markers if utc(m.get("started_at"))), default=None)
    if last is not None and now - last < MIN_MARKER_GAP:
        raise ValueError("too_soon")
    out["started_at"] = now
    return out


def validate_cash_flow(body: Any, now: datetime) -> dict:
    """{kind, amount, ts, note, confirm_duplicate}. The date (`ts`) is REQUIRED: a deposit or withdrawal has to carry
    the day it really happened, because a default of "now" put two earlier deposits into the wrong period (found
    2026-10-23). A future date is refused. ValueError(list[str]) on bad input."""
    allowed = {"kind", "amount", "ts", "note", "confirm_duplicate"}
    if not isinstance(body, dict) or set(body) - allowed:
        raise ValueError(["body: must be {\"kind\", \"amount\", \"ts\", \"note\"?, \"confirm_duplicate\"?}"])
    errs: list[str] = []
    out: dict = {}
    kind = body.get("kind")
    if kind not in FLOW_KINDS:
        errs.append(f"kind: must be one of {list(FLOW_KINDS)}")
    out["kind"] = kind
    amt = body.get("amount")
    if isinstance(amt, bool) or not isinstance(amt, (int, float)) or amt != amt or not 0 < amt <= AMOUNT_MAX:
        errs.append(f"amount: must be a number above 0 and at most {AMOUNT_MAX:g}")
    else:
        out["amount"] = round(float(amt), 2)
        if out["amount"] <= 0:
            errs.append("amount: must be at least 0.01")
    ts = None
    if body.get("ts") is None or body.get("ts") == "":
        errs.append("ts: required (the date the money moved, e.g. 2026-10-17)")
    else:
        ts = utc(body["ts"]) if isinstance(body["ts"], str) else None
        if ts is None:
            errs.append("ts: must be an ISO date or date-time")
        elif ts > now + FUTURE_SLACK:
            errs.append("ts: cannot be in the future")
        elif ts < EARLIEST_FLOW_TS:
            errs.append("ts: too far in the past")
    out["ts"] = ts
    cd = body.get("confirm_duplicate", False)
    if not isinstance(cd, bool):
        errs.append("confirm_duplicate: must be true or false")
    out["confirm_duplicate"] = cd is True
    try:
        out["note"] = clean_text(body.get("note"), "note", maxlen=NOTE_MAX)
    except ValueError as e:
        errs.append(str(e))
    if errs:
        raise ValueError(errs)
    return out


def find_duplicate(doc: dict, flows: Iterable[dict]) -> dict | None:
    """An existing flow of the same kind, the same amount (to the cent) and the same UTC calendar date as `doc`."""
    ts = utc(doc.get("ts"))
    for f in flows:
        t = utc(f.get("ts"))
        if (t is not None and ts is not None and f.get("kind") == doc.get("kind") and t.date() == ts.date()
                and round(float(f.get("amount") or 0), 2) == doc.get("amount")):
            return f
    return None


def flow_warnings(doc: dict, current_start: datetime | None, snapshots: Iterable[dict]) -> list[dict]:
    """Non-blocking notes shown after a cash flow is saved: [{code, message}].
    * date_in_current_period: the date falls inside the current period, so returns are no longer like-for-like;
    * no_balance_jump: account snapshots exist on both sides of the date but cash moved by less than half the amount
      in the matching direction (the money may not have arrived, or the date is wrong)."""
    out: list[dict] = []
    ts = utc(doc.get("ts"))
    if ts is None:
        return out
    if current_start is not None and ts >= current_start:
        out.append({"code": "date_in_current_period",
                    "message": "This date is inside the current period: returns are no longer like-for-like."})
    cash = []
    for s in snapshots:
        t, c = utc(s.get("ts")), pnlsvc._f(s.get("cash"))
        if t is not None and c is not None and ts - timedelta(days=1) <= t <= ts + timedelta(days=2):
            cash.append((t, c))
    cash.sort()
    if len(cash) >= 2:
        sign = 1.0 if doc.get("kind") == "deposit" else -1.0
        best = max(sign * (b[1] - a[1]) for a, b in zip(cash, cash[1:]))
        if best < 0.5 * float(doc.get("amount") or 0):
            out.append({"code": "no_balance_jump",
                        "message": "No matching jump in the account cash balance around this date; check the date "
                                   "and amount."})
    return out


def valid_id(raw: Any) -> bool:
    return isinstance(raw, str) and bool(ID_RE.match(raw))


# ---------------------------------------------------------------- timeline
def timeline(markers: Iterable[dict]) -> list[dict]:
    """Period 1 (implicit) then one entry per stored marker, oldest first:
    [{id, name, started_at, ended_at, note, current, implicit, created_by}] with datetimes (None = open end, and
    p1's start None = the beginning of data). Exactly one entry is `current` (the last)."""
    ms = sorted((m for m in markers if utc(m.get("started_at"))), key=lambda m: utc(m["started_at"]))
    out = [{"id": P1_ID, "name": P1_NAME, "started_at": None, "ended_at": utc(ms[0]["started_at"]) if ms else None,
            "note": None, "current": not ms, "implicit": True, "created_by": None}]
    for i, m in enumerate(ms):
        end = utc(ms[i + 1]["started_at"]) if i + 1 < len(ms) else None
        out.append({"id": str(m.get("id")), "name": m.get("name"), "started_at": utc(m["started_at"]),
                    "ended_at": end, "note": m.get("note"), "current": end is None, "implicit": False,
                    "created_by": m.get("created_by")})
    return out


def public_period(p: dict) -> dict:
    return {"id": p["id"], "name": p["name"], "started_at": iso(p["started_at"]), "ended_at": iso(p["ended_at"]),
            "note": p.get("note"), "current": bool(p["current"]), "implicit": bool(p.get("implicit"))}


def public_flow(f: dict) -> dict:
    return {"id": f.get("id"), "ts": iso(f.get("ts")), "kind": f.get("kind"),
            "amount": pnlsvc._f(f.get("amount")), "note": f.get("note"), "created_by": f.get("created_by")}


def resolve(tl: list[dict], spec: str | None) -> dict | None:
    """The timeline entry for `all` (None = no filter), `current`, or an id. ValueError("unknown_period")."""
    if spec is None or spec == "all":
        return None
    if spec == "current":
        return tl[-1]
    for p in tl:
        if p["id"] == spec:
            return p
    raise ValueError("unknown_period")


def window_of(p: dict | None) -> tuple[datetime | None, datetime | None]:
    return (None, None) if p is None else (p["started_at"], p["ended_at"])


def in_window(ts: Any, start: datetime | None, end: datetime | None) -> bool:
    t = utc(ts)
    return t is not None and (start is None or t >= start) and (end is None or t < end)


def lookback_start(start: datetime | None, now: datetime) -> datetime:
    """Earliest time worth reading from the store for a window starting at `start` (retention floor)."""
    floor = now - RETENTION
    return floor if start is None else max(start, floor)


def cover_days(start: datetime | None, now: datetime, allowed: tuple[int, ...]) -> int:
    """Smallest allowed `days` value that reaches back to `start` (largest if none does)."""
    need = (now - lookback_start(start, now)).total_seconds() / 86400.0
    for d in sorted(allowed):
        if d >= need:
            return d
    return max(allowed)


# ---------------------------------------------------------------- period summary
def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 4) if b > 0 else None


def _bucket() -> dict:
    return {"fills": 0, "contracts": 0.0, "money_spent": 0.0, "fees": 0.0, "settled": 0, "wins": 0,
            "settled_cost": 0.0, "pnl": 0.0, "open_fills": 0, "open_at_risk": 0.0}


def _add(b: dict, *, count: float, cost: float, fee: float, pnl: float | None, win: bool) -> None:
    b["fills"] += 1
    b["contracts"] += count
    b["money_spent"] += cost
    b["fees"] += fee
    if pnl is None:
        b["open_fills"] += 1
        b["open_at_risk"] += cost
    else:
        b["settled"] += 1
        b["wins"] += int(win)
        b["settled_cost"] += cost
        b["pnl"] += pnl


def _finish(b: dict) -> dict:
    """Rounded public figures for one bucket, with the return-on-money-spent lines."""
    n = b["settled"]
    return {"fills": b["fills"], "contracts": round(b["contracts"], 2), "settled": n, "wins": b["wins"],
            "losses": n - b["wins"], "win_rate": _ratio(b["wins"], n),
            "pnl": round(b["pnl"], 2) if n else None,
            "money_spent": round(b["money_spent"], 2), "settled_cost": round(b["settled_cost"], 2),
            "fees": round(b["fees"], 2),
            "return_on_settled_cost": _ratio(b["pnl"], b["settled_cost"]) if n else None,
            "return_on_all_cost": _ratio(b["pnl"], b["money_spent"]) if n else None,
            "open_fills": b["open_fills"], "open_at_risk": round(b["open_at_risk"], 2)}


def _equity(snap: dict) -> float | None:
    c, e = pnlsvc._f(snap.get("cash")), pnlsvc._f(snap.get("exposure"))
    return None if c is None or e is None else c + e


def equity_points(snaps: Iterable[dict]) -> list[tuple[datetime, float]]:
    pts = [(utc(s.get("ts")), _equity(s)) for s in snaps]
    return sorted(((t, v) for t, v in pts if t is not None and v is not None), key=lambda p: p[0])


def cash_flow_block(flows: Iterable[dict], snaps: Iterable[dict], period: dict, pnl: float | None,
                    now: datetime) -> dict:
    """Deposits / withdrawals dated inside the period, shown beside P&L (never netted into it), plus the account
    equity (cash + open exposure) at the start and end taken from the account snapshots when we have them.

    * equity_change_ex_flows includes settlements of positions filled in EARLIER periods and unrealised moves, so it
      is not the period's P&L; `equity_change_includes_earlier_settlements` / `equity_change_label` say so. The
      headline P&L of a period is the by-fill-date `pnl` of the summary;
    * capital_base = start equity + deposits in the period (money that was available to work with);
    * return_on_capital = pnl / capital_base;
    * equity_change_ex_flows = end equity - start equity - (deposits - withdrawals): what the account moved by
      once the owner's own money movements are taken out (unrealised moves and open positions included)."""
    start, end = window_of(period)
    items = [f for f in flows if in_window(f.get("ts"), start, end)]
    items.sort(key=lambda f: utc(f["ts"]))
    dep = round(sum(pnlsvc._f(f.get("amount")) or 0.0 for f in items if f.get("kind") == "deposit"), 2)
    wd = round(sum(pnlsvc._f(f.get("amount")) or 0.0 for f in items if f.get("kind") == "withdrawal"), 2)
    pts = equity_points(snaps)
    start_eq = end_eq = None
    if pts:
        before = [p for p in pts if start is not None and p[0] <= start]
        inside = [p for p in pts if in_window(p[0], start, end)]
        start_eq = (before[-1] if before else (inside[0] if inside else None))
        end_eq = ([p for p in pts if end is None or p[0] <= end] or [None])[-1]
        start_eq = start_eq[1] if start_eq else None
        end_eq = end_eq[1] if end_eq else None
    base = (start_eq + dep) if start_eq is not None else None
    return {"deposits": dep, "withdrawals": wd, "net": round(dep - wd, 2),
            "items": [public_flow(f) for f in items],
            "start_equity": None if start_eq is None else round(start_eq, 2),
            "end_equity": None if end_eq is None else round(end_eq, 2),
            "capital_base": None if base is None else round(base, 2),
            "return_on_capital": _ratio(pnl, base) if pnl is not None and base else None,
            "equity_change_ex_flows": (round(end_eq - start_eq - (dep - wd), 2)
                                       if start_eq is not None and end_eq is not None else None),
            "equity_change_includes_earlier_settlements": True,
            "equity_change_label": EQUITY_CHANGE_LABEL}


def summarize(fills: Iterable[dict], settlements: Iterable[dict], period: dict, *, env: str | None,
              source: str = "bot", flows: Iterable[dict] = (), snaps: Iterable[dict] = (),
              now: datetime, truncated: bool = False) -> dict:
    """The period summary. Fills are attributed to the period by FILL time ([started_at, ended_at)); a fill counts
    as settled when its market has a yes/no settlement (whenever that happened), and its P&L follows the fill."""
    start, end = window_of(period)
    res = {}
    for s in settlements:
        t, r = s.get("ticker"), s.get("market_result") or s.get("result")
        if t and r in ("yes", "no"):
            res[str(t)] = r
    ids = bucket_ids()
    by: dict[str, dict] = {k: _bucket() for k in ids}
    total, sel = _bucket(), _bucket()
    first = last = None
    for f in fills:
        if env and f.get("env") and f.get("env") != env:
            continue
        ts = utc(f.get("ts"))
        if ts is None or not in_window(ts, start, end):
            continue
        side = pnlsvc.fill_side(f)
        price = pnlsvc.fill_price(f, side) if side else None
        if side is None or price is None:
            continue
        count = pnlsvc.fill_count(f)
        fee = pnlsvc._f(f.get("fee_cost")) or 0.0
        r = res.get(str(f.get("ticker") or f.get("market_ticker") or ""))
        pnl = pnlsvc.fill_pnl(f, r) if r else None
        src, strat = fill_label(f)
        key = "you" if src == "you" else (strat or "maker")
        kw = dict(count=count, cost=count * price, fee=fee, pnl=pnl, win=bool(r and r == side))
        _add(by.setdefault(key, _bucket()), **kw)
        _add(total, **kw)
        if source == "all" or (source == "you") == (src == "you"):
            _add(sel, **kw)
        first = ts if first is None or ts < first else first
        last = ts if last is None or ts > last else last
    out = _finish(sel)
    flow = cash_flow_block(flows, snaps, period, out["pnl"], now)
    return {"period": public_period(period), "env": env, "source": source, "pnl_basis": "fill_date", **out,
            "by_source": {k: _finish(v) for k, v in by.items() if v["fills"] or k in ids},
            "total": _finish(total), "cash_flows": flow,
            "data_from": iso(first), "data_to": iso(last), "generated_utc": iso(now), "truncated": bool(truncated)}


# ---------------------------------------------------------------- store reads
async def load_timeline(store) -> list[dict]:
    """Timeline built from the stored markers. Raises on store errors (callers wrap it)."""
    return timeline(await store.list_periods())


async def read_summary(store, period: dict, *, env: str | None, source: str, now: datetime) -> dict:
    """Bounded, projected reads -> summarize(). Raises on store errors (callers wrap it)."""
    start, end = window_of(period)
    since = lookback_start(start, now)
    fills = await store.list_fills(since=since, until=end, limit=FILL_LIMIT, fields=pnlsvc.FILL_FIELDS)
    settl = await store.list_settlements(since=since, limit=SETTLEMENT_LIMIT,
                                         fields=("ticker", "market_result", "result", "ts"))
    flows = await store.list_cash_flows(since=None, until=None)
    snaps = await store.list_account_snapshots(since=since - timedelta(days=2), limit=10000)
    return summarize(fills, settl, period, env=env, source=source, flows=flows, snaps=snaps, now=now,
                     truncated=len(fills) >= FILL_LIMIT or len(settl) >= SETTLEMENT_LIMIT)


# ---------------------------------------------------------------- rolling windows clipped to a period (2A-2 S4)
ROLLING_DAYS = (7, 30)


def _day(d: datetime | None) -> str:
    return d.strftime("%Y-%m-%d") if d else "the beginning"


def rolling_window(period: dict | None, days: int, now: datetime) -> dict:
    """The "last N days" window, clipped to a period: {label, from, to, period_id, days, clipped}.

    * period None (`all`): [now - N days, now), period_id "all", never clipped;
    * current period: [max(now - N days, period start), now);
    * an ENDED period: the N days are counted back from the period's END (not from today), clipped to its start,
      so Period 1 shows its own last 7 / 30 days instead of an empty window.
    `from` / `to` are ISO times; `to` is exclusive and is the instant the window was measured at."""
    start, end = window_of(period)
    anchor = now if end is None or end > now else end
    lo = anchor - timedelta(days=days)
    clipped = start is not None and start > lo
    if clipped:
        lo = start
    if period is None:
        label = f"Last {days} days (all periods)"
    else:
        name = period.get("name") or period["id"]
        if not clipped:
            label = f"Last {days} days within {name}"
        elif end is None or end > now:
            label = f"{name} so far (since {_day(start)}, under {days} days)"
        else:
            label = f"All of {name} (shorter than {days} days)"
    return {"label": label, "from": iso(lo), "to": iso(anchor), "period_id": period["id"] if period else "all",
            "days": days, "clipped": bool(clipped)}


async def rolling_pnl(store, period: dict | None, now: datetime, *, taker: bool = False, env: str | None = None) -> dict:
    """The Overview's rolling P&L for a chosen period, attributed by FILL date (same rule as the period summary):
    {d7, d30, settled_fills, basis: "fill_date", period, window: {d7: <window>, d30: <window>}}. d7 / d30 are None
    when nothing settled in that window. Raises on store errors (callers wrap it)."""
    w7, w30 = rolling_window(period, 7, now), rolling_window(period, 30, now)
    hi = utc(w30["to"])
    res = await pnlsvc.compute_by_fill(store, utc(w7["from"]), utc(w30["from"]), hi, taker=taker, env=env)
    return {**res, "basis": "fill_date", "period": public_period(period) if period else None,
            "window": {"d7": w7, "d30": w30}}


async def rolling_pnl_pair(store, period: dict | None, now: datetime, *, env: str | None = None) -> tuple[dict, dict]:
    """(maker, arbitrage) blocks, each exactly the shape rolling_pnl() returns, from one pair of store reads."""
    w7, w30 = rolling_window(period, 7, now), rolling_window(period, 30, now)
    maker, arb = await pnlsvc.compute_by_fill_pair(store, utc(w7["from"]), utc(w30["from"]), utc(w30["to"]), env=env)
    extra = {"basis": "fill_date", "period": public_period(period) if period else None,
             "window": {"d7": w7, "d30": w30}}
    return {**maker, **extra}, {**arb, **extra}
