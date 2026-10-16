"""DEMO PLACEHOLDER bracket-arbitrage scanner (public stand-in). Textbook check only, no tuning or research.

An event with N mutually exclusive, exhaustive brackets pays $1 on exactly one bracket, so:
  NO-arb : buy NO on every bracket (sell into each YES bid). Exactly N-1 NOs pay $1.
  YES-arb: buy YES on every bracket at its ask (1 - best NO bid). Exactly one YES pays $1.
This stand-in only looks at the best level of each book (no depth walk) and uses Kalshi's published general
taker fee. It has the interface the control API uses: DEFAULT_SERIES, KILL_PATH, LOG_PATH, Opportunity,
scan_once (never executes by itself when client is None) and execute. The private overlay replaces this file.
"""
from __future__ import annotations

import csv
import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SERIES = ["KXDEMOA", "KXDEMOB", "KXDEMOG", "KXDEMOH"]
LOG_PATH = Path(__file__).with_name("logs") / "arb_opportunities.csv"
KILL_PATH = Path(__file__).with_name("ARB_KILL_SWITCH")


def taker_fee(contracts: float, price: float) -> float:
    """Kalshi general taker fee: round up to the cent of 0.07 x C x P x (1 - P)."""
    return math.ceil(round(0.07 * contracts * price * (1 - price) * 100, 6)) / 100


def best_level(ob: dict, side: str) -> tuple[float, float] | None:
    """(price, qty) of the best (highest) bid for `side` ('yes'|'no'), fp format, or None."""
    lv = [(float(p), float(q)) for p, q in (ob.get(f"{side}_dollars") or []) if float(q) > 0]
    return max(lv) if lv else None


@dataclass
class Opportunity:
    event: str
    kind: str            # "NO" | "YES"
    contracts: float     # contracts per leg
    profit: float        # guaranteed $ after taker fees
    cost: float          # capital outlay $
    legs: list           # [(ticker, book_side, limit_price_yes, contracts)]
    sum_best: float


def best_opportunity(event: str, books: dict[str, dict], max_contracts: float) -> Opportunity | None:
    tickers = sorted(books)
    best = None
    for kind in ("NO", "YES"):
        levels = {t: best_level(books[t], "yes" if kind == "NO" else "no") for t in tickers}
        if not tickers or any(v is None for v in levels.values()):
            continue
        n = float(int(min(max_contracts, min(q for _, q in levels.values()))))
        if n < 1:
            continue
        cost, legs = 0.0, []
        for t in tickers:
            p = levels[t][0]
            paid = 1.0 - p                                   # NO at 1-yes_bid, or YES at 1-no_bid
            cost += n * paid + taker_fee(n, paid)
            legs.append((t, "ask", round(p, 4), n) if kind == "NO" else (t, "bid", round(1.0 - p, 4), n))
        payout = (len(tickers) - 1 if kind == "NO" else 1) * n
        profit = payout - cost
        if profit > 0 and (best is None or profit > best.profit):
            sb = sum(v[0] for v in levels.values()) if kind == "NO" else sum(1 - v[0] for v in levels.values())
            best = Opportunity(event, kind, n, round(profit, 4), round(cost, 4), legs, round(sb, 4))
    return best


def log_opp(op: Opportunity, executed: str) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    new = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["utc", "event", "kind", "contracts", "profit", "cost", "sum_best", "legs", "executed"])
        w.writerow([datetime.now(timezone.utc).isoformat(), op.event, op.kind, op.contracts, op.profit, op.cost,
                    op.sum_best, json.dumps(op.legs), executed])


def live_enabled() -> bool:
    return (os.getenv("ARB_LIVE", "false").lower() == "true"
            and os.getenv("KALSHI_LIVE_CONFIRM") == "I_UNDERSTAND_REAL_MONEY"
            and not KILL_PATH.exists())


def execute(client, op: Opportunity) -> str:
    """All legs as limit orders (smallest first). Any partial fill -> arb kill switch (legging risk) + report."""
    filled = {}
    for t, book_side, limit_yes, n in sorted(op.legs, key=lambda x: x[3]):
        r = client.create_order(ticker=t, side=book_side, count=f"{n:.2f}", price=f"{limit_yes:.4f}",
                                client_order_id=f"arb-{uuid.uuid4().hex[:16]}")
        o = r.get("order", r)
        filled[t] = float(o.get("fill_count_fp") or o.get("fill_count") or 0)
        if filled[t] + 1e-9 < n:
            KILL_PATH.write_text(json.dumps({"reason": "partial fill / legging risk", "event": op.event,
                                             "fills": filled, "utc": datetime.now(timezone.utc).isoformat()}))
            return f"PARTIAL {filled}"
    return f"FILLED {filled}"


def scan_once(pub, client, series: list[str], max_contracts: float, min_profit: float) -> list[Opportunity]:
    """Opportunities in the open events of `series`. Executes only if a client is passed (the control API never
    passes one: it executes through its own gated path)."""
    found = []
    for s in series:
        events: dict[str, list[str]] = {}
        for m in pub.get_markets(series_ticker=s, status="open") or []:
            events.setdefault(m["event_ticker"], []).append(m["ticker"])
        for e, tks in events.items():
            books = {}
            for t in tks:
                ob = pub.get_orderbook(t) or {}
                books[t] = ob.get("orderbook_fp") or ob.get("orderbook") or {}
            op = best_opportunity(e, books, max_contracts)
            if op and op.profit >= min_profit:
                executed = execute(client, op) if (client is not None and live_enabled()) else "log-only"
                log_opp(op, executed)
                found.append(op)
    return found
