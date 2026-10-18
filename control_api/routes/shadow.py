"""Shadow mode estimate (read only): GET /api/shadow/summary and GET /api/shadow/episodes.

Both need login. Neither changes anything, so no 2FA and no audit. The summary always answers (shadow off, stand-in
installed or the store unreachable give the zeroed shape with the `enabled` / `standin` flags), never an error.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth.deps import current_user
from ..db.schemas import SHADOW_STATES
from ..services import botfiles
from ..services import shadow_model as sm

router = APIRouter(prefix="/api/shadow")
STORE_TIMEOUT_S = 5.0
_INTERNAL = ("_id",)


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items() if k not in _INTERNAL and not str(k).startswith("\x00")}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


@router.get("/summary")
async def shadow_summary(request: Request, user: dict = Depends(current_user)) -> dict:
    runner = request.app.state.runner
    enabled = bool(runner.shadow_enabled())
    standin = bool(botfiles.is_standin())
    out = sm.empty_summary(enabled=enabled, standin=standin)
    try:
        doc = await asyncio.wait_for(request.app.state.store.get_shadow_summary(), STORE_TIMEOUT_S)
    except Exception:  # noqa: BLE001   (store down: the zeroed shape)
        doc = None
    if isinstance(doc, dict):
        for k in out:
            if k in doc and k not in ("enabled", "standin"):
                out[k] = doc[k]
    out["enabled"], out["standin"] = enabled, standin
    return _jsonable(out)


@router.get("/episodes")
async def shadow_episodes(request: Request, user: dict = Depends(current_user),
                          limit: int = Query(50, ge=1, le=200), state: str | None = Query(None)) -> dict:
    if state is not None and state not in SHADOW_STATES:
        raise HTTPException(400, f"state must be one of {list(SHADOW_STATES)}")
    try:
        eps = await asyncio.wait_for(request.app.state.store.shadow_recent(limit, state), STORE_TIMEOUT_S)
    except ValueError as e:
        raise HTTPException(400, "invalid state") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, "store unavailable") from e
    return {"episodes": [_jsonable(ep) for ep in (eps or []) if isinstance(ep, dict)]}
