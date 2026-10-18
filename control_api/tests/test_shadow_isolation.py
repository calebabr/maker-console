"""Shadow mode never delays Stop and never shares the trading path: its own thread and timeout, no pass lock, no env
lock, leader only, skipped while a pass or Stop runs, and a timed-out cycle writes nothing half-done."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from control_api import runner as runmod
from control_api.db import MemoryStore
from control_api.services import shadow as sh
from control_api.tests.test_shadow_cycle import FakeStrategy, leader_runner, live_md, yes_quote

pytestmark = pytest.mark.asyncio


class HangingMD:
    """markets() blocks until the gate opens (a hung Kalshi read)."""
    def __init__(self, gate: threading.Event, started: threading.Event):
        self.gate, self.started = gate, started
        self.inner = live_md()

    def markets(self, *a, **k):
        self.started.set()
        self.gate.wait(10)
        return self.inner.markets(*a, **k)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class NoTouchLock:
    """Stands in for the pass lock: any acquire fails the test."""
    def locked(self):
        return False

    async def acquire(self):
        raise AssertionError("shadow took the pass lock")

    async def __aenter__(self):
        raise AssertionError("shadow took the pass lock")

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def gate():
    g = threading.Event()
    yield g
    g.set()


@pytest.fixture
def demo_keys(monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "kid")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "pk")


async def _until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "condition not reached"
        await asyncio.sleep(0.01)


async def test_cycle_runs_on_its_own_thread_without_pass_or_env_lock(settings, bot_paths, monkeypatch):
    store = MemoryStore()
    threads = []
    md = live_md()
    r = await leader_runner(settings, store, md, monkeypatch, strategy=FakeStrategy([yes_quote()]))

    def factory():
        threads.append(threading.current_thread().name)
        assert not runmod._ENV_LOCK.locked() or threading.current_thread() is not threading.main_thread()
        return md
    r.shadow_factory = factory
    r._pass_lock = NoTouchLock()
    with runmod._ENV_LOCK:                       # the env lock held elsewhere: shadow still completes
        out = await asyncio.wait_for(r.shadow_cycle(), 5)
    assert out["ran"] is True
    assert threads and threads[0].startswith("shadow")
    await r.shutdown()


async def test_skipped_while_pass_stop_standby_or_shutdown(settings, bot_paths, monkeypatch):
    md = live_md()
    r = await leader_runner(settings, MemoryStore(), md, monkeypatch, strategy=FakeStrategy([yes_quote()]))
    for attr, val, reason in (("running_pass", True, "pass_running"), ("_strategy_running", True, "pass_running"),
                              ("_stopping", 1, "stop_running")):
        old = getattr(r, attr)
        setattr(r, attr, val)
        assert await r.shadow_cycle() == {"ran": False, "reason": reason}
        setattr(r, attr, old)
    await r._pass_lock.acquire()                 # a pass holds the lock: shadow skips, it never waits for it
    try:
        assert (await asyncio.wait_for(r.shadow_cycle(), 1))["reason"] == "pass_running"
    finally:
        r._pass_lock.release()
    monkeypatch.setattr(r, "role", lambda: "standby")
    assert (await r.shadow_cycle())["reason"] == "standby"
    monkeypatch.setattr(r, "role", lambda: "leader")
    r.settings.shadow_enabled = False
    assert (await r.shadow_cycle())["reason"] == "disabled"
    r.settings.shadow_enabled = True
    r._shutting_down = True
    assert (await r.shadow_cycle())["reason"] == "shutting_down"
    r._shutting_down = False
    assert md.calls == []                        # none of the skipped cycles read anything
    await r.shutdown()


async def test_timeout_discards_the_partial_result(settings, bot_paths, monkeypatch, gate):
    started = threading.Event()
    store = MemoryStore()
    r = await leader_runner(settings, store, HangingMD(gate, started), monkeypatch,
                            strategy=FakeStrategy([yes_quote()]))
    monkeypatch.setattr(sh, "SHADOW_BUDGET_S", 0.3)
    monkeypatch.setattr(sh, "DEADLINE_MARGIN_S", 0.0)
    out = await r.shadow_cycle()
    assert out == {"ran": False, "reason": "error", "error": "TimeoutError"}
    assert (await r.shadow_cycle())["reason"] == "busy"      # never queues a second worker behind a hung one
    gate.set()                                               # the abandoned worker wakes past its deadline
    await _until(lambda: r._shadow_cf.done())
    # The abandoned worker either notices its deadline (ShadowDeadline) or finishes normally; both are harmless
    # because the async side already gave up and wrote nothing (asserted below).
    assert r._shadow_cf.exception() is None or isinstance(r._shadow_cf.exception(), sh.ShadowDeadline)
    assert await store.shadow_recent(50) == []               # nothing half-done reached the store
    [p] = await store.shadow_recent_passes(5)
    assert p["ok"] is False and p["errors"] == ["TimeoutError"]
    assert await store.get_shadow_summary() is None
    await r.shutdown()


@pytest.mark.slow
async def test_hung_shadow_cycle_does_not_delay_stop(settings, bot_paths, kalshi, demo_keys, gate, monkeypatch):
    monkeypatch.setattr(runmod, "CANCEL_TIMEOUT_S", 2.0)
    started = threading.Event()
    r = await leader_runner(settings, MemoryStore(), HangingMD(gate, started), monkeypatch,
                            strategy=FakeStrategy([yes_quote()]))
    r.kalshi_factory = lambda mode: kalshi
    cycle = asyncio.ensure_future(r.shadow_cycle())
    await _until(started.is_set)
    t0 = time.monotonic()
    res = await asyncio.wait_for(r.stop(by="owner"), 5)
    took = time.monotonic() - t0
    assert res["errors"] == [] and res["cancelled"] == 2 and sorted(kalshi.cancelled) == ["o1", "o3"]
    assert took < 1.0, f"Stop took {took:.2f} s behind a hung shadow cycle"
    assert not cycle.done()                                  # shadow is still stuck; Stop did not wait for it
    gate.set()
    await asyncio.wait_for(cycle, 5)
    await r.shutdown()


async def test_shutdown_cancels_the_shadow_task_without_waiting(settings, bot_paths, monkeypatch):
    r = await leader_runner(settings, MemoryStore(), live_md(), monkeypatch)
    never = asyncio.Event()

    async def stuck():
        await never.wait()
    r._shadow_task = asyncio.create_task(stuck())
    await asyncio.wait_for(r.shutdown(), 5)
    await asyncio.sleep(0)
    assert r._shadow_task.cancelled() or r._shadow_task.done()
    assert r._shadow_task not in r._tasks


async def test_shadow_never_receives_the_trading_factory(settings, bot_paths, monkeypatch):
    calls = []
    r = await leader_runner(settings, MemoryStore(), live_md(), monkeypatch, strategy=FakeStrategy([yes_quote()]))
    r.kalshi_factory = lambda mode: calls.append(mode)
    r.public_factory = lambda env: calls.append(env)
    assert (await r.shadow_cycle())["ran"] is True
    assert calls == []
    await r.shutdown()
