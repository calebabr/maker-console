"""B2 pending-release endpoints: the workflow registers + polls with X-Release-Token; the owner approves with
fresh 2FA (agents never can) or rejects; 404 while RELEASE_TOKEN is unset."""
from __future__ import annotations

from datetime import timedelta

import pytest

pytestmark = pytest.mark.asyncio
TOKEN = "release-token-for-tests-0123456789"
BODY = {"version": "2026.10.16-1", "commit": "abc1234def", "summary": "fix: something", "ci": "success"}


@pytest.fixture
def rel_env(env, monkeypatch):
    monkeypatch.setattr(env.settings, "release_token", TOKEN)
    return env


def _t(tok=TOKEN):
    return {"X-Release-Token": tok}


async def _register(e):
    r = await e.client.post("/api/release/pending", json=BODY, headers=_t())
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _audits(e):
    return [a["action"] for a in await e.store.list_audit(limit=100)]


async def test_everything_404_when_token_unset(env):
    assert env.settings.release_token is None
    assert (await env.client.post("/api/release/pending", json=BODY, headers=_t())).status_code == 404
    assert (await env.client.post("/api/release/pending", content=b"junk", headers=_t())).status_code == 404
    assert (await env.client.get("/api/release/pending/abc", headers=_t())).status_code == 404
    assert (await env.client.get("/api/release/pending", headers=env.auth)).status_code == 404
    assert (await env.client.post("/api/release/pending/abc/reject", headers=env.auth)).status_code == 404
    r = await env.client.post("/api/release/pending/abc/approve", headers=await env.h2fa())
    assert r.status_code == 404


@pytest.mark.parametrize("bad", [None, "", "x", TOKEN + "x", TOKEN[:-1], "t" * 300])
async def test_token_required(rel_env, bad):
    h = {} if bad is None else _t(bad)
    r = await rel_env.client.post("/api/release/pending", json=BODY, headers=h)
    assert r.status_code == 401 and TOKEN not in r.text
    assert (await rel_env.client.get("/api/release/pending/abc", headers=h)).status_code == 401


async def test_login_is_not_the_release_token(rel_env):
    r = await rel_env.client.post("/api/release/pending", json=BODY, headers=rel_env.auth)
    assert r.status_code == 401


@pytest.mark.parametrize("patch", [{"version": "bad version"}, {"commit": "XYZ"}, {"commit": "abc"},
                                   {"summary": "s" * 3001}, {"ci": "c" * 501}, {"extra": 1}])
async def test_input_limits(rel_env, patch):
    r = await rel_env.client.post("/api/release/pending", json={**BODY, **patch}, headers=_t())
    assert r.status_code == 422


async def test_body_size_limit(rel_env):
    r = await rel_env.client.post("/api/release/pending", content=b"{" + b" " * 9000 + b"}", headers=_t())
    assert r.status_code == 413


async def test_register_poll_approve_with_fresh_2fa(rel_env):
    e = rel_env
    rid = await _register(e)
    assert (await e.client.get(f"/api/release/pending/{rid}", headers=_t())).json() == {"status": "pending"}
    latest = (await e.client.get("/api/release/pending", headers=e.auth)).json()["release"]
    assert latest["id"] == rid and latest["commit"] == BODY["commit"] and latest["status"] == "pending"
    # no 2FA code (an agent with the owner's login but no authenticator): refused
    r = await e.client.post(f"/api/release/pending/{rid}/approve", headers=e.auth)
    assert r.status_code == 403 and r.json()["detail"] == "2fa_required"
    r = await e.client.post(f"/api/release/pending/{rid}/approve", headers={**e.auth, "X-2FA-Code": "000000"})
    assert r.status_code == 403
    assert (await e.client.post(f"/api/release/pending/{rid}/approve")).status_code == 401
    r = await e.client.post(f"/api/release/pending/{rid}/approve", headers=await e.h2fa())
    assert r.status_code == 200 and r.json() == {"id": rid, "status": "approved"}
    assert (await e.client.get(f"/api/release/pending/{rid}", headers=_t())).json() == {"status": "approved"}
    assert (await e.client.get("/api/release/pending", headers=e.auth)).json() == {"release": None}
    r = await e.client.post(f"/api/release/pending/{rid}/reject", headers=e.auth)       # already decided
    assert r.status_code == 409
    acts = await _audits(e)
    assert "release.register" in acts and "release.approve" in acts


async def test_reject_needs_only_a_login(rel_env):
    e = rel_env
    rid = await _register(e)
    assert (await e.client.post(f"/api/release/pending/{rid}/reject")).status_code == 401
    r = await e.client.post(f"/api/release/pending/{rid}/reject", headers=e.auth)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert (await e.client.get(f"/api/release/pending/{rid}", headers=_t())).json() == {"status": "rejected"}
    r = await e.client.post(f"/api/release/pending/{rid}/approve", headers=await e.h2fa())
    assert r.status_code == 409
    assert "release.reject" in await _audits(e)


async def test_expiry_after_30_minutes(rel_env):
    e = rel_env
    rid = await _register(e)
    doc = e.store._releases[rid]
    doc["expires_at"] = doc["created_at"] - timedelta(seconds=1)
    assert (await e.client.get(f"/api/release/pending/{rid}", headers=_t())).json() == {"status": "expired"}
    assert (await e.client.get("/api/release/pending", headers=e.auth)).json() == {"release": None}
    r = await e.client.post(f"/api/release/pending/{rid}/approve", headers=await e.h2fa())
    assert r.status_code == 409 and r.json()["detail"] == "release_not_pending"


async def test_unknown_or_malformed_id(rel_env):
    e = rel_env
    assert (await e.client.get("/api/release/pending/nope", headers=_t())).status_code == 404
    assert (await e.client.get("/api/release/pending/" + "a" * 65, headers=_t())).status_code == 422
    r = await e.client.post("/api/release/pending/nope/reject", headers=e.auth)
    assert r.status_code == 404


async def test_rate_limit_on_token_endpoints(rel_env):
    codes = [(await rel_env.client.get("/api/release/pending/x", headers=_t("wrong"))).status_code
             for _ in range(61)]
    assert codes[:60] == [401] * 60 and codes[60] == 429


async def test_infra_workflow_shapes(rel_env):
    """The release workflow's assumptions: 3000-char summary, a short ci string, a full sha, {id, status}."""
    body = {"version": "v2026.10.16-1", "commit": "a" * 40, "summary": "s" * 3000,
            "ci": "green: python, frontend, e2e, semgrep, gitleaks"}
    r = await rel_env.client.post("/api/release/pending", json=body, headers=_t())
    assert r.status_code == 200 and set(r.json()) == {"id", "status"}
    import re
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", r.json()["id"])
