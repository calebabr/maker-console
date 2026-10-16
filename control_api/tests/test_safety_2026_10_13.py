"""Start/Stop race (Stop always wins), S-10 (RUNNER_ENABLED=false is a hard stop), S-12 (stored config validated),
monitor/legging kill switches persisted, fills sync + P&L, dev CORS, PUT /cloud 400, /markets shape."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from control_api.db import MemoryStore
from control_api.runner import Runner, StopInProgress
from control_api.services import pnl as pnlsvc
from control_api.settings import Settings

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- Start/Stop race
class SlowStore(MemoryStore):
    """set_state(desired=running) waits on an event, so a Stop can run while Start is mid-write."""

    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()

    async def set_state(self, **kw):
        if kw.get("desired") == "running":
            await self.gate.wait()
        return await super().set_state(**kw)


async def test_stop_during_start_write_wins(env):
    store = SlowStore()
    env.runner.store = store
    start = asyncio.create_task(env.runner.start_trading(by="owner"))
    await asyncio.sleep(0)                       # Start is now blocked inside its store write
    res = await env.runner.stop(by="owner")      # Stop runs to completion meanwhile
    assert res["desired"] == "stopped"
    store.gate.set()                             # Start's (late) running write now lands
    with pytest.raises(StopInProgress):
        await start
    assert env.runner.desired == "stopped" and env.runner.kill_switch()
    assert (await store.get_state())["desired"] == "stopped"     # stopped re-asserted after the late write


async def test_start_refused_while_stop_in_progress(env):
    gate = asyncio.Event()

    class SlowCancel:
        def get_orders(self, status=None):
            import time
            while not gate.is_set():
                time.sleep(0.01)
            return []
    env.runner.kalshi_factory = lambda m: SlowCancel()
    stop = asyncio.create_task(env.runner.stop(by="owner"))
    await asyncio.sleep(0.05)                    # Stop is cancelling orders (in a thread)
    r = await env.client.post("/api/bot/start", headers=await env.h2fa())
    assert r.status_code == 409 and r.json()["detail"] == "stop_in_progress"
    gate.set()
    await stop
    assert env.runner.desired == "stopped"


async def test_start_after_completed_stop_still_works(env):
    await env.runner.stop(by="owner")
    r = await env.client.post("/api/bot/start", headers=await env.h2fa())
    assert r.status_code == 200 and env.runner.desired == "running"


# ---------------------------------------------------------------- S-10
async def test_runner_disabled_blocks_tick_run_once_and_start(env, monkeypatch):
    import time
    ran = []
    monkeypatch.setattr(env.runner.ms, "main", lambda: ran.append(time.time()) or 0)
    await env.store.set_state(by="t", desired="running")
    await env.runner.rehydrate()
    env.settings.runner_enabled = False
    try:
        r = await env.client.post("/api/tick", headers={"X-Tick-Token": "tick-secret-token"})
        assert r.json() == {"ran": False, "reason": "runner_disabled"}
        r = await env.client.post("/api/bot/run-once", headers=env.auth)
        assert r.json()["ran"] is False
        r = await env.client.post("/api/bot/start", headers=await env.h2fa())
        assert r.status_code == 409 and r.json()["detail"] == "runner_disabled"
        await env.runner._pass("direct")          # even a direct pass refuses
        await asyncio.sleep(0.05)
        assert ran == []
    finally:
        env.settings.runner_enabled = True


async def test_runner_disabled_startup_runs_stop(settings, bot_paths, kalshi):
    settings.runner_enabled = False
    store = MemoryStore()
    await store.set_state(by="t", desired="running", arb_desired="running")
    r = Runner(settings, store, kalshi_factory=lambda m: kalshi)
    await r.start()
    try:
        assert r.desired == "stopped" and r.kill_switch() and r.arb_kill_switch()
        assert sorted(kalshi.cancelled) == ["o1", "o3"]
        st = await store.get_state()
        assert st["desired"] == "stopped" and st["arb_desired"] == "stopped"
        assert r._tasks == []
    finally:
        await r.shutdown()


# ---------------------------------------------------------------- S-12
async def test_rehydrate_rejects_stored_config_over_api_ceiling(settings, bot_paths, kalshi):
    store = MemoryStore()
    r0 = Runner(settings, store, kalshi_factory=lambda m: kalshi)
    good = r0._safe_local_config()
    await store.save_config({**good, "run_budget": 10000, "max_open_frac": 0.9}, before=None, by="attacker")
    await store.set_state(by="attacker", desired="running")
    r = Runner(settings, store, kalshi_factory=lambda m: kalshi)
    await r.rehydrate()
    assert r.desired == "stopped" and "validation" in (r.last_error or "")
    assert r.config["run_budget"] <= settings.max_run_budget
    on_disk = __import__("json").loads(r.config_path.read_text(encoding="utf-8"))
    assert on_disk["run_budget"] != 10000
    assert (await r.tick("t"))["ran"] is False


async def test_rehydrate_valid_config_resumes_running(settings, bot_paths, kalshi):
    store = MemoryStore()
    r0 = Runner(settings, store, kalshi_factory=lambda m: kalshi)
    await store.save_config({**r0._safe_local_config(), "run_budget": 25}, before=None, by="owner")
    await store.set_state(by="owner", desired="running")
    r = Runner(settings, store, kalshi_factory=lambda m: kalshi)
    await r.rehydrate()
    assert r.desired == "running" and r.config["run_budget"] == 25


async def test_rehydrate_rejects_bad_arb_block(settings, bot_paths, kalshi):
    store = MemoryStore()
    r0 = Runner(settings, store, kalshi_factory=lambda m: kalshi)
    await store.save_config({**r0._safe_local_config(), "arb": {"arb_max_contracts": 100000}}, before=None, by="x")
    await store.set_state(by="x", desired="running")
    r = Runner(settings, store, kalshi_factory=lambda m: kalshi)
    await r.rehydrate()
    assert r.desired == "stopped"


async def test_apply_config_revalidates(env):
    with pytest.raises(ValueError):
        await env.runner.apply_config({**env.runner.config, "run_budget": 9000})


# ---------------------------------------------------------------- kill switches persisted (ephemeral disk)
async def test_monitor_kill_switch_becomes_persisted_stop(env, monkeypatch):
    await env.store.set_state(by="t", desired="running")
    env.runner.desired = "running"
    mon = env.runner.ms  # placeholder to keep import

    class FakeMon:
        LOGS = str(env.paths)

        @staticmethod
        def main(argv):
            env.runner.kill_path.write_text("maker_monitor: every market confidently losing; bot stopped")
    monkeypatch.setattr("control_api.services.botfiles.monitor", lambda: FakeMon)
    await env.runner.run_monitor("weekly")
    assert env.runner.desired == "stopped"
    assert (await env.store.get_state())["desired"] == "stopped"
    assert mon is not None


# ---------------------------------------------------------------- fills + P&L
class FillsKalshi:
    def __init__(self, fills, settlements):
        self.fills, self.settlements = fills, settlements

    def get_fills(self, **kw):
        return list(self.fills)

    def get_settlements(self, **kw):
        return list(self.settlements)

    def get_orders(self, status=None):
        return []


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


async def test_fills_sync_and_status_pnl(env):
    now = datetime.now(timezone.utc)
    fills = [
        {"fill_id": "f1", "ticker": "KXDEMOA-A", "side": "no", "no_price_dollars": "0.40", "count_fp": "10",
         "is_taker": False, "fee_cost": "0", "created_time": _iso(now - timedelta(days=3))},     # wins: +6.00
        {"fill_id": "f2", "ticker": "KXDEMOB-B", "side": "no", "no_price_dollars": "0.45", "count_fp": "10",
         "is_taker": False, "fee_cost": "0.05", "created_time": _iso(now - timedelta(days=20))},  # loses: -4.55
        {"fill_id": "f3", "ticker": "KXDEMOB-C", "side": "yes", "yes_price_dollars": "0.30", "count_fp": "5",
         "is_taker": True, "created_time": _iso(now - timedelta(days=2))},                        # taker: not maker
        {"fill_id": "f4", "ticker": "KXDEMOC-D", "side": "no", "no_price_dollars": "0.4", "count_fp": "1",
         "is_taker": False, "created_time": _iso(now - timedelta(hours=1))},                      # unsettled
    ]
    settlements = [
        {"ticker": "KXDEMOA-A", "market_result": "no", "settled_time": _iso(now - timedelta(days=2))},
        {"ticker": "KXDEMOB-B", "market_result": "yes", "settled_time": _iso(now - timedelta(days=19))},
        {"ticker": "KXDEMOB-C", "market_result": "no", "settled_time": _iso(now - timedelta(days=1))},
    ]
    env.runner.kalshi_factory = lambda m: FillsKalshi(fills, settlements)
    res = await env.runner.sync_fills("demo")
    assert res["synced"] is True and res["fills"] == 4 and res["settlements"] == 3
    assert (await env.runner.sync_fills("demo"))["reason"] == "not_due"          # throttled
    body = (await env.client.get("/api/status", headers=env.auth)).json()
    assert body["pnl"]["d7"] == 6.0 and body["pnl"]["d30"] == round(6.0 - 4.55, 2)
    assert body["pnl"]["settled_fills"] == 2
    assert body["fills_sync"]["error"] is None


async def test_pnl_null_without_data(env):
    assert (await env.client.get("/api/status", headers=env.auth)).json()["pnl"] is None


async def test_fills_sync_failure_never_raises(env):
    class Boom:
        def get_fills(self, **kw):
            raise RuntimeError("kalshi down")
    env.runner.kalshi_factory = lambda m: Boom()
    res = await env.runner.sync_fills("demo", force=True)
    assert res["synced"] is False and env.runner.fills_sync_error == "RuntimeError"


async def test_pnl_windows_env_filter():
    now = datetime.now(timezone.utc)
    f = {"ticker": "T", "side": "no", "no_price_dollars": "0.4", "count_fp": "1", "env": "live"}
    s = [{"ticker": "T", "market_result": "no", "ts": now - timedelta(days=1)}]
    assert pnlsvc.windows([f], s, now, env="demo") is None
    assert pnlsvc.windows([f], s, now, env="live")["d7"] == 0.6


# ---------------------------------------------------------------- dev CORS
async def test_dev_cors_only_in_dev():
    dev = Settings.from_env({"STORE": "memory", "ALLOWED_ORIGIN": "https://app.example.com"})
    assert "http://localhost:5173" in dev.allowed_origins and dev.dev_mode
    off = Settings.from_env({"STORE": "memory", "DEV_MODE": "false", "ALLOWED_ORIGIN": "https://app.example.com"})
    assert off.allowed_origins == ["https://app.example.com"]
    prod = Settings.from_env({"MONGODB_URI": "mongodb://x", "JWT_SECRET": "j", "TOTP_ENC_KEY": "k",
                              "ALLOWED_ORIGIN": "https://app.example.com"})
    assert "http://localhost:5173" not in prod.allowed_origins and not prod.dev_mode
    render = Settings.from_env({"STORE": "memory", "RENDER": "true", "DEV_MODE": "true",
                                "ALLOWED_ORIGIN": "https://app.example.com,http://localhost:5173"})
    assert render.allowed_origins == ["https://app.example.com"] and not render.dev_mode


async def test_dev_cors_preflight_allows_vite(bot_paths):
    import httpx
    from control_api.main import create_app
    s = Settings.from_env({"STORE": "memory", "RUNNER_ENABLED": "true", "ALLOWED_ORIGIN": "https://a.example"})
    app = create_app(s, store=MemoryStore(), kalshi_factory=lambda m: None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.options("/api/status", headers={"Origin": "http://localhost:5173",
                                                    "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5173"


# ---------------------------------------------------------------- contract fixes
async def test_put_cloud_type_error_is_400(env):
    r = await env.client.put("/api/cloud", json={"keepalive_minutes": "abc"}, headers=await env.h2fa())
    assert r.status_code == 400 and isinstance(r.json()["detail"], list)
    r = await env.client.put("/api/cloud", json={"pinger_enabled": "yes"}, headers=await env.h2fa())
    assert r.status_code == 400
    r = await env.client.put("/api/cloud", content=b"not json", headers={**(await env.h2fa()),
                                                                       "Content-Type": "application/json"})
    assert r.status_code == 400


async def test_pinger_sync_returns_cloud_shape(env):
    r = await env.client.post("/api/cloud/pinger/sync", headers=await env.h2fa())
    g = await env.client.get("/api/cloud", headers=env.auth)
    assert r.status_code == 200 and set(r.json()) == set(g.json())
    assert r.json()["pinger"]["synced"] is True


async def test_markets_rows_from_real_monitor_summary(env):
    summary = {"per_series": {"KXDEMOA": {"fills": 120, "cost": 900.0, "pnl": -20.0, "events": 60, "roi": -0.02,
                                            "ci_low": -0.04, "ci_high": -0.001},
                              "KXDEMOB": {"fills": 30, "cost": 200.0, "pnl": 3.0, "events": 15, "roi": 0.015,
                                           "ci_low": None, "ci_high": None}},
               "actions": {"pause": ["KXDEMOA"], "keep": [], "need_more": {"KXDEMOB": 70},
                           "collateral_return": "not yet"}}
    await env.store.add_monitor_summary({"ts": datetime.now(timezone.utc), "by": "t", "summary": summary})
    rows = {m["series"]: m for m in (await env.client.get("/api/markets", headers=env.auth)).json()["markets"]}
    assert rows["KXDEMOA"]["verdict"] == "pause" and rows["KXDEMOA"]["roi"] == -0.02
    assert rows["KXDEMOB"]["need_more"] == 70 and rows["KXDEMOB"]["verdict"] is None
    assert rows["KXDEMOC"]["fills"] is None
    keys = {"series", "enabled", "fills", "cost", "pnl", "events", "roi", "ci_low", "ci_high", "verdict", "need_more",
            "fill_quality", "queue_stats"}
    assert all(set(m) == keys for m in rows.values())
