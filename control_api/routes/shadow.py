"""Shadow mode estimate (read only): GET /api/shadow/summary and GET /api/shadow/episodes.

Both need login. Neither changes anything, so no 2FA and no audit. The summary always answers (shadow off, stand-in
installed or the store unreachable give the zeroed shape with the `enabled` / `standin` flags), never an error,
except a malformed or unknown `period` (422).

`period=<id|current|all>` (Sprint 2A-2 S4; default `current`). An episode belongs to the period in which its
hypothetical bid was placed (`join_ts` in [started_at, ended_at)), wherever it settles. A window that covers all of
history (`all`, or `current` while Period 1 is the only period) serves the runner's cached summary unchanged; any
other period is computed on request from the stored episodes (bounded, projected reads; a dedicated one-worker pool,
never the default pool Stop uses; cached per app for CACHE_TTL_S).
"""
from __future__ import annotations

import asyncio
import functools
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth.deps import current_user
from ..db.schemas import SHADOW_STATES
from ..services import botfiles
from ..services import periods as P
from ..services import shadow as shadowsvc
from ..services import shadow_model as sm
from .periods import window_for

router = APIRouter(prefix="/api/shadow")
STORE_TIMEOUT_S = 5.0
COMPUTE_TIMEOUT_S = 20.0
CACHE_TTL_S = 60.0
CACHE_MAX_KEYS = 8
EPISODES_SCAN = 500                  # newest episodes scanned when /episodes is filtered by a period
_INTERNAL = ("_id",)
_POOL: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    """Period summaries run on their own worker, never the default pool Stop's cancel uses."""
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shadow-route")
    return _POOL


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items() if k not in _INTERNAL and not str(k).startswith("\x00")}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _whole_history(p: dict | None) -> bool:
    return p is None or (p.get("started_at") is None and p.get("ended_at") is None)


def _period_meta(spec: str, p: dict | None) -> dict:
    """{spec, id, name, started_at, ended_at, current, label} (id "all" when there is no filter)."""
    if p is None:
        return {"spec": spec, "id": "all", "name": "All periods", "started_at": None, "ended_at": None,
                "current": False, "label": "All periods"}
    pub = P.public_period(p)
    return {"spec": spec, **{k: pub[k] for k in ("id", "name", "started_at", "ended_at", "current")},
            "label": str(pub["name"])}


async def _resolve(request: Request, spec: str) -> tuple[dict | None, bool]:
    """(timeline entry or None for all, ok). A store failure reading the markers gives ok=False (the summary then
    answers with the zeroed shape); a bad id raises 422 via window_for."""
    try:
        return await window_for(request, spec), True
    except HTTPException as e:
        if e.status_code == 503:
            return None, False
        raise


def _in_period(ep: dict, start: datetime | None, end: datetime | None) -> bool:
    return P.in_window(ep.get("join_ts"), start, end)


async def _period_summary(request: Request, p: dict, now: datetime, enabled: bool, standin: bool) -> dict:
    """summarize() over the episodes placed inside the period (same projections and caps as the runner's cached
    summary). Raises on store errors (the caller falls back to the zeroed shape)."""
    st = request.app.state.store
    start, end = P.window_of(p)
    floor = now - timedelta(days=shadowsvc.SHADOW_SUMMARY_DAYS)
    since = floor if start is None else max(start, floor)       # settled_ts >= join_ts, so this is a superset
    fields = shadowsvc.SUMMARY_FIELDS
    cap, cap_open = shadowsvc.SHADOW_SUMMARY_MAX_ROWS, shadowsvc.SHADOW_SUMMARY_MAX_OPEN
    settled = list(await asyncio.wait_for(st.shadow_settled(since, fields=fields, limit=cap, scored_only=True),
                                          STORE_TIMEOUT_S) or [])
    open_eps = list(await asyncio.wait_for(st.shadow_open_episodes(fields=fields, limit=cap_open),
                                           STORE_TIMEOUT_S) or [])
    passes = list(await asyncio.wait_for(st.shadow_recent_passes(300), STORE_TIMEOUT_S) or [])
    eps = [e for e in settled + open_eps if isinstance(e, dict) and _in_period(e, start, end)]
    passes = [x for x in passes if isinstance(x, dict) and P.in_window(x.get("ts"), start, end)]
    fut = asyncio.get_running_loop().run_in_executor(
        _pool(), functools.partial(sm.summarize, eps, now, passes=passes, enabled=enabled, standin=standin))
    out = await asyncio.wait_for(fut, COMPUTE_TIMEOUT_S)
    if len(settled) >= cap or len(open_eps) >= cap_open:
        out["status"]["truncated"] = True
    return out


def _cache(request: Request) -> dict:
    c = getattr(request.app.state, "shadow_period_cache", None)
    if c is None:
        c = {}
        request.app.state.shadow_period_cache = c
    return c


@router.get("/summary")
async def shadow_summary(request: Request, user: dict = Depends(current_user),
                         period: str = Query("current", max_length=64)) -> dict:
    runner = request.app.state.runner
    enabled = bool(runner.shadow_enabled())
    standin = bool(botfiles.is_standin())
    now = datetime.now(timezone.utc)
    out = sm.empty_summary(enabled=enabled, standin=standin)
    p, ok = await _resolve(request, period)
    try:
        doc = await asyncio.wait_for(request.app.state.store.get_shadow_summary(), STORE_TIMEOUT_S)
    except Exception:  # noqa: BLE001   (store down: the zeroed shape)
        doc = None
    doc = doc if isinstance(doc, dict) else None
    if ok and _whole_history(p):
        if doc is not None:
            for k in out:
                if k in doc and k not in ("enabled", "standin"):
                    out[k] = doc[k]
    elif ok:
        key = (p["id"], str(((doc or {}).get("status") or {}).get("last_pass_ts")), enabled, standin)
        cache = _cache(request)
        hit = cache.get(key)
        if hit is not None and hit[0] > time.monotonic():
            out = hit[1]
        else:
            try:
                out = await _period_summary(request, p, now, enabled, standin)
                if len(cache) >= CACHE_MAX_KEYS:
                    cache.clear()
                cache[key] = (time.monotonic() + CACHE_TTL_S, out)
            except Exception:  # noqa: BLE001   (store down or too slow: the zeroed shape)
                out = sm.empty_summary(enabled=enabled, standin=standin)
        out = dict(out)
    start, end = P.window_of(p) if ok else (None, None)
    out["arb"] = sm.arb_view((doc or {}).get("arb"), now, enabled=shadowsvc.arb_enabled(), start=start, end=end)
    out["enabled"], out["standin"] = enabled, standin
    out["period"] = _period_meta(period, p) if ok else {**_period_meta(period, None), "id": None,
                                                       "label": "Period unavailable"}
    out["period_ok"] = ok
    out["period_rule"] = "join_ts"
    return _jsonable(out)


@router.get("/episodes")
async def shadow_episodes(request: Request, user: dict = Depends(current_user),
                          limit: int = Query(50, ge=1, le=200), state: str | None = Query(None),
                          period: str = Query("all", max_length=64)) -> dict:
    """Newest first. `period` (default all) keeps only episodes placed in that period; filtered views scan the
    newest EPISODES_SCAN episodes (best effort for old periods)."""
    if state is not None and state not in SHADOW_STATES:
        raise HTTPException(400, f"state must be one of {list(SHADOW_STATES)}")
    p = await window_for(request, period)
    start, end = P.window_of(p)
    filtered = not _whole_history(p)
    try:
        eps = await asyncio.wait_for(request.app.state.store.shadow_recent(EPISODES_SCAN if filtered else limit,
                                                                           state), STORE_TIMEOUT_S)
    except ValueError as e:
        raise HTTPException(400, "invalid state") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, "store unavailable") from e
    rows = [ep for ep in (eps or []) if isinstance(ep, dict) and (not filtered or _in_period(ep, start, end))]
    return {"episodes": [_jsonable(ep) for ep in rows[:limit]], "period": _period_meta(period, p)}
