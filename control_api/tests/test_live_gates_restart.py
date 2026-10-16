"""S-19 (live gate read once at startup, not from the mutable env) and S-21 (LIVE never restored on restart)."""
from __future__ import annotations

import os
import threading
import time

import pytest

from control_api import runner as runmod
from control_api.db import MemoryStore
from control_api.runner import Runner
from control_api.settings import Settings

pytestmark = pytest.mark.asyncio


class RecordingAlerter:
    def __init__(self):
        self.sent = []

    async def send(self, title, msg, priority="default"):
        self.sent.append((title, msg, priority))


# ---------------------------------------------------------------- S-19
@pytest.mark.slow
async def test_s19_live_gate_never_true_during_concurrent_demo_passes(settings, monkeypatch):
    """Regression for the pass-3 PoC: prod keys set, KALSHI_LIVE_CONFIRM unset, demo passes looping in a thread.
    The old live_allowed_env() returned True ~25% of the time; the startup snapshot never can."""
    monkeypatch.setenv("KALSHI_PROD_API_KEY_ID", "fake-id")
    monkeypatch.setenv("KALSHI_PROD_PRIVATE_KEY", "fake-key")
    monkeypatch.delenv("KALSHI_LIVE_CONFIRM", raising=False)
    snap = Settings.from_env()                       # startup snapshot from the real process env
    assert snap.prod_keys_present and not snap.live_confirm_ok
    r = Runner(snap, MemoryStore(), kalshi_factory=lambda m: None)
    stop = threading.Event()

    def passes():
        while not stop.is_set():
            with runmod.kalshi_env("demo"):
                pass
    t = threading.Thread(target=passes)
    t.start()
    hits = n = 0
    end = time.time() + 1.5
    try:
        while time.time() < end:
            n += 1
            hits += r.live_allowed()
            hits += runmod.live_allowed_settings(Settings.from_env({"STORE": "memory"}))
    finally:
        stop.set()
        t.join()
    assert n > 1000 and hits == 0
    assert os.environ.get("KALSHI_LIVE_CONFIRM") is None      # owner's env restored after the passes


async def test_s19_kalshi_env_restores_before_clearing_marker(monkeypatch):
    """Restore ordering: at the moment _SAVED_ENV is cleared, the owner's env is already back."""
    monkeypatch.delenv("KALSHI_LIVE_CONFIRM", raising=False)
    with runmod.kalshi_env("demo"):
        assert os.environ.get("KALSHI_LIVE_CONFIRM") == runmod.LIVE_CONFIRM
        assert runmod._SAVED_ENV is not None
    assert runmod._SAVED_ENV is None and os.environ.get("KALSHI_LIVE_CONFIRM") is None
    # source-order check of the finally block: restore loop before the marker is cleared
    import inspect
    src = inspect.getsource(runmod.kalshi_env)
    fin = src[src.index("finally:"):]
    assert fin.index("os.environ[k] = v") < fin.index("_SAVED_ENV = None")


async def test_s19_arb_live_refused_by_snapshot_even_if_env_set_later(arb_settings_env):
    env = arb_settings_env
    # env vars appear after startup (e.g. a pass): the snapshot in Settings still says no
    os.environ["KALSHI_LIVE_CONFIRM"] = runmod.LIVE_CONFIRM
    try:
        r = await env.client.put("/api/arb", json={"enabled": True, "live": True, "confirm": "LIVE"},
                                 headers=await env.h2fa())
        assert r.status_code == 409 and r.json()["detail"] == "live_not_allowed"
        r = await env.client.put("/api/mode", json={"mode": "live", "confirm": "LIVE"}, headers=await env.h2fa())
        assert r.status_code == 409
    finally:
        os.environ.pop("KALSHI_LIVE_CONFIRM", None)


@pytest.fixture
def arb_settings_env(env, monkeypatch, real_strategy):
    monkeypatch.setenv("KALSHI_PROD_API_KEY_ID", "fake-id")
    monkeypatch.setenv("KALSHI_PROD_PRIVATE_KEY", "fake-key")
    return env


# ---------------------------------------------------------------- S-21
async def _restart(settings, store, kalshi):
    r = Runner(settings, store, kalshi_factory=lambda m: kalshi)
    r.alerter = RecordingAlerter()
    await r.rehydrate()
    return r


async def test_s21_live_never_restored_maker(settings, bot_paths, kalshi, monkeypatch):
    monkeypatch.setattr(settings, "prod_keys_present", True)
    monkeypatch.setattr(settings, "live_confirm_ok", True)            # even with every env gate set
    store = MemoryStore()
    await store.set_state(by="owner", desired="running", mode="live")
    r = await _restart(settings, store, kalshi)
    assert r.mode == "demo" and r.desired == "running"                 # running kept, live dropped
    st = await store.get_state()
    assert st["mode"] == "demo" and st["desired"] == "running" and st["updated_by"] == "rehydrate"
    (title, msg, prio), = r.alerter.sent
    assert prio == "urgent" and "re-arm" in title.lower() and "LIVE" in msg and "maker" in msg


async def test_s21_arb_live_never_restored(settings, bot_paths, kalshi, monkeypatch):
    monkeypatch.setattr(settings, "prod_keys_present", True)
    monkeypatch.setattr(settings, "live_confirm_ok", True)
    store = MemoryStore()
    await store.set_state(by="attacker", arb_desired="running", arb_mode="live")
    r = await _restart(settings, store, kalshi)
    assert r.arb_mode == "demo" and r.arb_desired == "running" and r.arb_state() == "log-only"
    st = await store.get_state()
    assert st["arb_mode"] == "demo" and st["arb_desired"] == "running"
    (title, msg, prio), = r.alerter.sent
    assert prio == "urgent" and "arbitrage" in msg and "attacker" in msg


async def test_s21_live_downgrade_survives_dead_store_write(settings, bot_paths, kalshi):
    class FailingWrite(MemoryStore):
        async def set_state(self, **kw):
            if kw.get("by") == "rehydrate":
                raise RuntimeError("atlas down")
            return await super().set_state(**kw)
    store = FailingWrite()
    await MemoryStore.set_state(store, by="owner", desired="running", mode="live")
    r = await _restart(settings, store, kalshi)
    assert r.mode == "demo"                                           # memory is demo regardless


async def test_s21_resuming_demo_running_alerts_urgent(settings, bot_paths, kalshi):
    store = MemoryStore()
    await store.set_state(by="owner", desired="running")
    r = await _restart(settings, store, kalshi)
    (title, msg, prio), = r.alerter.sent
    assert prio == "urgent" and "maker running (demo)" in msg and "owner" in msg


async def test_s21_stopped_restart_is_quiet(settings, bot_paths, kalshi):
    r = await _restart(settings, MemoryStore(), kalshi)
    assert r.alerter.sent == []
