"""S-18: auth must never write back a stale user doc; ``sessions_valid_after`` and
``last_totp_step`` only move forward ($max), so neither can be rolled back by a late write."""
import asyncio
import os
import uuid
from datetime import timedelta

import httpx
import pyotp
import pytest
import pytest_asyncio

from control_api.auth import deps
from control_api.auth import security as sec
from control_api.db import MemoryStore
from control_api.main import create_app
from control_api.tests.conftest import PASSWORD, BrokenStore

pytestmark = pytest.mark.asyncio

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
    await s.client.drop_database(s.db.name) if hasattr(s, "client") else None
    await s.close()


# ---- store contract ----------------------------------------------------------------------
async def test_save_user_never_moves_monotonic_fields_backwards(store):
    t1 = sec.utcnow()
    await store.save_user({"username": "u", deps.SESSIONS_FIELD: t1, "last_totp_step": 100})
    doc = await store.save_user({"username": "u", deps.SESSIONS_FIELD: t1 - timedelta(hours=1),
                                 "last_totp_step": 99, "failed_logins": 3})
    assert doc["last_totp_step"] == 100
    assert abs((sec.as_utc(doc[deps.SESSIONS_FIELD]) - t1).total_seconds()) < 0.01
    assert doc["failed_logins"] == 3                       # ordinary fields still $set
    doc = await store.save_user({"username": "u", "last_totp_step": 101})
    assert doc["last_totp_step"] == 101                    # forward is fine


async def test_save_user_none_still_resets_for_tooling(store):
    await store.save_user({"username": "u", "last_totp_step": 100})
    assert (await store.save_user({"username": "u", "last_totp_step": None}))["last_totp_step"] is None


async def test_update_user_fields_set_max_inc(store):
    await store.save_user({"username": "u", "last_totp_step": 50, "failed_logins": 0,
                           "password_hash": "h"})
    d = await store.update_user_fields("u", inc={"failed_logins": 1})
    d = await store.update_user_fields("u", inc={"failed_logins": 1})
    assert d["failed_logins"] == 2 and d["password_hash"] == "h"
    d = await store.update_user_fields("u", max={"last_totp_step": 40})
    assert d["last_totp_step"] == 50
    d = await store.update_user_fields("u", set={"last_totp_step": 45, "locked_until": None})
    assert d["last_totp_step"] == 50                       # monotonic even via set
    d = await store.update_user_fields("u", max={"last_totp_step": 51})
    assert d["last_totp_step"] == 51


async def test_update_user_fields_never_upserts_or_stores_secrets(store):
    assert await store.update_user_fields("ghost", set={"failed_logins": 1}) is None
    assert await store.get_user("ghost") is None
    await store.save_user({"username": "u"})
    with pytest.raises(ValueError):
        await store.update_user_fields("u", set={"totp_secret": "plain"})


async def test_mongo_save_user_uses_max_for_monotonic_fields():
    """Shape check without a server: the Mongo update uses $max for the S-18 fields."""
    from control_api.db.mongo import MongoStore
    s = MongoStore("mongodb://127.0.0.1:1", "x")
    calls = []

    class Coll:
        async def find_one_and_update(self, flt, update, **kw):
            calls.append((flt, update, kw))
            return {}
    s._c = lambda name: Coll()
    t = sec.utcnow()
    await s.save_user({"username": "u", deps.SESSIONS_FIELD: t, "last_totp_step": 7, "x": 1})
    await s.update_user_fields("u", set={"failed_logins": 0, "last_totp_step": 8},
                               inc={"failed_logins_total": 1})
    (_, up1, kw1), (_, up2, kw2) = calls
    assert set(up1["$max"]) == {deps.SESSIONS_FIELD, "last_totp_step"}
    assert deps.SESSIONS_FIELD not in up1["$set"] and "last_totp_step" not in up1["$set"]
    assert up2["$max"] == {"last_totp_step": 8} and up2["$set"] == {"failed_logins": 0}
    assert kw2["upsert"] is False


# ---- auth paths write only the fields they own -----------------------------------------
FORBIDDEN_IN_AUTH_WRITES = {deps.SESSIONS_FIELD, "totp_secret_enc", "username"}


def _spy(store):
    log = []
    real_save, real_upd = store.save_user, store.update_user_fields

    async def save_user(doc):
        log.append(("save_user", dict(doc)))
        return await real_save(doc)

    async def update_user_fields(username, *, set=None, max=None, inc=None):
        log.append(("update", {**(set or {}), **(max or {}), **(inc or {})}))
        return await real_upd(username, set=set, max=max, inc=inc)
    store.save_user, store.update_user_fields = save_user, update_user_fields
    return log


async def test_login_2fa_stepup_failures_use_field_level_writes(env):
    log = _spy(env.store)
    mfa = (await env.client.post("/api/auth/login",
                                 json={"username": "owner", "password": PASSWORD})).json()["mfa_token"]
    assert (await env.client.post("/api/auth/2fa", json={"mfa_token": mfa, "code": "000000"})).status_code == 401
    assert (await env.client.post("/api/auth/2fa",
                                  json={"mfa_token": mfa, "code": pyotp.TOTP(env.secret).now()})).status_code == 200
    r = await env.client.put("/api/mode", json={"mode": "demo"}, headers={**env.auth, "X-2FA-Code": "000000"})
    assert r.status_code == 403
    assert log, "auth paths should have written something"
    assert all(kind == "update" for kind, _ in log), log    # never a whole-doc save_user
    for _, fields in log:
        assert not (FORBIDDEN_IN_AUTH_WRITES & set(fields)), fields


async def test_lockout_still_counts_via_atomic_inc(env):
    mfa = (await env.client.post("/api/auth/login",
                                 json={"username": "owner", "password": PASSWORD})).json()["mfa_token"]
    codes = [(await env.client.post("/api/auth/2fa", json={"mfa_token": mfa, "code": "000000"})).status_code
             for _ in range(5)]
    assert codes == [401] * 4 + [429]
    u = await env.store.get_user("owner")
    assert u["failed_logins"] == 0 and u["locked_until"] is not None


# ---- the reproduced attacks --------------------------------------------------------------
async def test_s18_overlapping_failed_stepup_cannot_roll_back_logout_all(env):
    """PoC from security pass 3: an attacker's failed step-up overlapping the owner's
    logout-all used to write back the old epoch; after a restart the stolen token worked."""
    store = env.store
    t0 = sec.utcnow() - timedelta(seconds=100)
    await store.save_user({"username": "owner", deps.SESSIONS_FIELD: t0, "last_totp_step": None,
                           "failed_logins": 0, "locked_until": None})
    env.app.state.auth_sessions_ttl_s = 0
    stolen = dict(env.auth)

    real_upd = store.update_user_fields

    async def slow_failure_write(username, **kw):
        if kw.get("inc"):                               # register_failure's write (latency)
            await asyncio.sleep(0.3)
        return await real_upd(username, **kw)
    store.update_user_fields = slow_failure_write

    async def attacker():
        return await env.client.put("/api/mode", json={"mode": "demo"},
                                    headers={**stolen, "X-2FA-Code": "000000"})

    async def owner():
        await asyncio.sleep(0.05)
        return await env.client.post("/api/auth/logout-all",
                                     headers={**env.auth, "X-2FA-Code": pyotp.TOTP(env.secret).now()})

    ra, rb = await asyncio.gather(attacker(), owner())
    assert ra.status_code == 403, ra.text
    assert rb.status_code == 200, rb.text
    stored = sec.as_utc((await store.get_user("owner"))[deps.SESSIONS_FIELD])
    assert stored > t0 + timedelta(seconds=50)          # not rolled back

    app2 = create_app(env.settings, store=store, kalshi_factory=lambda m: env.kalshi)  # "restart"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://t") as c2:
        r = await c2.get("/api/auth/me", headers=stolen)
    assert r.status_code == 401 and r.json()["detail"] == "session_expired"


async def test_s18_late_stale_write_cannot_roll_back_totp_step(env):
    """Variant: a late write of an old doc must not lower last_totp_step (code replay)."""
    stale = await env.store.get_user("owner")                  # last_totp_step None
    h = await env.h2fa()
    code = h["X-2FA-Code"]
    assert (await env.client.put("/api/mode", headers=h, json={"mode": "demo"})).status_code == 200
    used = (await env.store.get_user("owner"))["last_totp_step"]
    stale["last_totp_step"] = used - 1                         # a stale copy from just before
    await env.store.save_user(stale)                           # lands late
    assert (await env.store.get_user("owner"))["last_totp_step"] == used
    r = await env.client.put("/api/mode", headers={**env.auth, "X-2FA-Code": code}, json={"mode": "demo"})
    assert r.status_code == 403 and r.json()["detail"] == "2fa_invalid"


async def test_stop_still_works_with_store_down_after_s18(settings, bot_paths, kalshi):
    app = create_app(settings, store=BrokenStore(), kalshi_factory=lambda m: kalshi)
    tok = sec.create_token(settings, "owner", sec.TYP_ACCESS, 3600)[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/bot/stop", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert app.state.runner.kill_path.exists()
