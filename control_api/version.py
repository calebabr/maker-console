"""Version handshake (release process R4).

* ``API_VERSION``: the running build, from env ``APP_VERSION``, else ``RENDER_GIT_COMMIT[:7]`` (Render sets it
  per deploy), else ``0.0.0-dev``. Read once at import (an env read, no network).
* ``MIN_APP_VERSION``: the oldest phone-app version this API still answers correctly. The app shows "Update
  available, reopen the app" when it is older; it keeps showing data and Stop always works.
* ``SCHEMA_VERSION``: the data layout this code writes. A runner that finds a NEWER stored schema version stays
  standby (newer data written by a newer release); an older or missing one is overwritten with ours.
* ``HANDOVER_PROTOCOL``: the signed-handover token version (``services/handover.py``). The pre-deploy gate uses
  it (via ``/api/deploy/status`` ``version``) to know the running server can hand over.
"""
from __future__ import annotations

import os
import re

_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")


def _clean(v: str | None, default: str) -> str:
    v = (v or "").strip()
    return v if _VERSION_RE.match(v) else default


def resolve_api_version(env=None) -> str:
    """APP_VERSION if set and valid, else Render's RENDER_GIT_COMMIT[:7] (the release workflow's verify step
    accepts a >= 7 character commit prefix), else 0.0.0-dev."""
    env = os.environ if env is None else env
    commit = (env.get("RENDER_GIT_COMMIT") or "").strip().lower()[:7]
    fallback = commit if re.fullmatch(r"[0-9a-f]{7}", commit) else "0.0.0-dev"
    return _clean(env.get("APP_VERSION"), fallback)


API_VERSION: str = resolve_api_version()
MIN_APP_VERSION: str = "0.1.0"
SCHEMA_VERSION: int = 1
HANDOVER_PROTOCOL: int = 1


def handshake() -> dict:
    """The version fields /healthz and /api/status add (no secrets, no store read)."""
    return {"api_version": API_VERSION, "min_app_version": MIN_APP_VERSION, "schema_version": SCHEMA_VERSION}
