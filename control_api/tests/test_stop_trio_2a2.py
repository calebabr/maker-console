"""Sprint 2A-2 Stop trio again: Stop still wins while a shadow cycle with the arbitrage scan (SHADOW_ARB) is in
flight, with Atlas down and with Kalshi down. Shadow never blocks, restarts or delays Stop. Fakes only."""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from control_api.db import MemoryStore
from control_api.services import shadow as sh
from control_api.tests.conftest import BrokenStore
from control_api.tests.test_shadow_arb import ArbMD
from control_api.tests.test_shadow_cycle import FakeStrategy, leader_runner, use_strategy, yes_quote
from control_api.tests.test_shadow_isolation import demo_keys, gate  # noqa: F401  (fixtures)

pytestmark = pytest.mark.asyncio

ARB_CFG = {"arb": {"arb_series": ["KXDEMOA"], "arb_min_profit": 0.1}}


class HangingArbMD(ArbMD):
    """Normal episode reads, then the ARBITRAGE scan's first orderbook read hangs (orderbook calls 1-2 are the
    episode path, call 3 is the scan)."""
    def __init__(self, gate_ev: threading.Event, started: threading.Event):
        super().__init__(close=datetime.now(timezone.utc) + timedelta(hours=6))
        self.gate_ev, self.started, self._n = gate_ev, started, 0

    def orderbook(self, ticker, depth=None):
        self._n += 1
        if self._n >= 3:
            self.started.set()
            self.gate_ev.wait(10)
        return super().orderbook(ticker, depth)


async def _until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "condition not reached"
        await asyncio.sleep(0.01)


def _arm(r, monkeypatch):
    monkeypatch.setenv(sh.SHADOW_ARB_ENV, "true")
    r.config = {**r.config, **ARB_CFG}


async def test_stop_wins_over_an_in_flight_shadow_arb_scan(settings, bot_paths, kalshi, demo_keys, gate,
                                                           real_strategy, monkeypatch):
    started = threading.Event()
    r = await leader_runner(settings, MemoryStore(), HangingArbMD(gate, started), monkeypatch,
                            strategy=FakeStrategy([yes_quote()]))
    _arm(r, monkeypatch)
    r.kalshi_factory = lambda mode: kalshi
    cycle = asyncio.ensure_future(r.shadow_cycle())
    await _until(started.is_set)
    t0 = time.monotonic()
    res = await asyncio.wait_for(r.stop(by="owner"), 5)
    assert time.monotonic() - t0 < 1.0
    assert res["errors"] == [] and sorted(kalshi.cancelled) == ["o1", "o3"]       # mm- only, manual-1 untouched
    assert r.desired == "stopped" and r.kill_path.exists() and not cycle.done()
    gate.set()
    out = await asyncio.wait_for(cycle, 5)                      # the scan finishes harmlessly afterwards
    assert out["ran"] is True
    assert r.desired == "stopped" and r.kill_path.exists()      # shadow never restarted anything
    await r.shutdown()


async def test_http_stop_with_no_2fa_while_the_shadow_scan_is_hung(env, real_strategy, gate, monkeypatch):
    started = threading.Event()
    r = env.runner
    env.settings.shadow_enabled = True
    r.cloud["shadow_enabled"] = True
    monkeypatch.setattr(r, "role", lambda: "leader")
    r.shadow_factory = lambda: HangingArbMD(gate, started)
    use_strategy(monkeypatch, r, FakeStrategy([yes_quote()]))
    _arm(r, monkeypatch)
    r.desired = "running"
    cycle = asyncio.ensure_future(r.shadow_cycle())
    await _until(started.is_set)
    resp = await asyncio.wait_for(env.client.post("/api/bot/stop", headers=env.auth), 5)
    assert resp.status_code == 200 and "X-2FA-Code" not in env.auth
    body = resp.json()
    assert body["desired"] == "stopped" and body["kill_switch"] is True
    assert sorted(env.kalshi.cancelled) == ["o1", "o3"] and r.kill_path.exists()
    gate.set()
    await asyncio.wait_for(cycle, 5)
    assert r.desired == "stopped"
    await r.shutdown()


async def test_stop_with_atlas_and_kalshi_down_while_the_shadow_scan_is_hung(settings, bot_paths, kalshi, demo_keys,
                                                                             gate, real_strategy, monkeypatch):
    started = threading.Event()
    r = await leader_runner(settings, MemoryStore(), HangingArbMD(gate, started), monkeypatch,
                            strategy=FakeStrategy([yes_quote()]))
    _arm(r, monkeypatch)
    kalshi.raise_on_orders = True
    r.kalshi_factory = lambda mode: kalshi
    r.RETRY_FIRST_DELAY = 0.01
    cycle = asyncio.ensure_future(r.shadow_cycle())
    await _until(started.is_set)
    good, r.store = r.store, BrokenStore()                      # Atlas goes down mid-flight
    await asyncio.wait_for(r.stop(by="owner"), 10)
    assert r.desired == "stopped" and r.arb_desired == "stopped"
    assert r.kill_path.exists() and r.arb_kill_path.exists()
    gate.set()
    out = await asyncio.wait_for(cycle, 10)                     # shadow never raises, even with the store down
    assert isinstance(out, dict)
    r.store = good
    await _until(lambda: not r._stop_pending, 5)
    assert (await good.get_state())["desired"] == "stopped"
    await r.shutdown()


async def test_shadow_scan_does_not_run_during_stop_and_cannot_start_the_bot(settings, bot_paths, kalshi,
                                                                             demo_keys, real_strategy, monkeypatch):
    md = ArbMD()
    r = await leader_runner(settings, MemoryStore(), md, monkeypatch, strategy=FakeStrategy([yes_quote()]))
    _arm(r, monkeypatch)
    r.kalshi_factory = lambda mode: kalshi
    r.desired = "running"
    r._stopping = 1                                             # Stop is running right now
    assert await r.shadow_cycle() == {"ran": False, "reason": "stop_running"}
    assert md.calls == []                                       # no market read, no scan request
    r._stopping = 0
    await r.stop(by="owner")
    await r.shadow_cycle()                                      # after Stop, shadow (read only) may run
    assert r.desired == "stopped" and r.kill_path.exists()      # ... and changes nothing about Stop
    assert (await r.tick("x"))["ran"] is False                  # the maker is still stopped
    await r.shutdown()
