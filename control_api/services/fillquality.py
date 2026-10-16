"""Fill-quality telemetry (read-only): did the market trade THROUGH our maker fill within 60 min, and how did the
maker-side price move over those 60 min? Definitions:

- maker-side price of a trade q = yes price if we bought YES, else 1 - yes price;
- trade_through = some trade strictly AFTER the fill and within 60 min has q < our fill price (our level was
  exhausted, so a back-of-queue order would certainly have filled);
- move_60m_c = (q of the LAST trade in that window - our fill price) x 100, or None if nothing traded.

The market baseline to compare with is optional and private (services/benchmarks.py); null when not supplied.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from . import benchmarks as bench
from . import pnl as pnlsvc

WINDOW = timedelta(minutes=60)
SETTLE_DELAY = timedelta(minutes=65)    # evaluate once the 60-min window is complete
MAX_AGE = timedelta(days=3)             # older un-evaluated fills are left alone
MAX_FILLS_PER_PASS = 8                  # API-call cap: <= 8 x MAX_PAGES public requests per pass
MAX_PAGES = 3
FIELD = "fill_quality"


def _ts(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), timezone.utc)
    if isinstance(v, str) and v:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def trade_yes_price(t: dict) -> float | None:
    p = pnlsvc._f(t.get("yes_price_dollars"))
    if p is None:
        c = pnlsvc._f(t.get("yes_price"))
        p = c / 100.0 if c is not None else None
    return p


def fetch_trades(client: Any, ticker: str, start: datetime, end: datetime) -> list[dict]:
    """Public trades for one market in [start, end] (bounded pagination). Raises on API errors."""
    out: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        body = client.request("GET", "/markets/trades",
                              params={"ticker": ticker, "min_ts": int(start.timestamp()),
                                      "max_ts": int(end.timestamp()) + 1, "limit": 1000, "cursor": cursor})
        body = body if isinstance(body, dict) else {}
        trades = body.get("trades")
        page = [t for t in trades if isinstance(t, dict)] if isinstance(trades, list) else []
        out.extend(page)
        cursor = body.get("cursor")
        if not cursor or not page:
            break
    return out


def evaluate(fill: dict, trades: Iterable[dict]) -> dict | None:
    """fill_quality for one maker fill from the trades around it; None if the fill can't be read."""
    side = pnlsvc.fill_side(fill)
    ts = _ts(fill.get("ts")) or _ts(fill.get("created_time"))
    price = pnlsvc.fill_price(fill, side) if side else None
    if side is None or ts is None or price is None:
        return None
    end = ts + WINDOW
    window = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        tt, yp = _ts(t.get("created_time") or t.get("ts")), trade_yes_price(t)
        if tt is None or yp is None or not (ts < tt <= end):
            continue
        window.append((tt, yp if side == "yes" else 1.0 - yp))
    window.sort(key=lambda x: x[0])
    through = any(q < price - 1e-9 for _, q in window)
    move = round((window[-1][1] - price) * 100.0, 3) if window else None
    return {"trade_through": through, "move_60m_c": move, "trades_60m": len(window), "fill_price": price,
            "side": side, "evaluated_utc": datetime.now(timezone.utc)}


def aggregate(fills: Iterable[dict], *, env: str | None = None) -> dict:
    """{n, trade_through_share, avg_move_60m_c, baseline} over evaluated maker fills (n=0 -> nulls)."""
    rows = []
    for f in fills:
        if not isinstance(f, dict):
            continue
        if f.get("is_taker") or not isinstance(f.get(FIELD), dict) or "trade_through" not in f[FIELD]:
            continue
        if env and f.get("env") and f.get("env") != env:
            continue
        rows.append(f[FIELD])
    n = len(rows)
    moves = [r["move_60m_c"] for r in rows if isinstance(r.get("move_60m_c"), (int, float))]
    return {"n": n,
            "trade_through_share": round(sum(1 for r in rows if r.get("trade_through")) / n, 4) if n else None,
            "avg_move_60m_c": round(sum(moves) / len(moves), 3) if moves else None,
            "baseline": bench.fill_quality_baseline()}
