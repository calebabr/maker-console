"""``install_security(app, settings)``: strict CORS, foreign-Origin rejection, security headers.

Order (outermost first): security headers -> origin guard -> CORS -> app.
- CORS allows only ``settings.allowed_origins`` (never ``*``), headers
  ``Authorization, Content-Type, X-2FA-Code``, no cookies/credentials (Bearer tokens only).
- Any request carrying an ``Origin`` header that is not allowed gets 403 ``origin_not_allowed``
  (non-browser callers such as cron-job.org and the self-ping send no Origin and pass).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

log = logging.getLogger("control_api.auth")

ALLOWED_HEADERS = ["Authorization", "Content-Type", "X-2FA-Code"]
ALLOWED_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
# Response headers the cross-origin frontend may read (CSV export metadata, 429 back-off).
EXPOSE_HEADERS = ["X-Truncated", "Content-Disposition", "Retry-After"]

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (b"cross-origin-resource-policy", b"cross-origin"),
]
# JSON API responses only (a strict CSP would break FastAPI's /docs page if enabled).
API_ONLY_HEADERS = [
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
]


def normalize_origins(origins: Iterable[str] | str | None) -> list[str]:
    if origins is None:
        return []
    if isinstance(origins, str):
        origins = origins.split(",")
    out: list[str] = []
    for o in origins:
        o = (o or "").strip().rstrip("/")
        if not o:
            continue
        if o == "*" or "*" in o:
            log.error("refusing wildcard CORS origin %r", o)
            continue
        if o not in out:
            out.append(o)
    return out


class SecurityHeadersMiddleware:
    """Adds security headers to every response; no-store + strict CSP on /api paths."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        is_api = scope.get("path", "").startswith("/api")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                for k, v in SECURITY_HEADERS:
                    if k not in present:
                        headers.append((k, v))
                if is_api:
                    for k, v in API_ONLY_HEADERS:
                        if k not in present:
                            headers.append((k, v))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class OriginGuardMiddleware:
    """403 for any request whose Origin header is present but not allow-listed."""

    def __init__(self, app: Any, allowed_origins: list[str]):
        self.app = app
        self.allowed = set(allowed_origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            origin = None
            for k, v in scope.get("headers", []):
                if k == b"origin":
                    origin = v.decode("latin-1").rstrip("/")
                    break
            if origin is not None and origin not in self.allowed:
                body = json.dumps({"detail": "origin_not_allowed"}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def install_security(app: FastAPI, settings: Any) -> None:
    origins = normalize_origins(getattr(settings, "allowed_origins", None))
    if not origins:
        log.warning("ALLOWED_ORIGIN is empty: browsers will be refused by CORS")
    # add_middleware prepends, so the last one added is outermost.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
        expose_headers=EXPOSE_HEADERS,
        max_age=600,
    )
    app.add_middleware(OriginGuardMiddleware, allowed_origins=origins)
    app.add_middleware(SecurityHeadersMiddleware)
