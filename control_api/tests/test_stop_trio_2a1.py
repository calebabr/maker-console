"""Sprint 2A-1 Stop trio: Stop needs no 2FA, works with Atlas down and with Kalshi down, and still works while an
arbitrage execution and a maker pass are both in flight. Plus: Start with an invalid stored config fails clearly and
stays stopped; the periods marker and cash-flow routes need fresh 2FA. Fakes only; no network."""
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from control_api import runner as rn
from control_api.auth import security as sec
from control_api.main import create_app
from control_api.tests.conftest import BrokenStore, FakeKalshi
from control_api.tests.test_gate_integration import arb_on, demo_env  # noqa: F401  (demo_env is a fixture)

pytestmark = pytest.mark.asyncio


async def _wait(cond, n=500):
    for _ in range(n):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_stop_with_no_2fa_header_and_a_valid_login_works(env):
    env.runner.desired = "running"
    r = await env.client.post("/api/bot/stop", headers=env.auth)
    assert r.status_code == 200 and "X-2FA-Code" not in env.auth
    assert env.runner.desired == "stopped" and env.runner.arb_desired == "stopped"
    assert env.runner.kill_path.exists() and env.runner.arb_kill_path.exists()


async def test_stop_with_atlas_down_while_arb_and_maker_are_on(settings, bot_paths):
    kalshi = FakeKalshi()
    app = create_app(settings, store=BrokenStore(), kalshi_factory=lambda m: kalshi)
    runner = app.state.runner
    runner.desired, runner.arb_desired = "running", "running"
    tok = sec.create_token(settings, "owner", sec.TYP_ACCESS, 3600)[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/bot/stop", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and runner.desired == "stopped" and runner.arb_desired == "stopped"
    assert sorted(kalshi.cancelled) == ["o1", "o3"] and runner.kill_path.exists()
    assert runner._stop_pending                              # persisted later, retried in the background


async def test_stop_with_kalshi_down_still_sets_every_switch(env):
    env.kalshi.raise_on_orders = True
    env.runner.desired, env.runner.arb_desired = "running", "running"
    r = await env.client.post("/api/bot/stop", headers=env.auth)
    assert r.status_code == 200
    assert env.runner.kill_path.exists() and env.runner.arb_kill_path.exists()
    assert (await env.store.get_state())["desired"] == "stopped"


async def test_stop_with_atlas_and_kalshi_both_down(settings, bot_paths):
    kalshi = FakeKalshi(raise_on_orders=True)
    app = create_app(settings, store=BrokenStore(), kalshi_factory=lambda m: kalshi)
    tok = sec.create_token(settings, "owner", sec.TYP_ACCESS, 3600)[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/bot/stop", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and app.state.runner.kill_path.exists()
    assert app.state.runner.arb_kill_path.exists()


async def test_stop_while_an_arb_set_and_a_maker_pass_are_both_in_flight(demo_env, monkeypatch):
    env = demo_env
    r = env.runner
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "demo-id")
    r.last_monitor_utc = rn.utcnow()
    await arb_on(env)
    env.public.events = 2
    env.trader.resting = [{"order_id": "o1", "client_order_id": "mm-a", "ticker": "T1"},
                          {"order_id": "o2", "client_order_id": "manual-1", "ticker": "T2"},
                          {"order_id": "o3", "client_order_id": "mm-b", "ticker": "T3"}]
    r.desired, r.mode = "running", "demo"
    await env.store.set_state(by="t", desired="running")   # the pass adopts a persisted Stop otherwise
    maker_in, maker_go, calls = threading.Event(), threading.Event(), []
    arb_in, arb_go = threading.Event(), threading.Event()

    def maker_main(*a, **k):
        calls.append(1)
        maker_in.set()
        maker_go.wait(5)
        return 0
    monkeypatch.setattr(r.ms, "main", maker_main)

    def hold_first_order(ticker):
        if not env.trader.orders:
            arb_in.set()
            arb_go.wait(5)
    env.trader.on_order = hold_first_order

    maker = asyncio.create_task(r._pass("t"))
    scan = asyncio.create_task(r.arb_scan())
    assert await _wait(lambda: maker_in.is_set() and arb_in.is_set())
    # both are mid-flight: Stop, with no 2FA, must answer promptly
    resp = await asyncio.wait_for(env.client.post("/api/bot/stop", headers=env.auth), timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert body["desired"] == "stopped" and body["kill_switch"] and body["arb_kill_switch"]
    assert r.desired == "stopped" and r.arb_desired == "stopped"
    cancelled_at_stop = sorted(env.trader.cancelled)
    assert cancelled_at_stop == ["o1", "o3"]                  # mm- only; manual-1 untouched
    maker_go.set()
    arb_go.set()
    await asyncio.wait_for(asyncio.gather(maker, scan), timeout=10)
    # no second set after Stop, nothing restarted
    assert len(env.trader.orders) <= 3
    assert r.desired == "stopped" and r.arb_desired == "stopped" and r.kill_path.exists()
    assert (await r.tick("x"))["ran"] is False and len(calls) == 1
    assert (await r.arb_scan())["scanned"] is False
    assert (await env.store.get_state())["desired"] == "stopped"
    assert (await env.store.get_state())["arb_desired"] == "stopped"


async def test_stop_while_in_flight_with_atlas_and_kalshi_down(demo_env, monkeypatch):
    env = demo_env
    r = env.runner
    await arb_on(env)
    r.desired = "running"
    arb_in, arb_go = threading.Event(), threading.Event()

    def hold(ticker):
        if not env.trader.orders:
            arb_in.set()
            arb_go.wait(5)
    env.trader.on_order = hold
    scan = asyncio.create_task(r.arb_scan())
    assert await _wait(arb_in.is_set)
    env.kalshi.raise_on_orders = True
    r.RETRY_FIRST_DELAY = 0.01
    good, r.store = r.store, BrokenStore()
    resp = await asyncio.wait_for(env.client.post("/api/bot/stop", headers=env.auth), timeout=10)
    assert resp.status_code == 200 and r.desired == "stopped" and r.kill_path.exists()
    arb_go.set()
    await asyncio.wait_for(scan, timeout=10)
    r.store = good                                            # Atlas returns; the pending Stop persists
    assert await _wait(lambda: not r._stop_pending)
    assert (await good.get_state())["desired"] == "stopped"


# ============================================================ Start with an invalid stored config
async def test_start_with_an_invalid_stored_config_fails_clearly_and_stays_stopped(env):
    cfg = dict(await env.store.get_config())
    cfg["poll_minutes"] = 99                                  # above the keep-awake limit of 14
    await env.store.save_config(cfg, before=None, by="test")
    r = await env.client.post("/api/bot/start", headers=await env.h2fa())
    assert r.status_code == 409 and r.json()["detail"].startswith("invalid_config:")
    assert "poll_minutes" in r.json()["detail"]
    assert env.runner.desired == "stopped"
    assert ((await env.store.get_state()) or {}).get("desired", "stopped") == "stopped"
    assert (await env.runner.tick("x"))["ran"] is False
    # Stop is still fine afterwards and does not read the config
    assert (await env.client.post("/api/bot/stop", headers=env.auth)).status_code == 200


# ============================================================ periods and cash-flow writes need fresh 2FA
async def test_periods_and_cash_flow_writes_need_fresh_2fa(env):
    c, h = env.client, env.auth
    assert (await c.post("/api/periods", json={"name": "Demo Period 2"}, headers=h)).status_code == 403
    assert (await c.post("/api/cash-flows", json={"kind": "deposit", "amount": 50}, headers=h)).status_code == 403
    assert (await c.delete("/api/cash-flows/abc123", headers=h)).status_code == 403
    assert (await c.get("/api/periods", headers=h)).json()["cash_flows"] == []       # nothing written
    assert [p["id"] for p in (await c.get("/api/periods", headers=h)).json()["periods"]] == ["p1"]


async def test_periods_and_cash_flow_writes_work_with_2fa_and_a_reused_code_is_refused(env):
    c = env.client
    hh = await env.h2fa()
    r = await c.post("/api/periods", json={"name": "Demo Period 2"}, headers=hh)
    assert r.status_code == 200 and r.json()["created"]
    r2 = await c.post("/api/cash-flows", json={"kind": "deposit", "amount": 50}, headers=hh)   # same code again
    assert r2.status_code in (401, 403)
    hh = await env.h2fa()
    r3 = await c.post("/api/cash-flows", json={"kind": "deposit", "amount": 50}, headers=hh)
    assert r3.status_code == 200
    fid = r3.json()["created"]
    assert (await c.delete(f"/api/cash-flows/{fid}", headers=env.auth)).status_code == 403
    assert (await c.delete(f"/api/cash-flows/{fid}", headers=await env.h2fa())).status_code == 200


async def test_period_writes_do_not_touch_the_stop_path(env):
    env.runner.desired = "running"
    env.store.add_period = _boom
    env.store.list_periods = _boom
    r = await env.client.post("/api/periods", json={"name": "x"}, headers=await env.h2fa())
    assert r.status_code == 503
    assert (await env.client.post("/api/bot/stop", headers=env.auth)).status_code == 200


async def _boom(*a, **k):
    raise RuntimeError("atlas down")
