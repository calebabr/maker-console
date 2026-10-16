import httpx
import pytest

from control_api.db import MemoryStore
from control_api.main import create_app
from control_api.tests.conftest import BrokenStore


pytestmark = pytest.mark.asyncio


async def test_stop_without_2fa_cancels_only_mm_and_sets_kill(env):
    env.runner.desired = "running"
    r = await env.client.post("/api/bot/stop", headers=env.auth)   # no X-2FA-Code
    assert r.status_code == 200
    body = r.json()
    assert body["desired"] == "stopped" and body["kill_switch"] is True
    assert sorted(env.kalshi.cancelled) == ["o1", "o3"]            # manual order untouched
    assert body["cancelled"] == 2
    assert env.runner.kill_path.exists()
    assert env.runner.desired == "stopped"
    assert (await env.store.get_state())["desired"] == "stopped"


async def test_stop_requires_login_but_not_2fa(env):
    assert (await env.client.post("/api/bot/stop")).status_code == 401


async def test_stop_works_when_kalshi_raises(env):
    env.kalshi.raise_on_orders = True
    r = await env.client.post("/api/bot/stop", headers=env.auth)
    assert r.status_code == 200
    assert env.runner.kill_path.exists() and env.runner.desired == "stopped"
    assert any(e.startswith("cancel") for e in r.json()["errors"])


async def test_stop_works_when_store_raises(settings, bot_paths, kalshi):
    app = create_app(settings, store=BrokenStore(), kalshi_factory=lambda m: kalshi)
    from control_api.auth import security as sec
    tok = sec.create_token(settings, "owner", sec.TYP_ACCESS, 3600)[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/bot/stop", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert app.state.runner.kill_path.exists()
    assert sorted(kalshi.cancelled) == ["o1", "o3"]
    assert any(e.startswith("store") for e in r.json()["errors"])


async def test_stop_works_when_everything_raises(settings, bot_paths, kalshi):
    kalshi.raise_on_orders = True
    app = create_app(settings, store=BrokenStore(), kalshi_factory=lambda m: kalshi)
    from control_api.auth import security as sec
    tok = sec.create_token(settings, "owner", sec.TYP_ACCESS, 3600)[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/bot/stop", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert app.state.runner.kill_path.exists()


async def test_start_without_2fa_is_403_and_does_not_start(env):
    r = await env.client.post("/api/bot/start", headers=env.auth)
    assert r.status_code == 403 and r.json()["detail"] == "2fa_required"
    assert env.runner.desired == "stopped"


async def test_start_with_2fa_removes_kill_switch_then_stop_restores(env):
    await env.client.post("/api/bot/stop", headers=env.auth)
    r = await env.client.post("/api/bot/start", headers=await env.h2fa())
    assert r.status_code == 200 and env.runner.desired == "running"
    assert not env.runner.kill_path.exists()
    await env.client.post("/api/bot/stop", headers=env.auth)
    assert env.runner.kill_path.exists() and env.runner.desired == "stopped"


async def test_start_with_dead_store_returns_503_and_stays_stopped(settings, bot_paths, kalshi, env):
    env.runner.store = BrokenStore()
    r = await env.client.post("/api/bot/start", headers=await env.h2fa())
    assert r.status_code == 503
    assert env.runner.desired == "stopped"


async def test_stop_concurrent_with_start_ends_stopped(env):
    import asyncio
    h = await env.h2fa()
    start = env.client.post("/api/bot/start", headers=h)
    stop = env.client.post("/api/bot/stop", headers=env.auth)
    await asyncio.gather(start, stop)
    # Run the stop last to prove it is authoritative regardless of interleaving.
    await env.client.post("/api/bot/stop", headers=env.auth)
    assert env.runner.desired == "stopped" and env.runner.kill_path.exists()
    assert (await env.runner.tick("x")) == {"ran": False, "reason": "stopped"}


# ---- S-3: a Stop that could not reach the store is never undone
async def test_stop_pending_retries_until_store_recovers(env):
    import asyncio
    good = env.store
    await good.set_state(by="t", desired="running")
    env.runner.desired = "running"
    env.runner.RETRY_FIRST_DELAY = 0.01
    env.runner.store = BrokenStore()
    r = await env.client.post("/api/bot/stop", headers=env.auth)
    assert r.status_code == 200 and env.runner._stop_pending
    assert (await env.runner.tick("x"))["ran"] is False
    env.runner.store = good                       # Atlas comes back
    for _ in range(100):
        if not env.runner._stop_pending:
            break
        await asyncio.sleep(0.02)
    assert not env.runner._stop_pending
    assert (await good.get_state())["desired"] == "stopped"


async def test_stop_pending_blocks_pass_even_if_desired_running(env):
    env.runner.desired = "running"
    env.runner._stop_pending = True
    assert (await env.runner.tick("x")) == {"ran": False, "reason": "stop_pending"}


async def test_rehydrate_with_kill_switch_stays_stopped_and_persists(settings, bot_paths, kalshi, env):
    import asyncio
    await env.store.set_state(by="t", desired="running")   # store never got the Stop
    env.runner.kill_path.write_text("stopped\n", encoding="utf-8")
    app = create_app(settings, store=env.store, kalshi_factory=lambda m: kalshi)
    runner = app.state.runner
    runner.RETRY_FIRST_DELAY = 0.01
    await runner.rehydrate()                                # simulated restart
    assert runner.desired == "stopped"
    assert (await runner.tick("x"))["ran"] is False
    for _ in range(100):
        if not runner._stop_pending:
            break
        await asyncio.sleep(0.02)
    assert (await env.store.get_state())["desired"] == "stopped"


async def test_start_after_pending_stop_cancels_retry(env):
    env.runner.RETRY_FIRST_DELAY = 0.05
    good = env.store
    env.runner.store = BrokenStore()
    await env.client.post("/api/bot/stop", headers=env.auth)
    env.runner.store = good
    r = await env.client.post("/api/bot/start", headers=await env.h2fa())
    assert r.status_code == 200 and env.runner.desired == "running" and not env.runner._stop_pending
    import asyncio
    await asyncio.sleep(0.2)
    assert (await good.get_state())["desired"] == "running"


# ---- S-4: a Stop during a running pass cancels resting mm- orders again after the pass
async def test_stop_during_pass_recancels_after_pass(env, monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "kid")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "pk")
    r = env.runner
    r.desired, r.mode = "running", "demo"
    r.last_monitor_utc = __import__("control_api.runner", fromlist=["utcnow"]).utcnow()

    def fake_pass(mode):
        r.desired = "stopped"            # Stop lands mid-pass (its own cancel already ran)
        env.kalshi.cancelled.clear()
        return 0, ""
    monkeypatch.setattr(r, "_run_strategy_sync", fake_pass)
    await r._pass("t")
    assert sorted(env.kalshi.cancelled) == ["o1", "o3"]


async def test_normal_pass_does_not_cancel(env, monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "kid")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "pk")
    r = env.runner
    r.desired, r.mode = "running", "demo"
    r.last_monitor_utc = __import__("control_api.runner", fromlist=["utcnow"]).utcnow()
    monkeypatch.setattr(r, "_run_strategy_sync", lambda mode: (0, ""))
    await r._pass("t")
    assert env.kalshi.cancelled == []
