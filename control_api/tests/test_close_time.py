"""Market close time (read-only telemetry): set on new quote episodes (immutable), on fills and settlements; cache
bounds/TTL; Kalshi failures never raise; no extra calls when market data already has it; Stop unaffected;
MemoryStore / MongoStore parity. Fakes only: no network, no Kalshi, no Atlas (a real Mongo is used only if
TEST_MONGODB_URI is set)."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from control_api import runner as runmod
from control_api.db import MemoryStore
from control_api.db import schemas as S
from control_api.services import closetime as ct
from control_api.services import queuepos as qp

from .test_queue_position import FakeAccount, FakePublic, T, quote_row, resting, write_journal

pytestmark = pytest.mark.asyncio
CLOSE = datetime(2026, 10, 14, 4, 59, tzinfo=timezone.utc)
CLOSE2 = datetime(2026, 10, 15, 4, 59, tzinfo=timezone.utc)
T2 = "KXDEMOB-26OCT13-B60"


class MarketPublic(FakePublic):
    """FakePublic + GET /markets/{t} (get_market) and get_markets; records every market call."""

    def __init__(self, closes=None, fail_market=False, slow_market=0.0, field="close_time", **kw):
        super().__init__(**kw)
        self.closes = dict(closes or {})
        self.fail_market, self.slow_market, self.field = fail_market, slow_market, field
        self.market_calls: list[str] = []

    def get_market(self, ticker):
        self.market_calls.append(ticker)
        if self.slow_market:
            time.sleep(self.slow_market)
        if self.fail_market:
            raise RuntimeError("kalshi 503 api_key=SECRET")
        c = self.closes.get(ticker)
        return {"ticker": ticker, self.field: c.isoformat().replace("+00:00", "Z") if c else None}

    def get_markets(self, **kw):
        return [{"ticker": t, "event_ticker": t.rsplit("-", 1)[0], "close_time": c.isoformat()}
                for t, c in self.closes.items()]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- parsing
async def test_parsing_and_ticker_validation():
    assert ct.from_market({"close_time": "2026-10-14T04:59:00Z"}) == CLOSE
    assert ct.from_market({"expected_expiration_time": "2026-10-14T04:59:00Z"}) == CLOSE      # fallback
    assert ct.from_market({"close_time": "2026-10-14T04:59:00Z", "expected_expiration_time": "2027-01-01T00:00Z"}) \
        == CLOSE                                                                                # close_time first
    assert ct.from_market({"close_time": None}) is None and ct.from_market("x") is None
    assert ct.from_market({"close_time": "garbage"}) is None
    assert ct.to_utc(datetime(2026, 10, 14, 4, 59)).tzinfo is not None                        # naive -> UTC
    assert ct.to_utc(True) is None
    assert ct.valid_ticker(T) and not ct.valid_ticker("../portfolio/orders") and not ct.valid_ticker("A?b=1")
    assert not ct.valid_ticker("") and not ct.valid_ticker(None)


# ---------------------------------------------------------------- cache
async def test_cache_lru_bound_and_ttls():
    clk = Clock()
    c = ct.CloseTimeCache(maxsize=3, hit_ttl=100, miss_ttl=10, clock=clk)
    for i in range(5):
        c.put("demo", f"T{i}", CLOSE)
    assert len(c) == 3 and c.lookup("demo", "T0") == (False, None) and c.get("demo", "T4") == CLOSE   # LRU evicted
    c.get("demo", "T2")                    # touch T2 -> T3 is now the oldest
    c.put("demo", "T5", CLOSE)
    assert c.lookup("demo", "T3") == (False, None) and c.get("demo", "T2") == CLOSE
    c.put("demo", "M", None)               # a miss is cached briefly
    assert c.lookup("demo", "M") == (True, None)
    clk.t += 11
    assert c.lookup("demo", "M") == (False, None)            # miss TTL over: may be fetched again
    assert c.get("demo", "T2") == CLOSE
    clk.t += 100
    assert c.lookup("demo", "T2") == (False, None)           # hit TTL over
    c.seed("demo", "S", CLOSE)
    c.seed("demo", "S", None)                                # a None never overwrites a known value
    assert c.get("demo", "S") == CLOSE
    assert c.get("live", "S") is None                        # keyed per env
    c.put("demo", "../x", CLOSE)
    assert c.lookup("demo", "../x") == (False, None)         # invalid tickers are never cached


@pytest.mark.slow
async def test_resolve_bounds_dedupes_and_failures_are_non_fatal():
    pub = MarketPublic(closes={f"T{i}": CLOSE for i in range(10)})
    c = ct.CloseTimeCache()
    wanted = [("demo", f"T{i}") for i in range(10)] + [("demo", "T0"), ("demo", "bad/ticker")]
    out = c.resolve(lambda env: pub, wanted, max_fetch=3)
    assert len(out) == 3 and pub.market_calls == ["T0", "T1", "T2"] and c.fetches == 3        # bounded per run
    out = c.resolve(lambda env: pub, wanted, max_fetch=3)
    assert len(out) == 6 and pub.market_calls[3:] == ["T3", "T4", "T5"]                         # cache first
    bad = MarketPublic(fail_market=True)
    c2 = ct.CloseTimeCache()
    assert c2.resolve(lambda env: bad, [("demo", T)], max_fetch=5) == {} and c2.errors == 1
    assert c2.resolve(lambda env: bad, [("demo", T)], max_fetch=5) == {} and len(bad.market_calls) == 1  # miss TTL
    boom = ct.CloseTimeCache()
    assert boom.resolve(lambda env: (_ for _ in ()).throw(RuntimeError("factory")), [("demo", T)], max_fetch=5) == {}
    assert boom.resolve(None, [("demo", T)], max_fetch=5) == {}
    # deadline: stops before the cap
    slow = MarketPublic(closes={f"T{i}": CLOSE for i in range(5)}, slow_market=0.2)
    c3 = ct.CloseTimeCache()
    t0 = time.monotonic()
    c3.resolve(lambda env: slow, [("demo", f"T{i}") for i in range(5)], max_fetch=5, deadline=t0 + 0.3)
    assert len(slow.market_calls) < 5 and time.monotonic() - t0 < 1.0


async def test_request_fallback_without_get_market():
    class ReqOnly:
        def __init__(self):
            self.paths = []

        def request(self, method, endpoint, params=None, **kw):
            self.paths.append((method, endpoint, kw.get("auth")))
            return {"market": {"ticker": T, "close_time": "2026-10-14T04:59:00Z"}}
    r = ReqOnly()
    assert ct.fetch_market_close(r, T) == CLOSE and r.paths == [("GET", f"/markets/{T}", None)]   # public, read-only
    assert ct.fetch_market_close(r, "a/b") is None and len(r.paths) == 1


async def test_recording_public_seeds_cache_and_passes_through():
    pub = MarketPublic(closes={T: CLOSE, T2: CLOSE2})
    c = ct.CloseTimeCache()
    rec = ct.RecordingPublic(pub, c, "demo")
    rows = rec.get_markets(series_ticker="KXDEMOA", status="open")
    assert rows == pub.get_markets()                          # unchanged for the caller
    assert rec.get_orderbook(T) == {"orderbook_fp": {}}       # everything else delegated
    assert c.get("demo", T) == CLOSE and c.get("demo", T2) == CLOSE2
    out = c.resolve(lambda env: pub, [("demo", T), ("demo", T2)], max_fetch=5)
    assert out == {("demo", T): CLOSE, ("demo", T2): CLOSE2} and pub.market_calls == []   # no extra calls

    class Weird:
        def get_markets(self, **kw):
            return "not a list"
    assert ct.RecordingPublic(Weird(), c, "demo").get_markets() == "not a list"


# ---------------------------------------------------------------- runner: episodes, fills, settlements
def _setup(env, acct, pub):
    env.runner.kalshi_factory = lambda mode: acct
    env.runner.public_factory = lambda e: pub
    return env.paths / "journal.csv"


async def test_new_episode_gets_close_time_on_insert_and_it_is_immutable(env):
    now = datetime.now(timezone.utc)
    pub = MarketPublic(closes={T: CLOSE}, books={T: {"no_dollars": [["0.4500", "20"]]}})
    jpath = _setup(env, FakeAccount(resting=[resting("A")]), pub)
    write_journal(jpath, [quote_row(now - timedelta(minutes=5), "A")])
    await env.runner._sync_journal("demo")
    res = await env.runner.evaluate_queue_position("demo")
    assert res["new"] == 1 and "error" not in res
    ep = (await env.store.list_quote_episodes())[0]
    assert ep["close_time"] == CLOSE and ep["close_time"].tzinfo is not None
    assert pub.market_calls == [T]
    # a later write (and the next evaluation) can't move it
    await env.store.upsert_quote_episodes([{**ep, "close_time": CLOSE2, "status": "open"}])
    assert not await env.store.set_quote_episode_close_time("A", CLOSE2)
    pub.closes[T] = CLOSE2
    env.runner.close_times = ct.CloseTimeCache()
    await env.runner.evaluate_queue_position("demo")
    await env.runner.backfill_close_times("demo")
    assert (await env.store.list_quote_episodes())[0]["close_time"] == CLOSE
    # a second quote on the same ticker: from the cache, no extra call
    calls = len(pub.market_calls)
    write_journal(jpath, [quote_row(now - timedelta(minutes=1), "B")])
    await env.runner._sync_journal("demo")
    await env.runner.evaluate_queue_position("demo")
    eps = {e["order_id"]: e for e in await env.store.list_quote_episodes()}
    assert eps["B"]["close_time"] == CLOSE and len(pub.market_calls) == calls     # cache (seeded from the store)


async def test_kalshi_failure_is_non_fatal_and_backfill_fills_it_later(env):
    now = datetime.now(timezone.utc)
    pub = MarketPublic(closes={T: CLOSE}, fail_market=True, books={T: {"no_dollars": [["0.4500", "20"]]}})
    jpath = _setup(env, FakeAccount(resting=[resting("A")]), pub)
    write_journal(jpath, [quote_row(now - timedelta(minutes=5), "A")])
    await env.runner._sync_journal("demo")
    res = await env.runner.evaluate_queue_position("demo")
    assert res["new"] == 1 and "error" not in res                              # the episode is still recorded
    ep = (await env.store.list_quote_episodes())[0]
    assert "close_time" not in ep and ep["queue_ahead_at_join"] == 10.0        # rest of the telemetry unaffected
    # recovery: Kalshi back, miss TTL over -> the backfill sets it once
    pub.fail_market = False
    env.runner.close_times = ct.CloseTimeCache()
    out = await env.runner.backfill_close_times("demo")
    assert out["episodes"] == 1 and "error" not in out
    assert (await env.store.list_quote_episodes())[0]["close_time"] == CLOSE


@pytest.mark.slow
async def test_backfill_fills_and_settlements_bounded_and_store_values_reused(env, monkeypatch):
    now = datetime.now(timezone.utc)
    await env.store.upsert_fills(
        [{"fill_id": f"f{i}", "ticker": f"KXDEMOA-26OCT13-B{i}", "env": "demo", "ts": now - timedelta(hours=1),
          "is_taker": i % 2 == 0} for i in range(15)]
        + [{"fill_id": "known", "ticker": T2, "env": "demo", "ts": now, "close_time": CLOSE2},
           {"fill_id": "same", "ticker": T2, "env": "demo", "ts": now}])
    await env.store.upsert_settlements([{"ticker": T, "settled_time": (now - timedelta(hours=2)).isoformat(),
                                         "market_result": "no"}])
    closes = {f"KXDEMOA-26OCT13-B{i}": CLOSE for i in range(15)}
    pub = MarketPublic(closes={**closes, T: CLOSE})
    env.runner.public_factory = lambda e: pub
    monkeypatch.setattr(ct, "MAX_FETCH_BACKFILL", 4)
    out = await env.runner.backfill_close_times("demo")
    assert out["fetches"] == 4 and len(pub.market_calls) == 4                    # bounded per run
    fills = {f["fill_id"]: f for f in await env.store.list_fills(limit=100)}
    assert fills["same"]["close_time"] == CLOSE2                                 # from the store, no call
    assert T2 not in pub.market_calls
    for _ in range(5):
        await env.runner.backfill_close_times("demo")
    fills = {f["fill_id"]: f for f in await env.store.list_fills(limit=100)}
    assert all(f["close_time"] == (CLOSE2 if f["ticker"] == T2 else CLOSE) for f in fills.values())   # maker + taker
    st = (await env.store.list_settlements())[0]
    assert st["close_time"] == CLOSE
    n = len(pub.market_calls)
    assert (await env.runner.backfill_close_times("demo"))["fetches"] == 0 and len(pub.market_calls) == n
    # a re-sync of the same Kalshi rows (no close_time) never erases it
    await env.store.upsert_fills([{"fill_id": "f1", "ticker": "KXDEMOA-26OCT13-B1", "env": "demo",
                                   "ts": now - timedelta(hours=1), "close_time": None}])
    await env.store.upsert_settlements([{"ticker": T, "market_result": "no", "close_time": None}])
    assert (await env.store.list_fills(fill_ids=["f1"]))[0]["close_time"] == CLOSE
    assert (await env.store.list_settlements())[0]["close_time"] == CLOSE


async def test_sync_fills_uses_kalshi_close_time_or_cache_without_calls(env):
    now = datetime.now(timezone.utc)

    class Acct(FakeAccount):
        def get_settlements(self, min_ts=None):
            return [{"ticker": T, "settled_time": now.isoformat(), "close_time": "2026-10-14T04:59:00Z"},
                    {"ticker": T2, "settled_time": now.isoformat()}]
    acct = Acct(fills=[{"fill_id": "x", "ticker": T2, "ts": now.isoformat(), "order_id": "o"}])
    pub = MarketPublic()
    _setup(env, acct, pub)
    env.runner.close_times.seed("demo", T2, CLOSE2)
    res = await env.runner.sync_fills("demo", force=True)
    assert res["synced"] is True
    sts = {s["ticker"]: s for s in await env.store.list_settlements()}
    assert sts[T]["close_time"] == CLOSE and isinstance(sts[T]["close_time"], datetime)   # Kalshi's, as UTC
    assert sts[T2]["close_time"] == CLOSE2
    assert (await env.store.list_fills())[0]["close_time"] == CLOSE2
    assert pub.market_calls == []                                                          # no calls in the pass
    assert env.runner.close_times.get("demo", T) == CLOSE                                  # learned from Kalshi


async def test_arb_scan_records_close_times_at_no_extra_cost(env, monkeypatch):
    pub = MarketPublic(closes={T: CLOSE})
    env.runner.public_factory = lambda e: pub
    from control_api.services import arb as arbsvc
    sc = arbsvc.scanner()
    seen = {}

    def scan_once(p, client, series, mx, mn):
        seen["rows"] = p.get_markets(series_ticker="KXDEMOA", status="open")
        return []
    monkeypatch.setattr(sc, "scan_once", scan_once)
    env.runner._arb_scan_sync({"arb_series": ["KXDEMOA"], "arb_max_contracts": 1, "arb_min_profit": 0.01},
                              False, env.runner._stop_gen, "demo")
    assert seen["rows"][0]["ticker"] == T and env.runner.close_times.get("demo", T) == CLOSE
    assert pub.market_calls == []


# ---------------------------------------------------------------- safety
@pytest.mark.slow
async def test_stop_is_unaffected_by_a_hanging_close_time_lookup(env, kalshi):
    now = datetime.now(timezone.utc)
    pub = MarketPublic(closes={T: CLOSE}, slow_market=1.5)
    env.runner.public_factory = lambda e: pub
    await env.store.upsert_fills([{"fill_id": "f", "ticker": T, "env": "demo", "ts": now}])
    task = env.runner._start_close_time("demo")
    assert env.runner._start_close_time("demo") is None                 # one at a time
    await asyncio.sleep(0.1)
    t0 = time.monotonic()
    res = await env.runner.stop(by="owner")
    assert time.monotonic() - t0 < 0.9 and res["desired"] == "stopped" and res["errors"] == []
    assert sorted(kalshi.cancelled) == ["o1", "o3"]                      # Stop's cancel ran as usual
    out = await asyncio.wait_for(task, 10)
    assert out.get("fills") == 1


async def test_backfill_budget_and_store_outage_never_raise(env, monkeypatch, caplog):
    class Broken:
        def __getattr__(self, name):
            async def boom(*a, **k):
                raise RuntimeError("atlas down mongodb+srv://user:pw@x")
            return boom
    env.runner.store = Broken()
    out = await env.runner.backfill_close_times("demo")
    assert out["error"] == "RuntimeError"
    env.runner.store = MemoryStore()
    await env.runner.store.upsert_fills([{"fill_id": "f", "ticker": T, "env": "demo",
                                          "ts": datetime.now(timezone.utc)}])
    env.runner.public_factory = lambda e: MarketPublic(closes={T: CLOSE}, slow_market=3)
    monkeypatch.setattr(runmod, "CLOSE_TIME_BUDGET_S", 0.5)
    t0 = time.monotonic()
    res = await env.runner.run_close_time("demo")
    # bounded either way: the outer budget fires ("error") or the inner deadline lapses first ("pending"), a
    # race that depends on machine load (seen under xdist); both mean "gave up quickly, rest next pass"
    assert time.monotonic() - t0 < 2 and ("error" in res or res.get("pending", 0) >= 1)
    assert "pw@" not in caplog.text and "SECRET" not in caplog.text


async def test_pass_starts_backfill_outside_the_lock(env, monkeypatch):
    pub = MarketPublic(closes={T: CLOSE}, books={T: {"no_dollars": [["0.4500", "20"]]}})
    jpath = _setup(env, FakeAccount(resting=[resting("A")]), pub)

    def fake_main():
        write_journal(jpath, [quote_row(datetime.now(timezone.utc), "A")])
        return 0
    monkeypatch.setattr(env.runner.ms, "main", fake_main)
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "demo-id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "demo-key")
    env.runner.last_monitor_utc = datetime.now(timezone.utc)
    env.runner.desired = "running"
    await env.runner._pass("test")
    assert not env.runner._pass_lock.locked() and env.runner._ct_task is not None
    await asyncio.wait_for(asyncio.gather(env.runner._qp_task, env.runner._ct_task), 15)
    ep = (await env.store.list_quote_episodes())[0]
    assert ep["close_time"] == CLOSE and pub.market_calls == [T]       # the backfill reused the cached value


# ---------------------------------------------------------------- store parity (MemoryStore / MongoStore)
class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *a, **k):
        self.docs.sort(key=lambda d: d.get("ts"), reverse=True)
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def to_list(self, *a):
        return self.docs


def _match(doc, flt):
    for k, v in flt.items():
        if isinstance(v, dict) and set(v) <= {"$gte", "$lt", "$in"}:
            x = doc.get(k)
            if "$gte" in v and (x is None or x < v["$gte"]):
                return False
            if "$lt" in v and (x is None or x >= v["$lt"]):
                return False
            if "$in" in v and x not in v["$in"]:
                return False
        elif v is None:
            if doc.get(k) is not None:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _Res:
    def __init__(self, matched=0, modified=0, upserted=0):
        self.matched_count, self.modified_count, self.upserted_count = matched, modified, upserted


class FakeColl:
    """The few Mongo operators the close-time paths use, with Mongo's semantics."""

    def __init__(self):
        self.docs: list[dict] = []
        self.ops: list = []

    def _update(self, flt, upd, upsert):
        hit = [d for d in self.docs if _match(d, flt)]
        if hit:
            d = hit[0]
            before = dict(d)
            d.update(upd.get("$set", {}))
            return _Res(1, int(before != d))
        if upsert:
            d = {k: v for k, v in flt.items() if not isinstance(v, dict)}
            d.update(upd.get("$setOnInsert", {}))
            d.update(upd.get("$set", {}))
            self.docs.append(d)
            return _Res(0, 0, 1)
        return _Res()

    async def bulk_write(self, ops, ordered=True):
        self.ops.extend(ops)
        n = 0
        for op in ops:
            n += self._update(op._filter, op._doc, op._upsert).upserted_count
        return _Res(upserted=n)

    async def update_one(self, flt, upd, upsert=False):
        self.ops.append(("update_one", flt, upd))
        return self._update(flt, upd, upsert)

    async def find_one(self, flt, proj=None):
        hit = [d for d in self.docs if _match(d, flt)]
        return dict(hit[0]) if hit else None

    def find(self, flt, proj=None):
        docs = [dict(d) for d in self.docs if _match(d, flt)]
        if proj:
            keep = {k for k, v in proj.items() if v and k != "_id"}
            if keep:
                docs = [{k: v for k, v in d.items() if k in keep} for d in docs]
        return _Cursor(docs)

    async def create_index(self, *a, **k):
        return None


class FakeMongoClient:
    def __init__(self):
        self.colls: dict[str, FakeColl] = {}

        class DB:
            def __getitem__(db, name):
                return self.colls.setdefault(name, FakeColl())
        self._db = DB()

    def __getitem__(self, name):
        return self._db


_params = ["memory", "mongo-fake"] + (["mongo"] if os.environ.get("TEST_MONGODB_URI") else [])


@pytest_asyncio.fixture(params=_params)
async def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    from control_api.db.mongo import MongoStore
    if request.param == "mongo-fake":
        s = MongoStore("", client=FakeMongoClient())
        s._fake = True
        yield s
        return
    s = MongoStore(os.environ["TEST_MONGODB_URI"], "kalshi_test_" + uuid.uuid4().hex[:8])
    await s.ensure_indexes()
    yield s
    await s.client.drop_database(s.db.name)
    await s.close()


def _ep(oid, close=None, ts=None):
    ep = qp.episode_from_quote(quote_row(ts or datetime.now(timezone.utc) - timedelta(minutes=5), oid),
                               "demo", ["KXDEMOA"])
    if close is not None:
        ep["close_time"] = close
    return ep


async def test_store_parity_episode_close_time(store):
    assert await store.upsert_quote_episodes([_ep("A", CLOSE), _ep("B"), {**_ep("C"), "close_time": None}]) == 3
    eps = {e["order_id"]: e for e in await store.list_quote_episodes()}
    assert eps["A"]["close_time"] == CLOSE and eps["A"]["close_time"].tzinfo is not None
    assert "close_time" not in eps["B"] and "close_time" not in eps["C"]          # never stored as null
    # immutable on later upserts, including an explicit None
    await store.upsert_quote_episodes([{**_ep("A", CLOSE2), "status": "closed"}, {**_ep("A"), "close_time": None}])
    a = [e for e in await store.list_quote_episodes() if e["order_id"] == "A"][0]
    assert a["close_time"] == CLOSE and a["status"] == "open"   # second write re-set status: still a $set field
    # an upsert can't add it after the insert; the one-time setter can, once
    await store.upsert_quote_episodes([_ep("B", CLOSE2)])
    assert "close_time" not in [e for e in await store.list_quote_episodes() if e["order_id"] == "B"][0]
    assert await store.set_quote_episode_close_time("B", "2026-10-15T04:59:00Z") is True
    assert await store.set_quote_episode_close_time("B", CLOSE) is False             # already set
    assert await store.set_quote_episode_close_time("A", CLOSE2) is False
    assert await store.set_quote_episode_close_time("nope", CLOSE) is False          # no upsert
    assert await store.set_quote_episode_close_time("C", None) is False
    eps = {e["order_id"]: e for e in await store.list_quote_episodes()}
    assert eps["A"]["close_time"] == CLOSE and eps["B"]["close_time"] == CLOSE2 and "close_time" not in eps["C"]
    assert "nope" not in eps
    if getattr(store, "_fake", False):
        bw = [op for op in store.db[S.QUOTE_EPISODES].ops if not isinstance(op, tuple)]
        assert "close_time" in bw[0]._doc["$setOnInsert"] and "close_time" not in bw[0]._doc.get("$set", {})
        assert "close_time" not in bw[1]._doc["$setOnInsert"]                       # unknown -> not frozen as null
        one = [op for op in store.db[S.QUOTE_EPISODES].ops if isinstance(op, tuple)][0]
        assert one[1] == {"order_id": "B", "close_time": None}                       # only while missing


async def test_store_parity_fill_and_settlement_close_time(store):
    now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    await store.upsert_fills([{"fill_id": "f", "ticker": T, "ts": now, "close_time": "2026-10-14T04:59:00Z"},
                              {"fill_id": "g", "ticker": T, "ts": now, "close_time": None}])
    fs = {f["fill_id"]: f for f in await store.list_fills(since=now - timedelta(days=1))}
    assert fs["f"]["close_time"] == CLOSE and "close_time" not in fs["g"]
    assert await store.set_fill_fields("g", {"close_time": CLOSE2}) is True
    await store.upsert_fills([{"fill_id": "g", "ticker": T, "ts": now}])           # Kalshi re-sync: kept
    assert (await store.list_fills(since=now - timedelta(days=1), fill_ids=["g"]))[0]["close_time"] == CLOSE2
    await store.upsert_settlements([{"ticker": T, "settled_time": now.isoformat(), "close_time": None}])
    assert "close_time" not in (await store.list_settlements(since=now - timedelta(days=1)))[0]
    assert await store.set_settlement_fields(T, {"close_time": CLOSE, "ticker": "X", "ts": None}) is True
    assert await store.set_settlement_fields("missing", {"close_time": CLOSE}) is False
    assert await store.set_settlement_fields(T, {"close_time": None}) is True       # exists; null not written
    await store.upsert_settlements([{"ticker": T, "settled_time": now.isoformat(), "market_result": "no"}])
    s = (await store.list_settlements(since=now - timedelta(days=1)))[0]
    assert s["close_time"] == CLOSE and s["ticker"] == T and s["ts"] == now and s["market_result"] == "no"


async def test_schema_docs_and_immutables():
    assert "close_time" in S.QUOTE_EPISODE_IMMUTABLE and "close_time" in S.QUOTE_EPISODE_DOC
    assert "close_time" in S.FILL_DOC and "close_time" in S.SETTLEMENT_DOC
    assert "close_time" not in S.prepare_quote_episode({"order_id": "x", "close_time": "garbage"})


# ---------------------------------------------------------------- S-31: dedicated executor, short client, ticker regex
class HangingPublic(MarketPublic):
    """get_market blocks until released (a hung Kalshi GET); records the worker thread names."""

    def __init__(self, release, **kw):
        super().__init__(**kw)
        self.release = release
        self.threads: list[str] = []

    def get_market(self, ticker):
        import threading
        self.threads.append(threading.current_thread().name)
        self.market_calls.append(ticker)
        self.release.wait(10)
        return {"ticker": ticker, "close_time": CLOSE.isoformat()}


class SpyExecutor(__import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor):
    """Default-executor spy: a 1-worker pool (the worst case for Stop) that counts submissions."""

    def __init__(self):
        super().__init__(max_workers=1, thread_name_prefix="default-spy")
        self.calls = 0

    def submit(self, fn, *a, **k):
        self.calls += 1
        return super().submit(fn, *a, **k)


async def test_s31_ticker_regex_rejects_dot_segments():
    for bad in (".", "..", "...", ".hidden", "-x", "_x", "A\n", "A/B", "A?b", "A%2F", "A B", "x" * 129, ""):
        assert not ct.valid_ticker(bad), bad
    for ok in (T, T2, "KXDEMOF-26OCT131905ABCXYZ-ABC", "A", "a.b-c_d", "9" * 128):
        assert ct.valid_ticker(ok), ok
    pub = MarketPublic(closes={".": CLOSE})
    assert ct.CloseTimeCache().resolve(lambda e: pub, [("demo", ".."), ("demo", ".")], max_fetch=5) == {}
    assert pub.market_calls == []


async def test_s31_close_time_client_has_no_retries_and_short_timeout(env):
    c = runmod.closetime_public_factory("demo")               # constructing a client makes no request
    assert c.max_retries == 0 and c.timeout == 5.0 and not c.has_credentials
    assert runmod.default_public_factory("demo").max_retries == 2        # the arb/telemetry client is unchanged
    env.runner.public_factory = runmod.default_public_factory
    assert env.runner._ct_factory("demo").max_retries == 0                # default path -> the short client
    fake = MarketPublic()
    env.runner.public_factory = lambda e: fake
    assert env.runner._ct_factory("demo") is fake                         # injected factory (tests) is honoured


async def test_s31_close_time_work_never_uses_the_default_executor(env):
    loop = asyncio.get_running_loop()
    spy = SpyExecutor()
    loop.set_default_executor(spy)
    try:
        pub = MarketPublic(closes={T: CLOSE}, books={T: {"no_dollars": [["0.4500", "20"]]}})
        names = []
        orig = pub.get_market

        def get_market(t):
            import threading
            names.append(threading.current_thread().name)
            return orig(t)
        pub.get_market = get_market
        env.runner.public_factory = lambda e: pub
        ep = _ep("A")
        await env.runner._close_times_for_new_episodes([ep])
        assert ep["close_time"] == CLOSE
        await env.store.upsert_fills([{"fill_id": "f", "ticker": T2, "env": "demo", "ts": datetime.now(timezone.utc)}])
        pub.closes[T2] = CLOSE2
        out = await env.runner.backfill_close_times("demo")
        assert out["fills"] == 1
        assert spy.calls == 0                                             # nothing went to the default pool
        assert names and all(n.startswith("closetime") for n in names)
    finally:
        spy.shutdown(wait=False, cancel_futures=True)


@pytest.mark.slow
async def test_s31_hanging_lookups_do_not_block_stop_cancel(env, kalshi, monkeypatch):
    import threading
    loop = asyncio.get_running_loop()
    spy = SpyExecutor()                    # default pool of ONE worker: any lingering telemetry thread would block Stop
    loop.set_default_executor(spy)
    release = threading.Event()
    try:
        pub = HangingPublic(release, closes={})
        env.runner.public_factory = lambda e: pub
        now = datetime.now(timezone.utc)
        await env.store.upsert_fills([{"fill_id": f"f{i}", "ticker": f"KXDEMOA-26OCT13-B{i}", "env": "demo",
                                       "ts": now} for i in range(10)])
        monkeypatch.setattr(runmod, "CLOSE_TIME_BUDGET_S", 0.6)
        monkeypatch.setattr(runmod, "CLOSE_TIME_INSERT_TIMEOUT_S", 0.3)
        # 10 lookups abandoned mid-GET: backfills plus new-episode lookups, all timed out
        for i in range(5):
            res = await env.runner.run_close_time("demo")
            assert "error" in res
            await env.runner._close_times_for_new_episodes([_ep(f"N{i}")])
            env.runner.close_times = ct.CloseTimeCache()
        assert pub.threads and all(n.startswith("closetime") for n in pub.threads)
        assert len(set(pub.threads)) <= ct.EXECUTOR_WORKERS               # bounded: queued work was cancelled
        t0 = time.monotonic()
        res = await env.runner.stop(by="owner")
        assert time.monotonic() - t0 < 1.0 and res["errors"] == [] and res["cancelled"] == 2
        # CA-2: Stop's cancel now runs on its own pool, so it never even touches the default one
        assert sorted(kalshi.cancelled) == ["o1", "o3"] and spy.calls == 0
    finally:
        release.set()
        spy.shutdown(wait=False, cancel_futures=True)


async def test_s31_shutdown_closes_the_close_time_executor(env):
    await env.runner.shutdown()
    with pytest.raises(RuntimeError):
        env.runner._ct_executor.submit(lambda: None)
    assert await env.runner.backfill_close_times("demo") is not None      # still never raises afterwards
