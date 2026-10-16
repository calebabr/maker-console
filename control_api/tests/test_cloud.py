import httpx
import pytest

from control_api.services import keepalive as ka
from control_api.tests.conftest import TICK

pytestmark = pytest.mark.asyncio


async def _put(env, body):
    return await env.client.put("/api/cloud", headers=await env.h2fa(), json=body)


async def test_get_cloud_needs_login(env):
    assert (await env.client.get("/api/cloud")).status_code == 401
    assert (await env.client.get("/api/cloud", headers=env.auth)).status_code == 200


async def test_put_cloud_needs_2fa(env):
    r = await env.client.put("/api/cloud", headers=env.auth, json={"pinger_minutes": 5})
    assert r.status_code == 403 and env.cron.calls == []


@pytest.mark.parametrize("body", [
    {"keepalive_minutes": 0}, {"keepalive_minutes": 15}, {"pinger_minutes": 0}, {"pinger_minutes": 15},
    {"pinger_minutes": -3}, {"pinger_minutes": 2.5}, {"pinger_enabled": "maybe"}, {"silent_alert_minutes": 5},
    {"silent_alert_minutes": 241},
])
async def test_out_of_range_rejected(env, body):
    r = await _put(env, body)
    assert r.status_code in (400, 422), r.text   # 400 = our validator, 422 = pydantic type check


@pytest.mark.parametrize("n", [1, 14])
async def test_range_boundaries_accepted(env, n):
    r = await _put(env, {"keepalive_minutes": n, "pinger_minutes": n})
    assert r.status_code == 200
    assert r.json()["settings"]["keepalive_minutes"] == n


async def test_put_calls_cronjob_with_right_schedule_url_token(env):
    r = await _put(env, {"pinger_minutes": 7, "pinger_enabled": True})
    assert r.status_code == 200
    job_id, kw = env.cron.calls[-1]
    assert job_id is None
    assert kw == {"public_url": "https://api.example.com", "tick_token": TICK, "minutes": 7, "enabled": True}
    assert r.json()["pinger"]["job_id"] == 4242 and r.json()["pinger"]["synced"] is True
    # second change updates the same job id
    await _put(env, {"pinger_minutes": 3})
    assert env.cron.calls[-1][0] == 4242 and env.cron.calls[-1][1]["minutes"] == 3


async def test_keepalive_only_change_does_not_resync_once_job_exists(env):
    await _put(env, {"pinger_minutes": 5})
    n = len(env.cron.calls)
    await _put(env, {"keepalive_minutes": 9})
    assert len(env.cron.calls) == n


async def test_sync_failure_is_reported_not_fatal(env):
    env.cron.fail = True
    r = await _put(env, {"pinger_minutes": 5})
    assert r.status_code == 200
    p = r.json()["pinger"]
    assert p["synced"] is False and "RuntimeError" in p["error"]
    assert r.json()["settings"]["pinger_minutes"] == 5            # the setting itself was saved
    assert "cron-key" not in r.text


async def test_sync_not_configured_reports_missing(env):
    env.settings.cronjob_api_key = None
    r = await _put(env, {"pinger_minutes": 5})
    assert r.status_code == 200 and "CRONJOB_API_KEY" in r.json()["pinger"]["error"] and env.cron.calls == []


async def test_manual_sync_needs_2fa_and_calls_cron(env):
    assert (await env.client.post("/api/cloud/pinger/sync", headers=env.auth)).status_code == 403
    r = await env.client.post("/api/cloud/pinger/sync", headers=await env.h2fa())
    assert r.status_code == 200 and len(env.cron.calls) == 1


async def test_cron_schedule_and_job_body():
    from control_api.services.cronjob import job_body, schedule
    assert schedule(5)["minutes"] == list(range(0, 60, 5))
    assert schedule(99)["minutes"] == list(range(0, 60, 14))      # clamped to 14
    b = job_body("https://x.io/", "tok", 5, True)["job"]
    assert b["url"] == "https://x.io/api/tick" and b["requestMethod"] == 1
    assert b["extendedData"]["headers"] == {"X-Tick-Token": "tok"}


async def test_cronjob_client_uses_bearer_and_creates_then_updates():
    from control_api.services.cronjob import CronJobClient
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, req.headers.get("authorization")))
        if req.method == "PATCH":
            return httpx.Response(404)
        return httpx.Response(200, json={"jobId": 77})
    c = CronJobClient("K", transport=httpx.MockTransport(handler))
    jid = await c.sync(5, public_url="https://x.io", tick_token="t", minutes=5, enabled=True)
    assert jid == 77
    assert seen == [("PATCH", "/jobs/5", "Bearer K"), ("PUT", "/jobs", "Bearer K")]


async def test_tick_token_required(env):
    assert (await env.client.post("/api/tick")).status_code == 403
    assert (await env.client.post("/api/tick", headers={"X-Tick-Token": "wrong"})).status_code == 403
    r = await env.client.post("/api/tick", headers={"X-Tick-Token": TICK})
    assert r.status_code == 200 and r.json() == {"ran": False, "reason": "stopped"}


async def test_tick_disabled_when_no_token_configured(env):
    env.settings.tick_token = None
    r = await env.client.post("/api/tick", headers={"X-Tick-Token": "anything"})
    assert r.status_code == 403


async def test_ping_once_hits_healthz_and_records(env):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json={"ok": True})
    st = await ka.ping_once(env.runner, transport=httpx.MockTransport(handler))
    assert seen == ["https://api.example.com/healthz"] and st["ok"] is True


async def test_ping_once_failure_recorded_and_alerts(env):
    def handler(req):
        raise httpx.ConnectError("no")
    alerts = []

    async def send(*a, **k):
        alerts.append(a)
    env.runner.alerter.send = send
    st = await ka.ping_once(env.runner, transport=httpx.MockTransport(handler))
    assert st["ok"] is False and alerts


async def test_keepalive_loop_uses_configured_interval(env, monkeypatch):
    import asyncio
    sleeps = []
    pings = []

    async def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    async def fake_ping(runner, transport=None):
        pings.append(1)
    env.runner.cloud["keepalive_minutes"] = 7
    monkeypatch.setattr(ka.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ka, "ping_once", fake_ping)
    with pytest.raises(asyncio.CancelledError):
        await ka.keepalive_loop(env.runner)
    assert sleeps[0] == 7 * 60 and len(pings) == 1
