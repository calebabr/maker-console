"""Keep-awake: self-ping loop, cloud-settings validation and the cron-job.org job sync."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("control_api.keepalive")

SLEEP_TIMEOUT_MINUTES = 15
SHADOW_CAPITAL_MIN, SHADOW_CAPITAL_MAX, SHADOW_CAPITAL_DEFAULT = 50, 1000, 150   # dollars; the API ceilings for writes
CLOUD_FIELDS = {
    "keepalive_minutes": {"type": "integer", "unit": "minutes", "min": 1, "max": 14,
                          "help": "Self-ping interval (GET PUBLIC_URL/healthz) to keep the service awake."},
    "pinger_minutes": {"type": "integer", "unit": "minutes", "min": 1, "max": 14,
                       "help": "cron-job.org POST /api/tick interval (backup pinger)."},
    "pinger_enabled": {"type": "boolean", "unit": None, "min": None, "max": None,
                       "help": "Enable the external cron-job.org job."},
    "silent_alert_minutes": {"type": "integer", "unit": "minutes", "min": 15, "max": 240,
                             "help": "Alert if no heartbeat for this long."},
    "shadow_enabled": {"type": "boolean", "unit": None, "min": None, "max": None,
                       "help": "Shadow mode: record what the strategy would bid (read only, places no orders)."},
    "shadow_capital": {"type": "number", "unit": "dollars", "min": SHADOW_CAPITAL_MIN, "max": SHADOW_CAPITAL_MAX,
                       "default": SHADOW_CAPITAL_DEFAULT,
                       "help": "Capital the shadow estimate assumes. Shadow dollar P&L and its open-position "
                               "ceiling scale with it; rates and ROI do not. It is a research sampling tool, not a "
                               "mirror of the real balance."},
    "shadow_intervals": {"type": "int_list", "unit": "minutes", "allowed": [5, 7, 10], "max_items": 3,
                         "min": None, "max": None,
                         "help": "Shadow cadences to compare (5, 7 and/or 10 minutes); needs SHADOW_VARIANTS."},
}


def cloud_schema() -> dict:
    return {k: dict(v, editable=True) for k, v in CLOUD_FIELDS.items()}


def validate_cloud(update: dict) -> dict:
    errs, out = [], {}
    for k, v in update.items():
        if v is None:
            continue
        spec = CLOUD_FIELDS.get(k)
        if spec is None:
            errs.append(f"{k}: not a cloud setting")
            continue
        if spec["type"] == "boolean":
            if not isinstance(v, bool):
                errs.append(f"{k}: must be true/false")
            else:
                out[k] = v
            continue
        if spec["type"] == "number":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
                errs.append(f"{k}: must be a number")
            elif not spec["min"] <= v <= spec["max"]:
                errs.append(f"{k}: {spec['unit']}, must be {spec['min']}-{spec['max']}")
            else:
                out[k] = v
            continue
        if spec["type"] == "int_list":
            ok = (isinstance(v, list) and 1 <= len(v) <= spec["max_items"]
                  and all(isinstance(x, int) and not isinstance(x, bool) and x in spec["allowed"] for x in v)
                  and len(set(v)) == len(v))
            if ok:
                out[k] = sorted(v)
            else:
                errs.append(f"{k}: up to {spec['max_items']} distinct values from {spec['allowed']}")
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
            errs.append(f"{k}: must be a whole number")
            continue
        if not spec["min"] <= int(v) <= spec["max"]:
            errs.append(f"{k}: {spec['unit']}, must be {spec['min']}-{spec['max']}")
            continue
        out[k] = int(v)
    if errs:
        raise ValueError(errs)
    return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def ping_once(runner: Any, transport: Any = None) -> dict:
    url = runner.settings.public_url
    st = runner.keepalive_state
    if not url:
        st.update(ok=None, error="PUBLIC_URL / RENDER_EXTERNAL_URL not set")
        return st
    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as c:
            r = await c.get(f"{url}/healthz")
        ok = r.status_code == 200
        st.update(last_ping_utc=utcnow().isoformat(), ok=ok, error=None if ok else f"HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        st.update(last_ping_utc=utcnow().isoformat(), ok=False, error=type(e).__name__)
    if st["ok"] is False:
        await runner.alerter.send("Self-ping failed", st["error"] or "unknown")
    return st


async def keepalive_loop(runner: Any) -> None:
    while True:
        minutes = int(runner.cloud.get("keepalive_minutes") or 10)
        await asyncio.sleep(max(1, min(14, minutes)) * 60)
        try:
            await ping_once(runner)
            await runner.tick("self-ping")
        except Exception as e:  # noqa: BLE001
            log.error("keepalive failed: %s", type(e).__name__)


def pinger_status(settings: Any, cloud: dict) -> dict:
    sync = cloud.get("pinger_sync") or {}
    return {"configured": bool(settings.cronjob_api_key and settings.public_url and settings.tick_token),
            "job_id": cloud.get("cronjob_job_id"), "synced": sync.get("ok"),
            "last_sync_utc": sync.get("ts").isoformat() if isinstance(sync.get("ts"), datetime) else sync.get("ts"),
            "error": sync.get("error")}


async def sync_pinger(runner: Any, cronjob_factory: Any, by: str) -> dict:
    """Create/update the cron-job.org job; record the result in cloud_settings. Never raises."""
    s = runner.settings
    cloud = runner.cloud
    if not (s.cronjob_api_key and s.public_url and s.tick_token):
        missing = [n for n, v in (("CRONJOB_API_KEY", s.cronjob_api_key), ("PUBLIC_URL", s.public_url),
                                  ("TICK_TOKEN", s.tick_token)) if not v]
        result = {"ok": None, "ts": utcnow(), "error": f"not configured: {', '.join(missing)} unset"}
        job_id = cloud.get("cronjob_job_id")
    else:
        try:
            client = cronjob_factory(s.cronjob_api_key)
            job_id = await client.sync(cloud.get("cronjob_job_id"), public_url=s.public_url,
                                       tick_token=s.tick_token, minutes=int(cloud.get("pinger_minutes") or 5),
                                       enabled=bool(cloud.get("pinger_enabled", True)))
            result = {"ok": True, "ts": utcnow(), "error": None}
        except Exception as e:  # noqa: BLE001
            job_id = cloud.get("cronjob_job_id")
            code = getattr(getattr(e, "response", None), "status_code", None)
            result = {"ok": False, "ts": utcnow(), "error": f"{type(e).__name__}" + (f" HTTP {code}" if code else "")}
            await runner.alerter.send("Pinger sync failed", result["error"])
    cloud.update(cronjob_job_id=job_id, pinger_sync=result)
    try:
        saved = await runner.store.save_cloud_settings({"cronjob_job_id": job_id, "pinger_sync": result}, by=by)
        cloud.update(saved or {})
    except Exception as e:  # noqa: BLE001
        log.error("save cloud settings failed: %s", type(e).__name__)
    return pinger_status(s, cloud)
