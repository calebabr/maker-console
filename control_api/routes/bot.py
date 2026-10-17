"""Status, bot control (start/stop/run-once), mode, tick and monitor routes."""
from __future__ import annotations

import asyncio
import hmac
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from ..auth.audit import audit
from ..auth.deps import current_user, current_user_stop, require_fresh_2fa
from ..runner import RunnerDisabled, StopInProgress
from ..services import benchmarks as bench
from ..services import botfiles
from ..services import strategies as strat

router = APIRouter(prefix="/api")


def _runner(request: Request):
    return request.app.state.runner


async def _safe(coro, default=None, timeout: float = 5.0):
    try:
        return await asyncio.wait_for(coro, timeout)
    except Exception:  # noqa: BLE001
        return default


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@router.get("/status")
async def status(request: Request, user: dict = Depends(current_user)) -> dict:
    r = _runner(request)
    store = request.app.state.store
    out = r.status()
    now = datetime.now(timezone.utc)
    hb = await _safe(store.last_heartbeat())
    out["heartbeat_age_s"] = round((now - hb["ts"]).total_seconds()) if hb and hb.get("ts") else None
    snaps = await _safe(store.list_account_snapshots(limit=1), default=[])
    out["account"] = ({"cash": _num(snaps[0].get("cash")), "exposure": _num(snaps[0].get("exposure")),
                       "headroom": _num(snaps[0].get("headroom"))} if snaps else None)
    cfg = r.config or {}
    out["limits"] = {"run_budget": cfg.get("run_budget"), "max_open_frac": cfg.get("max_open_frac")}
    # realised maker P&L (non-taker fills x settlement results, by settlement time); null until data exists
    out["pnl"] = await r.pnl(taker=False)
    # fill-quality telemetry vs the optional private market baseline (services/benchmarks.py); n=0 until scored
    out["fill_quality"] = r.fill_quality()          # S-23: cached once per pass; no store read here
    # queue-position telemetry (overall only; per series on /markets); cached once per pass, null if unknown
    out["queue_stats"] = r.queue_stats()
    out["expected_roi"] = bench.default_roi()        # private benchmark file only; null without it
    out["strategy_installed"] = not botfiles.is_standin()   # false = public stand-in: LIVE is refused
    # UX sprint item 25 (additive): today's realised P&L by source, the last 12 passes, timing in contract names
    out["pnl_today"] = await r.pnl_today() or dict.fromkeys(strat.bucket_ids() + ["bot", "total"])   # 60 s cache
    out["recent_passes"] = await r.recent_passes()
    out["last_run_at"] = out.get("last_run_utc")
    trading = r.desired == "running" and not out.get("kill_switch") and r.settings.runner_enabled
    out["next_run_at"] = out.get("next_due_utc") if trading else None
    return out


@router.post("/bot/stop")
async def stop(request: Request, user: dict = Depends(current_user_stop)) -> dict:
    """Stops the maker AND arbitrage. No 2FA; the auth dependency fails open on store errors, and the runner's
    flags + kill switches never touch the store, Kalshi or the network. Always 200 (errors are reported)."""
    res = await _runner(request).stop(by=user.get("username", "user"))
    await audit(request, "bot.stop", ok=not res["errors"], user=user, details=res)
    return res


@router.post("/bot/start")
async def start(request: Request, user: dict = Depends(require_fresh_2fa)) -> dict:
    try:
        res = await _runner(request).start_trading(by=user.get("username", "user"))
    except (StopInProgress, RunnerDisabled, PermissionError) as e:
        # Stop always wins a race; RUNNER_ENABLED=false is final; LIVE on the stand-in: strategy_not_installed
        await audit(request, "bot.start", ok=False, user=user, details={"error": str(e)})
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001  (fail-safe: no store write -> no start)
        await audit(request, "bot.start", ok=False, user=user, details={"error": type(e).__name__})
        raise HTTPException(503, "store_unavailable") from e
    await audit(request, "bot.start", user=user)
    return res


@router.post("/bot/run-once")
async def run_once(request: Request, user: dict = Depends(current_user)) -> dict:
    res = await _runner(request).run_once(by=user.get("username", "user"))
    await audit(request, "bot.run_once", ok=res["ran"], user=user, details=res)
    if res.get("reason") == "too_soon":           # S-22: a pass started less than 60 s ago
        raise HTTPException(409, "too_soon")
    return res


class ModeIn(BaseModel):
    mode: Literal["demo", "live"]
    confirm: str | None = None


@router.put("/mode")
async def set_mode(body: ModeIn, request: Request, user: dict = Depends(require_fresh_2fa)) -> dict:
    r = _runner(request)
    if body.mode == "live" and body.confirm != "LIVE":
        await audit(request, "mode.set", ok=False, user=user, details={"mode": body.mode, "error": "confirm"})
        raise HTTPException(400, "type LIVE to confirm")
    try:
        res = await r.set_mode(body.mode, by=user.get("username", "user"))
    except PermissionError as e:
        why = str(e) if str(e) in ("strategy_not_installed", "live_not_allowed") else "live_not_allowed"
        await audit(request, "mode.set", ok=False, user=user, details={"mode": body.mode, "error": why})
        raise HTTPException(409, why) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, "store_unavailable") from e
    await audit(request, "mode.set", user=user, details={"mode": body.mode})
    return res


@router.post("/tick")
async def tick(request: Request, x_tick_token: str | None = Header(default=None)) -> dict:
    expected = request.app.state.settings.tick_token
    if not expected:
        raise HTTPException(403, "tick_disabled")
    if not x_tick_token or not hmac.compare_digest(x_tick_token.encode(), expected.encode()):
        raise HTTPException(403, "bad_tick_token")
    return await _runner(request).tick("external")


@router.post("/monitor/run")
async def monitor_run(request: Request, user: dict = Depends(current_user)) -> dict:
    """S-6, intentional exception: no fresh 2FA. This is the read-only weekly health check run on demand. Its
    only possible config change is maker_monitor --apply PAUSING (removing) series that are confidently losing,
    or creating the kill switch if all would be paused, so it can only reduce risk, never add any. The result
    is validated (S-12), versioned by="monitor" in the store, alerted via ntfy and audited here."""
    res = await _runner(request).run_monitor(by=user.get("username", "user"))
    if res.get("error") == "monitor_running":     # CA-2: single-flight; a second tap does not start another run
        await audit(request, "monitor.run", ok=False, user=user, details="monitor_running")
        raise HTTPException(409, "monitor_running")
    await audit(request, "monitor.run", ok="error" not in res, user=user)
    return {"summary": res.get("summary")}
