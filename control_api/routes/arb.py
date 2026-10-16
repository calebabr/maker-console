"""Bracket arbitrage: GET/PUT /api/arb and GET /api/arb/opportunities.

Log-only by default. Turning arb on, turning arb live on, and changing arb settings all need fresh 2FA (PUT).
Arb live also needs typed confirm "LIVE" and the same env gates as maker live (prod key + KALSHI_LIVE_CONFIRM).
Turning things OFF through PUT also needs 2FA (one endpoint); the 2FA-free way to stop arbitrage is Stop.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth.audit import audit
from ..auth.deps import current_user, require_fresh_2fa
from ..runner import RunnerDisabled, StopInProgress
from ..services import arb as arbsvc

router = APIRouter(prefix="/api")
CONTROL_KEYS = ("enabled", "live", "confirm", "ack_legging")


def _iso(doc: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in doc.items()}


async def arb_pnl(request: Request) -> dict | None:
    """Arb P&L = sum of the guaranteed profit of FILLED arb sets, by time executed (d7 / d30)."""
    store = request.app.state.store
    now = datetime.now(timezone.utc)
    try:
        docs = await store.list_arb_opportunities(since=now - timedelta(days=30), limit=100000, executed_only=True)
    except Exception:  # noqa: BLE001
        return None
    filled = [d for d in docs if str(d.get("executed", "")).startswith("FILLED") and isinstance(d.get("ts"), datetime)]
    if not filled:
        return None
    d7 = sum(float(d.get("profit") or 0) for d in filled if d["ts"] >= now - timedelta(days=7))
    d30 = sum(float(d.get("profit") or 0) for d in filled)
    return {"d7": round(d7, 2), "d30": round(d30, 2), "sets": len(filled)}


async def arb_view(request: Request) -> dict:
    r = request.app.state.runner
    s = request.app.state.settings
    cfg = r.arb_settings()
    return {"settings": {"enabled": r.arb_desired == "running", "live": r.arb_mode == "live", **cfg},
            "schema": arbsvc.schema(s), "ceilings": arbsvc.ceilings(s), "status": r.arb_status(),
            "pnl": await arb_pnl(request), "expectation": arbsvc.EXPECTATION}


@router.get("/arb")
async def get_arb(request: Request, user: dict = Depends(current_user)) -> dict:
    return await arb_view(request)


@router.put("/arb")
async def put_arb(request: Request, user: dict = Depends(require_fresh_2fa)) -> dict:
    r = request.app.state.runner
    by = user.get("username", "user")
    try:
        body: Any = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, ["body: must be a JSON object"])
    errs: list[str] = []
    for k in ("enabled", "live", "ack_legging"):
        if k in body and body[k] is not None and not isinstance(body[k], bool):
            errs.append(f"{k}: must be true/false")
    if "confirm" in body and body["confirm"] is not None and not isinstance(body["confirm"], str):
        errs.append("confirm: must be a string")
    update = {k: v for k, v in body.items() if k not in CONTROL_KEYS}
    new_cfg = None
    if update:
        try:
            new_cfg = arbsvc.validate(r.arb_settings(), update, request.app.state.settings)
        except ValueError as e:
            errs.extend(e.args[0])
    if errs:
        await audit(request, "arb.update", ok=False, user=user, details={"errors": errs})
        raise HTTPException(400, errs)
    enabled, live = body.get("enabled"), body.get("live")
    if live is True and body.get("confirm") != "LIVE":
        await audit(request, "arb.live", ok=False, user=user, details={"error": "confirm"})
        raise HTTPException(400, ["type LIVE to confirm"])
    if live is True and not r.live_allowed():
        why = r.live_block_reason() or "live_not_allowed"     # strategy_not_installed on the public stand-in
        await audit(request, "arb.live", ok=False, user=user, details={"error": why})
        raise HTTPException(409, why)

    if new_cfg is not None and new_cfg != r.arb_settings():
        before = dict(r.config or {})
        after = {**before, "arb": new_cfg}
        try:
            version = await request.app.state.store.save_config(after, before=before, by=by)
        except Exception as e:  # noqa: BLE001
            await audit(request, "arb.update", ok=False, user=user, details={"error": "store"})
            raise HTTPException(503, "store_unavailable") from e
        await r.apply_config(after)          # atomic file write, keeps _help keys and cells
        await audit(request, "arb.update", user=user, details={"update": update, "version": version})

    if enabled is not None or live is not None:
        try:
            res = await r.set_arb(enabled=enabled, live=live, by=by, ack_legging=bool(body.get("ack_legging")))
        except PermissionError as e:
            await audit(request, "arb.toggle", ok=False, user=user, details={"error": str(e)})
            raise HTTPException(409, str(e)) from e
        except (StopInProgress, RunnerDisabled) as e:
            await audit(request, "arb.toggle", ok=False, user=user, details={"error": str(e)})
            raise HTTPException(409, str(e)) from e
        except Exception as e:  # noqa: BLE001   (fail-safe: no store write -> no change)
            await audit(request, "arb.toggle", ok=False, user=user, details={"error": type(e).__name__})
            raise HTTPException(503, "store_unavailable") from e
        await audit(request, "arb.live" if live is not None else "arb.toggle", user=user,
                    details={"enabled": enabled, "live": live, **res})
    return await arb_view(request)


@router.get("/arb/opportunities")
async def opportunities(request: Request, limit: int = Query(100, ge=1, le=1000),
                        executed_only: bool = False, user: dict = Depends(current_user)) -> dict:
    r = request.app.state.runner
    try:
        items = await request.app.state.store.list_arb_opportunities(limit=limit, executed_only=executed_only)
    except Exception:  # noqa: BLE001   store down: serve this process's recent sightings
        items = [d for d in r.arb_recent if not executed_only or d.get("executed") != "log-only"][:limit]
    return {"items": [_iso(i) for i in items]}
