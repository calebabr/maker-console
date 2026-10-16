import json

import pytest

from control_api.services import botfiles

pytestmark = pytest.mark.asyncio


async def _put(env, body):
    return await env.client.put("/api/config", headers=await env.h2fa(), json=body)


async def test_put_config_needs_2fa(env):
    r = await env.client.put("/api/config", headers=env.auth, json={"run_budget": 10})
    assert r.status_code == 403


@pytest.mark.parametrize("body", [
    {"run_budget": 501}, {"run_budget": 0}, {"run_budget": -5},
    {"max_open_frac": 0.51}, {"max_open_frac": 0}, {"max_open_frac": 1.5},
    {"poll_minutes": 15}, {"poll_minutes": 0}, {"poll_minutes": -1},
    {"max_order": -1}, {"series": []}, {"series": ["NOTAREALSERIES"]},
    {"series": ["KXDEMOA", "KXDEMOA"]}, {"series": ["KXDEMOA", "KXDEMOB", "KXDEMOA"]},
])
async def test_out_of_range_rejected_400_and_file_unchanged(env, body):
    before = botfiles.read_config_file(env.runner.config_path)
    nver = len(await env.store.list_config_versions())
    r = await _put(env, body)
    assert r.status_code == 400, r.text
    assert botfiles.read_config_file(env.runner.config_path) == before
    assert len(await env.store.list_config_versions()) == nver


async def test_cells_edit_rejected(env):
    r = await _put(env, {"cells": [[1, 2]]})
    assert r.status_code == 400 and "cells" in json.dumps(r.json())


async def test_unknown_field_rejected(env):
    assert (await _put(env, {"evil": 1})).status_code == 400


async def test_ceiling_boundaries_accepted(env):
    r = await _put(env, {"run_budget": 500, "max_open_frac": 0.5, "poll_minutes": 14})
    assert r.status_code == 200, r.text


async def test_success_creates_version_audit_file_and_keeps_help(env):
    path = env.runner.config_path
    help_keys_before = {k for k in json.loads(path.read_text()) if k.startswith("_")}
    cells_before = botfiles.read_config_file(path).get("cells")
    nver = len(await env.store.list_config_versions())
    r = await _put(env, {"run_budget": 100})
    assert r.status_code == 200 and r.json()["version"] >= 1
    on_disk = json.loads(path.read_text())
    assert on_disk["run_budget"] == 100
    assert help_keys_before <= set(on_disk)                   # _help keys kept
    assert botfiles.read_config_file(path).get("cells") == cells_before
    assert len(await env.store.list_config_versions()) == nver + 1
    audit = await env.store.list_audit()
    assert any(a.get("action") == "config.update" and a.get("ok") for a in audit)
    assert not list(path.parent.glob("*.tmp"))                # atomic write leaves no temp files
    assert env.runner.config["run_budget"] == 100


async def test_get_config_requires_login_and_hides_help(env):
    assert (await env.client.get("/api/config")).status_code == 401
    r = await env.client.get("/api/config", headers=env.auth)
    assert r.status_code == 200
    assert r.json()["ceilings"] == {"run_budget": 500.0, "max_open_frac": 0.5, "poll_minutes": 14}
    assert not any(k.startswith("_") for k in r.json()["config"])


async def test_pause_market_and_cannot_pause_last(env):
    series = list(botfiles.public_config(botfiles.read_config_file(env.runner.config_path))["series"])
    assert len(series) > 1
    for s in series[:-1]:
        r = await env.client.put(f"/api/markets/{s}", headers=await env.h2fa(), json={"enabled": False})
        assert r.status_code == 200, r.text
    last = series[-1]
    r = await env.client.put(f"/api/markets/{last}", headers=await env.h2fa(), json={"enabled": False})
    assert r.status_code == 400
    assert env.runner.config["series"] == [last]


async def test_unknown_market_400(env):
    r = await env.client.put("/api/markets/NOPE", headers=await env.h2fa(), json={"enabled": False})
    assert r.status_code == 400


async def test_failed_edit_is_audited(env):
    await _put(env, {"run_budget": 9999})
    audit = await env.store.list_audit()
    assert any(a.get("action") == "config.update" and a.get("ok") is False for a in audit)
