"""R1: single-leader lease + fencing (services/lease.py) against MemoryStore, with injected clocks (no sleeps)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from control_api.db import MemoryStore
from control_api.services.lease import LEADER, STANDBY, LeaseManager

pytestmark = pytest.mark.asyncio
T0 = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


class Clocks:
    """One fake monotonic clock + one fake wall clock (the store's `now`), moved together or apart."""
    def __init__(self):
        self.mono = 1000.0
        self.wall = T0

    def advance(self, s: float, *, wall: bool = True):
        self.mono += s
        if wall:
            self.wall += timedelta(seconds=s)


def mgr(store, clocks, name, ttl=60, renew=20):
    return LeaseManager(store, instance_id=name, version="v-test", ttl_s=ttl, renew_s=renew,
                        clock=lambda: clocks.mono, wall=lambda: clocks.wall)


async def test_two_instances_acquire_at_once_exactly_one_wins():
    store, c = MemoryStore(), Clocks()
    a, b = mgr(store, c, "a"), mgr(store, c, "b")
    res = await asyncio.gather(a.try_acquire(), b.try_acquire())
    assert sorted(res) == [False, True]
    assert sorted([a.role, b.role]) == [LEADER, STANDBY]
    winner = a if a.role == LEADER else b
    assert winner.fence_ok() and not (b if winner is a else a).fence_ok()


async def test_renew_and_stale_generation_and_takeover_after_ttl():
    store, c = MemoryStore(), Clocks()
    a, b = mgr(store, c, "a"), mgr(store, c, "b")
    assert await a.try_acquire() and a.generation == 1
    c.advance(20)
    assert await a.renew() and a.fence_ok()
    assert not await b.try_acquire()                         # held and not expired
    assert not await store.renew_lease("a", 99, 60, now=c.wall)     # stale generation cannot renew
    c.advance(61)                                            # a stops renewing (killed): lease expires
    assert not a.fence_ok()                                  # local fence lapsed on its own
    assert await b.try_acquire() and b.generation == 2       # takeover with a higher generation
    assert not await a.renew() and a.role == STANDBY         # the old holder learns it lost it
    assert not a.fence_ok() and b.fence_ok()


async def test_killed_leader_no_overlap_window():
    """At every instant at most one fence is open while the standby polls every renew interval."""
    store, c = MemoryStore(), Clocks()
    a, b = mgr(store, c, "a"), mgr(store, c, "b")
    await a.try_acquire()
    for _ in range(12):                                      # a is dead (never renews); b polls each 10 s
        c.advance(10)
        await b.step()
        assert not (a.fence_ok() and b.fence_ok())
    assert b.role == LEADER


async def test_store_errors_never_extend_the_lease():
    store, c = MemoryStore(), Clocks()
    a = mgr(store, c, "a")
    await a.try_acquire()

    async def boom(*a_, **k):
        raise RuntimeError("atlas down")
    store.renew_lease = boom
    c.advance(30)
    assert not await a.renew()
    assert a.role == LEADER and a.fence_ok() and not a.fence_ok(min_remaining_s=31)
    c.advance(31)                                            # 61 s since the last good renew
    assert not a.fence_ok()
    assert not await a.renew() and a.role == STANDBY


async def test_store_unreachable_means_standby():
    class Broken:
        def __getattr__(self, n):
            async def f(*a, **k):
                raise RuntimeError("down")
            return f
    c = Clocks()
    a = mgr(Broken(), c, "a")
    assert not await a.step() and a.role == STANDBY and not a.fence_ok()
    assert a.last_error and "down" not in a.last_error           # type name only


async def test_hung_store_times_out():
    class Hung:
        async def acquire_lease(self, *a, **k):
            await asyncio.sleep(10)
    c = Clocks()
    a = LeaseManager(Hung(), instance_id="a", version="v", ttl_s=60, renew_s=20, clock=lambda: c.mono,
                     store_timeout_s=0.05)
    assert not await a.try_acquire() and a.role == STANDBY


@pytest.mark.parametrize("jump", [-3600, 3600])
async def test_wall_clock_jump_never_double_leader(jump):
    """A wall-clock jump on the leader's side changes the store's view, never the local monotonic fence."""
    store, c = MemoryStore(), Clocks()
    a, b = mgr(store, c, "a"), mgr(store, c, "b")
    await a.try_acquire()
    c.wall += timedelta(seconds=jump)                        # jump without monotonic time passing
    await b.step()
    if b.role == LEADER:                                     # forward jump: the store saw the lease expire
        assert not await a.renew() and a.role == STANDBY     # a's next renew fails: it fences itself off
    for _ in range(6):
        c.advance(10)
        await a.step()
        await b.step()
        assert not (a.fence_ok() and b.fence_ok())


async def test_release_expires_now_and_stores_the_handover():
    store, c = MemoryStore(), Clocks()
    a, b = mgr(store, c, "a"), mgr(store, c, "b")
    await a.try_acquire()
    assert await a.release(handover={"v": 1, "x": "y"})
    assert a.role == STANDBY and not a.fence_ok()
    doc = await b.read()
    assert doc["handover"] == {"v": 1, "x": "y"}
    assert await b.try_acquire() and b.generation == 2


async def test_snapshot_shape():
    store, c = MemoryStore(), Clocks()
    a = mgr(store, c, "a")
    assert a.snapshot() == {"role": "standby", "lease_generation": None, "lease_expires_at": None,
                            "lease_holder": None}
    await a.try_acquire()
    s = a.snapshot()
    assert s["role"] == "leader" and s["lease_generation"] == 1 and s["lease_holder"] == "a"
    assert s["lease_expires_at"].startswith("2026-10-16T12:01:00")
