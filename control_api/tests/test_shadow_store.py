"""Shadow-mode store: MemoryStore vs MongoStore (driven through a small in-memory fake of the driver)."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import UpdateOne

from control_api.db import schemas as S
from control_api.db.mongo import MongoStore
from control_api.db.store import MemoryStore
from control_api.services import keepalive as ka

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ minimal fake of the async driver
def _get(doc, key):
    return doc.get(key)


def _match(doc, flt):
    for k, cond in flt.items():
        v = _get(doc, k)
        if isinstance(cond, dict):
            for op, arg in cond.items():
                if op == "$ne" and v == arg:
                    return False
                if op == "$in" and v not in arg:
                    return False
                if op == "$lte" and (v is None or not v <= arg):
                    return False
                if op == "$gte" and (v is None or not v >= arg):
                    return False
        elif v != cond:                                   # None matches a missing field, as in MongoDB
            return False
    return True


class Cursor:
    def __init__(self, docs):
        self.docs, self._lim = docs, None

    def sort(self, spec):
        for key, direction in reversed(spec):
            self.docs.sort(key=lambda d: (d.get(key) is not None, d.get(key)), reverse=direction < 0)
        return self

    def limit(self, n):
        self._lim = n
        return self

    async def to_list(self):
        return self.docs[:self._lim] if self._lim is not None else self.docs


class Result:
    def __init__(self, upserted=0):
        self.upserted_count = upserted


class Coll:
    def __init__(self):
        self.docs: dict = {}
        self.indexes: dict = {}
        self._auto = 0

    async def create_index(self, keys, **kw):
        self.indexes[kw["name"]] = {"keys": keys, **kw}

    @staticmethod
    def _out(d, proj):
        d = copy.deepcopy(d)
        if proj and proj.get("_id") == 0:
            d.pop("_id", None)
        keep = {k for k, v in (proj or {}).items() if v == 1}
        if keep:
            d = {k: v for k, v in d.items() if k in keep}
        return d

    def find(self, flt, proj=None):
        return Cursor([self._out(d, proj) for d in self.docs.values() if _match(d, flt)])

    async def find_one(self, flt, proj=None):
        hits = self.find(flt, proj).docs
        return hits[0] if hits else None

    async def count_documents(self, flt):
        return sum(1 for d in self.docs.values() if _match(d, flt))

    async def insert_one(self, doc):
        self._auto += 1
        self.docs[f"auto{self._auto}"] = {"_id": f"auto{self._auto}", **copy.deepcopy(doc)}

    async def replace_one(self, flt, doc, upsert=False):
        self.docs[flt["_id"]] = {"_id": flt["_id"], **copy.deepcopy(doc)}

    async def bulk_write(self, ops, ordered=True):
        new = 0
        for op in ops:
            assert isinstance(op, UpdateOne)
            _id = op._filter["_id"]
            cur = self.docs.get(_id)
            if cur is None:
                cur = self.docs[_id] = {"_id": _id}
                cur.update(copy.deepcopy(op._doc.get("$setOnInsert", {})))
                new += 1
            cur.update(copy.deepcopy(op._doc.get("$set", {})))
        return Result(new)


class Client:
    def __init__(self):
        self.colls: dict[str, Coll] = {}

    def __getitem__(self, _):
        return self

    def _get_coll(self, name):
        return self.colls.setdefault(name, Coll())


class _DB:
    def __init__(self, client):
        self.client = client

    def __getitem__(self, name):
        return self.client._get_coll(name)


class DbClient(Client):
    def __getitem__(self, _):
        return _DB(self)


@pytest.fixture(params=["memory", "mongo"])
def store(request):
    return MemoryStore() if request.param == "memory" else MongoStore("", client=DbClient())


def ep(n, **kw):
    d = {"ep_id": f"ep{n}", "env": "prod", "ticker": f"KXDEMO-{n}", "series": "KXDEMO", "side": "yes",
         "book_side": "yes", "price": 0.9, "order_price": 90, "count": 5, "cost": 4.5, "cell": ["a", 1],
         "close_time": T0 + timedelta(hours=n), "join_ts": T0 + timedelta(minutes=n), "end_ts": None,
         "trades_read": False, "result": None, "settled_ts": None, "standin": False}
    d.update(kw)
    return d


def ids(docs):
    return [d["ep_id"] for d in docs]


# ------------------------------------------------------------------ tests
async def test_upsert_idempotent_counts_new_and_merges(store):
    assert await store.shadow_upsert_episodes([ep(1), ep(2)]) == 2
    assert await store.shadow_upsert_episodes([ep(1), ep(3)]) == 1
    assert await store.shadow_upsert_episodes([]) == 0
    await store.shadow_upsert_episodes([{"ep_id": "ep1", "n_obs": 4}])        # partial update merges
    got = {d["ep_id"]: d for d in await store.shadow_recent(10)}
    assert len(got) == 3 and got["ep1"]["n_obs"] == 4 and got["ep1"]["ticker"] == "KXDEMO-1"
    assert got["ep1"]["ts"] == T0 + timedelta(minutes=1)                      # ts = join_ts, unmoved by the partial


async def test_docs_have_no_id_and_aware_datetimes(store):
    await store.shadow_upsert_episodes([ep(1, join_ts="2026-10-01T12:01:00Z", last_seen_ts=T0.replace(tzinfo=None))])
    [d] = await store.shadow_recent(5)
    assert "_id" not in d
    assert d["join_ts"].tzinfo is not None and d["last_seen_ts"].tzinfo is not None
    d["cell"].append("x")                                                      # copies, not references
    assert (await store.shadow_recent(5))[0]["cell"] == ["a", 1]


async def test_open_ended_unread_and_unsettled_queries(store):
    await store.shadow_upsert_episodes([
        ep(1),
        ep(2, end_ts=T0 + timedelta(hours=3)),
        ep(3, end_ts=T0 + timedelta(hours=2), trades_read=True),
        ep(4, end_ts=T0 + timedelta(hours=1)),
        ep(5, result="yes", settled_ts=T0 + timedelta(days=1)),
    ])
    assert ids(await store.shadow_open_episodes()) == ["ep1", "ep5"]
    assert ids(await store.shadow_ended_unread(10)) == ["ep4", "ep2"]          # oldest end first
    assert ids(await store.shadow_ended_unread(1)) == ["ep4"]
    # unsettled: result null and close_time <= now, oldest close first
    assert ids(await store.shadow_unsettled(T0 + timedelta(hours=3), 10)) == ["ep1", "ep2", "ep3"]
    assert ids(await store.shadow_unsettled(T0 + timedelta(hours=3), 2)) == ["ep1", "ep2"]
    assert await store.shadow_unsettled(T0, 10) == []


async def test_settled_recent_and_count(store):
    await store.shadow_upsert_episodes([
        ep(1, result="no", settled_ts=T0 + timedelta(days=2)),
        ep(2, result="yes", settled_ts=T0 + timedelta(days=1)),
        ep(3, end_ts=T0 + timedelta(hours=9)),
        ep(4),
    ])
    assert ids(await store.shadow_settled(T0 + timedelta(days=1))) == ["ep2", "ep1"]
    assert ids(await store.shadow_settled(T0 + timedelta(days=2))) == ["ep1"]
    assert ids(await store.shadow_recent(10)) == ["ep4", "ep3", "ep2", "ep1"]  # newest join first
    assert ids(await store.shadow_recent(2)) == ["ep4", "ep3"]
    assert ids(await store.shadow_recent(10, "ended")) == ["ep3"]
    assert ids(await store.shadow_recent(10, "settled")) == ["ep2", "ep1"]
    with pytest.raises(ValueError):
        await store.shadow_recent(10, "bogus")
    assert await store.shadow_count_new_since(T0 + timedelta(minutes=3)) == 2
    assert await store.shadow_count_new_since(T0 + timedelta(days=9)) == 0


async def test_passes_newest_first_and_summary_roundtrip(store):
    for i in range(3):
        await store.add_shadow_pass({"ts": T0 + timedelta(minutes=i), "ok": True, "duration_s": 1.5, "quotes": i,
                                     "errors": []})
    got = await store.shadow_recent_passes(2)
    assert [p["quotes"] for p in got] == [2, 1] and "_id" not in got[0] and got[0]["ts"].tzinfo
    assert await store.shadow_recent_passes(0) == []
    assert await store.get_shadow_summary() is None
    await store.set_shadow_summary({"generated": T0, "n": 1, "_id": "x"})
    await store.set_shadow_summary({"generated": T0, "n": 2})
    s = await store.get_shadow_summary()
    assert s == {"generated": T0, "n": 2}
    s["n"] = 9
    assert (await store.get_shadow_summary())["n"] == 2
    assert len(await store.shadow_recent_passes(10)) == 3                    # summary is not a pass


async def test_episode_without_ep_id_is_rejected(store):
    with pytest.raises(ValueError):
        await store.shadow_upsert_episodes([{"ticker": "KXDEMO-1"}])


async def test_ensure_indexes_shadow_ttl_and_idempotent():
    client = DbClient()
    st = MongoStore("", client=client)
    await st.ensure_indexes()
    await st.ensure_indexes()
    month = 30 * 24 * 3600
    eps, passes = client.colls[S.SHADOW_EPISODES].indexes, client.colls[S.SHADOW_PASSES].indexes
    assert eps["ts_ttl"]["expireAfterSeconds"] == month and passes["ts_ttl"]["expireAfterSeconds"] == month
    assert {"open_join", "ended_unread", "unsettled_rotation", "settled_ts", "join_ts_desc"} <= set(eps)
    assert {S.SHADOW_EPISODES, S.SHADOW_PASSES} <= set(S.COLLECTIONS)


async def test_cloud_shadow_enabled_default_and_validation():
    assert S.DEFAULT_CLOUD_SETTINGS["shadow_enabled"] is False
    assert ka.validate_cloud({"shadow_enabled": True}) == {"shadow_enabled": True}
    for bad in ("true", 1, 0):
        with pytest.raises(ValueError):
            ka.validate_cloud({"shadow_enabled": bad})
    assert ka.cloud_schema()["shadow_enabled"]["type"] == "boolean"


async def test_cloud_shadow_enabled_persists():
    store = MemoryStore()
    saved = await store.save_cloud_settings({"shadow_enabled": True}, by="owner")
    assert saved["shadow_enabled"] is True
    assert (await store.get_cloud_settings())["shadow_enabled"] is True


# ------------------------------------------------------------------ security review SH-1 / SH-2
async def test_unsettled_rotates_by_settle_checked_ts(store):
    await store.shadow_upsert_episodes([ep(1), ep(2), ep(3)])
    now = T0 + timedelta(hours=5)
    assert ids(await store.shadow_unsettled(now, 10)) == ["ep1", "ep2", "ep3"]
    # ep1 and ep2 were just checked (a void-pending or failing ticker): the never-checked one goes first
    await store.shadow_upsert_episodes([{"ep_id": "ep1", "settle_checked_ts": now},
                                        {"ep_id": "ep2", "settle_checked_ts": now - timedelta(minutes=10)}])
    assert ids(await store.shadow_unsettled(now, 10)) == ["ep3", "ep2", "ep1"]
    assert ids(await store.shadow_unsettled(now, 1)) == ["ep3"]
    await store.shadow_upsert_episodes([{"ep_id": "ep3", "result": "void", "settled_ts": now}])
    assert ids(await store.shadow_unsettled(now, 10)) == ["ep2", "ep1"]


async def test_settled_projection_scored_only_and_cap(store):
    fill = {"lo": 0, "mid": 1, "hi": 2}
    await store.shadow_upsert_episodes([
        ep(1, result="yes", fill=fill, settled_ts=T0 + timedelta(days=1)),
        ep(2, result="no", fill=fill, settled_ts=T0 + timedelta(days=2)),
        ep(3, result="void", settled_ts=T0 + timedelta(days=2)),
        ep(4, result="yes", fill=None, settled_ts=T0 + timedelta(days=3)),
        ep(5, result="no", fill=fill, settled_ts=T0 + timedelta(days=4)),
    ])
    assert ids(await store.shadow_settled(T0)) == ["ep1", "ep2", "ep3", "ep4", "ep5"]   # unchanged default
    got = await store.shadow_settled(T0, scored_only=True, fields=("result", "fill", "price"))
    assert sorted(ids(got)) == ["ep1", "ep2", "ep5"]
    assert all(set(d) <= {"ep_id", "result", "fill", "price"} for d in got)
    assert ids(await store.shadow_settled(T0, scored_only=True, limit=2)) == ["ep5", "ep2"]   # newest kept
    opened = await store.shadow_open_episodes(fields=("price",), limit=1)
    assert len(opened) == 1 and set(opened[0]) <= {"ep_id", "price"}


# ------------------------------------------------------------------ sprint 2A-3 episode fields and settings
async def test_old_episodes_read_with_defaults(store):
    await store.shadow_upsert_episodes([ep(1)])
    [d] = await store.shadow_recent(5)
    assert (d["through_vol"], d["capped"], d["cap_reason"], d["interval"], d["fee_mode"]) == (None, False, None, 10, None)
    [p] = await store.shadow_open_episodes(fields=["price", "capped"])
    assert p["capped"] is False and "interval" not in p and p["price"] == 0.9


async def test_new_fields_stored_and_partial_update_keeps_them(store):
    await store.shadow_upsert_episodes([ep(1, through_vol=12.5, capped=True, cap_reason="series cap",
                                           interval=5, fee_mode="taker")])
    await store.shadow_upsert_episodes([{"ep_id": "ep1", "n_obs": 2}])
    [d] = await store.shadow_recent(5)
    assert (d["through_vol"], d["capped"], d["cap_reason"], d["interval"], d["fee_mode"]) == (
        12.5, True, "series cap", 5, "taker")


@pytest.mark.parametrize("bad", [{"through_vol": -1}, {"through_vol": "x"}, {"capped": "yes"},
                                 {"interval": 0}, {"interval": True}, {"cap_reason": 5}])
async def test_bad_new_fields_rejected(store, bad):
    with pytest.raises(ValueError):
        await store.shadow_upsert_episodes([ep(1, **bad)])


async def test_shadow_cloud_settings_validation():
    assert ka.validate_cloud({"shadow_capital": 150, "shadow_intervals": [10, 5]}) == {
        "shadow_capital": 150, "shadow_intervals": [5, 10]}
    assert ka.validate_cloud({"shadow_capital": 20.5})["shadow_capital"] == 20.5
    for bad in ({"shadow_capital": 19}, {"shadow_capital": 100001}, {"shadow_capital": True},
                {"shadow_capital": float("nan")}, {"shadow_intervals": []}, {"shadow_intervals": [6]},
                {"shadow_intervals": [5, 5]}, {"shadow_intervals": [5, 7, 10, 5]}, {"shadow_intervals": "10"},
                {"shadow_intervals": [True]}):
        with pytest.raises(ValueError):
            ka.validate_cloud(bad)
    assert S.DEFAULT_CLOUD_SETTINGS["shadow_capital"] == 150 and S.DEFAULT_CLOUD_SETTINGS["shadow_intervals"] == [10]
