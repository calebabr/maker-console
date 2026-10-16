"""Public stand-in gate: with the demo placeholder kalshi_bot (IS_STANDIN) LIVE is refused everywhere with a clear
error, demo still works, and Stop is unaffected. Tests force the stand-in with the `standin` fixture, so they also
hold after the private overlay."""
from __future__ import annotations

import sys
import types

import pytest

from control_api import runner as runmod
from control_api.services import botfiles

pytestmark = pytest.mark.asyncio

LIVE_ENV = {"KALSHI_PROD_API_KEY_ID": "prod-id", "KALSHI_PROD_PRIVATE_KEY": "not-a-real-key",
            "KALSHI_LIVE_CONFIRM": runmod.LIVE_CONFIRM}


def _arm_env_gates(env, monkeypatch):
    """Every env gate for LIVE is set (prod keys + confirm, snapshotted as at startup)."""
    for k, v in LIVE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(env.settings, "prod_keys_present", True)
    monkeypatch.setattr(env.settings, "live_confirm_ok", True)


# ---------------------------------------------------------------- is_standin (fail-closed)
def _fake_modules(monkeypatch, ms_attrs: dict | None, overlay: dict | None | str):
    if ms_attrs is None:
        monkeypatch.setitem(sys.modules, "maker_strategy", None)          # import raises ImportError
    else:
        monkeypatch.setitem(sys.modules, "maker_strategy", types.SimpleNamespace(**ms_attrs))
    if overlay == "missing":
        monkeypatch.setitem(sys.modules, "_overlay", None)
    else:
        monkeypatch.setitem(sys.modules, "_overlay", types.SimpleNamespace(**(overlay or {})))


@pytest.mark.parametrize("ms_attrs,overlay,want", [
    ({"IS_STANDIN": True}, "missing", True),                 # the public stand-in as shipped
    ({"IS_STANDIN": True}, {"IS_STANDIN": False}, True),     # marker written but the stand-in strategy still there
    ({}, "missing", True),                                   # real strategy copied, overlay marker missing
    ({}, {"IS_STANDIN": True}, True),
    ({}, {}, True),                                          # marker without the flag
    ({}, {"IS_STANDIN": "false"}, True),                     # only the boolean False counts
    ({"IS_STANDIN": 0}, {"IS_STANDIN": False}, True),
    (None, {"IS_STANDIN": False}, True),                     # strategy import fails
    ({}, {"IS_STANDIN": False}, False),                      # overlaid: the only "installed" case
    ({"IS_STANDIN": False}, {"IS_STANDIN": False}, False),
])
async def test_is_standin_is_fail_closed(monkeypatch, ms_attrs, overlay, want):
    _fake_modules(monkeypatch, ms_attrs, overlay)
    assert botfiles.is_standin() is want


async def test_public_tree_ships_the_standin():
    """Without the overlay the installed kalshi_bot is the stand-in (skipped once the private files are overlaid)."""
    if (botfiles.BOT_DIR / "_overlay.py").exists():
        pytest.skip("private strategy overlaid")
    assert botfiles.strategy().IS_STANDIN is True and botfiles.is_standin() is True


async def test_live_gate_ignores_env_when_standin(settings, standin):
    settings.prod_keys_present = settings.live_confirm_ok = True
    assert runmod.live_block_reason(settings) == "strategy_not_installed"
    assert runmod.live_allowed_settings(settings) is False


async def test_live_gate_env_reason_when_strategy_installed(settings, real_strategy):
    assert runmod.live_block_reason(settings) == "live_not_allowed"
    settings.prod_keys_present = settings.live_confirm_ok = True
    assert runmod.live_block_reason(settings) is None and runmod.live_allowed_settings(settings) is True


# ---------------------------------------------------------------- LIVE refused with a clear error
async def test_live_mode_switch_refused_on_standin(env, monkeypatch, standin):
    _arm_env_gates(env, monkeypatch)
    r = await env.client.put("/api/mode", headers=await env.h2fa(), json={"mode": "live", "confirm": "LIVE"})
    assert r.status_code == 409 and r.json()["detail"] == "strategy_not_installed"
    assert env.runner.mode == "demo" and (await env.store.get_state() or {}).get("mode", "demo") == "demo"
    audits = [a for a in await env.store.list_audit(limit=20) if a.get("action") == "mode.set"]
    assert audits and audits[0]["ok"] is False and audits[0]["details"]["error"] == "strategy_not_installed"


async def test_start_in_live_mode_refused_on_standin(env, monkeypatch, standin):
    _arm_env_gates(env, monkeypatch)
    env.runner.mode = "live"                       # e.g. a live mode set before a downgrade to the stand-in
    r = await env.client.post("/api/bot/start", headers=await env.h2fa())
    assert r.status_code == 409 and r.json()["detail"] == "strategy_not_installed"
    assert env.runner.desired == "stopped" and (await env.store.get_state() or {}).get("desired") != "running"
    assert env.runner._skip_reason() in ("stopped", "live_not_allowed")


async def test_live_pass_never_runs_on_standin(env, monkeypatch, standin):
    _arm_env_gates(env, monkeypatch)
    env.runner.mode, env.runner.desired = "live", "running"
    ran = []
    monkeypatch.setattr(env.runner, "_run_strategy_sync", lambda mode: ran.append(mode) or (0, ""))
    assert await env.runner.tick("test") == {"ran": False, "reason": "live_not_allowed"}
    await env.runner._pass("forced")               # even a forced pass refuses before the strategy runs
    assert ran == [] and "live not allowed" in (env.runner.last_error or "")


async def test_arb_live_refused_on_standin(env, monkeypatch, standin):
    _arm_env_gates(env, monkeypatch)
    r = await env.client.put("/api/arb", json={"enabled": True, "live": True, "confirm": "LIVE"},
                             headers=await env.h2fa())
    assert r.status_code == 409 and r.json()["detail"] == "strategy_not_installed"
    assert env.runner.arb_mode == "demo"
    with pytest.raises(PermissionError, match="strategy_not_installed"):
        await env.runner.set_arb(enabled=True, live=True, by="test")


async def test_status_reports_the_standin(env, standin):
    body = (await env.client.get("/api/status", headers=env.auth)).json()
    assert body["live_allowed"] is False and body["live_block_reason"] == "strategy_not_installed"
    assert body["strategy_installed"] is False


# ---------------------------------------------------------------- demo still works, Stop unaffected
async def test_demo_mode_start_and_pass_work_with_the_standin(env, monkeypatch, standin):
    if not getattr(botfiles.strategy(), "IS_STANDIN", False):
        pytest.skip("private strategy overlaid: its main() would call Kalshi")
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "demo-id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "not-a-real-key")
    h = await env.h2fa()
    assert (await env.client.put("/api/mode", headers=h, json={"mode": "demo"})).status_code == 200
    r = await env.client.post("/api/bot/start", headers=await env.h2fa())
    assert r.status_code == 200 and env.runner.desired == "running"
    await env.runner._pass("test")                 # the real stand-in main(): no network, no orders
    assert env.runner.last_error is None
    hb = await env.store.last_heartbeat()
    assert hb and hb["ok"] is True and hb["mode"] == "demo"
    assert "no quotes placed" in "\n".join(str(x) for x in env.runner.logs.tail(2000))


async def test_stop_is_unaffected_by_the_standin_gate(env, monkeypatch, standin):
    def boom():
        raise AssertionError("Stop must not consult the strategy gate")
    monkeypatch.setattr(botfiles, "is_standin", boom)
    env.runner.desired = "running"
    r = await env.client.post("/api/bot/stop", headers=env.auth)          # no 2FA
    body = r.json()
    assert r.status_code == 200 and body["errors"] == [] and body["cancelled"] == 2
    assert env.runner.desired == "stopped" and env.runner.kill_switch()
    assert sorted(env.kalshi.cancelled) == ["o1", "o3"]                   # only mm- orders
