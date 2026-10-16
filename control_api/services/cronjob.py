"""cron-job.org REST client for the external tick job.

Keep calls minimal: the free API allows ~100 requests/day and 5 job creations/minute. The backend syncs
only on a cloud-settings change or a manual "Sync" (never per tick).
"""
from __future__ import annotations

from typing import Any

import httpx

API = "https://api.cron-job.org"


def schedule(minutes: int) -> dict:
    minutes = max(1, min(14, int(minutes)))
    return {"timezone": "UTC", "expiresAt": 0, "hours": [-1], "mdays": [-1], "months": [-1], "wdays": [-1],
            "minutes": list(range(0, 60, minutes))}


def job_body(public_url: str, tick_token: str, minutes: int, enabled: bool) -> dict:
    return {"job": {
        "url": f"{public_url.rstrip('/')}/api/tick", "enabled": bool(enabled), "title": "kalshi-control tick",
        "saveResponses": False, "requestMethod": 1, "requestTimeout": 30,      # 1 = POST
        "extendedData": {"headers": {"X-Tick-Token": tick_token}, "body": ""},
        "schedule": schedule(minutes),
    }}


class CronJobClient:
    def __init__(self, api_key: str, *, base: str = API, transport: Any = None):
        self._key = api_key
        self._base = base
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base, timeout=15, transport=self._transport,
                                 headers={"Authorization": f"Bearer {self._key}"})

    async def sync(self, job_id: int | None, *, public_url: str, tick_token: str, minutes: int,
                   enabled: bool) -> int:
        """Create (PUT /jobs) or update (PATCH /jobs/{id}) the job; returns the job id. Raises on failure."""
        body = job_body(public_url, tick_token, minutes, enabled)
        async with self._client() as c:
            if job_id:
                r = await c.patch(f"/jobs/{int(job_id)}", json=body)
                if r.status_code != 404:
                    r.raise_for_status()
                    return int(job_id)
            r = await c.put("/jobs", json=body)
            r.raise_for_status()
            return int(r.json()["jobId"])
