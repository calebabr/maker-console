"""S-15: session epoch (``sessions_valid_after``), logout-all, bootstrap resets, and Stop
keeping working with the store down (fail open to the last-known value; others fail closed)."""
import argparse
import time
import uuid
from datetime import timedelta

import httpx
import jwt
import pyotp
import pytest

from control_api.auth import bootstrap
from control_api.auth import deps
from control_api.auth import security as sec
from control_api.main import create_app
from control_api.tests.conftest import PASSWORD, BrokenStore

pytestmark = pytest.mark.asyncio


def _token(settings, iat: int, sub="owner", typ=sec.TYP_ACCESS, ttl=3600) -> str:
    claims = {"sub": sub, "typ": typ, "jti": uuid.uuid4().hex, "iat": iat, "exp": iat + ttl}
    return jwt.encode(claims, settings.jwt_secret, algorithm=sec.JWT_ALG)


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _set_sva(env, when):
    await env.store.save_user({"username": "owner", deps.SESSIONS_FIELD: when})


@pytest.fixture
def nocache(env):
    env.app.state.auth_sessions_ttl_s = 0      # every request re-reads the store
    return env


# ---- core iat check ----------------------------------------------------------------------
async def test_token_issued_before_epoch_is_rejected(nocache):
    env = nocache
    await _set_sva(env, sec.utcnow() + timedelta(seconds=1))
    r = await env.client.get("/api/auth/me", headers=env.auth)
    assert r.status_code == 401 and r.json()["detail"] == "session_expired"


async def test_token_issued_after_epoch_is_accepted(nocache):
    env = nocache
    now = int(time.time())
    await _set_sva(env, sec.utcnow() - timedelta(seconds=30))
    assert (await env.client.get("/api/auth/me", headers=_h(_token(env.settings, now)))).status_code == 200
    old = _token(env.settings, now - 60)
    r = await env.client.get("/api/auth/me", headers=_h(old))
    assert r.status_code == 401 and r.json()["detail"] == "session_expired"


async def test_no_epoch_means_no_constraint(nocache):
    assert (await nocache.client.get("/api/auth/me", headers=nocache.auth)).status_code == 200


async def test_same_second_token_is_rejected_fail_safe():
    assert deps.token_predates_sessions(100, 100.0)
    assert deps.token_predates_sessions(100, 100.4)
    assert not deps.token_predates_sessions(101, 100.4)
    assert not deps.token_predates_sessions(5, 0.0)
    assert deps.token_predates_sessions("garbage", 1.0)


async def test_epoch_also_applies_to_stop_when_store_is_up(nocache):
    env = nocache
    await _set_sva(env, sec.utcnow() + timedelta(seconds=1))
    r = await env.client.post("/api/bot/stop", headers=env.auth)
    assert r.status_code == 401 and r.json()["detail"] == "session_expired"
    assert not env.runner.kill_path.exists()


async def test_mfa_token_issued_before_epoch_is_rejected(nocache):
    env = nocache
    mfa = (await env.client.post("/api/auth/login",
                                 json={"username": "owner", "password": PASSWORD})).json()["mfa_token"]
    await _set_sva(env, sec.utcnow() + timedelta(seconds=1))
    r = await env.client.post("/api/auth/2fa", json={"mfa_token": mfa, "code": pyotp.TOTP(env.secret).now()})
    assert r.status_code == 401 and r.json()["detail"] == "invalid_mfa_token"


# ---- store down: Stop fails open, everything else fails closed ---------------------------
async def test_stop_works_with_store_down_and_no_cached_epoch(settings, bot_paths, kalshi):
    app = create_app(settings, store=BrokenStore(), kalshi_factory=lambda m: kalshi)
    tok = sec.create_token(settings, "owner", sec.TYP_ACCESS, 3600)[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/bot/stop", headers=_h(tok))
        assert r.status_code == 200
        assert app.state.runner.kill_path.exists()
        # non-Stop endpoints fail closed
        me = await c.get("/api/auth/me", headers=_h(tok))
        assert me.status_code == 503 and me.json()["detail"] == "store_unavailable"


async def test_stop_with_store_down_uses_cached_epoch(nocache):
    env = nocache
    sva = sec.utcnow() - timedelta(seconds=30)
    await _set_sva(env, sva)
    assert (await env.client.get("/api/auth/me", headers=env.auth)).status_code == 200  # primes cache
    old = _token(env.settings, int(sva.timestamp()) - 60)   # issued before the epoch
    env.app.state.store = BrokenStore()                     # Atlas goes down (auth's view)
    r = await env.client.post("/api/bot/stop", headers=_h(old))
    assert r.status_code == 401 and r.json()["detail"] == "session_expired"
    r = await env.client.post("/api/bot/stop", headers=env.auth)   # issued after: OK
    assert r.status_code == 200
    assert env.runner.kill_path.exists()
    assert (await env.client.get("/api/auth/me", headers=env.auth)).status_code == 503


async def test_stop_with_hanging_store_is_bounded(nocache, monkeypatch):
    import asyncio
    env = nocache
    monkeypatch.setattr(deps, "SESSIONS_TIMEOUT_S", 0.2)
    monkeypatch.setattr(deps, "REVOCATION_TIMEOUT_S", 0.2)

    class Hanging:
        async def get_user(self, *a, **k):
            await asyncio.sleep(30)

        async def is_revoked(self, *a, **k):
            await asyncio.sleep(30)

        def __getattr__(self, name):
            async def boom(*a, **k):
                raise RuntimeError("atlas down")
            return boom

    env.app.state.store = Hanging()
    t0 = time.monotonic()
    r = await env.client.post("/api/bot/stop", headers=env.auth)
    assert r.status_code == 200
    assert time.monotonic() - t0 < 2.0


async def test_current_user_stop_dependency_always_fails_open(nocache):
    from starlette.requests import Request
    env = nocache
    env.app.state.store = BrokenStore()
    scope = {"type": "http", "method": "GET", "path": "/anything", "headers":
             [(b"authorization", f"Bearer {env.token}".encode())], "app": env.app,
             "query_string": b""}
    user = await deps.current_user_stop(Request(scope))
    assert user["username"] == "owner"
    with pytest.raises(Exception) as ei:
        await deps.current_user(Request(scope))
    assert getattr(ei.value, "status_code", None) == 503


async def test_cached_epoch_never_moves_backwards(env):
    from starlette.requests import Request
    req = Request({"type": "http", "app": env.app, "headers": [], "method": "GET", "path": "/"})
    deps.remember_sessions_valid_after(req, "owner", 200.0)
    assert deps.remember_sessions_valid_after(req, "owner", 100.0) == 200.0


# ---- logout-all --------------------------------------------------------------------------
async def test_logout_all_requires_fresh_2fa(nocache):
    r = await nocache.client.post("/api/auth/logout-all", headers=nocache.auth)
    assert r.status_code == 403 and r.json()["detail"] == "2fa_required"


async def test_logout_all_kills_every_existing_token(nocache):
    env = nocache
    other = _token(env.settings, int(time.time()) - 5)
    h = await env.h2fa()
    r = await env.client.post("/api/auth/logout-all", headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert (await env.store.get_user("owner"))[deps.SESSIONS_FIELD] is not None
    for tok in (env.token, other):
        r = await env.client.get("/api/auth/me", headers=_h(tok))
        assert r.status_code == 401
    # even Stop with an old token is refused while the store is up ...
    assert (await env.client.post("/api/bot/stop", headers=_h(other))).status_code == 401
    # ... and while it is down (cached epoch)
    env.app.state.store = BrokenStore()
    assert (await env.client.post("/api/bot/stop", headers=_h(other))).status_code == 401
    # a token minted after the epoch still stops the bot with the store down
    fresh = _token(env.settings, int(time.time()) + 2)
    assert (await env.client.post("/api/bot/stop", headers=_h(fresh))).status_code == 200
    actions = [a["action"] for a in await env.store.list_audit(50)]
    assert "auth.logout_all" in actions


async def test_logout_all_fails_closed_when_store_write_fails(nocache, monkeypatch):
    env = nocache
    h = await env.h2fa()
    real_save = env.store.save_user

    async def save_user(doc):
        if deps.SESSIONS_FIELD in doc:
            raise RuntimeError("atlas down")
        return await real_save(doc)
    monkeypatch.setattr(env.store, "save_user", save_user)
    r = await env.client.post("/api/auth/logout-all", headers=h)
    assert r.status_code == 503
    # this process still rejects the old token
    assert (await env.client.get("/api/auth/me", headers=env.auth)).status_code == 401


# ---- bootstrap ---------------------------------------------------------------------------
def _args(**kw):
    base = dict(username="owner", reset_2fa=False, revoke_sessions=False, allow_memory=True)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def boot(env, monkeypatch):
    async def get_store(settings):
        return env.store
    monkeypatch.setattr(bootstrap, "_load_settings", lambda: env.settings)
    monkeypatch.setattr(bootstrap, "_get_store", get_store)
    monkeypatch.setattr(bootstrap, "print_enrollment", lambda *a, **k: None)
    monkeypatch.setattr(bootstrap, "_confirm_totp", lambda secret: 1)
    monkeypatch.setattr(env.store, "close", lambda: None, raising=False)
    env.app.state.auth_sessions_ttl_s = 0
    return env


async def _assert_old_token_dead(env):
    r = await env.client.get("/api/auth/me", headers=env.auth)
    assert r.status_code == 401 and r.json()["detail"] == "session_expired"


async def test_bootstrap_reset_2fa_invalidates_tokens(boot):
    assert (await boot.client.get("/api/auth/me", headers=boot.auth)).status_code == 200
    assert await bootstrap._enroll(_args(reset_2fa=True)) == 0
    await _assert_old_token_dead(boot)


async def test_bootstrap_overwrite_invalidates_tokens(boot, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "OVERWRITE")
    monkeypatch.setattr(bootstrap, "_prompt_password", lambda: "a brand new long password")
    assert await bootstrap._enroll(_args()) == 0
    await _assert_old_token_dead(boot)


async def test_bootstrap_revoke_sessions_keeps_credentials(boot):
    before = await boot.store.get_user("owner")
    assert await bootstrap._enroll(_args(revoke_sessions=True)) == 0
    after = await boot.store.get_user("owner")
    assert after["password_hash"] == before["password_hash"]
    assert after["totp_secret_enc"] == before["totp_secret_enc"]
    await _assert_old_token_dead(boot)


async def test_bootstrap_revoke_and_reset_are_exclusive():
    with pytest.raises(SystemExit):
        bootstrap.main(["--username", "owner", "--revoke-sessions", "--reset-2fa"])
