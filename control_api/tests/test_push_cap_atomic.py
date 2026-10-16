"""Z3: the 10-subscription cap holds under concurrent sign-ups in both stores (MemoryStore + MongoStore)."""
from __future__ import annotations

import asyncio

import pytest
from pymongo.errors import DuplicateKeyError

from control_api.db import schemas as S
from control_api.db.mongo import MongoStore
from control_api.db.store import MemoryStore

pytestmark = pytest.mark.asyncio
CAP = S.PUSH_SUBSCRIPTIONS_MAX


def _doc(i):
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/dev-{i}", "p256dh": "p", "auth": "a"}


class _Cur:
    def __init__(self, docs):
        self.docs = docs

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def to_list(self, *a):
        await asyncio.sleep(0)               # yield: lets racing writers interleave like a real network call
        return self.docs


class _Coll:
    """Minimal collection with Mongo's unique-index behaviour on sub_id and (sparse) slot."""

    def __init__(self):
        self.docs: list[dict] = []

    def find(self, flt, proj=None):
        return _Cur([{k: v for k, v in d.items() if k != "_id"} for d in self.docs])

    async def insert_one(self, doc):
        await asyncio.sleep(0)
        for d in self.docs:
            if d["sub_id"] == doc["sub_id"] or ("slot" in doc and d.get("slot") == doc["slot"]):
                raise DuplicateKeyError("dup")
        self.docs.append(dict(doc))

    async def update_one(self, flt, upd):
        for d in self.docs:
            if d["sub_id"] == flt["sub_id"]:
                d.update(upd["$set"])
                return type("R", (), {"matched_count": 1})()
        return type("R", (), {"matched_count": 0})()

    async def count_documents(self, flt):
        return len(self.docs)

    async def delete_one(self, flt):
        n = len(self.docs)
        self.docs = [d for d in self.docs if d["sub_id"] != flt["sub_id"]]
        return type("R", (), {"deleted_count": n - len(self.docs)})()


def _mongo():
    coll = _Coll()
    client = {S.DB_NAME_DEFAULT: {S.PUSH_SUBSCRIPTIONS: coll}}
    return MongoStore("", client=client), coll


def _stores():
    return [("memory", MemoryStore), ("mongo", lambda: _mongo()[0])]


@pytest.mark.parametrize("name,make", _stores())
async def test_concurrent_signups_never_exceed_cap(name, make):
    store = make()
    res = await asyncio.gather(*(store.save_push_subscription(_doc(i)) for i in range(CAP + 8)),
                               return_exceptions=True)
    ok = [r for r in res if r is True]
    assert len(ok) == CAP and sum(isinstance(r, ValueError) for r in res) == 8
    assert len(await store.list_push_subscriptions()) == CAP


@pytest.mark.parametrize("name,make", _stores())
async def test_concurrent_same_endpoint_is_one_row(name, make):
    store = make()
    res = await asyncio.gather(*(store.save_push_subscription(_doc(1)) for _ in range(6)))
    assert res.count(True) == 1 and len(await store.list_push_subscriptions()) == 1


@pytest.mark.parametrize("name,make", _stores())
async def test_resubscribe_at_cap_and_slot_reuse_after_delete(name, make):
    store = make()
    for i in range(CAP):
        assert await store.save_push_subscription(_doc(i)) is True
    assert await store.save_push_subscription(_doc(3)) is False          # existing endpoint still allowed
    with pytest.raises(ValueError):
        await store.save_push_subscription(_doc(99))
    assert await store.delete_push_subscription(S.push_sub_id(_doc(0)["endpoint"]))
    assert await store.save_push_subscription(_doc(99)) is True
    assert all("slot" not in d for d in await store.list_push_subscriptions())


async def test_mongo_legacy_slotless_rows_still_count():
    store, coll = _mongo()
    for i in range(CAP):
        coll.docs.append({**S.prepare_push_subscription(_doc(i))})      # pre-slot rows
    with pytest.raises(ValueError):
        await store.save_push_subscription(_doc(50))
    assert len(coll.docs) == CAP


async def test_mongo_creates_unique_sparse_slot_index():
    rec = []

    class C:
        def __init__(self, n):
            self.n = n

        async def create_index(self, keys, **kw):
            rec.append((self.n, kw))

    class DB:
        def __getitem__(self, n):
            return C(n)

    class Client:
        def __getitem__(self, n):
            return DB()
    await MongoStore("", client=Client()).ensure_indexes()
    slot = [kw for n, kw in rec if n == S.PUSH_SUBSCRIPTIONS and kw["name"] == "slot_unique"]
    assert slot and slot[0]["unique"] is True and slot[0]["sparse"] is True


async def test_route_caps_at_ten_with_409(env):
    from cryptography.hazmat.primitives.asymmetric import ec
    import httpx
    from control_api.services import webpush as w
    key = ec.generate_private_key(ec.SECP256R1())
    env.settings.vapid_private_key = w.b64u(key.private_numbers().private_value.to_bytes(32, "big"))
    env.settings.vapid_subject = "mailto:owner@example.com"
    env.runner.push = w.PushService(env.settings, env.store, transport=httpx.MockTransport(lambda r: httpx.Response(201)))

    def sub(i):
        ua = ec.generate_private_key(ec.SECP256R1())
        return {"endpoint": f"https://fcm.googleapis.com/fcm/send/dev-{i}",
                "keys": {"p256dh": w.b64u(w.public_bytes(ua)), "auth": w.b64u(b"0123456789abcdef")}}
    for i in range(CAP):                     # fill via the store (the route is rate-limited to 10/min)
        await env.store.save_push_subscription(_doc(i))
    r = await env.client.post("/api/push/subscribe", json=sub(99), headers=env.auth)
    assert r.status_code == 409 and r.json()["detail"] == "too_many_subscriptions"
