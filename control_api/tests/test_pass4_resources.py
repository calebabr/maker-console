"""Security review pass 4 (512 MB instance): S-22 Run once rate limit, S-23 cached fill-quality aggregate and
bounded /markets store read, S-24 fill-quality evaluation outside the pass lock with a time budget."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from control_api import runner as runmod
from control_api.routes import config as config_routes
from control_api.tests.conftest import FAKE_BASELINE

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- S-22
async def test_run_once_too_soon_is_409(env, monkeypatch):
    monkeypatch.setattr(env.runner.ms, "main", lambda: 0)
    env.runner.public_factory = lambda e: SlowTrades()     # no network from the background evaluation
    env.runner.last_monitor_utc = datetime.now(timezone.utc)   # no weekly monitor run in this test
    env.runner.desired = "running"
    env.runner.last_pass_started_utc = datetime.now(timezone.utc) - timedelta(seconds=10)
    r = await env.client.post("/api/bot/run-once", headers=env.auth)
    assert r.status_code == 409 and r.json()["detail"] == "too_soon"
    env.runner.last_pass_started_utc = datetime.now(timezone.utc) - timedelta(seconds=61)
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "demo-id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "demo-key")
    r = await env.client.post("/api/bot/run-once", headers=env.auth)
    assert r.status_code == 200 and r.json()["ran"] is True
    for _ in range(50):                                   # let the background pass finish
        if not env.runner.running_pass:
            break
        await asyncio.sleep(0.02)
    r = await env.client.post("/api/bot/run-once", headers=env.auth)   # that pass just started
    assert r.status_code == 409 and r.json()["detail"] == "too_soon"


async def test_run_once_stopped_still_200_not_409(env):
    r = await env.client.post("/api/bot/run-once", headers=env.auth)
    assert r.status_code == 200 and r.json() == {"ran": False, "reason": "stopped"}


# ---------------------------------------------------------------- S-23
async def test_status_and_markets_serve_cached_fill_quality(env, monkeypatch):
    store = env.store
    store.fill_reads = 0
    real = store.list_fills

    async def counting(**kw):
        store.fill_reads += 1
        return await real(**kw)
    monkeypatch.setattr(store, "list_fills", counting)
    await env.runner.refresh_fill_quality()
    before = store.fill_reads
    for _ in range(5):
        st = (await env.client.get("/api/status", headers=env.auth)).json()
        mk = (await env.client.get("/api/markets", headers=env.auth)).json()
    assert st["fill_quality"] == {"n": 0, "trade_through_share": None, "avg_move_60m_c": None,
                                  "baseline": FAKE_BASELINE}
    assert mk["fill_quality"]["n"] == 0 and all(m["fill_quality"]["n"] == 0 for m in mk["markets"])
    # /status still reads fills for P&L only when settlements exist (none here); /markets reads none
    assert store.fill_reads == before


async def test_fill_quality_null_when_never_computed_or_mode_changed(env):
    env.runner.fill_quality_cache = None
    assert (await env.client.get("/api/status", headers=env.auth)).json()["fill_quality"] is None
    await env.runner.refresh_fill_quality()
    assert env.runner.fill_quality() is not None
    env.runner.mode = "live"                              # cache was computed for demo
    assert env.runner.fill_quality() is None


async def test_markets_store_read_is_bounded(env, monkeypatch):
    async def hang():
        await asyncio.sleep(30)
    monkeypatch.setattr(env.store, "last_monitor_summary", hang)
    monkeypatch.setattr(config_routes, "STORE_TIMEOUT_S", 0.2)
    t0 = time.monotonic()
    r = await env.client.get("/api/markets", headers=env.auth)
    assert r.status_code == 200 and time.monotonic() - t0 < 5
    assert r.json()["report_utc"] is None and len(r.json()["markets"]) == 6


# ---------------------------------------------------------------- S-24
class SlowTrades:
    def request(self, *a, **k):
        time.sleep(0.4)
        return {"trades": [], "cursor": None}


async def _seed_fills(store, n=5):
    now = datetime.now(timezone.utc)
    await store.upsert_fills([{"fill_id": f"f{i}", "ticker": f"KXDEMOA-A{i}", "side": "no",
                               "no_price_dollars": "0.4", "is_taker": False,
                               "created_time": (now - timedelta(minutes=90 + i)).isoformat()} for i in range(n)])


async def test_fill_quality_has_total_time_budget(env, monkeypatch):
    await _seed_fills(env.store)
    env.runner.public_factory = lambda e: SlowTrades()
    monkeypatch.setattr(runmod, "FILL_QUALITY_BUDGET_S", 0.5)
    t0 = time.monotonic()
    res = await env.runner.run_fill_quality()
    assert time.monotonic() - t0 < 2.0
    assert res.get("error") == "TimeoutError"
    assert env.runner.fill_quality() is not None          # aggregate still refreshed after the budget


async def test_fill_quality_runs_outside_pass_lock_and_stop_never_waits(env, monkeypatch):
    await _seed_fills(env.store)
    env.runner.public_factory = lambda e: SlowTrades()
    monkeypatch.setattr(env.runner.ms, "main", lambda: 0)
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "demo-id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "demo-key")
    env.runner.last_monitor_utc = datetime.now(timezone.utc)
    env.runner.desired = "running"
    await env.runner._pass("test")                        # returns once the pass itself is done
    assert not env.runner._pass_lock.locked() and not env.runner.running_pass
    task = env.runner._fq_task
    assert task is not None and not task.done()            # evaluation continues in the background
    assert env.runner._start_fill_quality() is None        # only one at a time
    t0 = time.monotonic()
    res = await env.runner.stop(by="owner")
    assert time.monotonic() - t0 < 1.0 and res["desired"] == "stopped"
    task.cancel()
    with pytest.raises(BaseException):
        await task
