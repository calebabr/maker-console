"""Security review pass 11 regressions (S-53, S-54, S-59) with two runners that each have their OWN kill files, as
Render's per-instance ephemeral disks do (a shared kill file masks these bugs)."""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from control_api.db import MemoryStore
from control_api.runner import Runner

pytestmark = pytest.mark.asyncio


class RecordingAlerter:
    def __init__(self):
        self.sent = []

    async def send(self, title, body="", priority="default"):
        self.sent.append((title, body, priority))
        return True


class OwnDiskRunner(Runner):
    """A runner whose kill files live in its own directory (`disk`)."""
    disk: Path

    @property
    def kill_path(self) -> Path:
        return self.disk / "MAKER_KILL"

    @property
    def arb_kill_path(self) -> Path:
        return self.disk / "ARB_KILL"


@pytest.fixture
def demo_keys(monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "kid")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "pk")


def _runner(settings, store, kalshi, disk: Path) -> OwnDiskRunner:
    disk.mkdir(parents=True, exist_ok=True)
    r = OwnDiskRunner(settings, store, kalshi_factory=lambda m: kalshi)
    r.disk = disk
    r.alerter = RecordingAlerter()
    r.last_monitor_utc = datetime.now(timezone.utc)
    return r


async def _drain(r):
    await asyncio.gather(*list(r._bg), return_exceptions=True)


async def test_disks_are_separate(settings, bot_paths, kalshi, demo_keys, tmp_path):
    store = MemoryStore()
    a, b = _runner(settings, store, kalshi, tmp_path / "a"), _runner(settings, store, kalshi, tmp_path / "b")
    await b.stop(by="owner")
    assert b.kill_switch() and not a.kill_switch()


async def test_s53_standby_stop_during_leader_shutdown_cancels_the_pass_orders(settings, bot_paths, kalshi,
                                                                                 demo_keys, monkeypatch, tmp_path):
    """Deploy overlap: A (old leader) is mid-pass, B (new standby) gets the owner's Stop, then A gets SIGTERM.
    The order A's pass placed after B's cancel must still be cancelled."""
    store = MemoryStore()
    await store.set_state(by="owner", desired="running")
    a, b = _runner(settings, store, kalshi, tmp_path / "a"), _runner(settings, store, kalshi, tmp_path / "b")
    await a.rehydrate()
    await b.rehydrate()
    assert a.role() == "leader" and b.role() == "standby"
    in_step, release = threading.Event(), threading.Event()

    def step(mode):
        in_step.set()
        release.wait(5)
        kalshi.orders.append({"order_id": "o9", "client_order_id": "mm-new", "ticker": "T9"})  # rests after B's cancel
        return 0, ""
    monkeypatch.setattr(a, "_run_strategy_sync", step)
    assert (await a.tick("t"))["ran"] is True
    await asyncio.get_running_loop().run_in_executor(None, in_step.wait, 5)
    await b.stop(by="owner")                         # the phone talks to the new instance
    assert "o9" not in kalshi.cancelled and not a.kill_switch()
    sd = asyncio.create_task(a.shutdown())           # SIGTERM to the old instance
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.wait_for(sd, 10)
    assert "o9" in kalshi.cancelled                  # S-53 (a): the shutdown adopted the remote Stop
    assert (await store.get_lease())["handover"] is None
    await b._lease_tick()
    assert b.role() == "leader" and b.desired == "stopped"
    assert (await b.tick("t"))["ran"] is False


async def test_s53_leader_that_comes_up_stopped_cancels_once(settings, bot_paths, kalshi, demo_keys, tmp_path):
    """Belt and braces (SIGKILL, overrun): a new leader that starts stopped cancels resting mm- orders."""
    store = MemoryStore()
    await store.set_state(by="owner", desired="stopped")
    first = _runner(settings, store, kalshi, tmp_path / "first")
    await first.rehydrate()
    await _drain(first)
    assert kalshi.cancelled == []                    # first-ever lease: no predecessor, nothing to clean up
    store._lease["expires_at"] = datetime(2000, 1, 1, tzinfo=timezone.utc)      # first was SIGKILLed
    r = _runner(settings, store, kalshi, tmp_path / "r")
    await r.rehydrate()
    await _drain(r)
    assert r.role() == "leader" and sorted(kalshi.cancelled) == ["o1", "o3"]       # manual order kept


async def test_s53_running_leader_does_not_cancel(settings, bot_paths, kalshi, demo_keys, tmp_path):
    store = MemoryStore()
    await store.set_state(by="owner", desired="running")
    r = _runner(settings, store, kalshi, tmp_path / "r")
    await r.rehydrate()
    await _drain(r)
    assert r.desired == "running" and kalshi.cancelled == []


async def test_s54_remote_stop_between_read_and_own_write_is_adopted(settings, bot_paths, kalshi, demo_keys,
                                                                     monkeypatch, tmp_path):
    """The new leader reads running+live, then writes mode=demo (S-21). A Stop written by another instance between
    that read and that write must not be hidden behind the leader's own write."""
    store = MemoryStore()
    await store.set_state(by="owner", desired="running", mode="live")
    real_set = store.set_state
    fired = []

    async def set_state(*, by, **kw):
        if by == "rehydrate" and not fired:
            fired.append(1)
            await real_set(by="owner", desired="stopped", arb_desired="stopped", arb_mode="demo")
        return await real_set(by=by, **kw)
    monkeypatch.setattr(store, "set_state", set_state)
    b = _runner(settings, store, kalshi, tmp_path / "b")
    await b.rehydrate()
    assert fired and (await store.get_state())["desired"] == "stopped"
    calls = []
    monkeypatch.setattr(b, "_run_strategy_sync", lambda m: (calls.append(m) or 0, ""))
    await b._pass("first pass right away")           # _pre_order_fence re-reads the changed doc
    assert calls == [] and b.desired == "stopped" and b.kill_switch()
    assert (await b.tick("t"))["ran"] is False


async def test_s54_lease_tick_also_adopts_it(settings, bot_paths, kalshi, demo_keys, monkeypatch, tmp_path):
    store = MemoryStore()
    await store.set_state(by="owner", desired="running", mode="live")
    real_set = store.set_state
    fired = []

    async def set_state(*, by, **kw):
        if by == "rehydrate" and not fired:
            fired.append(1)
            await real_set(by="other", desired="stopped")
        return await real_set(by=by, **kw)
    monkeypatch.setattr(store, "set_state", set_state)
    b = _runner(settings, store, kalshi, tmp_path / "b")
    await b.rehydrate()
    await b._lease_tick()
    assert b.desired == "stopped"


async def test_s59_pending_stop_gets_one_last_write_at_shutdown(settings, bot_paths, kalshi, demo_keys,
                                                                monkeypatch, tmp_path):
    store = MemoryStore()
    await store.set_state(by="owner", desired="running")
    a = _runner(settings, store, kalshi, tmp_path / "a")
    await a.rehydrate()
    real_set = store.set_state
    down = [True]

    async def flaky(**kw):
        if down[0]:
            raise RuntimeError("atlas down")
        return await real_set(**kw)
    monkeypatch.setattr(store, "set_state", flaky)
    a.RETRY_FIRST_DELAY = 3600                       # the background retry never lands before the deploy
    res = await a.stop(by="owner")
    assert res["errors"] and a._stop_pending
    down[0] = False                                  # Atlas is back just as the deploy starts
    await a.shutdown()
    assert (await store.get_state())["desired"] == "stopped"
    b = _runner(settings, store, kalshi, tmp_path / "b")
    await b.rehydrate()
    assert b.desired == "stopped"


async def test_s59_pending_stop_unwritable_alerts(settings, bot_paths, kalshi, demo_keys, monkeypatch, tmp_path):
    store = MemoryStore()
    a = _runner(settings, store, kalshi, tmp_path / "a")
    await a.rehydrate()

    async def boom(**kw):
        raise RuntimeError("atlas down")
    monkeypatch.setattr(store, "set_state", boom)
    a.RETRY_FIRST_DELAY = 3600
    await a.stop(by="owner")
    await a.shutdown()
    assert any("not persisted" in t for t, _, _ in a.alerter.sent)


def test_state_stamp_differs_when_a_stop_ties_the_timestamp():
    """Two writes in one millisecond share updated_utc; a Stop must still read as a changed doc."""
    from control_api.runner import _state_stamp
    t = datetime(2026, 10, 21, 18, 0, 0, tzinfo=timezone.utc)
    running = {"updated_utc": t, "updated_by": "owner", "desired": "running", "mode": "demo",
               "arb_desired": "stopped", "arb_mode": "demo"}
    stopped = {**running, "desired": "stopped"}
    assert _state_stamp(running) != _state_stamp(stopped)
    assert _state_stamp(running) == _state_stamp(dict(running)) and _state_stamp(None) == _state_stamp({})
