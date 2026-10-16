import asyncio
from datetime import timedelta

import pytest

from control_api import runner as rn
from control_api.db import MemoryStore
from control_api.main import create_app
from control_api.services import botfiles
from control_api.tests.conftest import BrokenStore

pytestmark = pytest.mark.asyncio


@pytest.fixture
def demo_keys(monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "pk")


async def _drain(r):
    while r._bg:
        await asyncio.gather(*list(r._bg), return_exceptions=True)


@pytest.fixture
def no_monitor(env):
    env.runner.last_monitor_utc = rn.utcnow()
    return env


async def test_no_pass_when_stopped(env):
    assert await env.runner.tick() == {"ran": False, "reason": "stopped"}
    assert (await env.runner.run_once("t")) == {"ran": False, "reason": "stopped"}


async def test_no_pass_when_kill_switch_present(env):
    env.runner.desired = "running"
    env.runner.kill_path.write_text("x")
    assert (await env.runner.tick())["reason"] == "kill_switch"


async def test_no_pass_when_pass_running(env):
    env.runner.desired = "running"
    env.runner.running_pass = True
    assert (await env.runner.tick())["reason"] == "pass_running"
    assert (await env.runner.run_once("t"))["reason"] == "pass_running"


async def test_not_due(env):
    env.runner.desired = "running"
    env.runner.last_run_utc = rn.utcnow()
    assert (await env.runner.tick())["reason"] == "not_due"


async def test_live_mode_without_env_skips(env):
    env.runner.desired, env.runner.mode = "running", "live"
    assert (await env.runner.tick())["reason"] == "live_not_allowed"


async def test_due_pass_runs_main_writes_heartbeat(no_monitor, demo_keys, monkeypatch):
    env = no_monitor
    calls = []
    monkeypatch.setattr(env.runner.ms, "main", lambda *a, **k: calls.append(1) or 0)
    env.runner.desired = "running"
    assert (await env.runner.tick("t"))["ran"] is True
    # a concurrent tick right after must not start a second pass
    assert (await env.runner.tick("t"))["reason"] == "pass_running"
    await _drain(env.runner)
    assert calls == [1] and env.runner.last_error is None
    hb = await env.store.last_heartbeat()
    assert hb["ok"] is True
    assert (await env.runner.tick())["reason"] == "not_due"


async def test_pass_survives_exception_and_records_last_error(no_monitor, demo_keys, monkeypatch):
    env = no_monitor

    def boom(*a, **k):
        raise ValueError("kaboom")
    monkeypatch.setattr(env.runner.ms, "main", boom)
    alerts = []

    async def send(*a, **k):
        alerts.append(a)
    env.runner.alerter.send = send
    env.runner.desired = "running"
    await env.runner.tick()
    await _drain(env.runner)
    assert "kaboom" in env.runner.last_error
    assert env.runner.running_pass is False and alerts
    assert (await env.store.last_heartbeat())["ok"] is False


async def test_pass_without_keys_records_error_and_never_calls_main(no_monitor, monkeypatch):
    env = no_monitor
    for k in ("KALSHI_DEMO_API_KEY_ID", "KALSHI_DEMO_PRIVATE_KEY"):
        monkeypatch.delenv(k, raising=False)
    called = []
    monkeypatch.setattr(env.runner.ms, "main", lambda *a, **k: called.append(1) or 0)
    env.runner.desired = "running"
    await env.runner.tick()
    await _drain(env.runner)
    assert not called and "keys" in env.runner.last_error


async def test_stays_stopped_when_store_down_at_startup(settings, bot_paths, kalshi):
    app = create_app(settings, store=BrokenStore(), kalshi_factory=lambda m: kalshi)
    r = app.state.runner
    r.desired = "running"
    await r.rehydrate()
    assert r.desired == "stopped" and r.store_ok is False and "store unreachable" in r.last_error


async def test_rehydrate_restores_running_state(env):
    await env.store.set_state(by="t", desired="running", mode="demo")
    await env.runner.rehydrate()
    assert env.runner.desired == "running"


async def test_rehydrate_downgrades_live_when_env_disallows(env):
    await env.store.set_state(by="t", mode="live")
    await env.runner.rehydrate()
    assert env.runner.mode == "demo"


async def test_journal_tail_is_idempotent(env):
    j = env.runner.journal_path
    j.write_text("utc,event,note\n2026-10-01T00:00:00+00:00,order,a\n", encoding="utf-8")
    tail = botfiles.JournalTail(j)
    assert len(tail.read_new()) == 1
    assert tail.read_new() == []
    with j.open("a", encoding="utf-8") as f:
        f.write("2026-10-01T00:01:00+00:00,order,b\n")
        f.write("2026-10-01T00:02:00+00:00,order,part")      # incomplete line: not consumed
    assert [r["note"] for r in tail.read_new()] == ["b"]
    with j.open("a", encoding="utf-8") as f:
        f.write("ial\n")
    assert [r["note"] for r in tail.read_new()] == ["partial"]
    assert tail.read_new() == []


async def test_sync_journal_twice_stores_rows_once(env):
    j = env.runner.journal_path
    j.write_text("utc,event,note\n2026-10-01T00:00:00+00:00,order,a\n", encoding="utf-8")
    env.runner._tail = botfiles.JournalTail(j)
    assert await env.runner._sync_journal() == 1
    assert await env.runner._sync_journal() == 0
    assert len(await env.store.list_journal()) == 1


async def test_store_dedupes_same_row_replayed(env):
    row = {"utc": "2026-10-01T00:00:00+00:00", "event": "order", "note": "a"}
    assert await env.store.add_journal_rows([row]) == 1
    assert await env.store.add_journal_rows([row]) == 0


async def test_weekly_monitor_scheduled_after_ok_pass(env, demo_keys, monkeypatch):
    monkeypatch.setattr(env.runner.ms, "main", lambda *a, **k: 0)
    ran = []

    async def fake_monitor(by):
        ran.append(by)
        return {}
    env.runner.run_monitor = fake_monitor
    env.runner.desired = "running"
    env.runner.last_monitor_utc = None
    await env.runner.tick()
    await _drain(env.runner)
    assert ran == ["weekly"]


async def test_monitor_not_rerun_within_week(env, demo_keys, monkeypatch):
    monkeypatch.setattr(env.runner.ms, "main", lambda *a, **k: 0)
    ran = []

    async def fake_monitor(by):
        ran.append(by)
    env.runner.run_monitor = fake_monitor
    env.runner.desired = "running"
    env.runner.last_monitor_utc = rn.utcnow() - timedelta(days=1)
    await env.runner.tick()
    await _drain(env.runner)
    assert ran == []


async def test_silence_alert_once(env):
    sent = []

    async def send(*a, **k):
        sent.append(a)
    env.runner.alerter.send = send
    env.runner.desired = "running"
    env.runner.last_run_utc = rn.utcnow() - timedelta(hours=5)
    await env.runner.check_silence()
    await env.runner.check_silence()
    assert len(sent) == 1


async def test_stop_while_running_makes_next_tick_skip(env):
    env.runner.desired = "running"
    await env.runner.stop("t")
    assert (await env.runner.tick())["reason"] == "stopped"
