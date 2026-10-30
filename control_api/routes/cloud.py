"""Cloud (keep-awake) settings: GET/PUT /api/cloud and POST /api/cloud/pinger/sync."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth.audit import audit
from ..auth.deps import current_user, require_fresh_2fa
from ..services import keepalive as ka

router = APIRouter(prefix="/api")


def cloud_view(request: Request) -> dict:
    r = request.app.state.runner
    c = r.cloud
    inbound = getattr(request.app.state, "last_inbound_utc", None)
    return {"settings": {k: c.get(k) for k in ka.CLOUD_FIELDS}, "schema": ka.cloud_schema(),
            "keepalive": dict(r.keepalive_state),
            "pinger": ka.pinger_status(request.app.state.settings, c),
            "last_inbound_utc": inbound.isoformat() if isinstance(inbound, datetime) else inbound,
            "sleep_timeout_minutes": ka.SLEEP_TIMEOUT_MINUTES}


@router.get("/cloud")
async def get_cloud(request: Request, user: dict = Depends(current_user)) -> dict:
    return cloud_view(request)


@router.put("/cloud")
async def put_cloud(request: Request, user: dict = Depends(require_fresh_2fa)) -> dict:
    """Partial cloud settings. Every validation problem (wrong type, out of range, unknown key, bad JSON) is
    400 with detail = [messages]; never pydantic's 422."""
    r = request.app.state.runner
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, ["body: must be a JSON object"])
    update = {k: v for k, v in body.items() if v is not None}
    try:
        clean = ka.validate_cloud(update)
    except ValueError as e:
        await audit(request, "cloud.update", ok=False, user=user, details={"update": update})
        raise HTTPException(400, e.args[0]) from e
    by = user.get("username", "user")
    changed = {k: {"from": r.cloud.get(k), "to": v} for k, v in clean.items()
               if k == "shadow_capital" and r.cloud.get(k) != v}        # before/after of the shadow capital write
    try:
        saved = await request.app.state.store.save_cloud_settings(clean, by=by)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, "store_unavailable") from e
    r.cloud.update(saved or clean)
    if {"pinger_minutes", "pinger_enabled"} & set(clean) or r.cloud.get("cronjob_job_id") is None:
        await ka.sync_pinger(r, request.app.state.cronjob_factory, by)
    await audit(request, "cloud.update", user=user, details={"update": clean, **({"changed": changed} if changed else {})})
    return cloud_view(request)


@router.post("/cloud/pinger/sync")
async def sync(request: Request, user: dict = Depends(require_fresh_2fa)) -> dict:
    """Re-creates/updates the cron-job.org job now. Returns exactly the GET /api/cloud shape (sync result in
    pinger.{synced, last_sync_utc, error}); a sync failure is reported there, not as an HTTP error."""
    r = request.app.state.runner
    res = await ka.sync_pinger(r, request.app.state.cronjob_factory, user.get("username", "user"))
    await audit(request, "cloud.pinger_sync", ok=res.get("synced") is not False, user=user)
    return cloud_view(request)
