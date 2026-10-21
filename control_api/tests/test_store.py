"""Store contract tests. Run against MongoStore too only if TEST_MONGODB_URI is set."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from control_api.db import MemoryStore

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)

_params = ["memory"] + (["mongo"] if os.environ.get("TEST_MONGODB_URI") else [])


@pytest_asyncio.fixture(params=_params)
async def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    from control_api.db.mongo import MongoStore
    s = MongoStore(os.environ["TEST_MONGODB_URI"], "kalshi_test_" + uuid.uuid4().hex[:8])
    await s.ensure_indexes()
    yield s
    await s.client.drop_database(s.db.name) if hasattr(s, "client") else None
    await s.close()


async def test_state_defaults_and_partial_updates(store):
    await store.set_state(by="a", desired="running")
    st = await store.get_state()
    assert st["desired"] == "running" and st["mode"] == "demo"
    await store.set_state(by="a", mode="live")
    st = await store.get_state()
    assert st["desired"] == "running" and st["mode"] == "live"


async def test_state_invalid_values_rejected(store):
    with pytest.raises(ValueError):
        await store.set_state(by="a", desired="maybe")
    with pytest.raises(ValueError):
        await store.set_state(by="a", mode="paper")


async def test_first_state_write_defaults_stopped(store):
    await store.set_state(by="a", mode="demo")
    assert (await store.get_state())["desired"] == "stopped"


async def test_config_versions(store):
    v1 = await store.save_config({"run_budget": 1}, before=None, by="a")
    v2 = await store.save_config({"run_budget": 2}, before={"run_budget": 1}, by="b")
    assert v2 > v1
    assert (await store.get_config())["run_budget"] == 2
    assert len(await store.list_config_versions()) == 2


async def test_journal_dedupe_and_filter(store):
    rows = [{"utc": "2026-10-01T00:00:00+00:00", "event": "order", "note": "a"},
            {"utc": "2026-10-01T00:01:00+00:00", "event": "fill", "note": "b"}]
    assert await store.add_journal_rows(rows) == 2
    assert await store.add_journal_rows(rows) == 0
    assert len(await store.list_journal(events=["fill"])) == 1


async def test_heartbeat_last(store):
    await store.add_heartbeat({"ts": NOW, "ok": True})
    await store.add_heartbeat({"ts": NOW + timedelta(minutes=5), "ok": False})
    assert (await store.last_heartbeat())["ok"] is False


async def test_account_snapshots_newest_first(store):
    for i in range(3):
        await store.add_account_snapshot({"ts": NOW + timedelta(hours=i), "cash": i})
    snaps = await store.list_account_snapshots()
    assert [s["cash"] for s in snaps] == [2, 1, 0]


async def test_fills_upsert_idempotent(store):
    f = {"fill_id": "f1", "ts": NOW, "pnl": 1.0}
    assert await store.upsert_fills([f]) == 1
    assert await store.upsert_fills([f]) == 0
    assert len(await store.list_fills()) == 1


async def test_audit_newest_first(store):
    await store.add_audit({"action": "a", "ts": NOW})
    await store.add_audit({"action": "b", "ts": NOW + timedelta(seconds=1)})
    assert (await store.list_audit())[0]["action"] == "b"


async def test_user_merge_and_secret_guard(store):
    await store.save_user({"username": "u", "password_hash": "h"})
    await store.save_user({"username": "u", "failed_logins": 3})
    u = await store.get_user("u")
    assert u["password_hash"] == "h" and u["failed_logins"] == 3
    assert await store.get_user("nobody") is None
    with pytest.raises(ValueError):
        await store.save_user({"username": "u", "api_key": "sk-123"})
    with pytest.raises(ValueError):
        await store.save_user({"password_hash": "x"})


async def test_revocation_roundtrip(store):
    assert await store.is_revoked("j") is False
    await store.revoke_jti("j", NOW + timedelta(days=3650))
    assert await store.is_revoked("j") is True


async def test_cloud_settings_merge_and_secret_guard(store):
    await store.save_cloud_settings({"pinger_minutes": 5}, by="a")
    await store.save_cloud_settings({"keepalive_minutes": 9}, by="a")
    c = await store.get_cloud_settings()
    assert c["pinger_minutes"] == 5 and c["keepalive_minutes"] == 9
    with pytest.raises(ValueError):
        await store.save_cloud_settings({"cronjob_api_key": "k"}, by="a")


async def test_monitor_summary_last(store):
    await store.add_monitor_summary({"ts": NOW, "summary": {"a": 1}})
    await store.add_monitor_summary({"ts": NOW + timedelta(days=1), "summary": {"a": 2}})
    assert (await store.last_monitor_summary())["summary"]["a"] == 2


async def test_ping(store):
    assert await store.ping() is True


async def test_memory_state_stamp_strictly_increases_within_one_millisecond(monkeypatch):
    """Two state writes in the same millisecond must carry different stamps: the runner tells a changed state doc by
    its stamp, so a tie would hide a Stop written by another instance (seen on a fast Linux CI runner)."""
    from control_api.db import schemas as S
    fixed = S.utcnow()
    monkeypatch.setattr(S, "utcnow", lambda: fixed)
    store = MemoryStore()
    a = await store.set_state(by="owner", desired="running")
    b = await store.set_state(by="owner", desired="stopped")
    assert b["updated_utc"] > a["updated_utc"]
