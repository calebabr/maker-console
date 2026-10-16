"""S-33 (list/clear push subscriptions, cleared on logout-all) and S-35 (overall timeout on POST /api/push/test)."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from control_api.routes import alerts as alerts_routes
from control_api.services import webpush as w

pytestmark = pytest.mark.asyncio

ENDPOINT = "https://fcm.googleapis.com/fcm/send/device-secret-456"


def _enable_push(env):
    key = ec.generate_private_key(ec.SECP256R1())
    env.settings.vapid_private_key = w.b64u(key.private_numbers().private_value.to_bytes(32, "big"))
    env.settings.vapid_subject = "mailto:owner@example.com"
    env.runner.push = w.PushService(env.settings, env.store,
                                    transport=httpx.MockTransport(lambda req: httpx.Response(201)))


def _sub(endpoint=ENDPOINT):
    ua = ec.generate_private_key(ec.SECP256R1())
    return {"endpoint": endpoint, "keys": {"p256dh": w.b64u(w.public_bytes(ua)),
                                            "auth": w.b64u(b"0123456789abcdef")}}


async def _subscribe(env, n=2):
    for i in range(n):
        r = await env.client.post("/api/push/subscribe", json=_sub(f"{ENDPOINT}{i}"), headers=env.auth)
        assert r.status_code == 200


async def test_list_shows_host_and_time_only(env):
    _enable_push(env)
    await _subscribe(env, 2)
    r = await env.client.get("/api/push/subscriptions", headers=env.auth)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert all(set(s) == {"host", "created_utc"} and s["host"] == "fcm.googleapis.com" for s in body["subscriptions"])
    assert "device-secret" not in r.text and "p256dh" not in r.text and "auth" not in r.text


async def test_list_and_clear_need_login(env):
    assert (await env.client.get("/api/push/subscriptions")).status_code == 401
    assert (await env.client.delete("/api/push/subscriptions")).status_code == 401


async def test_clear_all_removes_everything_and_is_audited(env):
    _enable_push(env)
    await _subscribe(env, 3)
    r = await env.client.delete("/api/push/subscriptions", headers=env.auth)
    assert r.status_code == 200 and r.json() == {"removed": 3}
    assert await env.store.list_push_subscriptions() == []
    audits = [a for a in await env.store.list_audit() if a.get("action") == "push.clear"]
    assert audits and "device-secret" not in str(audits)
    again = await env.client.delete("/api/push/subscriptions", headers=env.auth)
    assert again.json() == {"removed": 0}


async def test_clear_works_when_push_is_not_configured(env):
    assert (await env.client.delete("/api/push/subscriptions", headers=env.auth)).json() == {"removed": 0}


async def test_logout_all_clears_push_subscriptions(env):
    _enable_push(env)
    await _subscribe(env, 2)
    r = await env.client.post("/api/auth/logout-all", headers=await env.h2fa())
    assert r.status_code == 200, r.text
    assert await env.store.list_push_subscriptions() == []


async def test_logout_all_still_succeeds_if_clearing_fails(env, monkeypatch):
    async def boom():
        raise RuntimeError("atlas down")
    monkeypatch.setattr(env.store, "clear_push_subscriptions", boom)
    r = await env.client.post("/api/auth/logout-all", headers=await env.h2fa())
    assert r.status_code == 200, r.text


async def test_push_test_has_an_overall_timeout(env, monkeypatch):
    class SlowPush:
        enabled = True

        async def send_all(self, *a, **k):
            await asyncio.sleep(5)
            return {"sent": 1}
    env.runner.push = SlowPush()
    monkeypatch.setattr(alerts_routes, "PUSH_TEST_TIMEOUT_S", 0.05)
    r = await asyncio.wait_for(env.client.post("/api/push/test", headers=env.auth), 2)
    assert r.status_code == 200
    assert r.json() == {"sent": 0, "failed": 0, "removed": 0, "error": "timeout"}
