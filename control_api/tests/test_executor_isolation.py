"""Pre-deploy audit fixes: Stop's cancel never waits behind other thread work, the monitor is single-flight, arb
scans stay on their own pool (CA-2), oversized bodies are refused before parsing (B-1), and argon2 runs off the
event loop on a bounded pool so login floods can't delay Stop (B-3)."""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import pytest_asyncio

from control_api import runner as runmod
from control_api.auth import middleware as mw
from control_api.auth import security as sec
from control_api.db import MemoryStore
from control_api.runner import Runner
from control_api.services import botfiles

pytestmark = pytest.mark.asyncio


class Alerter:
    def __init__(self):
        self.sent = []

    async def send(self, title, body="", priority="default"):
        self.sent.append(title)
        return True


@pytest.fixture
def demo_keys(monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "kid")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "pk")


@pytest.fixture
def gate():
    g = threading.Event()
    yield g
    g.set()                     # never leave a parked worker behind


@pytest_asyncio.fixture
async def small_default_pool():
    """Shrink the loop's default executor to 2 workers, standing in for min(32, cpu+4) on the real instance."""
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-default")
    loop.set_default_executor(pool)
    yield loop, pool
    pool.shutdown(wait=False, cancel_futures=True)


async def _runner(settings, kalshi) -> Runner:
    r = Runner(settings, MemoryStore(), kalshi_factory=lambda m: kalshi)
    r.alerter = Alerter()
    await r.rehydrate()
    return r


def _fill(loop, n, gate) -> list:
    return [loop.run_in_executor(None, gate.wait, 10) for _ in range(n)]


async def _until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "condition not reached"
        await asyncio.sleep(0.01)


# ---- CA-2: Stop's own pool -----------------------------------------------------------------------------------------
async def test_stop_cancels_with_the_default_executor_full(settings, bot_paths, kalshi, demo_keys, gate,
                                                           small_default_pool, monkeypatch):
    loop, _ = small_default_pool
    monkeypatch.setattr(runmod, "CANCEL_TIMEOUT_S", 2.0)      # a regression fails fast instead of waiting 25 s
    r = await _runner(settings, kalshi)
    parked = _fill(loop, 2, gate)                              # every default worker is busy
    res = await r.stop(by="owner")
    assert res["errors"] == [] and res["cancelled"] == 2
    assert sorted(kalshi.cancelled) == ["o1", "o3"]
    gate.set()
    await asyncio.gather(*parked)
    await r.shutdown()


async def test_best_effort_cancel_uses_the_stop_pool(settings, bot_paths, kalshi, demo_keys, gate,
                                                     small_default_pool, monkeypatch):
    loop, _ = small_default_pool
    monkeypatch.setattr(runmod, "CANCEL_TIMEOUT_S", 2.0)
    r = await _runner(settings, kalshi)
    names = []
    real = r._cancel_resting
    monkeypatch.setattr(r, "_cancel_resting", lambda m: (names.append(threading.current_thread().name), real(m))[1])
    parked = _fill(loop, 2, gate)
    assert await r._best_effort_cancel("demo", "test") == 2
    assert names and names[0].startswith("stop")
    gate.set()
    await asyncio.gather(*parked)
    await r.shutdown()


async def test_stop_cancel_still_runs_after_the_stop_pool_is_shut_down(settings, bot_paths, kalshi, demo_keys):
    r = await _runner(settings, kalshi)
    await r.shutdown()                                         # pools gone (a Stop racing the end of shutdown)
    res = await r.stop(by="owner")
    assert res["errors"] == [] and res["cancelled"] == 2


# ---- CA-2: monitor single-flight -----------------------------------------------------------------------------------
async def test_monitor_is_single_flight_and_off_the_default_pool(settings, bot_paths, kalshi, demo_keys, gate,
                                                                 small_default_pool, monkeypatch):
    loop, _ = small_default_pool
    monkeypatch.setattr(runmod, "CANCEL_TIMEOUT_S", 2.0)
    r = await _runner(settings, kalshi)
    started = threading.Event()
    threads = []

    def slow_main(argv=None):
        threads.append(threading.current_thread().name)
        started.set()
        gate.wait(10)
        return 0
    monkeypatch.setattr(botfiles.monitor(), "main", slow_main)
    first = asyncio.ensure_future(r.run_monitor("owner"))
    await _until(started.is_set)
    taps = await asyncio.gather(*(r.run_monitor("owner") for _ in range(5)))   # repeated taps return at once
    assert all(t.get("error") == "monitor_running" for t in taps)
    assert threads == [threads[0]] and threads[0].startswith("monitor")
    parked = _fill(loop, 2, gate)
    res = await r.stop(by="owner")                             # default pool full, monitor running: Stop cancels
    assert res["errors"] == [] and res["cancelled"] == 2
    gate.set()
    await asyncio.gather(first, *parked)
    assert r._monitor_running is False
    assert "error" not in await asyncio.wait_for(r.run_monitor("owner"), 5)   # free again once it finished
    await r.shutdown()


async def test_monitor_route_answers_409_while_a_run_is_in_progress(env, demo_keys, gate, monkeypatch):
    started = threading.Event()

    def slow_main(argv=None):
        started.set()
        gate.wait(10)
        return 0
    monkeypatch.setattr(botfiles.monitor(), "main", slow_main)
    first = asyncio.ensure_future(env.runner.run_monitor("weekly"))
    await _until(started.is_set)
    r = await env.client.post("/api/monitor/run", headers=env.auth)
    assert r.status_code == 409 and r.json()["detail"] == "monitor_running"
    gate.set()
    await first
    actions = [(a.get("action"), a.get("ok")) for a in await env.store.list_audit(limit=10)]
    assert ("monitor.run", False) in actions


# ---- CA-2 / CA-3: arb scans on their own pool -----------------------------------------------------------------------
async def test_abandoned_arb_scans_never_take_default_workers(settings, bot_paths, kalshi, demo_keys, gate,
                                                              small_default_pool, monkeypatch):
    loop, _ = small_default_pool
    monkeypatch.setattr(runmod, "ARB_SCAN_TIMEOUT_S", 0.1)
    r = await _runner(settings, kalshi)
    r.arb_desired = "running"
    inside = []

    def hung_scan(*a):
        inside.append(threading.current_thread().name)
        gate.wait(10)
        return []
    monkeypatch.setattr(r, "_arb_scan_sync", hung_scan)
    for _ in range(3):                                         # three loop iterations, each abandoned at the timeout
        res = await r.arb_scan()
        assert res.get("reason") in ("error", None) or res.get("scanned") is False
    assert len(inside) == 1 and inside[0].startswith("arb")    # one hung thread, not one per scan
    assert await asyncio.wait_for(loop.run_in_executor(None, lambda: 7), 2) == 7    # default pool still free
    res = await r.stop(by="owner")
    assert res["errors"] == [] and res["cancelled"] == 2
    gate.set()
    await r.shutdown()


# ---- B-1: request body caps ----------------------------------------------------------------------------------------
async def test_oversized_login_body_is_refused_before_parsing_and_rate_limiting(env):
    big = b'{"username":"owner","password":"' + b"x" * (1024 * 1024) + b'"}'
    r = await env.client.post("/api/auth/login", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json() == {"detail": "body_too_large"}
    lim = getattr(env.app.state, "auth_ratelimiter", None)
    assert lim is None or not lim._hits                        # the limiter never saw it


async def test_chunked_oversized_body_is_cut_off(env):
    async def chunks():
        for _ in range(40):
            yield b"y" * 4096                                  # 160 KB, no content-length
    r = await env.client.post("/api/auth/login", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"] == "body_too_large", r.text


async def test_bad_content_length_is_refused():
    sent = []
    called = []

    async def app(scope, receive, send):
        called.append(1)

    async def send(m):
        sent.append(m)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    guard = mw.BodySizeLimitMiddleware(app)
    await guard({"type": "http", "path": "/api/x", "headers": [(b"content-length", b"nope")]}, receive, send)
    assert not called and sent[0]["status"] == 413


async def test_body_caps_per_path(env):
    assert mw.body_limit_for("/api/auth/login") == mw.MAX_AUTH_BODY_BYTES == 64 * 1024
    assert mw.body_limit_for("/api/config") == mw.MAX_BODY_BYTES
    mid = b'{"x":"' + b"z" * (100 * 1024) + b'"}'              # over the auth cap, under the default one
    r = await env.client.post("/api/auth/2fa", content=mid, headers={"content-type": "application/json"})
    assert r.status_code == 413
    r = await env.client.put("/api/config", content=mid, headers={**env.auth, "content-type": "application/json"})
    assert r.status_code != 413
    huge = b'{"x":"' + b"z" * (mw.MAX_BODY_BYTES + 10) + b'"}'
    r = await env.client.put("/api/config", content=huge, headers={**env.auth, "content-type": "application/json"})
    assert r.status_code == 413


async def test_normal_login_and_empty_stop_are_unaffected(env):
    r = await env.client.post("/api/auth/login", json={"username": "owner", "password": "wrong-password-123"})
    assert r.status_code == 401
    r = await env.client.post("/api/bot/stop", headers=env.auth)
    assert r.status_code == 200 and r.json()["desired"] == "stopped"


# ---- B-3: argon2 off the event loop, bounded ------------------------------------------------------------------------
@pytest.mark.slow
async def test_login_hashing_does_not_block_stop(env, monkeypatch):
    active = [0]
    peak = [0]
    lock = threading.Lock()

    def slow_verify(password_hash, password):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.6)                                        # a slow argon2 verify at Render's CPU share
        with lock:
            active[0] -= 1
        return False
    monkeypatch.setattr(sec, "verify_password", slow_verify)
    logins = [asyncio.ensure_future(env.client.post("/api/auth/login",
                                                    json={"username": u, "password": "wrong-password-123"}))
              for u in ("owner", "nobody", "owner")]
    await asyncio.sleep(0.1)                                   # first hash is running
    t0 = time.monotonic()
    r = await env.client.post("/api/bot/stop", headers=env.auth)
    took = time.monotonic() - t0
    assert r.status_code == 200 and took < 0.3, took
    codes = [x.status_code for x in await asyncio.gather(*logins)]
    assert codes == [401, 401, 401]
    assert peak[0] == 1                                        # one 64 MiB hash in memory at a time


async def test_hash_pool_is_bounded_and_named():
    assert sec.HASH_WORKERS == 1
    assert await sec.verify_password_async(None, "anything-long-enough") is False
    h = await sec.hash_password_async("a-good-long-password")
    assert await sec.verify_password_async(h, "a-good-long-password") is True
