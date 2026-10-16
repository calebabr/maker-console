"""Web Push to the Keel PWA (UX item 45): RFC 8030 delivery, VAPID (RFC 8292) and aes128gcm payload encryption
(RFC 8291), built on `cryptography` and `httpx` (both already pinned), so no new dependency is added.

Supply-chain note: pywebpush was assessed and not added. It pulls requests + aiohttp (and aiohttp's tree:
multidict, yarl, frozenlist, aiosignal, attrs, propcache, aiohappyeyeballs), http-ece, py-vapid and six, i.e. ~10
new packages to do ~100 lines of work that `cryptography` already covers. The encryption here is checked against
the RFC 8291 section 5 test vector in control_api/tests/test_webpush.py.

Security:
* the VAPID private key comes from the VAPID_PRIVATE_KEY env only; it is never logged, stored or returned;
* a subscription endpoint is a bearer capability: it is never logged or returned, and only https endpoints on the
  known browser push services are accepted (also re-checked before every send), so the server can't be made to POST
  to arbitrary hosts (SSRF);
* payloads carry only a short title/body (already redacted), a type and a relative app path. No secrets;
* subscriptions answering 404/410 are deleted.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger("control_api.webpush")

ALLOWED_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com")
ALLOWED_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".push.services.mozilla.com")
ENDPOINT_MAX = 1024
RECORD_SIZE = 4096
MAX_PAYLOAD = RECORD_SIZE - 17 - 100          # one record (pad delimiter + GCM tag), with headroom
JWT_TTL_S = 12 * 3600
SEND_TIMEOUT_S = 10.0


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def b64u_dec(s: Any) -> bytes:
    s = str(s or "").strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


# ---------------------------------------------------------------- keys
def load_private_key(raw: str) -> ec.EllipticCurvePrivateKey:
    """VAPID private key: PEM, or the raw 32-byte P-256 scalar as base64url (what `web-push generate-vapid-keys`
    prints). ValueError (without the key material) on anything else."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("VAPID private key missing")
    try:
        if raw.startswith("-----BEGIN"):
            key = serialization.load_pem_private_key(raw.replace("\\n", "\n").encode(), password=None)
        else:
            d = b64u_dec(raw)
            if len(d) != 32:
                raise ValueError("bad length")
            key = ec.derive_private_key(int.from_bytes(d, "big"), ec.SECP256R1())
    except Exception as e:  # noqa: BLE001   (never echo the input)
        raise ValueError(f"VAPID private key unreadable ({type(e).__name__})") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("VAPID private key must be an EC P-256 key")
    return key


def public_bytes(key: ec.EllipticCurvePrivateKey) -> bytes:
    """Uncompressed X9.62 point (65 bytes), the applicationServerKey format browsers want."""
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ---------------------------------------------------------------- subscriptions
def endpoint_allowed(endpoint: str) -> bool:
    try:
        u = urlsplit(str(endpoint))
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return (u.scheme == "https" and len(str(endpoint)) <= ENDPOINT_MAX and not u.username and not u.password
            and u.port in (None, 443) and bool(host)
            and (host in ALLOWED_HOSTS or any(host.endswith(s) for s in ALLOWED_SUFFIXES)))


def validate_subscription(endpoint: Any, p256dh: Any, auth: Any) -> tuple[str, str, str]:
    """-> (endpoint, p256dh, auth) or ValueError([msgs]). Messages never echo the values."""
    errs = []
    if not isinstance(endpoint, str) or not endpoint_allowed(endpoint):
        errs.append("endpoint: must be an https URL of a known browser push service")
    try:
        pub = b64u_dec(p256dh) if isinstance(p256dh, str) and len(p256dh) <= 200 else b""
        if len(pub) != 65 or pub[0] != 4:
            raise ValueError
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub)
    except Exception:  # noqa: BLE001
        errs.append("keys.p256dh: must be a P-256 public key (base64url, 65 bytes)")
    try:
        if not isinstance(auth, str) or len(auth) > 64 or len(b64u_dec(auth)) != 16:
            raise ValueError
    except Exception:  # noqa: BLE001
        errs.append("keys.auth: must be 16 bytes (base64url)")
    if errs:
        raise ValueError(errs)
    return endpoint, p256dh.strip(), auth.strip()


# ---------------------------------------------------------------- VAPID (RFC 8292)
def vapid_authorization(key: ec.EllipticCurvePrivateKey, endpoint: str, subject: str,
                        now: float | None = None) -> str:
    u = urlsplit(endpoint)
    aud = f"{u.scheme}://{u.netloc}"
    enc = lambda o: b64u(json.dumps(o, separators=(",", ":")).encode())   # noqa: E731
    signing_input = f"{enc({'typ': 'JWT', 'alg': 'ES256'})}.{enc({'aud': aud, 'exp': int(now or time.time()) + JWT_TTL_S, 'sub': subject})}"
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = f"{signing_input}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={jwt}, k={b64u(public_bytes(key))}"


# ---------------------------------------------------------------- aes128gcm (RFC 8291 + RFC 8188)
def encrypt(payload: bytes, ua_public: bytes, auth_secret: bytes, *, _test_salt: bytes | None = None,
            _test_as_private: ec.EllipticCurvePrivateKey | None = None, rs: int = RECORD_SIZE) -> bytes:
    """One-record aes128gcm body for a push message. A fresh salt and ephemeral key are drawn for every message;
    the `_test_*` overrides exist only for the RFC 8291 vector test (reusing them would reuse key + nonce)."""
    as_private = _test_as_private or ec.generate_private_key(ec.SECP256R1())
    salt = _test_salt or os.urandom(16)
    if len(salt) != 16 or len(auth_secret) != 16 or len(ua_public) != 65:
        raise ValueError("bad key sizes")
    if len(payload) + 17 > rs:
        raise ValueError("payload too large for one record")
    as_public = public_bytes(as_private)
    ecdh = as_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hmac(_hmac(auth_secret, ecdh), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)          # 0x02 = last-record delimiter, no padding
    return salt + rs.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + ct


# ---------------------------------------------------------------- sender
class PushService:
    """Sends to every stored subscription. Never raises from send_all; logs status codes / error types only."""

    def __init__(self, settings: Any, store: Any, transport: Any = None):
        self.settings = settings
        self.store = store
        self.transport = transport
        self._key: ec.EllipticCurvePrivateKey | None = None
        self._key_error: str | None = None
        raw = getattr(settings, "vapid_private_key", None)
        if raw:
            try:
                self._key = load_private_key(raw)
            except ValueError as e:
                self._key_error = str(e)
                log.error("web push disabled: %s", self._key_error)

    @property
    def subject(self) -> str | None:
        s = (getattr(self.settings, "vapid_subject", None) or "").strip()
        if s.startswith(("mailto:", "https://")):
            return s
        pub = getattr(self.settings, "public_url", None) or ""
        return pub if pub.startswith("https://") else None

    @property
    def enabled(self) -> bool:
        return self._key is not None and self.subject is not None

    def public_key(self) -> str | None:
        return b64u(public_bytes(self._key)) if self._key is not None else None

    def status(self) -> dict:
        return {"enabled": self.enabled, "configured": self._key is not None or bool(self._key_error),
                "error": self._key_error if self._key_error else (None if self.enabled or self._key is None
                                                                  else "VAPID_SUBJECT or PUBLIC_URL (https) missing")}

    async def send_all(self, payload: dict, *, urgency: str = "normal", ttl: int = 3600) -> dict:
        res = {"sent": 0, "failed": 0, "removed": 0, "subscriptions": 0}
        if not self.enabled:
            return {**res, "error": "push_not_configured"}
        try:
            subs = await self.store.list_push_subscriptions()
        except Exception as e:  # noqa: BLE001
            log.warning("push: subscription read failed: %s", type(e).__name__)
            return {**res, "error": "store_unavailable"}
        res["subscriptions"] = len(subs)
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_PAYLOAD:
            return {**res, "error": "payload_too_large"}
        if not subs:
            return res
        try:
            async with httpx.AsyncClient(timeout=SEND_TIMEOUT_S, transport=self.transport,
                                         follow_redirects=False) as client:
                for sub in subs:
                    await self._send_one(client, sub, data, urgency, ttl, res)
        except Exception as e:  # noqa: BLE001
            log.warning("push: send failed: %s", type(e).__name__)
            res["failed"] += 1
        return res

    async def _send_one(self, client: httpx.AsyncClient, sub: dict, data: bytes, urgency: str, ttl: int,
                        res: dict) -> None:
        endpoint = str(sub.get("endpoint") or "")
        try:
            if not endpoint_allowed(endpoint):            # defense in depth: stored data is not trusted
                raise ValueError("endpoint not allowed")
            body = encrypt(data, b64u_dec(sub.get("p256dh")), b64u_dec(sub.get("auth")))
            headers = {"Authorization": vapid_authorization(self._key, endpoint, self.subject),
                       "TTL": str(int(ttl)), "Content-Encoding": "aes128gcm",
                       "Content-Type": "application/octet-stream",
                       "Urgency": urgency if urgency in ("very-low", "low", "normal", "high") else "normal"}
            r = await client.post(endpoint, content=body, headers=headers)
        except Exception as e:  # noqa: BLE001
            res["failed"] += 1
            log.warning("push: one subscription failed: %s", type(e).__name__)
            return
        if 200 <= r.status_code < 300:
            res["sent"] += 1
        elif r.status_code in (404, 410):                 # expired / unsubscribed: clean up
            try:
                await self.store.delete_push_subscription(str(sub.get("sub_id")))
                res["removed"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("push: cleanup failed: %s", type(e).__name__)
        else:
            res["failed"] += 1
            log.warning("push: service answered HTTP %s", r.status_code)
