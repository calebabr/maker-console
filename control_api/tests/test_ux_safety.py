"""UX sprint safety gap-fill: Stop toast fields without 2FA, undo never carries cells, push routes need login,
alert history carries no secrets."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from control_api.main import create_app
from control_api.services import webpush as w
from control_api.tests.conftest import BrokenStore

pytestmark = pytest.mark.asyncio

ENDPOINT = "https://fcm.googleapis.com/fcm/send/device-secret-xyz"


async def test_stop_without_2fa_returns_toast_fields(env):
    env.runner.desired = "running"
    r = await env.client.post("/api/bot/stop", headers=env.auth)     # no X-2FA-Code
    assert r.status_code == 200
    b = r.json()
    assert datetime.fromisoformat(b["stopped_at"])
    assert b["orders_cancelled"] == 2 and b["cancelled"] == 2
    assert b["desired"] == "stopped" and b["kill_switch"] is True


async def test_stop_toast_fields_survive_a_broken_store_and_kalshi(settings, bot_paths, kalshi):
    kalshi.raise_on_orders = True
    app = create_app(settings, store=BrokenStore(), kalshi_factory=lambda m: kalshi)
    from control_api.auth import security as sec
    tok = sec.create_token(settings, "owner", sec.TYP_ACCESS, 3600)[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/bot/stop", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    b = r.json()
    assert b["kill_switch"] is True and "stopped_at" in b and b["errors"]
    assert "orders_cancelled" in b


async def test_undo_needs_2fa_and_takes_no_cells_from_the_body(env):
    assert (await env.client.post("/api/config/undo", headers=env.auth)).status_code in (401, 403)
    cfg = dict(env.runner.config)
    await env.store.save_config(dict(cfg, run_budget=cfg["run_budget"] + 1), before=cfg, by="owner")
    r = await env.client.post("/api/config/undo", json={"cells": [[0.1, 0.2, 0, 1, "yes"]]},
                              headers=await env.h2fa())
    assert env.runner.config["cells"] == cfg["cells"]
    assert r.status_code in (200, 400, 409, 422)
    on_disk = json.loads(env.runner.config_path.read_text()) if hasattr(env.runner, "config_path") else None
    if on_disk:
        assert on_disk["cells"] == cfg["cells"]


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/push/public-key", None),
    ("POST", "/api/push/subscribe", {"endpoint": ENDPOINT, "keys": {"p256dh": "a", "auth": "b"}}),
    ("DELETE", "/api/push/subscribe", {"endpoint": ENDPOINT}),
    ("POST", "/api/push/test", None),
    ("GET", "/api/alerts", None),
    ("GET", "/api/alerts/settings", None),
    ("PUT", "/api/alerts/settings", {"mode": "problems"}),
    ("POST", "/api/config/undo", None),
])
async def test_ux_routes_reject_unauthenticated_calls(env, method, path, body):
    r = await env.client.request(method, path, json=body)
    assert r.status_code == 401
    r = await env.client.request(method, path, json=body, headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


async def test_alert_history_carries_no_secrets(env):
    key = ec.generate_private_key(ec.SECP256R1())
    env.settings.vapid_private_key = w.b64u(key.private_numbers().private_value.to_bytes(32, "big"))
    env.settings.vapid_subject = "mailto:owner@example.com"
    env.runner.push = w.PushService(env.settings, env.store,
                                    transport=httpx.MockTransport(lambda req: httpx.Response(201)))
    ua = ec.generate_private_key(ec.SECP256R1())
    auth = w.b64u(b"0123456789abcdef")
    sub = {"endpoint": ENDPOINT, "keys": {"p256dh": w.b64u(w.public_bytes(ua)), "auth": auth}}
    await env.client.post("/api/push/subscribe", json=sub, headers=env.auth)
    await env.client.post("/api/push/test", headers=env.auth)
    await env.client.post("/api/bot/stop", headers=env.auth)
    for _ in range(50):
        if not env.runner._bg:
            break
        await asyncio.sleep(0.01)
    r = await env.client.get("/api/alerts", params={"limit": 200}, headers=env.auth)
    assert r.status_code == 200 and r.json()
    blob = r.text
    for secret in ("device-secret-xyz", auth, sub["keys"]["p256dh"], env.settings.vapid_private_key,
                   env.token, env.secret):
        assert secret not in blob
    for item in r.json():
        assert set(item) == {"id", "ts", "type", "title", "body"} and len(item["body"] or "") <= 200
