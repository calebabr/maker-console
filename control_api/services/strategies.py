"""Strategy registry and fill/order source attribution (UX sprint item Y1; docs/UX_CONTRACT.md section 5).

Every fill and order is labelled ``source = "bot" | "you"`` plus ``strategy = <registry id> | None``:

* an order's ``client_order_id`` prefix names the bot strategy (``mm-`` maker, ``arb-`` arbitrage); anything else on
  the account is the owner's own trade ("you");
* Kalshi fills don't carry the client id, so a fill is mapped through its ``order_id``, using, strongest first:
  the account's order list (``client_order_id`` prefix), the maker journal ``quote`` rows (``order_id=...``), the
  queue-position episodes (maker orders only), and the arbitrage log (a taker fill on a ticker of an executed arb
  set within a few minutes of it). Unknown order ids default to "you";
* a fill stored before this existed has no ``source`` yet: until the backfill labels it, readers use the legacy
  view (every fill was the bot's: taker = arb, maker = maker), so bot statistics never silently lose fills.

Manual trades are excluded from every bot statistic (P&L windows, analytics summary, fill quality). A new strategy
needs one STRATEGIES entry; the UI reads names and colours from GET /api/strategies. Read-only: nothing here places,
changes or cancels orders.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Iterable

SOURCES = ("bot", "you")
YOU = "you"
BOT = "bot"
STRATEGIES: dict[str, dict] = {
    "maker": {"name": "Bot · Maker", "color": "var(--accent)", "prefix": "mm-"},
    "arb": {"name": "Bot · Arb", "color": "var(--chart2)", "prefix": "arb-"},
}
YOU_ENTRY = {"id": YOU, "source": YOU, "name": "You", "color": "var(--warn)", "prefix": None}
# evidence, strongest first (a weaker one never overrides a stronger one already stored)
EVIDENCE_RANK = {"order": 5, "client_order_id": 5, "journal": 4, "episode": 3, "arb_log": 2, "no_order_id": 1,
                 "default": 0}
ARB_WINDOW_BEFORE = timedelta(minutes=10)     # fill time vs the arb log doc time (the doc is written after the scan)
ARB_WINDOW_AFTER = timedelta(minutes=2)
_OID = re.compile(r"order_id=([A-Za-z0-9_.:\-]+)")
_ARB_TICKER = re.compile(r"'([A-Z0-9][A-Z0-9_.\-]{2,79})'")
LABEL_FIELDS = ("source", "strategy", "source_by")


def registry() -> list[dict]:
    """Bot strategies (registry order) followed by the 'you' entry."""
    return [{"id": k, "source": BOT, **v} for k, v in STRATEGIES.items()] + [dict(YOU_ENTRY)]


def bucket_ids() -> list[str]:
    """P&L split keys: every strategy id, then 'you'."""
    return list(STRATEGIES) + [YOU]


def strategy_for_client_order_id(coid: Any) -> str | None:
    s = str(coid or "")
    for sid, meta in STRATEGIES.items():
        if meta.get("prefix") and s.startswith(meta["prefix"]):
            return sid
    return None


def order_label(order: dict) -> dict:
    """{source, strategy} for a Kalshi order (its client_order_id prefix)."""
    sid = strategy_for_client_order_id(order.get("client_order_id"))
    return {"source": BOT if sid else YOU, "strategy": sid}


def fill_label(f: dict) -> tuple[str, str | None]:
    """(source, strategy) of a fill doc: the stored label, else the legacy view (taker = arb, maker = maker)."""
    src = f.get("source")
    if src in SOURCES:
        st = f.get("strategy")
        return src, (st if src == BOT and st in STRATEGIES else None)
    return BOT, ("arb" if f.get("is_taker") else "maker")


def is_bot(f: dict) -> bool:
    return fill_label(f)[0] == BOT


def _ts(v: Any) -> datetime | None:
    from ..db.schemas import _safe_to_utc
    return _safe_to_utc(v)


class OrderIndex:
    """order_id -> (strategy id | None for a known manual order, evidence), plus the arb log for taker fills."""

    def __init__(self, maker_ids: Any = None) -> None:
        self.orders: dict[str, tuple[str | None, str]] = {}
        # maker order ids from the journal kept by the runner (a set/dict, looked up only; never copied)
        self.maker_ids = maker_ids if maker_ids is not None else {}
        self._arb: dict[str, list[datetime]] = {}            # ticker -> executed arb set times

    def _put(self, oid: Any, sid: str | None, evidence: str) -> None:
        oid = str(oid or "")
        if not oid:
            return
        cur = self.orders.get(oid)
        if cur is None or EVIDENCE_RANK[evidence] > EVIDENCE_RANK[cur[1]]:
            self.orders[oid] = (sid, evidence)

    def add_orders(self, orders: Iterable[dict]) -> "OrderIndex":
        """Kalshi /portfolio/orders rows: the client_order_id prefix is definitive (a non-bot prefix = manual)."""
        for o in orders or []:
            if isinstance(o, dict):
                self._put(o.get("order_id"), strategy_for_client_order_id(o.get("client_order_id")), "order")
        return self

    def add_journal(self, rows: Iterable[dict]) -> "OrderIndex":
        """Maker journal `quote` rows ("live order_id=...")."""
        for r in rows or []:
            if isinstance(r, dict) and r.get("event") == "quote":
                m = _OID.search(str(r.get("note") or ""))
                if m:
                    self._put(m.group(1), "maker", "journal")
        return self

    def add_episodes(self, eps: Iterable[dict]) -> "OrderIndex":
        for e in eps or []:
            if isinstance(e, dict):
                self._put(e.get("order_id"), "maker", "episode")
        return self

    def add_arb_docs(self, docs: Iterable[dict]) -> "OrderIndex":
        for d in docs or []:
            if not isinstance(d, dict):
                continue
            ex = str(d.get("executed") or "")
            t = _ts(d.get("ts"))
            if t is None or not ex.startswith(("FILLED", "PARTIAL", "ERROR")):
                continue
            for tk in _ARB_TICKER.findall(ex):
                self._arb.setdefault(tk, []).append(t)
        return self

    def _arb_hit(self, f: dict) -> bool:
        if not f.get("is_taker"):
            return False
        times = self._arb.get(str(f.get("ticker") or f.get("market_ticker") or ""))
        t = _ts(f.get("ts")) or _ts(f.get("created_time"))
        if not times or t is None:
            return False
        return any(a - ARB_WINDOW_BEFORE <= t <= a + ARB_WINDOW_AFTER for a in times)

    def classify(self, f: dict) -> dict:
        """{source, strategy, source_by} for one fill."""
        sid = strategy_for_client_order_id(f.get("client_order_id"))
        if sid:
            return {"source": BOT, "strategy": sid, "source_by": "client_order_id"}
        oid = str(f.get("order_id") or "")
        hit = self.orders.get(oid)
        if hit is None and oid and oid in self.maker_ids:
            hit = ("maker", "journal")
        if hit is not None:
            s, ev = hit
            return {"source": BOT if s else YOU, "strategy": s, "source_by": ev}
        if self._arb_hit(f):
            return {"source": BOT, "strategy": "arb", "source_by": "arb_log"}
        if not oid:
            # no order id at all (not an unknown one): nothing to map, keep the legacy view
            return {"source": BOT, "strategy": "arb" if f.get("is_taker") else "maker", "source_by": "no_order_id"}
        return {"source": YOU, "strategy": None, "source_by": "default"}


def journal_order_ids(rows: Iterable[dict]) -> list[str]:
    """Maker order ids from journal `quote` rows, in row order."""
    out = []
    for r in rows or []:
        if isinstance(r, dict) and r.get("event") == "quote":
            m = _OID.search(str(r.get("note") or ""))
            if m:
                out.append(m.group(1))
    return out


ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def unresolved(fills: Iterable[dict], index: OrderIndex, limit: int) -> list[str]:
    """Order ids of fills the index can't place (they would default to 'you'), safe for a URL path, deduplicated."""
    out: list[str] = []
    for f in fills:
        if not isinstance(f, dict) or index.classify(f)["source_by"] != "default":
            continue
        oid = str(f.get("order_id") or "")
        if ORDER_ID_RE.match(oid) and oid not in out:
            out.append(oid)
            if len(out) >= limit:
                break
    return out


def merge_label(old: dict, new: dict) -> dict | None:
    """The label to store, or None to keep the old one: a weaker evidence never replaces a stronger stored one."""
    if old.get("source") not in SOURCES:
        return new
    old_rank = EVIDENCE_RANK.get(str(old.get("source_by")), 0)
    if EVIDENCE_RANK[new["source_by"]] < old_rank:
        return None
    if all(old.get(k) == new.get(k) for k in LABEL_FIELDS):
        return None
    return new


def stamp(fills: list[dict], index: OrderIndex) -> int:
    """Set source/strategy/source_by on fill dicts about to be upserted. Returns the number labelled 'you'."""
    n = 0
    for f in fills:
        if isinstance(f, dict):
            lab = index.classify(f)
            f.update(lab)
            n += lab["source"] == YOU
    return n


def ticker_sources(fills: Iterable[dict]) -> dict[str, set[str]]:
    """ticker -> set of sources that traded it (for settlement / position labels)."""
    out: dict[str, set[str]] = {}
    for f in fills:
        t = str(f.get("ticker") or f.get("market_ticker") or "")
        if t:
            out.setdefault(t, set()).add(fill_label(f)[0])
    return out


def ticker_labels(fills: Iterable[dict]) -> dict[str, dict]:
    """ticker -> {sources, strategies, by_source: {source: net contracts per side}} from fills."""
    from . import pnl as pnlsvc
    out: dict[str, dict] = {}
    for f in fills:
        t = str(f.get("ticker") or f.get("market_ticker") or "")
        side = pnlsvc.fill_side(f)
        if not t or side is None:
            continue
        src, sid = fill_label(f)
        e = out.setdefault(t, {"sources": set(), "strategies": set(), "count": {}})
        e["sources"].add(src)
        if sid:
            e["strategies"].add(sid)
        key = (src, sid, side)
        sign = -1.0 if str(f.get("action") or "buy") == "sell" else 1.0
        e["count"][key] = e["count"].get(key, 0.0) + sign * pnlsvc.fill_count(f)
    return out
