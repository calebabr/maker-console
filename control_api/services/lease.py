"""Single-leader lease + fencing (release process R1; docs/RELEASE_CONTRACT.md sections 1-2).

Every API instance runs a ``LeaseManager``. Only the instance that holds the lease (the *leader*) may run maker
passes, arbitrage executions or non-Stop cancels; every other instance is a *standby* that serves the API and the
app but never trades. Stop never consults any of this.

Fencing (``fence_ok``) is purely local and fail-safe: the instance must be leader, its last SUCCESSFUL renew must be
younger than the TTL by the local MONOTONIC clock (wall-clock jumps cannot extend it), and the generation must still
be the one that renew returned. A store that times out or errors never extends the lease, so a partitioned leader
fences itself off before the lease can expire in the store and be taken by another instance.

This module only talks to the store (with timeouts). It never calls Kalshi.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

log = logging.getLogger("control_api.lease")

STORE_TIMEOUT_S = 5.0
LEADER = "leader"
STANDBY = "standby"


def new_instance_id() -> str:
    """Random per process. The hostname is only for logs (never returned by the API)."""
    return uuid.uuid4().hex


def hostname() -> str:
    try:
        return socket.gethostname()[:64]
    except Exception:  # noqa: BLE001
        return "unknown"


class LeaseManager:
    def __init__(self, store: Any, *, instance_id: str, version: str, ttl_s: float, renew_s: float,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], datetime] | None = None,
                 store_timeout_s: float = STORE_TIMEOUT_S):
        self.store = store
        self.instance_id = instance_id
        self.version = version
        self.ttl_s = float(ttl_s)
        self.renew_s = float(renew_s)
        self._clock = clock
        self._wall = wall           # tests inject the store's `now`; production passes nothing to the store
        self.store_timeout_s = store_timeout_s
        self.role = STANDBY
        self.generation: int | None = None
        self.expires_at: datetime | None = None
        self.holder: str | None = None          # last holder seen in the store (may be another instance)
        self.last_renew_mono: float | None = None
        self.last_error: str | None = None
        self.transitions = 0

    # ---- helpers
    def _now_kw(self) -> dict:
        return {"now": self._wall()} if self._wall is not None else {}

    async def _call(self, coro):
        return await asyncio.wait_for(coro, self.store_timeout_s)

    def _become_standby(self, why: str) -> None:
        if self.role == LEADER:
            self.transitions += 1
            log.warning("lease: leadership lost (%s); now standby", why)
        self.role = STANDBY
        self.last_renew_mono = None

    # ---- the three operations
    async def try_acquire(self) -> bool:
        """acquire_lease (atomic in the store). True if this instance now holds it. Never raises."""
        try:
            res = await self._call(self.store.acquire_lease(self.instance_id, self.version, self.ttl_s,
                                                            **self._now_kw()))
        except Exception as e:  # noqa: BLE001
            self.last_error = f"acquire: {type(e).__name__}"
            self._become_standby(self.last_error)
            return False
        self.last_error = None
        self.holder = str(res.get("holder") or "") or None
        self.expires_at = res.get("expires_at")
        if res.get("acquired") and res.get("holder") == self.instance_id:
            gen = int(res.get("generation") or 0)
            if self.role != LEADER:
                self.transitions += 1
                log.warning("lease: acquired (generation %s); now leader", gen)
            self.role, self.generation, self.last_renew_mono = LEADER, gen, self._clock()
            return True
        self._become_standby("held by another instance")
        return False

    async def renew(self) -> bool:
        """renew_lease for our generation. False (and standby) when the store says we lost it. A store error keeps
        the role but does NOT refresh last_renew_mono, so the fence lapses on its own after the TTL."""
        if self.role != LEADER or self.generation is None:
            return False
        started = self._clock()
        sent_wall = self._wall() if self._wall is not None else datetime.now(timezone.utc)
        try:
            ok = await self._call(self.store.renew_lease(self.instance_id, self.generation, self.ttl_s,
                                                         **self._now_kw()))
        except Exception as e:  # noqa: BLE001
            self.last_error = f"renew: {type(e).__name__}"
            log.warning("lease renew failed: %s", type(e).__name__)
            if self.last_renew_mono is None or self._clock() - self.last_renew_mono >= self.ttl_s:
                self._become_standby("renew failing past the TTL")
            return False
        if not ok:
            self.last_error = "renew: lost"
            self._become_standby("renew refused (taken or expired)")
            return False
        self.last_error = None
        # the renew may have been slow: count the lease from when we SENT it (conservative)
        self.last_renew_mono = started
        # the store moved expires_at to (its now + TTL) but renew_lease returns only a bool: mirror it from the send
        # time (never later than the stored value), so /status shows the renewed expiry, not the acquire-time one
        exp = sent_wall + timedelta(seconds=self.ttl_s)
        self.expires_at = exp.replace(microsecond=exp.microsecond // 1000 * 1000)   # stores keep milliseconds
        self.holder = self.instance_id
        return True

    async def step(self) -> bool:
        """One loop iteration: renew if leader, else try to acquire. Returns whether we are leader."""
        if self.role == LEADER:
            if await self.renew():
                return True
            if self.role == LEADER:          # store error inside the TTL: still leader, fence decides
                return True
        return await self.try_acquire()

    async def release(self, *, handover: dict | None = None) -> bool:
        """Expire our lease now (planned shutdown), storing `handover`. Never raises."""
        if self.generation is None or self.role != LEADER:
            return False
        try:
            ok = bool(await self._call(self.store.release_lease(self.instance_id, self.generation,
                                                                handover=handover, **self._now_kw())))
        except Exception as e:  # noqa: BLE001
            self.last_error = f"release: {type(e).__name__}"
            log.warning("lease release failed: %s", type(e).__name__)
            ok = False
        if ok:
            self.expires_at = self._wall() if self._wall is not None else datetime.now(timezone.utc)
        self._become_standby("released")
        return ok

    async def read(self) -> dict | None:
        """The stored lease doc (holder, version, generation, expires_at, handover) or None. Raises on store
        errors (callers decide)."""
        return await self._call(self.store.get_lease(**self._now_kw()))

    # ---- fencing
    def lease_age_s(self) -> float | None:
        if self.last_renew_mono is None:
            return None
        return self._clock() - self.last_renew_mono

    def fence_ok(self, *, min_remaining_s: float = 0.0) -> bool:
        """Leader, renewed within the TTL (local monotonic clock) with at least `min_remaining_s` left, and the
        generation is the one renew confirmed. Pure local check: safe to call from any thread."""
        if self.role != LEADER or self.generation is None:
            return False
        age = self.lease_age_s()
        if age is None or age < 0:
            return False
        return age + max(0.0, min_remaining_s) < self.ttl_s

    def snapshot(self) -> dict:
        exp = self.expires_at
        if isinstance(exp, datetime) and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return {"role": self.role, "lease_generation": self.generation,
                "lease_expires_at": exp.isoformat() if isinstance(exp, datetime) else None,
                "lease_holder": self.holder}
