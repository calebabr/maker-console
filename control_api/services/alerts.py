"""Alerts: ntfy (best effort, never raises, never sends secrets), plus the UX-sprint pieces (items 37, 38, 45):

* every alert is classified (fill, settled, daily, error, stop, breaker, test, system) and handed to an optional
  `on_alert` hook, which the runner uses to log it to `alert_history` and forward it as a web push;
* `notify()` is the everyday channel (fills, settled markets, daily summary): history + push only, never ntfy;
* alert settings (daily summary time, "problems" vs "every_fill", quiet hours) live in cloud_settings["alerts"],
  validated here. Stop and critical alerts always bypass quiet hours. ntfy keeps getting every alert it got before.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time as dtime, timezone
from typing import Any, Callable

import httpx

from .logbuffer import redact

log = logging.getLogger("control_api.alerts")

TYPES = ("fill", "settled", "daily", "error", "stop", "breaker", "test", "system")
PROBLEM_TYPES = ("error", "stop", "breaker")
EVERYDAY_TYPES = ("fill", "settled")
MODES = ("problems", "every_fill")
DEFAULT_TZ = "America/Chicago"
DEFAULT_ALERT_SETTINGS = {"daily_summary_time": "08:00", "mode": "problems",
                          "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"},
                          "tz": DEFAULT_TZ}
ALERT_FIELDS = ("daily_summary_time", "mode", "quiet_hours", "tz")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_TZ = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+\-]+){0,2}$|^UTC$")

# title prefix -> type, for the existing ntfy alerts (first match wins)
_TITLE_TYPES = (
    ("Arbitrage legging risk", "breaker"), ("Arbitrage stopped: kill switch", "breaker"),
    ("Bot stopped", "stop"),
    ("Trading pass error", "error"), ("No heartbeat", "error"), ("Monitor failed", "error"),
    ("Self-ping failed", "error"), ("Pinger sync failed", "error"),
    ("Arbitrage executed", "fill"),
    ("Order errors", "error"), ("Trading paused by Kalshi", "error"),
)


def classify(title: str, priority: str = "default") -> str:
    for prefix, t in _TITLE_TYPES:
        if str(title).startswith(prefix):
            return t
    return "error" if priority in ("high", "urgent") and "fail" in str(title).lower() else "system"


def is_critical(kind: str, priority: str = "default") -> bool:
    """Stop / circuit breaker / high-priority alerts: always delivered, quiet hours or not."""
    return kind in ("stop", "breaker") or priority in ("high", "urgent")


# ---------------------------------------------------------------- settings (item 38)
def merged_settings(stored: Any) -> dict:
    out = {**DEFAULT_ALERT_SETTINGS, "quiet_hours": dict(DEFAULT_ALERT_SETTINGS["quiet_hours"])}
    if isinstance(stored, dict):
        for k in ("daily_summary_time", "mode", "tz"):
            if k in stored:
                out[k] = stored[k]
        if isinstance(stored.get("quiet_hours"), dict):
            out["quiet_hours"].update({k: v for k, v in stored["quiet_hours"].items()
                                       if k in ("enabled", "start", "end")})
    return out


def _tz_ok(name: str) -> bool:
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def validate_settings(current: dict, update: Any) -> dict:
    """Merge a partial update into the current alert settings; ValueError([msgs]) on any problem."""
    if not isinstance(update, dict):
        raise ValueError(["body: must be a JSON object"])
    errs = []
    unknown = [k for k in update if k not in ALERT_FIELDS]
    if unknown:
        errs.append(f"unknown fields: {sorted(map(str, unknown))[:10]}")
    out = merged_settings(current)
    if "daily_summary_time" in update:
        v = update["daily_summary_time"]
        if v is not None and not (isinstance(v, str) and _HHMM.match(v)):
            errs.append("daily_summary_time: must be \"HH:MM\" (24 h) or null to turn it off")
        else:
            out["daily_summary_time"] = v
    if "mode" in update:
        if update["mode"] not in MODES:
            errs.append(f"mode: must be one of {list(MODES)}")
        else:
            out["mode"] = update["mode"]
    if "tz" in update:
        v = update["tz"]
        if not (isinstance(v, str) and len(v) <= 64 and _TZ.match(v) and _tz_ok(v)):
            errs.append("tz: must be an IANA time zone such as America/Chicago")
        else:
            out["tz"] = v
    if "quiet_hours" in update:
        q = update["quiet_hours"]
        if not isinstance(q, dict) or set(q) - {"enabled", "start", "end"}:
            errs.append("quiet_hours: must be {enabled, start, end}")
        else:
            if "enabled" in q and not isinstance(q["enabled"], bool):
                errs.append("quiet_hours.enabled: must be true or false")
            for k in ("start", "end"):
                if k in q and not (isinstance(q[k], str) and _HHMM.match(q[k])):
                    errs.append(f"quiet_hours.{k}: must be \"HH:MM\" (24 h)")
            if not errs:
                out["quiet_hours"].update(q)
    if errs:
        raise ValueError(errs)
    return out


def changes(before: dict, after: dict) -> list[dict]:
    """[{key, from, to}] for the toast (item 39); nested quiet_hours keys as quiet_hours.start etc."""
    def flat(d):
        o = {}
        for k, v in (d or {}).items():
            if isinstance(v, dict):
                o.update({f"{k}.{kk}": vv for kk, vv in v.items()})
            else:
                o[k] = v
        return o
    a, b = flat(before), flat(after)
    return [{"key": k, "from": a.get(k), "to": b.get(k)} for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def local_now(settings: dict, now: datetime | None = None) -> datetime:
    """`now` in the configured time zone (UTC if the zone database is unavailable)."""
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo(str(settings.get("tz") or DEFAULT_TZ)))
    except Exception:  # noqa: BLE001
        return now.astimezone(timezone.utc)


def _hm(s: str) -> dtime:
    h, m = str(s).split(":")
    return dtime(int(h), int(m))


def in_quiet_hours(settings: dict, now: datetime | None = None) -> bool:
    q = settings.get("quiet_hours") or {}
    if not q.get("enabled"):
        return False
    try:
        start, end = _hm(q["start"]), _hm(q["end"])
    except (KeyError, ValueError):
        return False
    t = local_now(settings, now).time().replace(second=0, microsecond=0)
    if start == end:
        return False
    return start <= t < end if start < end else (t >= start or t < end)   # overnight window wraps midnight


def should_push(settings: dict, kind: str, priority: str = "default", now: datetime | None = None) -> bool:
    """Web push filter: mode + quiet hours. Stop/critical alerts always go; tests always go."""
    if kind == "test":
        return True
    critical = is_critical(kind, priority)
    if not critical:
        if kind in EVERYDAY_TYPES and settings.get("mode") != "every_fill":
            return False
        if kind == "daily" and not settings.get("daily_summary_time"):
            return False
        if in_quiet_hours(settings, now):
            return False
    return True


# ---------------------------------------------------------------- sender
class Alerter:
    def __init__(self, settings, transport=None):
        self.topic = getattr(settings, "ntfy_topic", None)
        self.server = getattr(settings, "ntfy_server", "https://ntfy.sh")
        self.transport = transport
        self.sent: list[dict] = []          # recent alerts, for tests/debugging
        # on_alert(kind, title, body, priority, channels): set by the runner (history + push). Plain callable that
        # must not block (it spawns its own task); exceptions are swallowed here.
        self.on_alert: Callable[..., Any] | None = None

    def _hook(self, kind: str, title: str, message: str, priority: str, channels: list[str]) -> None:
        if self.on_alert is None:
            return
        try:
            self.on_alert(kind, title, message, priority, channels)
        except Exception as e:  # noqa: BLE001
            log.warning("alert hook failed: %s", type(e).__name__)

    async def send(self, title: str, message: str, *, priority: str = "default", tags: str = "") -> bool:
        ok = False
        try:
            message, title = redact(message)[:2000], redact(title)[:200]
            self.sent = (self.sent + [{"title": title, "message": message, "priority": priority}])[-50:]
            log.warning("ALERT %s: %s", title, message)
            if self.topic:
                headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": priority}
                if tags:
                    headers["Tags"] = tags
                async with httpx.AsyncClient(timeout=10, transport=self.transport) as c:
                    r = await c.post(f"{self.server}/{self.topic}", content=message.encode("utf-8"), headers=headers)
                    ok = r.status_code < 300
        except Exception as e:  # noqa: BLE001
            log.warning("ntfy send failed: %s", type(e).__name__)
            ok = False
        self._hook(classify(title, priority), title, message, priority, ["ntfy"] if self.topic else [])
        return ok

    def notify(self, kind: str, title: str, message: str, *, priority: str = "default") -> None:
        """Everyday alert (fill / settled / daily / test): history + web push only, never ntfy. Never raises."""
        try:
            self._hook(kind if kind in TYPES else "system", redact(title)[:200], redact(message)[:2000], priority, [])
        except Exception as e:  # noqa: BLE001
            log.warning("notify failed: %s", type(e).__name__)
