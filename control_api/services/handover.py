"""Signed handover token (release process R3, owner decision A2).

On a PLANNED shutdown while the maker runs in LIVE, the old leader writes a short-lived token onto the lease doc:

    {v: 1, nonce, from_instance, mode, desired, config_hash, issued_at, stop_seq, sig}
    sig = HMAC-SHA256(HANDOVER_HMAC_KEY, canonical JSON of every field except sig)

The key lives only in the Render environment (never in Atlas, git, logs or responses), so someone with only Atlas
write access cannot forge a token: S-21 (every restart drops LIVE to demo) stays the default for crashes,
unplanned restarts and rollbacks.

`stop_seq` is the state fingerprint: a hash of the stored state doc (updated_utc, updated_by and the state fields)
at the moment the token was issued. Every Stop writes the state doc, so "no Stop since" == the fingerprint is
unchanged.

Never log the key or a token. ``verify`` returns a short reason string (safe to log) or None when valid.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Any

TOKEN_VERSION = 1
MIN_KEY_BYTES = 32
FUTURE_SKEW_S = 30.0          # an issued_at further in the future than this is refused (clock trouble or forgery)
_FIELDS = ("v", "nonce", "from_instance", "mode", "desired", "config_hash", "issued_at", "stop_seq")
_MAX_FIELD_LEN = 128


def key_bytes(settings: Any) -> bytes | None:
    """The HMAC key, or None (handover disabled) when missing or shorter than 32 bytes."""
    k = getattr(settings, "handover_hmac_key", None)
    if not isinstance(k, str):
        return None
    b = k.encode("utf-8")
    return b if len(b) >= MIN_KEY_BYTES else None


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("ascii")


def config_hash(cfg: dict | None) -> str:
    """sha256 of the canonical JSON of the public config (help keys are already stripped by the runner)."""
    return hashlib.sha256(canonical(cfg or {})).hexdigest()


def state_fingerprint(state: dict | None) -> str | None:
    """The `stop_seq` value: a hash of the stored state doc (updated_utc, updated_by and every state field). Any
    Stop rewrites the doc, so a changed fingerprint means "something happened since". updated_utc alone is not
    enough: two writes inside the same millisecond would share it. None if unknown."""
    if not isinstance(state, dict):
        return None
    u = state.get("updated_utc")
    if isinstance(u, datetime):
        if u.tzinfo is None:
            u = u.replace(tzinfo=timezone.utc)
        u = u.astimezone(timezone.utc).isoformat()
    if not u:
        return None
    parts = {k: state.get(k) for k in ("desired", "mode", "arb_desired", "arb_mode", "updated_by")}
    parts["updated_utc"] = str(u)
    return hashlib.sha256(canonical(parts)).hexdigest()[:32]


def _sign(key: bytes, body: dict) -> str:
    return hmac.new(key, canonical({k: body.get(k) for k in _FIELDS}), hashlib.sha256).hexdigest()


def make_token(key: bytes, *, from_instance: str, mode: str, desired: str, config_hash: str, stop_seq: str,
               now: datetime) -> dict:
    body = {"v": TOKEN_VERSION, "nonce": secrets.token_hex(16), "from_instance": str(from_instance)[:64],
            "mode": mode, "desired": desired, "config_hash": config_hash,
            "issued_at": now.astimezone(timezone.utc).isoformat(), "stop_seq": stop_seq}
    body["sig"] = _sign(key, body)
    return body


def _parse_ts(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, str) and len(v) <= 64:
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def verify(key: bytes | None, token: Any, *, config_hash: str, stop_seq: str | None, now: datetime,
           max_age_s: float) -> str | None:
    """None if the token lets the new leader keep LIVE; else a short reason (no secrets in it).
    The caller must still consume the nonce (single use) and re-check the live gates."""
    if key is None:
        return "no_key"
    if not isinstance(token, dict):
        return "no_token"
    sig = token.get("sig")
    if not isinstance(sig, str) or len(sig) != 64:
        return "bad_signature"
    for k in _FIELDS:
        v = token.get(k)
        if isinstance(v, str) and len(v) > _MAX_FIELD_LEN:
            return "malformed"
    if not hmac.compare_digest(sig.encode("ascii", "replace"), _sign(key, token).encode("ascii")):
        return "bad_signature"
    if token.get("v") != TOKEN_VERSION:
        return "bad_version"
    if not isinstance(token.get("nonce"), str) or len(token["nonce"]) < 16:
        return "malformed"
    issued = _parse_ts(token.get("issued_at"))
    if issued is None:
        return "malformed"
    age = (now - issued).total_seconds()
    if age > max_age_s:
        return "expired"
    if age < -FUTURE_SKEW_S:
        return "issued_in_future"
    if token.get("mode") != "live" or token.get("desired") != "running":
        return "not_live_running"
    if not hmac.compare_digest(str(token.get("config_hash") or "").encode(), str(config_hash).encode()):
        return "config_changed"
    if stop_seq is None or not hmac.compare_digest(str(token.get("stop_seq") or "").encode(),
                                                   str(stop_seq).encode()):
        return "state_changed"
    return None
