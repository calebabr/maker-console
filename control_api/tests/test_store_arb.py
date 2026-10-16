"""Store additions (2026-10-13): list_heartbeats, arb state fields, arb opportunity log, fills windows,
settlements; plus offline checks of MongoStore index definitions and query shapes (no real DB)."""
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from control_api.db import MemoryStore
from control_api.db import schemas as S
from control_api.db.seed import seed

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

_params = ["memory"] + (["mongo"] if os.environ.get("TEST_MONGODB_URI") else [])


@pytest_asyncio.fixture(params=_params)
async def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    from control_api.db.mongo import MongoStore
    s = MongoStore(os.environ["TEST_MONGODB_URI"], "kalshi_test_" + uuid.uuid4().hex[:8])
    await s.ensure_indexes()
    yield s
    await s.client.drop_database(s.db.name)
    await s.close()


# ---------------------------------------------------------------- heartbeats
async def test_list_heartbeats_since_limit_newest_first(store):
    for i in range(5):
        await store.add_heartbeat({"ts": NOW + timedelta(minutes=10 * i), "ok": True, "quotes": i})
    hb = await store.list_heartbeats()
    assert [h["quotes"] for h in hb] == [4, 3, 2, 1, 0]
    assert all("_id" not in h and h["ts"].tzinfo is not None for h in hb)
    hb = await store.list_heartbeats(since=NOW + timedelta(minutes=20))
    assert [h["quotes"] for h in hb] == [4, 3, 2]                 # inclusive
    assert [h["quotes"] for h in await store.list_heartbeats(limit=2)] == [4, 3]
    assert await store.list_heartbeats(limit=0) == []


async def test_list_heartbeats_returns_copies(store):
    await store.add_heartbeat({"ts": NOW, "ok": True})
    (await store.list_heartbeats())[0]["ok"] = False
    assert (await store.list_heartbeats())[0]["ok"] is True


# ---------------------------------------------------------------- state: arb fields
async def test_state_has_arb_fields_with_safe_defaults(store):
    st = await store.set_state(by="a", desired="running")
    assert st["arb_desired"] == "stopped" and st["arb_mode"] == "demo"
    assert set(st) == set(S.STATE_FIELDS)
    st = await store.set_state(by="a", arb_desired="running", arb_mode="live")
    assert st["desired"] == "running" and st["mode"] == "demo"
    assert st["arb_desired"] == "running" and st["arb_mode"] == "live"
    assert await store.get_state() == st


async def test_stop_both_in_one_write(store):
    await store.set_state(by="a", desired="running", arb_desired="running", arb_mode="live")
    st = await store.set_state(by="stop", desired="stopped", arb_desired="stopped", arb_mode="demo")
    assert (st["desired"], st["arb_desired"], st["arb_mode"], st["updated_by"]) == ("stopped", "stopped", "demo", "stop")


async def test_invalid_arb_state_rejected_and_nothing_written(store):
    await store.set_state(by="a", desired="running")
    with pytest.raises(ValueError):
        await store.set_state(by="a", desired="stopped", arb_desired="go")
    with pytest.raises(ValueError):
        await store.set_state(by="a", arb_mode="paper")
    assert (await store.get_state())["desired"] == "running"        # validation precedes any write


async def test_old_state_doc_without_arb_fields_gets_defaults():
    s = MemoryStore()
    s._state = {"desired": "running", "mode": "live", "updated_utc": NOW, "updated_by": "x"}   # pre-arb doc
    st = await s.get_state()
    assert st["arb_desired"] == "stopped" and st["arb_mode"] == "demo" and st["mode"] == "live"
    assert await MemoryStore().get_state() is None


# ---------------------------------------------------------------- arb opportunities
def _op(ts, **kw):
    d = {"ts": ts, "event": "KXDEMOA-26OCT01", "kind": "NO", "contracts": 10.0, "profit": 0.42,
         "cost": 49.58, "sum_best": 1.07, "legs": [["KXDEMOA-26OCT01-B70.5", "ask", 0.31, 10.0],
                                                    ["KXDEMOA-26OCT01-B72.5", "ask", 0.76, 10.0]],
         "mode": "demo"}
    d.update(kw)
    return d


async def test_arb_log_only_sightings_collapse_within_window(store):
    assert await store.add_arb_opportunity(_op(NOW)) is True
    assert await store.add_arb_opportunity(_op(NOW + timedelta(seconds=15))) is False
    assert await store.add_arb_opportunity(_op(NOW + timedelta(seconds=30))) is False
    ops = await store.list_arb_opportunities()
    assert len(ops) == 1
    o = ops[0]
    assert o["seen_count"] == 3 and o["ts"] == NOW and o["last_seen"] == NOW + timedelta(seconds=30)
    assert o["executed"] == "log-only" and "_id" not in o and o["sig"]


async def test_arb_window_is_sliding_from_last_seen(store):
    await store.add_arb_opportunity(_op(NOW))
    for i in range(1, 12):                                         # seen every 60 s for 11 min
        await store.add_arb_opportunity(_op(NOW + timedelta(seconds=60 * i)))
    ops = await store.list_arb_opportunities()
    assert len(ops) == 1 and ops[0]["seen_count"] == 12
    # gap longer than ARB_DEDUPE_SECONDS -> a new doc
    later = NOW + timedelta(seconds=60 * 11 + S.ARB_DEDUPE_SECONDS + 1)
    assert await store.add_arb_opportunity(_op(later)) is True
    assert len(await store.list_arb_opportunities()) == 2


async def test_arb_different_book_is_new_doc(store):
    await store.add_arb_opportunity(_op(NOW))
    assert await store.add_arb_opportunity(_op(NOW + timedelta(seconds=15), profit=0.55)) is True
    assert await store.add_arb_opportunity(_op(NOW + timedelta(seconds=15), kind="YES")) is True
    assert len(await store.list_arb_opportunities()) == 3


async def test_arb_executed_always_inserted_and_filter(store):
    await store.add_arb_opportunity(_op(NOW))
    assert await store.add_arb_opportunity(_op(NOW + timedelta(seconds=1), executed="FILLED {}")) is True
    assert await store.add_arb_opportunity(_op(NOW + timedelta(seconds=2), executed="FILLED {}")) is True
    assert await store.add_arb_opportunity(_op(NOW + timedelta(seconds=3), executed="PARTIAL {}")) is True
    allops = await store.list_arb_opportunities()
    assert len(allops) == 4 and allops[0]["executed"] == "PARTIAL {}"          # newest first
    ex = await store.list_arb_opportunities(executed_only=True)
    assert [o["executed"] for o in ex] == ["PARTIAL {}", "FILLED {}", "FILLED {}"]
    assert len(await store.list_arb_opportunities(since=NOW + timedelta(seconds=2))) == 2
    assert len(await store.list_arb_opportunities(limit=1)) == 1


async def test_arb_accepts_scanner_dataclass_and_validates(store):
    @dataclass
    class Opportunity:                                             # same fields as arb_scanner.Opportunity
        event: str
        kind: str
        contracts: float
        profit: float
        cost: float
        legs: list
        sum_best: float

    op = Opportunity("KXDEMOB-26OCT01", "YES", 5.0, 0.3, 4.6, [("T1", "bid", 0.4, 5.0)], 0.43)
    assert await store.add_arb_opportunity(op) is True
    got = (await store.list_arb_opportunities())[0]
    assert got["event"] == "KXDEMOB-26OCT01" and got["legs"] == [["T1", "bid", 0.4, 5.0]]
    assert got["executed"] == "log-only"
    with pytest.raises(ValueError):
        await store.add_arb_opportunity({"event": "E", "kind": "MAYBE"})


async def test_arb_signature_stable_across_number_types():
    a = _op(NOW)
    b = _op(NOW, contracts=10, legs=[("KXDEMOA-26OCT01-B70.5", "ask", 0.31, 10.0),
                                     ("KXDEMOA-26OCT01-B72.5", "ask", 0.76, 10.0)])
    assert S.arb_signature(a) == S.arb_signature(b)


# ---------------------------------------------------------------- fills windows + settlements
async def test_fills_since_until_window_for_pnl(store):
    fills = [{"fill_id": f"f{i}", "ts": NOW - timedelta(days=d), "count": 1} for i, d in enumerate((1, 6, 8, 29, 31))]
    assert await store.upsert_fills(fills) == 5
    d7 = await store.list_fills(since=NOW - timedelta(days=7), until=NOW, limit=100000)
    d30 = await store.list_fills(since=NOW - timedelta(days=30), until=NOW, limit=100000)
    assert [f["fill_id"] for f in d7] == ["f0", "f1"]
    assert [f["fill_id"] for f in d30] == ["f0", "f1", "f2", "f3"]
    assert await store.list_fills(until=NOW - timedelta(days=1)) == [d for d in await store.list_fills()
                                                                     if d["fill_id"] != "f0"]  # until exclusive


async def test_fills_created_time_and_epoch_ts(store):
    await store.upsert_fills([{"fill_id": "a", "created_time": "2026-10-01T10:00:00Z"},
                              {"trade_id": "b", "ts": int(NOW.timestamp())}])
    got = {f["fill_id"]: f["ts"] for f in await store.list_fills()}
    assert got == {"a": datetime(2026, 10, 1, 10, tzinfo=timezone.utc), "b": NOW}


async def test_settlements_upsert_and_window(store):
    rows = [{"ticker": "T1", "market_result": "no", "revenue": 500, "settled_time": "2026-09-30T23:00:00Z"},
            {"ticker": "T2", "market_result": "yes", "revenue": 0, "settled_time": "2026-09-20T23:00:00Z"}]
    assert await store.upsert_settlements(rows) == 2
    assert await store.upsert_settlements([{**rows[0], "revenue": 600}]) == 0      # merge, not new
    got = await store.list_settlements()
    assert [g["ticker"] for g in got] == ["T1", "T2"] and got[0]["revenue"] == 600
    assert [g["ticker"] for g in await store.list_settlements(since=NOW - timedelta(days=7))] == ["T1"]
    assert [g["ticker"] for g in await store.list_settlements(until=NOW - timedelta(days=7))] == ["T2"]
    with pytest.raises(ValueError):
        await store.upsert_settlements([{"revenue": 1}])


# ---------------------------------------------------------------- seed
async def test_seed_writes_new_collections():
    s = MemoryStore()
    counts = await seed(s, now=NOW)
    assert counts["settlements"] > 0 and counts["arb_opportunities"] >= 1
    ops = await s.list_arb_opportunities(limit=1000)
    assert len(ops) == counts["arb_opportunities"] and max(o["seen_count"] for o in ops) == 3
    assert len(await s.list_heartbeats(limit=10000)) == counts["heartbeats"]
    assert (await s.get_state())["arb_desired"] == "stopped"


# ---------------------------------------------------------------- MongoStore, offline (recording fake client)
class _Rec:
    def __init__(self):
        self.calls = []

    def __getitem__(self, name):
        return _Coll(self, name)


class _Coll:
    def __init__(self, rec, name):
        self.rec, self.name = rec, name

    async def create_index(self, keys, **kw):
        self.rec.calls.append((self.name, "create_index", keys, kw))

    def find(self, q, proj):
        self.rec.calls.append((self.name, "find", q, proj))
        return _Cur()


class _Cur:
    def sort(self, *a):
        return self

    def limit(self, n):
        return self

    async def to_list(self):
        return []


class _Client:
    def __init__(self):
        self.db = _Rec()
        self.calls = self.db.calls

    def __getitem__(self, name):
        return self.db


async def test_mongo_index_definitions_offline():
    from control_api.db.mongo import MongoStore
    client = _Client()
    ms = MongoStore("", client=client)
    await ms.ensure_indexes()
    idx = [(c[0], c[2], c[3]) for c in client.calls if c[1] == "create_index"]
    ttl = [(coll, keys, kw) for coll, keys, kw in idx if "expireAfterSeconds" in kw]
    # TTL indexes must be single-field (MongoDB ignores expireAfterSeconds on compound indexes).
    assert all(len(keys) == 1 for _, keys, _ in ttl)
    ttl_colls = {coll: kw["expireAfterSeconds"] for coll, _, kw in ttl}
    for coll in (S.HEARTBEATS, S.JOURNAL_ROWS, S.ACCOUNT_SNAPSHOTS):
        assert ttl_colls[coll] == S.RETENTION_SECONDS
    assert ttl_colls[S.ARB_OPPORTUNITIES] == S.ARB_RETENTION_SECONDS
    assert ttl_colls[S.REVOKED_TOKENS] == 0
    uniques = {(coll, keys[0][0]) for coll, keys, kw in idx if kw.get("unique")}
    assert {(S.SETTLEMENTS, "ticker"), (S.FILLS, "fill_id"), (S.JOURNAL_ROWS, "row_hash"),
            (S.CONFIG_VERSIONS, "version")} <= uniques
    # index names are unique per collection (create_index with a clashing name would fail on Atlas)
    names = [(coll, kw["name"]) for coll, _, kw in idx]
    assert len(names) == len(set(names))


async def test_mongo_window_queries_offline():
    from control_api.db.mongo import MongoStore
    client = _Client()
    ms = MongoStore("", client=client)
    await ms.list_heartbeats(since=NOW)
    await ms.list_fills(since=NOW - timedelta(days=7), until=NOW)
    await ms.list_settlements()
    await ms.list_arb_opportunities(executed_only=True)
    finds = [(c[0], c[2], c[3]) for c in client.calls if c[1] == "find"]
    assert finds[0] == (S.HEARTBEATS, {"ts": {"$gte": NOW}}, {"_id": 0})
    assert finds[1] == (S.FILLS, {"ts": {"$gte": NOW - timedelta(days=7), "$lt": NOW}}, {"_id": 0})
    assert finds[2] == (S.SETTLEMENTS, {}, {"_id": 0})
    assert finds[3] == (S.ARB_OPPORTUNITIES, {"executed": {"$ne": "log-only"}}, {"_id": 0})


async def test_set_fill_fields_merge_semantics(store):
    await store.upsert_fills([{"fill_id": "f1", "ts": NOW, "count": 3}])
    naive = datetime(2026, 10, 1, 13, 0, 0, 123456)
    assert await store.set_fill_fields("f1", {"fill_quality": {"at": naive}, "fill_id": "x", "ts": NOW}) is True
    assert await store.set_fill_fields("f1", {}) is True                     # exists, nothing to write
    assert await store.set_fill_fields("nope", {"fill_quality": 1}) is False
    f = (await store.list_fills())[0]
    assert f["fill_id"] == "f1" and f["ts"] == NOW
    assert f["fill_quality"]["at"] == datetime(2026, 10, 1, 13, 0, 0, 123000, tzinfo=timezone.utc)
    await store.upsert_fills([{"fill_id": "f1", "ts": NOW, "count": 4}])  # re-sync keeps the extra field
    f = (await store.list_fills())[0]
    assert f["count"] == 4 and "fill_quality" in f
