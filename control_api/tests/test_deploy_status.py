"""R5: GET /api/deploy/status (read-only deploy-status key) and the R4 fields on /healthz and /api/status."""
from __future__ import annotations

import pytest

from control_api import version as ver
from control_api.routes.deploy_status import FIELDS

pytestmark = pytest.mark.asyncio
KEY = "deploy-status-key-for-tests-0123456789"
URL = "/api/deploy/status"


@pytest.fixture
def ds_env(env, monkeypatch):
    monkeypatch.setattr(env.settings, "deploy_status_key", KEY)
    return env


async def test_unset_key_is_401(env):
    assert env.settings.deploy_status_key is None
    r = await env.client.get(URL, headers={"X-Deploy-Status-Key": "anything"})
    assert r.status_code == 401


@pytest.mark.parametrize("bad", [None, "", "x", KEY + "x", KEY[:-1], "k" * 300])
async def test_wrong_or_missing_key_is_401(ds_env, bad):
    h = {} if bad is None else {"X-Deploy-Status-Key": bad}
    r = await ds_env.client.get(URL, headers=h)
    assert r.status_code == 401 and KEY not in r.text


async def test_login_token_is_not_the_key(ds_env):
    r = await ds_env.client.get(URL, headers=ds_env.auth)
    assert r.status_code == 401


async def test_exact_fields_and_no_secrets(ds_env):
    r = await ds_env.client.get(URL, headers={"X-Deploy-Status-Key": KEY})
    assert r.status_code == 200
    body = r.json()
    assert tuple(body) == FIELDS
    assert body["version"] == ver.API_VERSION and body["schema_version"] == ver.SCHEMA_VERSION
    assert body["role"] == "leader" and body["lease_holder"] == ds_env.runner.instance_id
    assert body["generation"] == 1 and body["mode"] == "demo"
    assert body["running"] is False and body["stopped"] is True and body["arb_executing"] is False
    assert body["healthy"] is True
    for leak in (KEY, ds_env.settings.tick_token, ds_env.settings.jwt_secret, "cash", "cells"):
        assert leak not in r.text


async def test_released_lease_has_no_holder(ds_env):
    await ds_env.runner.lease.release()
    body = (await ds_env.client.get(URL, headers={"X-Deploy-Status-Key": KEY})).json()
    assert body["lease_holder"] is None and body["role"] == "standby"


async def test_store_down_is_unhealthy(ds_env):
    from control_api.tests.conftest import BrokenStore
    ds_env.runner.lease.store = BrokenStore()
    body = (await ds_env.client.get(URL, headers={"X-Deploy-Status-Key": KEY})).json()
    assert body["healthy"] is False and tuple(body) == FIELDS


async def test_rate_limited_before_the_key_check(ds_env):
    codes = [(await ds_env.client.get(URL, headers={"X-Deploy-Status-Key": "wrong"})).status_code
             for _ in range(31)]
    assert codes[:30] == [401] * 30 and codes[30] == 429
    r = await ds_env.client.get(URL, headers={"X-Deploy-Status-Key": KEY})
    assert r.status_code == 429                                # the right key does not skip the limit


async def test_healthz_and_status_handshake(env):
    h = (await env.client.get("/healthz")).json()
    assert h["ok"] is True and h["api_version"] == ver.API_VERSION
    assert h["min_app_version"] == ver.MIN_APP_VERSION and h["schema_version"] == ver.SCHEMA_VERSION
    assert h["role"] == "leader" and h["lease_generation"] == 1
    s = (await env.client.get("/api/status", headers=env.auth)).json()
    for k in ("api_version", "min_app_version", "schema_version", "role", "lease_generation", "lease_expires_at"):
        assert k in s
    assert s["role"] == "leader"


async def test_version_env_is_sanitised(monkeypatch):
    assert ver._clean("2026.10.16-1", "d") == "2026.10.16-1"
    for bad in ("", " ", "<script>", "a" * 65, "v1 2"):
        assert ver._clean(bad, "d") == "d"


async def test_version_falls_back_to_the_render_commit():
    assert ver.resolve_api_version({"APP_VERSION": "2026.10.16-1", "RENDER_GIT_COMMIT": "abcdef0123"}) == "2026.10.16-1"
    assert ver.resolve_api_version({"APP_VERSION": "", "RENDER_GIT_COMMIT": "ABCDEF0123456"}) == "abcdef0"
    assert ver.resolve_api_version({"RENDER_GIT_COMMIT": "not-a-sha"}) == "0.0.0-dev"
    assert ver.resolve_api_version({"RENDER_GIT_COMMIT": "abc"}) == "0.0.0-dev"
    assert ver.resolve_api_version({}) == "0.0.0-dev"
