"""Periods and cash flows (Sprint 2A-1): validation, ordering, windows, copies, delete, seed; run against
MemoryStore always and MongoStore when TEST_MONGODB_URI is set; plus offline checks of MongoStore indexes and
query shapes (no real DB)."""
import os
import uuid
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


# ---------------------------------------------------------------- periods
async def test_add_and_list_periods_oldest_first(store):
    b = await store.add_period({"name": "Second", "started_at": NOW + timedelta(days=2), "created_by": "o"})
    a = await store.add_period({"name": "First", "started_at": NOW, "note": "  risk gate on ", "created_by": "o"})
    assert a["note"] == "risk gate on" and a["started_at"] == NOW and len(a["id"]) == 32
    out = await store.list_periods()
    assert [p["name"] for p in out] == ["First", "Second"]                 # oldest first, whatever the insert order
    assert out[1] == b
    assert all("_id" not in p and p["started_at"].tzinfo is not None for p in out)
    assert set(out[0]) == {"id", "name", "started_at", "note", "created_by"}


async def test_period_ties_keep_insertion_order(store):
    for n in ("a", "b", "c"):
        await store.add_period({"name": n, "started_at": NOW})
    assert [p["name"] for p in await store.list_periods()] == ["a", "b", "c"]


async def test_period_defaults_and_naive_times(store):
    p = await store.add_period({"name": "x", "started_at": datetime(2026, 10, 1, 12, 0, 0, 123456)})
    assert p["started_at"] == datetime(2026, 10, 1, 12, 0, 0, 123000, tzinfo=timezone.utc)   # aware UTC, ms
    assert p["note"] is None and p["created_by"] == ""
    q = await store.add_period({"name": "now"})
    assert abs((q["started_at"] - S.utcnow()).total_seconds()) < 5
    r = await store.add_period({"name": "iso", "started_at": "2026-10-03T00:00:00Z", "note": "   "})
    assert r["started_at"] == datetime(2026, 10, 3, tzinfo=timezone.utc) and r["note"] is None


@pytest.mark.parametrize("bad", [
    {"name": ""}, {"name": "   "}, {}, {"name": "x" * 61}, {"name": "ok", "note": "n" * 201},
    {"name": "ok", "started_at": "not a date"}, {"name": "ok", "id": "p1"},
])
async def test_period_validation(store, bad):
    with pytest.raises(ValueError):
        await store.add_period(bad)
    assert await store.list_periods() == []                                 # nothing written


async def test_period_limits_inclusive(store):
    await store.add_period({"name": "x" * 60, "note": "n" * 200})
    await store.add_period({"name": "y"})
    assert len(await store.list_periods()) == 2


async def test_period_is_append_only_duplicate_id_rejected(store):
    await store.add_period({"id": "dup", "name": "one", "started_at": NOW})
    with pytest.raises(ValueError):
        await store.add_period({"id": "dup", "name": "two", "started_at": NOW + timedelta(days=1)})
    out = await store.list_periods()
    assert [p["name"] for p in out] == ["one"]


async def test_periods_return_copies(store):
    p = await store.add_period({"name": "keep", "started_at": NOW})
    p["name"] = "changed"
    (await store.list_periods())[0]["name"] = "changed too"
    assert (await store.list_periods())[0]["name"] == "keep"


# ---------------------------------------------------------------- cash flows
async def test_add_and_list_cash_flows_newest_first(store):
    a = await store.add_cash_flow({"kind": "deposit", "amount": 100, "ts": NOW, "note": "start", "created_by": "o"})
    b = await store.add_cash_flow({"kind": "withdrawal", "amount": 25.5, "ts": NOW + timedelta(days=1)})
    assert a["amount"] == 100.0 and isinstance(a["amount"], float) and len(a["id"]) == 32
    assert b["note"] is None and b["created_by"] == ""
    out = await store.list_cash_flows()
    assert [c["id"] for c in out] == [b["id"], a["id"]]
    assert out[1] == a
    assert all("_id" not in c and c["ts"].tzinfo is not None for c in out)
    assert set(out[0]) == {"id", "ts", "kind", "amount", "note", "created_by"}


async def test_cash_flow_window_since_inclusive_until_exclusive(store):
    for i in range(5):
        await store.add_cash_flow({"kind": "deposit", "amount": i + 1, "ts": NOW + timedelta(days=i)})
    amounts = lambda out: [c["amount"] for c in out]                          # noqa: E731
    assert amounts(await store.list_cash_flows(since=NOW + timedelta(days=2))) == [5.0, 4.0, 3.0]
    assert amounts(await store.list_cash_flows(until=NOW + timedelta(days=2))) == [2.0, 1.0]
    assert amounts(await store.list_cash_flows(since=NOW + timedelta(days=1),
                                               until=NOW + timedelta(days=3))) == [3.0, 2.0]
    assert await store.list_cash_flows(since=NOW + timedelta(days=9)) == []
    naive = datetime(2026, 10, 3, 12, 0)                                      # naive bound is read as UTC
    assert amounts(await store.list_cash_flows(since=naive, until=naive + timedelta(days=1))) == [3.0]


async def test_cash_flow_ties_newest_insert_first(store):
    for n in (1, 2, 3):
        await store.add_cash_flow({"kind": "deposit", "amount": n, "ts": NOW})
    assert [c["amount"] for c in await store.list_cash_flows()] == [3.0, 2.0, 1.0]


@pytest.mark.parametrize("bad", [
    {"kind": "deposit", "amount": 0}, {"kind": "deposit", "amount": -5}, {"kind": "deposit", "amount": 100000.01},
    {"kind": "deposit", "amount": "abc"}, {"kind": "deposit", "amount": None}, {"kind": "deposit"},
    {"kind": "deposit", "amount": True}, {"kind": "deposit", "amount": float("nan")},
    {"kind": "deposit", "amount": float("inf")},
    {"kind": "transfer", "amount": 5}, {"amount": 5}, {"kind": "deposit", "amount": 5, "note": "n" * 201},
    {"kind": "deposit", "amount": 5, "ts": "garbage"},
])
async def test_cash_flow_validation(store, bad):
    with pytest.raises(ValueError):
        await store.add_cash_flow(bad)
    assert await store.list_cash_flows() == []


async def test_cash_flow_amount_bounds_inclusive_upper(store):
    await store.add_cash_flow({"kind": "deposit", "amount": 100000})
    await store.add_cash_flow({"kind": "withdrawal", "amount": 0.01})
    await store.add_cash_flow({"kind": "deposit", "amount": "12.5"})          # numeric strings are accepted
    assert sorted(c["amount"] for c in await store.list_cash_flows()) == [0.01, 12.5, 100000.0]


async def test_delete_cash_flow(store):
    a = await store.add_cash_flow({"kind": "deposit", "amount": 10, "ts": NOW})
    b = await store.add_cash_flow({"kind": "deposit", "amount": 20, "ts": NOW + timedelta(days=1)})
    assert await store.delete_cash_flow(a["id"]) is True
    assert await store.delete_cash_flow(a["id"]) is False                      # already gone
    assert await store.delete_cash_flow("nope") is False
    assert [c["id"] for c in await store.list_cash_flows()] == [b["id"]]


async def test_cash_flow_duplicate_id_rejected_and_copies(store):
    a = await store.add_cash_flow({"id": "cf", "kind": "deposit", "amount": 10})
    with pytest.raises(ValueError):
        await store.add_cash_flow({"id": "cf", "kind": "deposit", "amount": 99})
    a["amount"] = 1.0
    (await store.list_cash_flows())[0]["amount"] = 2.0
    assert [c["amount"] for c in await store.list_cash_flows()] == [10.0]


async def test_periods_and_cash_flows_are_independent_of_other_collections(store):
    await store.add_period({"name": "p", "started_at": NOW})
    await store.add_cash_flow({"kind": "deposit", "amount": 5, "ts": NOW})
    assert await store.list_fills() == [] and await store.list_audit(10) == []
    assert len(await store.list_periods()) == 1 and len(await store.list_cash_flows()) == 1


# ---------------------------------------------------------------- seed
async def test_seed_adds_period_and_cash_flows_idempotently():
    s = MemoryStore()
    counts = await seed(s, now=NOW)
    assert counts["periods"] == 1 and counts["cash_flows"] == 2
    ps = await s.list_periods()
    assert [p["name"] for p in ps] == ["Demo Period 2"] and ps[0]["started_at"] == NOW - timedelta(days=10)
    assert [c["kind"] for c in await s.list_cash_flows()] == ["withdrawal", "deposit"]
    again = await seed(s, now=NOW)                                             # re-seed: fixed ids, no duplicates
    assert again["periods"] == 0 and again["cash_flows"] == 0
    assert len(await s.list_periods()) == 1 and len(await s.list_cash_flows()) == 2


# ---------------------------------------------------------------- MongoStore, offline (recording fake client)
class _Dup(Exception):
    pass


class _Coll:
    def __init__(self, rec, name):
        self.rec, self.name = rec, name

    async def create_index(self, keys, **kw):
        self.rec.calls.append((self.name, "create_index", keys, kw))

    async def insert_one(self, doc):
        self.rec.calls.append((self.name, "insert_one", dict(doc), None))
        if self.rec.dup:
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError("dup")

    async def delete_one(self, q):
        self.rec.calls.append((self.name, "delete_one", q, None))

        class R:
            deleted_count = 1
        return R()

    def find(self, q, proj):
        self.rec.calls.append((self.name, "find", q, proj))
        rec = self.rec

        class Cur:
            def sort(s, spec):
                rec.calls.append((self.name, "sort", spec, None))
                return s

            async def to_list(s):
                return []
        return Cur()


class _Rec:
    def __init__(self):
        self.calls, self.dup = [], False

    def __getitem__(self, name):
        return _Coll(self, name)


class _Client:
    def __init__(self):
        self.db = _Rec()
        self.calls = self.db.calls

    def __getitem__(self, name):
        return self.db


async def test_mongo_indexes_for_periods_and_cash_flows_offline():
    from control_api.db.mongo import MongoStore
    client = _Client()
    ms = MongoStore("", client=client)
    await ms.ensure_indexes()
    await ms.ensure_indexes()                                                  # idempotent: same definitions again
    idx = [(c[0], c[2], c[3]) for c in client.calls if c[1] == "create_index"]
    mine = {(coll, kw["name"]): (keys, kw) for coll, keys, kw in idx if coll in (S.PERIODS, S.CASH_FLOWS)}
    assert mine[(S.PERIODS, "id_unique")][1]["unique"] and mine[(S.PERIODS, "id_unique")][0] == [("id", 1)]
    assert mine[(S.PERIODS, "started_at")][0] == [("started_at", 1)]
    assert mine[(S.CASH_FLOWS, "id_unique")][1]["unique"]
    assert mine[(S.CASH_FLOWS, "ts_desc")][0] == [("ts", -1)]
    assert not any("expireAfterSeconds" in kw for _, kw in mine.values())     # permanent: no TTL
    assert {S.PERIODS, S.CASH_FLOWS} <= set(S.COLLECTIONS)


async def test_mongo_query_shapes_offline():
    from control_api.db.mongo import MongoStore
    client = _Client()
    ms = MongoStore("", client=client)
    await ms.list_periods()
    await ms.list_cash_flows(since=NOW, until=NOW + timedelta(days=1))
    await ms.list_cash_flows()
    assert await ms.delete_cash_flow("abc") is True
    calls = [(c[0], c[1], c[2]) for c in client.calls if c[1] in ("find", "sort", "delete_one")]
    assert calls[0] == (S.PERIODS, "find", {})
    assert calls[1] == (S.PERIODS, "sort", [("started_at", 1), ("_id", 1)])
    assert calls[2] == (S.CASH_FLOWS, "find", {"ts": {"$gte": NOW, "$lt": NOW + timedelta(days=1)}})
    assert calls[3][1:] == ("sort", [("ts", -1), ("_id", -1)])
    assert calls[4] == (S.CASH_FLOWS, "find", {})
    assert calls[-1] == (S.CASH_FLOWS, "delete_one", {"id": "abc"})


async def test_mongo_duplicate_key_becomes_value_error_offline():
    from control_api.db.mongo import MongoStore
    client = _Client()
    client.db.dup = True
    ms = MongoStore("", client=client)
    with pytest.raises(ValueError):
        await ms.add_period({"name": "x"})
    with pytest.raises(ValueError):
        await ms.add_cash_flow({"kind": "deposit", "amount": 1})
    client.db.dup = False
    p = await ms.add_period({"id": "pid", "name": "x", "started_at": NOW})
    inserted = [c for c in client.calls if c[1] == "insert_one"][-1]
    assert inserted[0] == S.PERIODS and inserted[2] == p and "_id" not in p


async def test_mongo_validation_runs_before_any_write_offline():
    from control_api.db.mongo import MongoStore
    client = _Client()
    ms = MongoStore("", client=client)
    with pytest.raises(ValueError):
        await ms.add_cash_flow({"kind": "deposit", "amount": 0})
    with pytest.raises(ValueError):
        await ms.add_period({"name": ""})
    assert not [c for c in client.calls if c[1] == "insert_one"]


# ---------------------------------------------------------------- arb_spend_since (S2A1-1)
def _op(i, executed, cost, mode="demo-execute", ts=NOW):
    return {"ts": ts, "event": f"EV{i}", "kind": "NO", "contracts": 5.0, "profit": 1.0 + i, "cost": cost,
            "legs": [[f"EV{i}-A", "ask", 0.1, 5.0]], "executed": executed, "mode": mode}


async def test_arb_spend_since_sums_only_spent_rows(store):
    rows = [_op(0, "FILLED 3 legs", 10.0), _op(1, "PARTIAL 2/3", 4.5), _op(2, "ERROR timeout", 1.25),
            _op(3, "skipped: gate total", 99.0), _op(4, "log-only", 77.0),
            _op(5, "FILLED x", 8.0, mode="live"),                                     # other mode
            _op(6, "FILLED old", 50.0, ts=NOW - timedelta(hours=1))]                  # before since
    for r in rows:
        assert await store.add_arb_opportunity(r) is True
    assert await store.arb_spend_since(NOW, "demo-execute") == 15.75
    assert await store.arb_spend_since(NOW, "live") == 8.0
    assert await store.arb_spend_since(NOW - timedelta(hours=2), "demo-execute") == 65.75
    assert await store.arb_spend_since(NOW + timedelta(seconds=1), "demo-execute") == 0.0
    assert await store.arb_spend_since(NOW, "demo") == 0.0


async def test_arb_spend_since_has_no_row_limit(store):
    for i in range(250):
        await store.add_arb_opportunity(_op(i, "FILLED ok", 1.0))
    assert await store.arb_spend_since(NOW, "demo-execute") == 250.0


async def test_mongo_arb_spend_pipeline_offline():
    from control_api.db.mongo import MongoStore
    seen = {}

    class C(_Coll):
        async def aggregate(self, pipe):
            seen["pipe"] = pipe

            class Cur:
                async def to_list(s):
                    return [{"_id": None, "total": 7}]
            return Cur()

    class R(_Rec):
        def __getitem__(self, name):
            return C(self, name)

    client = _Client()
    client.db = R()
    ms = MongoStore("", client=client)
    assert await ms.arb_spend_since(NOW, "demo-execute") == 7.0
    m = seen["pipe"][0]["$match"]
    assert m["ts"] == {"$gte": NOW} and m["mode"] == "demo-execute"
    assert m["executed"] == {"$regex": "^(FILLED|PARTIAL|ERROR)"}
