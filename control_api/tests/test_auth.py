import time

import jwt
import pyotp
import pytest

from control_api.auth import security as sec
from control_api.tests.conftest import ORIGIN, PASSWORD

pytestmark = pytest.mark.asyncio


async def _login(env, password=PASSWORD):
    return await env.client.post("/api/auth/login", json={"username": "owner", "password": password})


async def test_login_then_2fa_gives_working_access_token(env):
    r = await _login(env)
    assert r.status_code == 200
    mfa = r.json()["mfa_token"]
    r2 = await env.client.post("/api/auth/2fa", json={"mfa_token": mfa, "code": pyotp.TOTP(env.secret).now()})
    assert r2.status_code == 200
    tok = r2.json()["access_token"]
    me = await env.client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.json() == {"username": "owner"}


async def test_mfa_token_is_single_use(env):
    mfa = (await _login(env)).json()["mfa_token"]
    code = pyotp.TOTP(env.secret).now()
    assert (await env.client.post("/api/auth/2fa", json={"mfa_token": mfa, "code": code})).status_code == 200
    await env.store.save_user({"username": "owner", "last_totp_step": None})
    assert (await env.client.post("/api/auth/2fa", json={"mfa_token": mfa, "code": code})).status_code == 401


async def test_unknown_user_and_wrong_password_look_the_same(env):
    a = await _login(env, "wrong password here!!")
    b = await env.client.post("/api/auth/login", json={"username": "nobody", "password": PASSWORD})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


async def test_reused_totp_code_rejected(env):
    h = await env.h2fa()
    assert (await env.client.put("/api/mode", headers=h, json={"mode": "demo"})).status_code == 200
    r = await env.client.put("/api/mode", headers=h, json={"mode": "demo"})
    assert r.status_code == 403 and r.json()["detail"] == "2fa_invalid"


async def test_lockout_after_five_bad_codes_is_429(env):
    mfa = (await _login(env)).json()["mfa_token"]
    codes = []
    for _ in range(5):
        codes.append((await env.client.post("/api/auth/2fa", json={"mfa_token": mfa, "code": "000000"})).status_code)
    assert codes[:4] == [401] * 4 and codes[4] == 429
    r = await _login(env)
    assert r.status_code == 429 and "retry-after" in r.headers
    # even a correct code is refused while locked
    r = await env.client.post("/api/auth/2fa", json={"mfa_token": mfa, "code": pyotp.TOTP(env.secret).now()})
    assert r.status_code == 429


async def test_bad_passwords_do_not_lock_out_the_owner(env):
    """SECURITY_REVIEW S-2: someone who knows the username must not be able to lock the owner out
    (and so out of Stop). Wrong passwords are only throttled per IP; the account lock counts TOTP failures."""
    for i in range(6):
        r = await env.client.post("/api/auth/login", json={"username": "owner", "password": "bad password 123"},
                                  headers={"X-Forwarded-For": f"203.0.113.{i}"})
        assert r.status_code == 401
    r = await _login(env)
    assert r.status_code == 200 and "mfa_token" in r.json()


@pytest.mark.slow
async def test_rate_limit_ignores_spoofed_forwarded_for(env):
    """SECURITY_REVIEW S-1: the client controls the first X-Forwarded-For hops; only the last one
    (appended by Render's proxy) identifies the caller, so rotating fake hops can't dodge the limit."""
    statuses = []
    for i in range(12):
        r = await env.client.post("/api/auth/login", json={"username": "x", "password": "y"},
                                  headers={"X-Forwarded-For": f"10.0.0.{i}, 198.51.100.7"})
        statuses.append(r.status_code)
    assert 429 in statuses


@pytest.mark.slow
async def test_per_ip_rate_limit_on_login(env):
    last = None
    for _ in range(11):
        last = await env.client.post("/api/auth/login", json={"username": "x", "password": "y"},
                                     headers={"X-Forwarded-For": "9.9.9.9"})
    assert last.status_code == 429


async def test_no_token_401(env):
    assert (await env.client.get("/api/status")).status_code == 401


async def test_mfa_typ_token_rejected_as_access(env):
    tok = sec.create_token(env.settings, "owner", sec.TYP_MFA, 300)[0]
    r = await env.client.post("/api/bot/stop", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


async def test_expired_token_401(env):
    now = int(time.time())
    tok = jwt.encode({"sub": "owner", "typ": "access", "jti": "j1", "iat": now - 4000, "exp": now - 3000},
                     env.settings.jwt_secret, algorithm="HS256")
    assert (await env.client.get("/api/status", headers={"Authorization": f"Bearer {tok}"})).status_code == 401


async def test_wrong_signature_401(env):
    now = int(time.time())
    tok = jwt.encode({"sub": "owner", "typ": "access", "jti": "j1", "iat": now, "exp": now + 100},
                     "some-other-secret-value-of-sufficient-length", algorithm="HS256")
    assert (await env.client.get("/api/status", headers={"Authorization": f"Bearer {tok}"})).status_code == 401


async def test_logout_revokes_token(env):
    assert (await env.client.post("/api/auth/logout", headers=env.auth)).status_code == 200
    assert (await env.client.get("/api/status", headers=env.auth)).status_code == 401


async def test_revoked_in_store_401(env):
    claims = sec.decode_token(env.settings, env.token, sec.TYP_ACCESS)
    await env.store.revoke_jti(claims["jti"], sec.exp_datetime(claims))
    assert (await env.client.get("/api/status", headers=env.auth)).status_code == 401


async def test_cors_foreign_origin_403_and_allowed_origin_ok(env):
    r = await env.client.get("/healthz", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403
    r = await env.client.get("/healthz", headers={"Origin": ORIGIN})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == ORIGIN
    assert (await env.client.get("/healthz")).status_code == 200   # no Origin (cron) passes


async def test_cors_exposes_csv_and_retry_headers_to_allowed_origin_only(env):
    """The cross-origin app must be able to read X-Truncated / Content-Disposition (trades.csv) and
    Retry-After (429s); a foreign origin gets no CORS headers at all (403 from the origin guard)."""
    r = await env.client.get("/api/analytics/trades.csv?days=7", headers={**env.auth, "Origin": ORIGIN})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == ORIGIN
    exposed = {h.strip().lower() for h in r.headers["access-control-expose-headers"].split(",")}
    assert exposed == {"x-truncated", "content-disposition", "retry-after"}
    assert "access-control-allow-credentials" not in r.headers
    r = await env.client.get("/api/analytics/trades.csv?days=7",
                             headers={**env.auth, "Origin": "https://evil.example.com"})
    assert r.status_code == 403
    assert not [k for k in r.headers if k.lower().startswith("access-control-")]


async def test_security_headers_present(env):
    r = await env.client.get("/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_no_secrets_in_logs_or_responses(env):
    mfa = (await _login(env)).json()["mfa_token"]
    code = pyotp.TOTP(env.secret).now()
    r2 = await env.client.post("/api/auth/2fa", json={"mfa_token": mfa, "code": code})
    logs = await env.client.get("/api/logs", headers=env.auth)
    audit = await env.client.get("/api/audit", headers=env.auth)
    blob = logs.text + audit.text
    for secret in (PASSWORD, env.secret, r2.json()["access_token"], mfa, env.settings.jwt_secret,
                   env.settings.tick_token, env.settings.cronjob_api_key):
        assert secret not in blob
    assert "password_hash" not in blob and "totp_secret" not in blob
