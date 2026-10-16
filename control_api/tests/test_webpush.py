"""Web push (UX item 45): RFC 8291 encryption, VAPID, subscription validation and the sender. No network."""
from __future__ import annotations

import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, hmac as chmac
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from control_api.db import MemoryStore
from control_api.db import schemas as S
from control_api.services import webpush as w
from control_api.settings import Settings


# RFC 8291 section 5 (the published example)
RFC = {
    "plaintext": b"When I grow up, I want to be a watermelon",
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "as_public": "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
    "ua_private": "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "body": "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBr"
            "u3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
}
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def _key(b64: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(w.b64u_dec(b64), "big"), ec.SECP256R1())


def _hm(key: bytes, data: bytes) -> bytes:
    h = chmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """Independent user-agent side of RFC 8291 (what the browser does), written from the RFC text."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public, ct = body[21:21 + idlen], body[21 + idlen:]
    assert rs >= 18 and idlen == 65
    ua_public = ua_private.public_key().public_bytes(w.serialization.Encoding.X962,
                                                     w.serialization.PublicFormat.UncompressedPoint)
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = _hm(_hm(auth, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hm(salt, ikm)
    cek = _hm(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hm(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    padded = AESGCM(cek).decrypt(nonce, ct, None)
    return padded.rstrip(b"\x00")[:-1]          # strip padding, then the 0x02 delimiter


def test_rfc8291_test_vector_exact():
    asp = _key(RFC["as_private"])
    assert w.b64u(w.public_bytes(asp)) == RFC["as_public"]
    body = w.encrypt(RFC["plaintext"], w.b64u_dec(RFC["ua_public"]), w.b64u_dec(RFC["auth"]),
                     _test_salt=w.b64u_dec(RFC["salt"]), _test_as_private=asp)
    assert w.b64u(body) == RFC["body"]
    assert decrypt(body, _key(RFC["ua_private"]), w.b64u_dec(RFC["auth"])) == RFC["plaintext"]


def test_encrypt_round_trip_with_random_keys_and_fresh_salt():
    ua = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"
    pub = w.public_bytes(ua)
    a, b = w.encrypt(b"hello", pub, auth), w.encrypt(b"hello", pub, auth)
    assert a != b                                       # fresh salt + ephemeral key every message
    assert decrypt(a, ua, auth) == b"hello" and decrypt(b, ua, auth) == b"hello"
    with pytest.raises(ValueError):
        w.encrypt(b"x" * 5000, pub, auth)


def test_vapid_header_is_a_valid_es256_jwt_for_the_endpoint_origin():
    key = ec.generate_private_key(ec.SECP256R1())
    hdr = w.vapid_authorization(key, ENDPOINT, "mailto:owner@example.com", now=1_700_000_000)
    assert hdr.startswith("vapid t=")
    t = hdr.split("t=")[1].split(",")[0]
    k = hdr.split("k=")[1]
    assert w.b64u_dec(k) == w.public_bytes(key)
    h, c, sig = t.split(".")
    assert json.loads(w.b64u_dec(h)) == {"typ": "JWT", "alg": "ES256"}
    claims = json.loads(w.b64u_dec(c))
    assert claims == {"aud": "https://fcm.googleapis.com", "exp": 1_700_000_000 + w.JWT_TTL_S,
                      "sub": "mailto:owner@example.com"}
    raw = w.b64u_dec(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))   # raises if invalid


def test_private_key_formats_and_errors_never_echo_the_key():
    key = ec.generate_private_key(ec.SECP256R1())
    raw = w.b64u(key.private_numbers().private_value.to_bytes(32, "big"))
    assert w.public_bytes(w.load_private_key(raw)) == w.public_bytes(key)
    pem = key.private_bytes(w.serialization.Encoding.PEM, w.serialization.PrivateFormat.PKCS8,
                            w.serialization.NoEncryption()).decode()
    assert w.public_bytes(w.load_private_key(pem.replace("\n", "\\n"))) == w.public_bytes(key)
    for bad in ("", "abc", raw[:-4], "-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----"):
        with pytest.raises(ValueError) as ei:
            w.load_private_key(bad)
        assert (not bad) or bad not in str(ei.value)


@pytest.mark.parametrize("endpoint,ok", [
    (ENDPOINT, True),
    ("https://web.push.apple.com/QGuQyavXutnMH", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("https://db5p.notify.windows.com/w/?token=x", True),
    ("http://fcm.googleapis.com/fcm/send/x", False),                  # not https
    ("https://evil.example.com/fcm.googleapis.com", False),           # unknown host
    ("https://fcm.googleapis.com.evil.com/x", False),
    ("https://user:pw@fcm.googleapis.com/x", False),                  # userinfo
    ("https://fcm.googleapis.com:8443/x", False),                     # odd port
    ("https://169.254.169.254/latest", False),
    ("https://fcm.googleapis.com/" + "a" * 1100, False),
])
def test_endpoint_allow_list(endpoint, ok):
    assert w.endpoint_allowed(endpoint) is ok


def _sub_keys():
    ua = ec.generate_private_key(ec.SECP256R1())
    return ua, w.b64u(w.public_bytes(ua)), w.b64u(b"0123456789abcdef")


def test_validate_subscription_messages_never_echo_values():
    _, p256dh, auth = _sub_keys()
    assert w.validate_subscription(ENDPOINT, p256dh, auth) == (ENDPOINT, p256dh, auth)
    with pytest.raises(ValueError) as ei:
        w.validate_subscription("https://evil.example.com/secret-token", "AAAA", "secret-auth")
    msgs = " ".join(ei.value.args[0])
    assert len(ei.value.args[0]) == 3 and "secret" not in msgs and "evil" not in msgs


class Recorder:
    def __init__(self, status=201):
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        st = self.status(request) if callable(self.status) else self.status
        return httpx.Response(st)


def _push(store, rec, **kw):
    key = ec.generate_private_key(ec.SECP256R1())
    raw = w.b64u(key.private_numbers().private_value.to_bytes(32, "big"))
    s = Settings(store="memory", vapid_private_key=raw, vapid_subject=kw.get("subject", "mailto:o@example.com"),
                 public_url=kw.get("public_url"))
    return w.PushService(s, store, transport=httpx.MockTransport(rec)), key


@pytest.mark.asyncio
async def test_send_all_encrypts_signs_counts_and_cleans_up_gone_subscriptions():
    store = MemoryStore()
    ua1, p1, a1 = _sub_keys()
    _, p2, a2 = _sub_keys()
    await store.save_push_subscription({"endpoint": ENDPOINT, "p256dh": p1, "auth": a1})
    await store.save_push_subscription({"endpoint": "https://web.push.apple.com/gone", "p256dh": p2, "auth": a2})
    rec = Recorder(lambda r: 410 if "apple" in str(r.url) else 201)
    push, key = _push(store, rec)
    assert push.enabled and push.public_key() == w.b64u(w.public_bytes(key))
    res = await push.send_all({"title": "T", "body": "B", "type": "test", "url": "/alerts"}, urgency="high")
    assert res == {"sent": 1, "failed": 0, "removed": 1, "subscriptions": 2}
    assert [s["endpoint"] for s in await store.list_push_subscriptions()] == [ENDPOINT]
    fcm = next(r for r in rec.requests if "fcm" in str(r.url))
    assert fcm.headers["content-encoding"] == "aes128gcm" and fcm.headers["urgency"] == "high"
    assert fcm.headers["authorization"].startswith("vapid t=") and int(fcm.headers["ttl"]) > 0
    payload = json.loads(decrypt(fcm.content, ua1, w.b64u_dec(a1)))
    assert payload == {"title": "T", "body": "B", "type": "test", "url": "/alerts"}


@pytest.mark.asyncio
async def test_send_all_skips_a_stored_endpoint_off_the_allow_list_and_counts_errors():
    store = MemoryStore()
    _, p, a = _sub_keys()
    store._push["x"] = {"sub_id": "x", "endpoint": "https://attacker.example/collect", "p256dh": p, "auth": a}
    await store.save_push_subscription({"endpoint": ENDPOINT, "p256dh": p, "auth": a})
    rec = Recorder(500)
    push, _ = _push(store, rec)
    res = await push.send_all({"title": "T"})
    assert res["sent"] == 0 and res["failed"] == 2 and res["removed"] == 0
    assert all("attacker" not in str(r.url) for r in rec.requests)


@pytest.mark.asyncio
async def test_disabled_without_key_or_subject_and_never_raises():
    store = MemoryStore()
    assert not w.PushService(Settings(store="memory"), store).enabled
    bad = w.PushService(Settings(store="memory", vapid_private_key="garbage", vapid_subject="mailto:a@b.c"), store)
    assert not bad.enabled and "garbage" not in str(bad.status())
    push, _ = _push(store, Recorder(), subject=None)
    assert not push.enabled                                   # no subject and no https public url
    push2, _ = _push(store, Recorder(), subject=None, public_url="https://api.example.com")
    assert push2.enabled and push2.subject == "https://api.example.com"
    assert (await w.PushService(Settings(store="memory"), store).send_all({}))["error"] == "push_not_configured"


@pytest.mark.asyncio
async def test_store_push_subscription_cap_and_upsert():
    store = MemoryStore()
    _, p, a = _sub_keys()
    assert await store.save_push_subscription({"endpoint": ENDPOINT, "p256dh": p, "auth": a}) is True
    assert await store.save_push_subscription({"endpoint": ENDPOINT, "p256dh": p, "auth": a}) is False
    for i in range(S.PUSH_SUBSCRIPTIONS_MAX - 1):
        await store.save_push_subscription({"endpoint": f"{ENDPOINT}{i}", "p256dh": p, "auth": a})
    with pytest.raises(ValueError):
        await store.save_push_subscription({"endpoint": ENDPOINT + "x", "p256dh": p, "auth": a})
    assert await store.delete_push_subscription(S.push_sub_id(ENDPOINT)) is True
    assert await store.delete_push_subscription(S.push_sub_id(ENDPOINT)) is False
