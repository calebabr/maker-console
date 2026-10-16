"""Primitives: argon2id passwords, Fernet-encrypted TOTP secrets, TOTP verification, JWTs.

Nothing here touches the store or logs secrets. Keys come from ``settings`` (``jwt_secret``,
``totp_enc_key``); there are no module-level secrets.
"""
from __future__ import annotations

import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

# ---- policy constants -------------------------------------------------------------------
MIN_PASSWORD_LEN = 12
MFA_TOKEN_TTL_S = 300          # typ=mfa: between password and TOTP step
ACCESS_TOKEN_TTL_S = 3600      # typ=access: <= 60 min (plan safety rule 6)
MAX_FAILED_LOGINS = 5          # consecutive failures (password, login TOTP, step-up TOTP)
LOCKOUT_SECONDS = 15 * 60
TOTP_INTERVAL_S = 30
TOTP_DIGITS = 6
TOTP_VALID_WINDOW = 1          # +-1 step
TOTP_ISSUER = "Kalshi Maker Bot"
JWT_ALG = "HS256"

TYP_MFA = "mfa"
TYP_ACCESS = "access"

_hasher = PasswordHasher()  # argon2id with argon2-cffi's RFC 9106 low-memory defaults
# Verified against when the username is unknown so both paths cost the same.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """Treat naive datetimes as UTC (defensive; the store should return aware ones)."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---- passwords ---------------------------------------------------------------------------
def validate_password_policy(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")


def hash_password(password: str) -> str:
    validate_password_policy(password)
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-ish time: always runs one argon2 verification, even with no stored hash."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and bool(password_hash)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False


# ---- TOTP secret at rest -------------------------------------------------------------------
def _fernet(settings: Any) -> Fernet:
    key = getattr(settings, "totp_enc_key", None)
    if not key:
        raise RuntimeError("TOTP_ENC_KEY is not configured")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_totp_secret(settings: Any, secret: str) -> str:
    return _fernet(settings).encrypt(secret.encode()).decode()


def decrypt_totp_secret(settings: Any, token: str) -> str:
    try:
        return _fernet(settings).decrypt(token.encode()).decode()
    except InvalidToken as e:  # wrong TOTP_ENC_KEY or corrupted record
        raise RuntimeError("cannot decrypt TOTP secret (wrong TOTP_ENC_KEY?)") from e


def new_totp_secret() -> str:
    return pyotp.random_base32()  # 32 base32 chars = 160 bits


def totp_uri(secret: str, username: str, issuer: str = TOTP_ISSUER) -> str:
    return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL_S).provisioning_uri(
        name=username, issuer_name=issuer)


def normalize_code(code: Any) -> str | None:
    if not isinstance(code, str):
        return None
    code = code.strip().replace(" ", "")
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    return code


def verify_totp(secret: str, code: Any, *, last_step: int | None = None,
                now: datetime | None = None) -> int | None:
    """Return the matched time step if ``code`` is valid within +-1 step and the step is
    strictly newer than ``last_step`` (so a code / step can never be used twice); else None."""
    code = normalize_code(code)
    if code is None or not secret:
        return None
    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL_S)
    current = int((now or utcnow()).timestamp()) // TOTP_INTERVAL_S
    matched: int | None = None
    for step in range(current - TOTP_VALID_WINDOW, current + TOTP_VALID_WINDOW + 1):
        if hmac.compare_digest(totp.at(step * TOTP_INTERVAL_S), code):
            matched = step
    if matched is None:
        return None
    if last_step is not None and matched <= int(last_step):
        return None
    return matched


# ---- JWT ---------------------------------------------------------------------------------
class TokenError(Exception):
    pass


def _jwt_secret(settings: Any) -> str:
    secret = getattr(settings, "jwt_secret", None)
    if not secret:
        raise RuntimeError("JWT_SECRET is not configured")
    return secret


def create_token(settings: Any, sub: str, typ: str, ttl_s: int) -> tuple[str, dict]:
    """Return (encoded token, claims). Claims: sub, typ, jti, iat, exp."""
    now = utcnow()
    claims = {
        "sub": sub,
        "typ": typ,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_s)).timestamp()),
    }
    return jwt.encode(claims, _jwt_secret(settings), algorithm=JWT_ALG), claims


def decode_token(settings: Any, token: str, typ: str) -> dict:
    """Verify signature, expiry, required claims and ``typ``. Raises TokenError."""
    if not token or not isinstance(token, str):
        raise TokenError("missing")
    try:
        claims = jwt.decode(token, _jwt_secret(settings), algorithms=[JWT_ALG],
                            options={"require": ["sub", "typ", "jti", "iat", "exp"]}, leeway=5)
    except jwt.PyJWTError as e:
        raise TokenError(type(e).__name__) from None
    if claims.get("typ") != typ:
        raise TokenError("wrong_typ")
    if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("jti"), str):
        raise TokenError("bad_claims")
    return claims


def exp_datetime(claims: dict) -> datetime:
    return datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
