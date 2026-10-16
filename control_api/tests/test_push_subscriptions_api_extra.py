"""Mobile UX wave 3: push device list/clear audit, rate limits, store failures and the cap as seen through the API;
plus the CSV filename for every filter combination."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from control_api.db import schemas as S
from control_api.routes import alerts as alerts_routes
from control_api.tests.test_push_subscriptions import ENDPOINT, _enable_push, _sub, _subscribe

pytestmark = pytest.mark.asyncio


async def test_clear_audit_records_user_count_and_no_endpoint(env):
    _enable_push(env)
    await _subscribe(env, 2)
    await env.client.delete("/api/push/subscriptions", headers=env.auth)
    a = [x for x in await env.store.list_audit() if x.get("action") == "push.clear"]
    assert len(a) == 1
    assert a[0]["ok"] is True and a[0]["details"] == {"removed": 2} and a[0]["user"]
    assert "device-secret" not in str(a) and "fcm.googleapis.com" not in str(a)


async def test_clear_all_never_needs_2fa_header(env):
    _enable_push(env)
    await _subscribe(env, 1)
    r = await env.client.delete("/api/push/subscriptions", headers=env.auth)   # env.auth carries no X-2FA-Code
    assert r.status_code == 200 and r.json() == {"removed": 1}


async def test_list_and_clear_return_503_when_store_is_down_not_500(env, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("atlas down")
    monkeypatch.setattr(env.store, "list_push_subscriptions", boom)
    monkeypatch.setattr(env.store, "clear_push_subscriptions", boom)
    r1 = await env.client.get("/api/push/subscriptions", headers=env.auth)
    r2 = await env.client.delete("/api/push/subscriptions", headers=env.auth)
    assert r1.status_code == 503 and r2.status_code == 503
    assert "atlas down" not in r1.text + r2.text


async def test_list_is_oldest_first(env):
    _enable_push(env)
    await _subscribe(env, 3)
    subs = (await env.client.get("/api/push/subscriptions", headers=env.auth)).json()["subscriptions"]
    stamps = [s["created_utc"] for s in subs]
    assert stamps == sorted(stamps)


async def test_list_is_rate_limited_per_user(env, monkeypatch):
    monkeypatch.setitem(alerts_routes.RATE_LIMITS, "push_list", (3, 60.0))
    codes = [(await env.client.get("/api/push/subscriptions", headers=env.auth)).status_code for _ in range(6)]
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]


async def test_subscription_cap_is_reflected_in_the_list(env):
    _enable_push(env)
    ok = 0
    for i in range(S.PUSH_SUBSCRIPTIONS_MAX + 2):
        r = await env.client.post("/api/push/subscribe", json=_sub(f"{ENDPOINT}cap{i}"), headers=env.auth)
        ok += r.status_code == 200
        if r.status_code == 429:      # the route's own rate limit can trip first; the store cap is covered elsewhere
            break
    body = (await env.client.get("/api/push/subscriptions", headers=env.auth)).json()
    assert body["count"] <= S.PUSH_SUBSCRIPTIONS_MAX
    assert body["count"] == len(body["subscriptions"])


@pytest.mark.parametrize("qs", ["", "?days=7", "?kind=taker", "?days=30&env=demo", "?days=7&kind=maker&result=win"])
async def test_trades_csv_filename_is_date_only_for_any_filter(env, qs):
    r = await env.client.get(f"/api/analytics/trades.csv{qs}", headers=env.auth)
    assert r.status_code == 200, r.text
    cd = r.headers["content-disposition"]
    assert cd == f'attachment; filename="trades-{datetime.now(timezone.utc):%Y-%m-%d}.csv"'
    assert re.fullmatch(r'attachment; filename="trades-\d{4}-\d{2}-\d{2}\.csv"', cd)
    assert "demo" not in cd and "live" not in cd and ("long" + "shot") not in cd     # the retired app name


async def test_trades_csv_needs_login(env):
    assert (await env.client.get("/api/analytics/trades.csv")).status_code == 401
