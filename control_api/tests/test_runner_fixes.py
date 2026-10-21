"""Contract section 5: Start reloads the config (and clears a stale invalid-config error), the shadow loop retries
soon after a transient skip, and the lease expiry shown by /status follows each renew."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from control_api import runner as rn
from control_api.db import MemoryStore
from control_api.services.lease import LeaseManager

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- Start reloads the config
async def _break_stored_config(env):
    cfg = dict(await env.store.get_config())
    cfg["run_budget"] = 10 ** 9                                       # far above the API ceiling
    await env.store.save_config(cfg, before=None, by="test")
    return cfg


async def test_start_after_fixing_the_stored_config_clears_the_error(env):
    good = dict(await env.store.get_config())
    await _break_stored_config(env)
    assert await env.runner._load_config() is False                   # e.g. at rehydrate: stays stopped
    assert env.runner.last_error.startswith("stored config failed validation")
    await env.store.save_config(good, before=None, by="owner")       # the owner fixes it (or restores a version)
    r = await env.client.post("/api/bot/start", headers=await env.h2fa())
    assert r.status_code == 200 and r.json() == {"desired": "running"}
    assert env.runner.desired == "running" and env.runner.last_error is None
    assert (await env.store.get_state())["desired"] == "running"


async def test_start_with_a_still_invalid_config_fails_with_the_reason(env):
    await _break_stored_config(env)
    r = await env.client.post("/api/bot/start", headers=await env.h2fa())
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail.startswith("invalid_config:") and "run_budget" in detail
    assert env.runner.desired == "stopped"
    assert ((await env.store.get_state()) or {}).get("desired", "stopped") == "stopped"   # never written running


async def test_start_picks_up_a_config_changed_in_the_store(env):
    cfg = dict(await env.store.get_config())
    cfg["run_budget"] = 33
    await env.store.save_config(cfg, before=None, by="other-instance")
    await env.runner.start_trading(by="owner")
    assert env.runner.config["run_budget"] == 33


async def test_stop_during_the_config_reload_wins(env):
    real = env.store.get_config
    gate = asyncio.Event()

    async def slow_get_config():
        await gate.wait()
        return await real()
    env.store.get_config = slow_get_config
    start = asyncio.create_task(env.runner.start_trading(by="owner"))
    await asyncio.sleep(0)
    res = await env.runner.stop(by="owner")
    assert res["desired"] == "stopped"
    gate.set()
    with pytest.raises(rn.StopInProgress):
        await start
    assert env.runner.desired == "stopped" and env.runner.kill_switch()
    assert (await env.store.get_state())["desired"] == "stopped"


async def test_stop_never_reloads_the_config(env):
    async def boom(*a, **k):
        raise AssertionError("Stop must not read the config")
    env.store.get_config = boom
    res = await env.runner.stop(by="owner")
    assert res["desired"] == "stopped" and not any("config" in e for e in res["errors"])


# ---------------------------------------------------------------- shadow retry after a transient skip
@pytest.mark.parametrize("res,full", [
    ({"ran": True, "pass": {}}, True),
    ({"ran": False, "reason": "disabled"}, True),
    ({"ran": False, "reason": "runner_disabled"}, True),
    ({"ran": False, "reason": "shutting_down"}, True),
    ({"ran": False, "reason": "standby"}, False),
    ({"ran": False, "reason": "pass_running"}, False),
    ({"ran": False, "reason": "busy"}, False),
    ({"ran": False, "reason": "stop_running"}, False),
    ({"ran": False, "reason": "error", "error": "TimeoutError"}, False),
    (None, False),                                                    # shadow_cycle raised
])
async def test_shadow_next_sleep(env, res, full):
    r = env.runner
    r.config = {**r.config, "poll_minutes": 10}
    assert r._shadow_next_sleep_s(res) == (600.0 if full else rn.SHADOW_RETRY_S)


async def test_shadow_loop_retries_soon_after_a_skip(env, monkeypatch):
    r = env.runner
    r.config = {**r.config, "poll_minutes": 10}
    results = iter([{"ran": False, "reason": "standby"}, {"ran": True, "pass": {}}])
    sleeps = []
    real_sleep = asyncio.sleep

    async def cycle():
        return next(results)

    async def fake_sleep(s, *a, **k):
        sleeps.append(s)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError
        await real_sleep(0)
    monkeypatch.setattr(r, "shadow_cycle", cycle)
    monkeypatch.setattr(rn.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await r._shadow_loop()
    monkeypatch.setattr(rn.asyncio, "sleep", real_sleep)
    assert sleeps == [300, rn.SHADOW_RETRY_S, 600.0]                 # offset, quick retry, then the full interval


# ---------------------------------------------------------------- lease expiry follows renews
T0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


async def test_lease_snapshot_follows_renew():
    store = MemoryStore()
    c = {"mono": 1000.0, "wall": T0}
    m = LeaseManager(store, instance_id="a", version="v", ttl_s=60, renew_s=20,
                     clock=lambda: c["mono"], wall=lambda: c["wall"])
    assert await m.try_acquire()
    assert m.snapshot()["lease_expires_at"].startswith("2026-10-21T12:01:00")
    for n in (1, 2, 3):
        c["mono"] += 20
        c["wall"] += timedelta(seconds=20)
        assert await m.renew()
        exp = (await store.get_lease())["expires_at"]
        assert m.snapshot()["lease_expires_at"] == exp.isoformat()
        assert exp == T0 + timedelta(seconds=60 + 20 * n)
    assert await m.release()
    assert m.snapshot()["lease_expires_at"] == c["wall"].isoformat()


async def test_failed_renew_does_not_move_the_shown_expiry():
    store = MemoryStore()
    c = {"mono": 1000.0, "wall": T0}
    m = LeaseManager(store, instance_id="a", version="v", ttl_s=60, renew_s=20,
                     clock=lambda: c["mono"], wall=lambda: c["wall"])
    await m.try_acquire()
    before = m.snapshot()["lease_expires_at"]

    async def boom(*a, **k):
        raise RuntimeError("atlas down")
    store.renew_lease = boom
    c["wall"] += timedelta(seconds=20)
    assert not await m.renew()
    assert m.snapshot()["lease_expires_at"] == before


async def test_status_lease_expiry_is_the_renewed_one(env):
    r = env.runner
    assert r.role() == "leader"
    first = (await env.client.get("/api/status", headers=env.auth)).json()["lease_expires_at"]
    await asyncio.sleep(0.01)
    assert await r.lease.renew()
    st = (await env.client.get("/api/status", headers=env.auth)).json()
    stored = (await env.store.get_lease())["expires_at"]
    shown = datetime.fromisoformat(st["lease_expires_at"])
    assert shown > datetime.fromisoformat(first)
    assert timedelta(0) <= stored - shown < timedelta(seconds=2)       # never later than the stored expiry
