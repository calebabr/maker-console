"""Sprint 2A-3 Stop trio again: Stop still wins while shadow variant cycles (SHADOW_VARIANTS on, cadences 5 and 7) are
in flight, with Atlas down and with Kalshi down. The variants never block, delay or restart Stop. Fakes only."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from control_api import runner as runmod
from control_api.db import MemoryStore
from control_api.services import shadow as sh
from control_api.tests.conftest import BrokenStore
from control_api.tests.test_shadow_cycle import FakeStrategy, leader_runner, use_strategy, yes_quote
from control_api.tests.test_shadow_isolation import HangingMD, demo_keys, gate  # noqa: F401  (fixtures)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _variants(monkeypatch, tmp_path):
    monkeypatch.setenv(sh.SHADOW_VARIANTS_ENV, "true")
    monkeypatch.setenv(sh.FEE_SCHEDULE_ENV, str(tmp_path / "absent_fee_schedule.json"))


async def _until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "condition not reached"
        await asyncio.sleep(0.01)


async def _runner_with_hung_variant(settings, monkeypatch, gate_ev, started, kalshi):
    r = await leader_runner(settings, MemoryStore(), HangingMD(gate_ev, started), monkeypatch,
                            strategy=FakeStrategy([yes_quote()]))
    r.cloud["shadow_intervals"] = [5, 7, 10]
    r.kalshi_factory = lambda mode: kalshi
    return r


async def test_stop_wins_with_a_variant_cycle_in_flight(settings, bot_paths, kalshi, demo_keys, gate, real_strategy,
                                                        monkeypatch):
    started = threading.Event()
    r = await _runner_with_hung_variant(settings, monkeypatch, gate, started, kalshi)
    cycle = asyncio.ensure_future(r.shadow_cycle(interval=5))
    await _until(started.is_set)
    assert r._shadow_cycle_lock.locked()                        # the variant holds the shadow lock, not Stop's
    t0 = time.monotonic()
    res = await asyncio.wait_for(r.stop(by="owner"), 5)
    assert time.monotonic() - t0 < 1.0
    assert res["errors"] == [] and sorted(kalshi.cancelled) == ["o1", "o3"]       # mm- only, manual-1 untouched
    assert r.desired == "stopped" and r.kill_path.exists() and not cycle.done()
    gate.set()
    out = await asyncio.wait_for(cycle, 5)
    assert out["ran"] is True
    assert r.desired == "stopped" and r.kill_path.exists()      # the variant restarted nothing
    await r.shutdown()


async def test_http_stop_without_2fa_while_the_variant_loop_is_running(env, real_strategy, gate, monkeypatch):
    started = threading.Event()
    monkeypatch.setattr(runmod, "SHADOW_VARIANT_TICK_S", 0.01)
    r = env.runner
    env.settings.shadow_enabled = True
    r.cloud["shadow_enabled"] = True
    r.cloud["shadow_intervals"] = [5, 10]
    monkeypatch.setattr(r, "role", lambda: "leader")
    r.shadow_factory = lambda: HangingMD(gate, started)
    use_strategy(monkeypatch, r, FakeStrategy([yes_quote()]))
    r.desired = "running"
    loop_task = asyncio.ensure_future(r._shadow_variant_loop())
    await _until(started.is_set)
    resp = await asyncio.wait_for(env.client.post("/api/bot/stop", headers=env.auth), 5)
    assert resp.status_code == 200 and "X-2FA-Code" not in env.auth
    body = resp.json()
    assert body["desired"] == "stopped" and body["kill_switch"] is True
    assert sorted(env.kalshi.cancelled) == ["o1", "o3"] and r.kill_path.exists()
    gate.set()
    loop_task.cancel()
    assert r.desired == "stopped"
    await r.shutdown()


@pytest.mark.slow
async def test_stop_with_atlas_down_while_a_variant_is_in_flight(settings, bot_paths, kalshi, demo_keys, gate,
                                                                 real_strategy, monkeypatch):
    started = threading.Event()
    r = await _runner_with_hung_variant(settings, monkeypatch, gate, started, kalshi)
    r.RETRY_FIRST_DELAY = 0.05                                  # the background store retry must land inside the test's wait
    cycle = asyncio.ensure_future(r.shadow_cycle(interval=7))
    await _until(started.is_set)
    good, r.store = r.store, BrokenStore()                      # Atlas goes down mid-flight
    t0 = time.monotonic()
    await asyncio.wait_for(r.stop(by="owner"), 10)
    assert time.monotonic() - t0 < 2.0
    assert r.desired == "stopped" and r.kill_path.exists()
    assert sorted(kalshi.cancelled) == ["o1", "o3"]
    gate.set()
    out = await asyncio.wait_for(cycle, 10)                     # the variant never raises with the store down
    assert isinstance(out, dict)
    r.store = good
    await _until(lambda: not r._stop_pending, 5)
    assert (await good.get_state())["desired"] == "stopped"
    await r.shutdown()


async def test_stop_with_kalshi_down_while_a_variant_is_in_flight(settings, bot_paths, kalshi, demo_keys, gate,
                                                                  real_strategy, monkeypatch):
    started = threading.Event()
    r = await _runner_with_hung_variant(settings, monkeypatch, gate, started, kalshi)
    kalshi.raise_on_orders = True
    r.RETRY_FIRST_DELAY = 0.01
    cycle = asyncio.ensure_future(r.shadow_cycle(interval=5))
    await _until(started.is_set)
    await asyncio.wait_for(r.stop(by="owner"), 10)
    assert r.desired == "stopped" and r.kill_path.exists()      # the kill switch is written whatever Kalshi does
    gate.set()
    out = await asyncio.wait_for(cycle, 10)
    assert isinstance(out, dict) and r.desired == "stopped"
    await r.shutdown()


async def test_no_variant_cycle_runs_during_stop_and_none_can_start_the_bot(settings, bot_paths, kalshi, demo_keys,
                                                                            real_strategy, monkeypatch):
    r = await leader_runner(settings, MemoryStore(), None, monkeypatch, strategy=FakeStrategy([yes_quote()]))
    from control_api.tests.test_shadow_cycle import live_md
    md = live_md()
    r.shadow_factory = lambda: md
    r.cloud["shadow_intervals"] = [5, 7, 10]
    r.kalshi_factory = lambda mode: kalshi
    r.desired = "running"
    r._stopping = 1                                             # Stop is running right now
    assert (await r.shadow_cycle(interval=5))["reason"] == "stop_running"
    assert (await r.shadow_cycle(interval=7))["reason"] == "stop_running"
    assert md.calls == []                                       # no read at all
    r._stopping = 0
    await r.stop(by="owner")
    await r.shadow_cycle(interval=5)                            # after Stop a variant (read only) may run
    assert r.desired == "stopped" and r.kill_path.exists()
    assert (await r.tick("x"))["ran"] is False                  # the maker is still stopped
    await r.shutdown()
