"""``install_security(app, settings)``: strict CORS, foreign-Origin rejection, security headers.

Order (outermost first): body-size cap -> security headers -> origin guard -> CORS -> app.
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


# B-1: request body caps. FastAPI reads and JSON-parses the whole body before a handler (and its rate limit) runs,
# so one unauthenticated ~80 MB login body could run the 512 MB instance out of memory. Auth bodies are a username,
# a password and a 6-digit code; every other body is a small JSON document (the config is a few KB).
MAX_BODY_BYTES = 256 * 1024
MAX_AUTH_BODY_BYTES = 64 * 1024
AUTH_PREFIX = "/api/auth/"


def body_limit_for(path: str) -> int:
    return MAX_AUTH_BODY_BYTES if path.startswith(AUTH_PREFIX) else MAX_BODY_BYTES


class BodySizeLimitMiddleware:
    """413 ``body_too_large`` for a body over the cap, before the app reads or parses it.

    (1) A ``content-length`` over the cap (or an unparsable one) is refused without calling the app.
    (2) Otherwise ``receive`` is wrapped and counts body bytes, so a chunked body (no content-length) or one that
        lies about its length is cut off as soon as it passes the cap: the 413 is sent right there, the app is told
        the client disconnected (it never gets the oversized chunk), and anything the app sends afterwards is
        dropped. No exception is used, so no middleware or body parser can turn it into a 400 or a 500.
    Stop's body is empty, so it is never affected."""

    def __init__(self, app: Any):
        self.app = app

    @staticmethod
    async def _reject(send) -> None:
        body = json.dumps({"detail": "body_too_large"}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = body_limit_for(scope.get("path", ""))
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    n = int(v)
                except ValueError:
                    n = limit + 1
                if n > limit or n < 0:
                    return await self._reject(send)
                break
        seen = 0
        started = False
        rejected = False

        async def counting_receive():
            nonlocal seen, started, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    rejected = True
                    if not started:
                        started = True
                        await self._reject(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            nonlocal started
            if rejected:
                return                                   # the 413 already went out
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not rejected:
                raise
            # the app reacting to the synthetic disconnect (e.g. ClientDisconnect): the 413 is already sent


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
    # B-1: outermost, so an oversized body is refused before any other layer or the app reads it.
    app.add_middleware(BodySizeLimitMiddleware)
