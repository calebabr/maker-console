"""Queue-position telemetry (read-only): estimator math, episode lifecycle, caps, budget, Stop, cache, shape, store.
Fakes only: no network, no Kalshi, no Atlas."""
from __future__ import annotations

import asyncio
import csv
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from control_api import runner as runmod
from control_api.db import MemoryStore
from control_api.db import schemas as S
from control_api.services import queuepos as qp

pytestmark = pytest.mark.asyncio
T = "KXDEMOA-26OCT13-B70"
SHAPE_KEYS = {"episodes", "fills", "median_queue_ahead_at_join", "requote_reset_share", "front_fill_share",
              "by_queue", "updated_utc"}
BUCKET_KEYS = {"bucket", "episodes", "fills", "fill_rate", "trade_through_share"}


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- fakes
class FakeAccount:
    """Authenticated client: resting orders, fills, queue positions (optional), cancel. Records every call."""

    def __init__(self, resting=None, fills=None, qpos=None, fail=False):
        self.resting = list(resting or [])
        self.fills = list(fills or [])
        self.qpos = qpos                 # None -> the endpoint 404s (estimator falls back to the book)
        self.fail = fail
        self.calls = []
        self.cancelled = []

    def get_orders(self, status=None):
        self.calls.append(("orders", status))
        if self.fail:
            raise RuntimeError("kalshi down")
        return list(self.resting)

    def get_fills(self, min_ts=None):
        self.calls.append(("fills", min_ts))
        if self.fail:
            raise RuntimeError("kalshi down")
        return list(self.fills)

    def request(self, method, endpoint, params=None, auth=False, **kw):
        self.calls.append(("request", endpoint, dict(params or {})))
        assert method == "GET" and auth is True and endpoint == "/portfolio/orders/queue_positions"
        if self.fail or self.qpos is None:
            raise RuntimeError("404")
        return {"queue_positions": [{"order_id": k, "market_ticker": T, "queue_position": v}
                                    for k, v in self.qpos.items()]}

    def cancel_order(self, order_id, market_ticker=None):
        self.cancelled.append(order_id)
        self.resting = [o for o in self.resting if o["order_id"] != order_id]


class FakePublic:
    def __init__(self, books=None, trades=None, fail=False, slow=0.0):
        self.books = books or {}
        self.trades = trades or {}
        self.fail, self.slow = fail, slow
        self.calls = []

    def get_orderbook(self, ticker, depth=None):
        self.calls.append(("orderbook", ticker))
        if self.slow:
            time.sleep(self.slow)
        if self.fail:
            raise RuntimeError("down")
        return {"orderbook_fp": self.books.get(ticker, {})}

    def request(self, method, endpoint, params=None, **kw):
        self.calls.append(("trades", params["ticker"]))
        assert method == "GET" and endpoint == "/markets/trades" and not kw.get("auth")
        if self.fail:
            raise RuntimeError("down")
        return {"trades": list(self.trades.get(params["ticker"], [])), "cursor": None}


def write_journal(path, rows):
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(S.JOURNAL_FIELDS)
        for r in rows:
            w.writerow([r.get(k, "") for k in S.JOURNAL_FIELDS])


def quote_row(ts, oid, price=0.45, side="no", ticker=T, count=10):
    return {"utc": ts.isoformat(), "event": "quote", "ticker": ticker, "side": side, "price": price, "count": count,
            "cost": round(price * count, 2), "cell": "[0.5, 0.49, 0, 24, \"no\"]", "note": f"live order_id={oid}"}


def cancel_row(ts, price=0.45, side="no", ticker=T):
    book_side = "bid" if side == "yes" else "ask"
    order_price = price if side == "yes" else round(1 - price, 4)
    return {"utc": ts.isoformat(), "event": "cancel", "note": json.dumps([ticker, book_side, f"{order_price:.4f}"])}


def resting(oid, remaining=10, ticker=T):
    return {"order_id": oid, "client_order_id": "mm-x", "ticker": ticker, "remaining_count_fp": str(remaining)}


# ---------------------------------------------------------------- estimator math
async def test_level_size_fp_and_cents_books():
    book = {"no_dollars": [["0.4500", "120.00"], ["0.4400", "50"]], "yes_dollars": [["0.0400", "7"]]}
    assert qp.level_size(book, "no", 0.45) == 120.0
    assert qp.level_size(book, "yes", 0.04) == 7.0
    assert qp.level_size(book, "no", 0.46) == 0.0
    assert qp.level_size({"no": [[45, 30]]}, "no", 0.45) == 30.0          # legacy cents book
    assert qp.level_size({}, "no", 0.45) == 0.0
    assert qp.level_size(None, "no", 0.45) is None
    assert qp.level_size({"no_dollars": [["x", "y"]]}, "no", 0.45) is None


async def test_scan_trades_side_through_window_and_exclude():
    t0 = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    trades = [
        {"created_time": _iso(t0 - timedelta(minutes=1)), "yes_price_dollars": "0.55", "count_fp": "99"},  # before
        {"created_time": _iso(t0 + timedelta(minutes=1)), "yes_price_dollars": "0.55", "count_fp": "10",
         "taker_side": "yes"},                                               # NO 0.45, hit our NO bids
        {"created_time": _iso(t0 + timedelta(minutes=2)), "yes_price_dollars": "0.55", "count": 4,
         "taker_side": "no"},                                                # taker bought NO: not our side
        {"created_time": _iso(t0 + timedelta(minutes=3)), "yes_price": 55, "count": 3},     # cents, no taker_side
        {"created_time": _iso(t0 + timedelta(minutes=4)), "yes_price_dollars": "0.55", "count": 6,
         "trade_id": "mine"},                                                # our own fill: excluded
        {"created_time": _iso(t0 + timedelta(minutes=5)), "yes_price_dollars": "0.53", "count": 1},  # NO 0.47 above
    ]
    vol, through = qp.scan_trades(trades, "no", 0.45, t0, exclude={"mine"})
    assert vol == 13.0 and through is False
    vol, through = qp.scan_trades(trades + [{"created_time": _iso(t0 + timedelta(minutes=6)),
                                             "yes_price_dollars": "0.56", "count": 1}], "no", 0.45, t0)
    assert through is True and vol == 19.0                                  # NO 0.44 < 0.45: traded through
    vol, _ = qp.scan_trades(trades, "no", 0.45, t0, until=t0 + timedelta(minutes=1, seconds=30))
    assert vol == 10.0
    # YES side: our YES bid at 0.47 is hit by takers buying NO
    yes = [{"created_time": _iso(t0 + timedelta(minutes=1)), "yes_price_dollars": "0.47", "count": 2,
            "taker_side": "no"},
           {"created_time": _iso(t0 + timedelta(minutes=2)), "yes_price_dollars": "0.47", "count": 5,
            "taker_side": "yes"}]
    assert qp.scan_trades(yes, "yes", 0.47, t0) == (2.0, False)


async def test_bounds_estimator():
    # no-cancel estimate = join - traded; upper = min(that, level_now - ours); lower = 0
    assert qp.bounds(100, 30, False, 200, 10) == {"nc": 70.0, "hi": 70.0, "lo": 0.0}
    assert qp.bounds(100, 30, False, 50, 10) == {"nc": 70.0, "hi": 40.0, "lo": 0.0}     # cancellations visible
    assert qp.bounds(100, 130, False, None, 10) == {"nc": 0.0, "hi": 0.0, "lo": 0.0}    # clipped at 0
    assert qp.bounds(100, 0, True, 500, 10) == {"nc": 0.0, "hi": 0.0, "lo": 0.0}        # trade-through
    assert qp.bounds(None, None, False, 60, 10) == {"nc": None, "hi": 50.0, "lo": 0.0}
    assert qp.bounds(None, None, False, None, None) == {"nc": None, "hi": None, "lo": None}
    assert qp.bounds(100, 30, False, 200, 10, kq=25) == {"nc": 70.0, "hi": 70.0, "lo": 25.0}  # Kalshi exact
    assert qp.bounds(None, None, False, None, None, kq=7) == {"nc": None, "hi": None, "lo": 7.0}


async def test_buckets():
    assert [qp.bucket(x) for x in (0, 0.99, 1, 50, 50.5, 200, 201, 1000, 1000.5)] == \
        ["0", "0", "1-50", "1-50", "51-200", "51-200", "201-1000", "201-1000", ">1000"]


async def test_front_at_fill_uses_trades_before_fill_only():
    t0 = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    ep = {"ts": t0, "side": "no", "price": 0.45, "queue_ahead_at_join": 20.0,
          "first_fill_utc": t0 + timedelta(minutes=10)}
    trades = [{"created_time": _iso(t0 + timedelta(minutes=2)), "yes_price_dollars": "0.55", "count": 20},
              {"created_time": _iso(t0 + timedelta(minutes=10)), "yes_price_dollars": "0.55", "count": 5,
               "trade_id": "ours"},
              {"created_time": _iso(t0 + timedelta(minutes=20)), "yes_price_dollars": "0.57", "count": 5}]
    assert qp.front_at_fill(ep, trades, ["ours"]) == (0.0, True)            # 20 ahead traded first: front
    ep["queue_ahead_at_join"] = 50.0
    assert qp.front_at_fill(ep, trades, ["ours"]) == (30.0, False)          # the later trade-through doesn't count
    assert qp.front_at_fill({**ep, "queue_ahead_at_join": None}, trades, []) == (None, None)


# ---------------------------------------------------------------- journal -> episodes
async def test_apply_journal_quote_reprice_cancel_and_cap():
    t0 = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    open_eps: dict = {}
    rows = [quote_row(t0, "A", 0.45), quote_row(t0, "B", 0.42, ticker="KXDEMOB-X"),
            {"utc": t0.isoformat(), "event": "quote", "ticker": T, "side": "no", "price": 0.4, "count": 1,
             "note": "live order_id="},                                              # no id: ignored
            {"utc": t0.isoformat(), "event": "would_quote", "ticker": T, "side": "no", "price": 0.4, "count": 1}]
    new, closed, dropped = qp.apply_journal(rows, open_eps, env="demo", series_list=["KXDEMOA", "KXDEMOB"],
                                            allowance=10, now=t0)
    assert [e["order_id"] for e in new] == ["A", "B"] and not closed and dropped == 0
    a = open_eps["A"]
    assert a["series"] == "KXDEMOA" and a["book_side"] == "ask" and a["order_price"] == 0.55 and a["status"] == "open"
    t1 = t0 + timedelta(minutes=10)
    rows = [cancel_row(t1, 0.45), quote_row(t1, "C", 0.46),                # moved price -> repriced
            cancel_row(t1, 0.42, ticker="KXDEMOB-X"),                     # no new quote -> cancelled
            cancel_row(t1, 0.50), {"utc": t1.isoformat(), "event": "cancel", "note": "garbage"}]
    new, closed, dropped = qp.apply_journal(rows, open_eps, env="demo", series_list=["KXDEMOA", "KXDEMOB"],
                                            allowance=10, now=t1)
    assert {e["order_id"]: e["outcome"] for e in closed} == {"A": "repriced", "B": "cancelled"}
    assert all(e["status"] == "closed" and e["closed_utc"] == t1 for e in closed)
    assert set(open_eps) == {"C"}
    new, closed, dropped = qp.apply_journal([quote_row(t1, f"Z{i}") for i in range(5)], open_eps, env="demo",
                                            series_list=["KXDEMOA"], allowance=2, now=t1)
    assert len(new) == 2 and dropped == 3


# ---------------------------------------------------------------- runner lifecycle
def _setup(env, acct, pub):
    env.runner.kalshi_factory = lambda mode: acct
    env.runner.public_factory = lambda e: pub
    return env.paths / "journal.csv"


async def test_lifecycle_join_observe_fill_front(env):
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(minutes=30)
    acct = FakeAccount(resting=[resting("A", 10)])
    pub = FakePublic(books={T: {"no_dollars": [["0.4500", "130"]]}})
    jpath = _setup(env, acct, pub)
    write_journal(jpath, [quote_row(t0, "A", 0.45, count=10)])
    assert await env.runner._sync_journal("demo") == 1
    res = await env.runner.evaluate_queue_position("demo")
    assert res["new"] == 1 and "error" not in res
    ep = (await env.store.list_quote_episodes())[0]
    # queue_positions endpoint unavailable -> join from the book: 130 at our level - our 10
    assert ep["queue_ahead_at_join"] == 120.0 and ep["join_source"] == "book" and ep["join_level_size"] == 130.0
    assert ep["status"] == "open" and ep["obs_count"] == 1 and ep["last_obs"]["hi"] == 120.0

    # next pass: 90 traded at our price, level now 60 (some ahead cancelled)
    pub.books[T] = {"no_dollars": [["0.4500", "60"]]}
    pub.trades[T] = [{"created_time": _iso(t0 + timedelta(minutes=5)), "yes_price_dollars": "0.55",
                      "count": 90, "taker_side": "yes", "trade_id": "x1"}]
    await env.runner.evaluate_queue_position("demo")
    ep = (await env.store.list_quote_episodes())[0]
    assert ep["last_obs"]["nc"] == 30.0 and ep["last_obs"]["hi"] == 30.0 and ep["last_obs"]["lo"] == 0.0
    assert ep["last_obs"]["traded"] == 90.0 and ep["obs_count"] == 2

    # then filled: gone from resting, a maker fill for all 10, 120 traded ahead before our fill -> front
    fill_ts = t0 + timedelta(minutes=20)
    pub.trades[T].append({"created_time": _iso(t0 + timedelta(minutes=15)), "yes_price_dollars": "0.55",
                          "count": 30, "taker_side": "yes", "trade_id": "x2"})
    pub.trades[T].append({"created_time": _iso(fill_ts), "yes_price_dollars": "0.55", "count": 10,
                          "taker_side": "yes", "trade_id": "ours"})
    acct.resting = []
    acct.fills = [{"fill_id": "f1", "trade_id": "ours", "order_id": "A", "ticker": T, "side": "no",
                   "no_price_dollars": "0.45", "count_fp": "10", "is_taker": False, "created_time": _iso(fill_ts)}]
    res = await env.runner.evaluate_queue_position("demo")
    ep = (await env.store.list_quote_episodes())[0]
    assert ep["status"] == "closed" and ep["outcome"] == "filled" and ep["filled_count"] == 10.0
    assert ep["fill_ids"] == ["f1"] and ep["front_fill"] is True and ep["upper_at_fill"] == 0.0
    # aggregate + link to fill_quality.trade_through
    await env.store.upsert_fills([{**acct.fills[0], "env": "demo",
                                   "fill_quality": {"trade_through": False, "move_60m_c": 0.0}}])
    stats = (await env.runner.refresh_queue_stats())["overall"]
    assert stats["episodes"] == 1 and stats["fills"] == 1 and stats["front_fill_share"] == 1.0
    assert stats["median_queue_ahead_at_join"] == 120.0 and stats["requote_reset_share"] == 0.0
    b = {x["bucket"]: x for x in stats["by_queue"]}
    assert b["51-200"] == {"bucket": "51-200", "episodes": 1, "fills": 1, "fill_rate": 1.0, "trade_through_share": 0.0}
    assert b["0"]["fill_rate"] is None and b["0"]["trade_through_share"] is None


async def test_join_from_kalshi_queue_position_when_available(env):
    now = datetime.now(timezone.utc)
    acct = FakeAccount(resting=[resting("A")], qpos={"A": 42})
    pub = FakePublic(books={T: {"no_dollars": [["0.4500", "500"]]}})
    write_journal(_setup(env, acct, pub), [quote_row(now - timedelta(minutes=1), "A")])
    await env.runner._sync_journal("demo")
    await env.runner.evaluate_queue_position("demo")
    ep = (await env.store.list_quote_episodes())[0]
    assert ep["queue_ahead_at_join"] == 42 and ep["join_source"] == "kalshi" and ep["kalshi_queue_at_join"] == 42
    assert ep["join_level_size"] == 500.0 and ep["last_obs"]["kq"] == 42 and ep["last_obs"]["lo"] == 42


async def test_reprice_and_stop_outcomes(env):
    now = datetime.now(timezone.utc)
    acct = FakeAccount(resting=[resting("A"), resting("B", ticker="KXDEMOB-X")])
    pub = FakePublic()
    jpath = _setup(env, acct, pub)
    write_journal(jpath, [quote_row(now - timedelta(minutes=20), "A"),
                          quote_row(now - timedelta(minutes=20), "B", 0.42, ticker="KXDEMOB-X")])
    await env.runner._sync_journal("demo")
    await env.runner.evaluate_queue_position("demo")
    # the strategy re-quotes A at a new price (priority reset)
    write_journal(jpath, [cancel_row(now - timedelta(minutes=10)), quote_row(now - timedelta(minutes=10), "A2", 0.46)])
    acct.resting = [resting("A2"), resting("B", ticker="KXDEMOB-X")]
    await env.runner._sync_journal("demo")
    await env.runner.evaluate_queue_position("demo")
    # Stop cancels the resting mm- orders: those episodes end as "stopped"
    res = await env.runner.stop(by="owner")
    assert res["cancelled"] == 2 and set(env.runner._stop_cancelled) == {"A2", "B"}
    await env.runner.evaluate_queue_position("demo")
    out = {e["order_id"]: e["outcome"] for e in await env.store.list_quote_episodes()}
    assert out == {"A": "repriced", "A2": "stopped", "B": "stopped"}
    stats = (await env.runner.refresh_queue_stats())["overall"]
    assert stats["episodes"] == 3 and stats["requote_reset_share"] == round(1 / 3, 4) and stats["fills"] == 0


async def test_gone_without_fresh_fills_waits_then_expires(env):
    now = datetime.now(timezone.utc)

    class NoFills(FakeAccount):
        def get_fills(self, min_ts=None):
            raise RuntimeError("fills down")
    acct = NoFills(resting=[resting("A")])
    write_journal(_setup(env, acct, FakePublic()), [quote_row(now - timedelta(minutes=5), "A")])
    await env.runner._sync_journal("demo")
    await env.runner.evaluate_queue_position("demo")
    acct.resting = []
    await env.runner.evaluate_queue_position("demo")
    ep = (await env.store.list_quote_episodes())[0]
    assert ep["status"] == "open" and ep["gone_utc"] is not None      # maybe filled: wait for the fills sync
    await env.store.upsert_quote_episodes([{"order_id": "A", "gone_utc": now - qp.GONE_GRACE - timedelta(minutes=1)}])
    await env.runner.evaluate_queue_position("demo")
    assert (await env.store.list_quote_episodes())[0]["outcome"] == "expired"


# ---------------------------------------------------------------- caps, failures, budget, Stop
async def test_api_call_caps_per_pass(env):
    now = datetime.now(timezone.utc)
    tickers = [f"KXDEMOA-26OCT13-B{i}" for i in range(60)]
    acct = FakeAccount(resting=[resting(f"O{i}", ticker=t) for i, t in enumerate(tickers)], qpos={})
    pub = FakePublic()
    write_journal(_setup(env, acct, pub), [quote_row(now - timedelta(minutes=1), f"O{i}", ticker=t)
                                           for i, t in enumerate(tickers)])
    await env.runner._sync_journal("demo")
    res = await env.runner.evaluate_queue_position("demo")
    assert res["new"] == 60
    books = [c for c in pub.calls if c[0] == "orderbook"]
    trades = [c for c in pub.calls if c[0] == "trades"]
    qpos = [c for c in acct.calls if c[0] == "request"]
    assert len(books) == qp.MAX_BOOK_READS
    assert len(trades) <= qp.MAX_TRADE_TICKERS * qp.MAX_PAGES
    assert len(qpos) == qp.MAX_QPOS_CALLS
    assert len([c for c in acct.calls if c[0] in ("orders", "fills")]) == 2
    total = len(books) + len(trades) + len(qpos) + 2
    assert total <= qp.MAX_BOOK_READS + qp.MAX_TRADE_TICKERS * qp.MAX_PAGES + qp.MAX_QPOS_CALLS + 2


async def test_kalshi_failures_never_raise(env):
    now = datetime.now(timezone.utc)
    acct = FakeAccount(fail=True)
    write_journal(_setup(env, acct, FakePublic(fail=True)), [quote_row(now - timedelta(minutes=1), "A")])
    await env.runner._sync_journal("demo")
    res = await env.runner.run_queue_position("demo")
    assert "error" not in res and res["new"] == 1 and res.get("errors")
    ep = (await env.store.list_quote_episodes())[0]
    assert ep["status"] == "open" and ep["queue_ahead_at_join"] is None       # nothing known: no guess
    env.runner.kalshi_factory = lambda m: (_ for _ in ()).throw(RuntimeError("factory"))
    res = await env.runner.run_queue_position("demo")
    assert res.get("error") == "RuntimeError"


async def test_store_down_never_raises_and_keeps_rows(env, monkeypatch):
    now = datetime.now(timezone.utc)
    env.runner._capture_queue_rows([quote_row(now, "A")], "demo")

    async def boom(*a, **k):
        raise RuntimeError("atlas down")
    monkeypatch.setattr(env.store, "list_quote_episodes", boom)
    res = await env.runner.run_queue_position("demo")
    assert res["error"] == "RuntimeError" and len(env.runner._qp_rows) == 1
    assert env.runner.queue_stats() is None                                    # store down -> null
    r = await env.client.get("/api/status", headers=env.auth)
    assert r.status_code == 200 and r.json()["queue_stats"] is None


async def test_time_budget(env, monkeypatch):
    now = datetime.now(timezone.utc)
    acct = FakeAccount(resting=[resting("A")])
    write_journal(_setup(env, acct, FakePublic(slow=2.0)), [quote_row(now - timedelta(minutes=1), "A")])
    await env.runner._sync_journal("demo")
    monkeypatch.setattr(runmod, "QUEUE_BUDGET_S", 0.5)
    t0 = time.monotonic()
    res = await env.runner.run_queue_position("demo")
    assert time.monotonic() - t0 < 1.5 and res.get("error") == "TimeoutError"
    assert env.runner.queue_stats() is not None                     # aggregate still refreshed after the budget
    assert (await env.store.list_quote_episodes())[0]["order_id"] == "A"     # journal part was saved first


async def test_runs_outside_pass_lock_and_stop_never_waits(env, monkeypatch):
    now = datetime.now(timezone.utc)
    acct = FakeAccount(resting=[resting("A")])
    jpath = _setup(env, acct, FakePublic(slow=1.0))
    env.runner.public_factory = lambda e: FakePublic(slow=1.0)

    def fake_main():
        write_journal(jpath, [quote_row(datetime.now(timezone.utc), "A")])
        return 0
    monkeypatch.setattr(env.runner.ms, "main", fake_main)
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "demo-id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "demo-key")
    env.runner.last_monitor_utc = now
    env.runner.desired = "running"
    await env.runner._pass("test")
    assert not env.runner._pass_lock.locked() and not env.runner.running_pass
    task = env.runner._qp_task
    assert task is not None and not task.done()
    assert env.runner._start_queue_position("demo") is None          # one at a time
    t0 = time.monotonic()
    res = await env.runner.stop(by="owner")
    assert time.monotonic() - t0 < 0.9 and res["desired"] == "stopped"
    await asyncio.wait_for(task, 5)
    assert (await env.store.list_quote_episodes())[0]["order_id"] == "A"


# ---------------------------------------------------------------- cache + API shape
async def test_cache_and_api_shape(env, monkeypatch):
    r = (await env.client.get("/api/status", headers=env.auth)).json()
    assert "queue_stats" in r and r["queue_stats"] is not None and r["queue_stats"]["episodes"] == 0  # rehydrate
    env.runner.queue_stats_cache = None
    assert (await env.client.get("/api/status", headers=env.auth)).json()["queue_stats"] is None   # never computed
    now = datetime.now(timezone.utc)
    await env.store.upsert_quote_episodes([
        {**qp.episode_from_quote(quote_row(now - timedelta(hours=1), "A"), "demo", ["KXDEMOA"]),
         "queue_ahead_at_join": 0.0, "filled_count": 10, "fill_ids": ["f1"], "front_fill": True,
         "status": "closed", "outcome": "filled"},
        {**qp.episode_from_quote(quote_row(now - timedelta(hours=1), "B", ticker="KXDEMOB-Y"), "demo",
                                 ["KXDEMOB"]), "queue_ahead_at_join": 5000.0},
        {**qp.episode_from_quote(quote_row(now - timedelta(hours=1), "L"), "live", ["KXDEMOA"])}])
    await env.runner.refresh_queue_stats()
    reads = []
    orig = env.store.list_quote_episodes

    async def counting(*a, **k):
        reads.append(1)
        return await orig(*a, **k)
    monkeypatch.setattr(env.store, "list_quote_episodes", counting)
    st = (await env.client.get("/api/status", headers=env.auth)).json()["queue_stats"]
    mk = (await env.client.get("/api/markets", headers=env.auth)).json()
    assert reads == []                                               # served from the cache
    assert set(st) == SHAPE_KEYS and st["episodes"] == 2 and st["fills"] == 1   # live episode excluded in demo
    assert [b["bucket"] for b in st["by_queue"]] == list(qp.BUCKETS)
    assert all(set(b) == BUCKET_KEYS for b in st["by_queue"])
    assert st["median_queue_ahead_at_join"] == 2500.0 and st["front_fill_share"] == 1.0
    assert st["requote_reset_share"] == 0.0                          # 1 closed, filled
    assert mk["queue_stats"] == st
    rows = {m["series"]: m for m in mk["markets"]}
    assert rows["KXDEMOA"]["queue_stats"]["episodes"] == 1 and rows["KXDEMOB"]["queue_stats"]["episodes"] == 1
    assert all(set(m["queue_stats"]) == SHAPE_KEYS for m in mk["markets"])
    env.runner.mode = "live"                                         # mode changed since -> null
    assert (await env.client.get("/api/status", headers=env.auth)).json()["queue_stats"] is None
    assert (await env.client.get("/api/markets", headers=env.auth)).json()["queue_stats"] is None


async def test_aggregate_empty_shape():
    a = qp.aggregate([], {})
    assert set(a) == SHAPE_KEYS and a["episodes"] == 0 and a["fills"] == 0
    assert a["median_queue_ahead_at_join"] is None and a["requote_reset_share"] is None
    assert a["front_fill_share"] is None and len(a["by_queue"]) == 5


# ---------------------------------------------------------------- store
async def test_memory_store_upsert_keeps_immutable_fields():
    s = MemoryStore()
    t0 = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    ep = qp.episode_from_quote(quote_row(t0, "A"), "demo", ["KXDEMOA"])
    assert await s.upsert_quote_episodes([ep]) == 1
    assert await s.upsert_quote_episodes([{**ep, "ts": t0 + timedelta(days=1), "price": 0.5, "ticker": "X",
                                           "status": "closed", "outcome": "filled"}]) == 0
    got = (await s.list_quote_episodes())[0]
    assert got["ts"] == t0 and got["price"] == 0.45 and got["ticker"] == T
    assert got["status"] == "closed" and got["outcome"] == "filled"
    await s.upsert_quote_episodes([{**qp.episode_from_quote(quote_row(t0 - timedelta(days=2), "B"), "live", [])}])
    assert [e["order_id"] for e in await s.list_quote_episodes()] == ["A", "B"]          # newest first
    assert [e["order_id"] for e in await s.list_quote_episodes(env="live")] == ["B"]
    assert [e["order_id"] for e in await s.list_quote_episodes(status="open")] == ["B"]
    assert [e["order_id"] for e in await s.list_quote_episodes(since=t0 - timedelta(days=1))] == ["A"]
    assert await s.list_quote_episodes(env="demo", fields=("status", "outcome")) ==         [{"status": "closed", "outcome": "filled"}]                                       # projection
    with pytest.raises(ValueError):
        await s.upsert_quote_episodes([{"ts": t0}])


async def test_mongo_indexes_ttl_and_atomic_upsert_offline():
    from control_api.db.mongo import MongoStore

    class Coll:
        def __init__(self, rec, name):
            self.rec, self.name = rec, name

        async def create_index(self, keys, **kw):
            self.rec.append((self.name, "create_index", keys, kw))

        async def bulk_write(self, ops, ordered=True):
            self.rec.append((self.name, "bulk_write", ops, ordered))

            class R:
                upserted_count = len(ops)
            return R()

    class DB:
        def __init__(self):
            self.rec = []

        def __getitem__(self, name):
            return Coll(self.rec, name)

    class Client:
        def __init__(self):
            self.db = DB()

        def __getitem__(self, name):
            return self.db

    c = Client()
    ms = MongoStore("", client=c)
    await ms.ensure_indexes()
    idx = [(r[2], r[3]) for r in c.db.rec if r[0] == S.QUOTE_EPISODES and r[1] == "create_index"]
    assert ([("order_id", 1)], {"unique": True, "name": "order_id_unique"}) in idx
    ttl = [(k, kw) for k, kw in idx if "expireAfterSeconds" in kw]
    assert ttl == [([("ts", 1)], {"expireAfterSeconds": 45 * 24 * 3600, "name": "ts_ttl"})]
    assert S.QUOTE_EPISODES in S.COLLECTIONS
    ep = qp.episode_from_quote(quote_row(datetime(2026, 10, 13, tzinfo=timezone.utc), "A"), "demo", [])
    assert await ms.upsert_quote_episodes([ep]) == 1
    op = [r for r in c.db.rec if r[1] == "bulk_write"][0][2][0]
    doc = op._doc
    assert op._filter == {"order_id": "A"} and op._upsert is True
    assert set(doc["$setOnInsert"]) == set(S.QUOTE_EPISODE_IMMUTABLE) - {"order_id", "close_time"}   # close_time: only when known
    assert not set(doc["$set"]) & set(doc["$setOnInsert"]) and "status" in doc["$set"]
