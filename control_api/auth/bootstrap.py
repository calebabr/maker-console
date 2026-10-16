"""Owner bootstrap CLI. Run on the owner's PC (Render free has no shell):

    python -m control_api.auth.bootstrap --gen-keys
        Print fresh JWT_SECRET, TOTP_ENC_KEY and TICK_TOKEN (paste into Render env vars).

    python -m control_api.auth.bootstrap --username NAME
        Needs MONGODB_URI and TOTP_ENC_KEY in the environment (same values as on Render).
        Prompts for the password twice (>= 12 chars), enrolls TOTP (prints the otpauth URI and
        an ASCII QR code once), asks for one code to confirm, then saves the user.

    python -m control_api.auth.bootstrap --username NAME --reset-2fa
        Re-enrolls TOTP for an existing user (password unchanged).

    python -m control_api.auth.bootstrap --username NAME --revoke-sessions
        Logs out every session (lost/stolen phone) without changing the password or 2FA.

Creating/overwriting the user and --reset-2fa also set ``sessions_valid_after`` = now, which
invalidates every access/mfa token issued before (SECURITY_REVIEW S-15). The running API
picks it up within ~10 s (its per-process cache TTL).

Single-user system: there is no registration endpoint; this CLI is the only way to create
the owner. Nothing secret is written anywhere except the user record (argon2 hash +
Fernet-encrypted TOTP secret).
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import inspect
import os
import re
import secrets
import sys
from types import SimpleNamespace
from typing import Any

from cryptography.fernet import Fernet

from . import security as sec
from .deps import SESSIONS_FIELD

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


def gen_keys() -> dict[str, str]:
    return {
        "JWT_SECRET": secrets.token_urlsafe(48),
        "TOTP_ENC_KEY": Fernet.generate_key().decode(),
        "TICK_TOKEN": secrets.token_urlsafe(32),
    }


def _load_settings() -> Any:
    """Backend's Settings.from_env() if it loads; else a minimal namespace from the env."""
    try:
        from control_api.settings import Settings  # lazy: owned by the backend agent
        return Settings.from_env()
    except Exception:
        uri = os.environ.get("MONGODB_URI") or None
        return SimpleNamespace(
            store=os.environ.get("STORE") or ("mongo" if uri else "memory"),
            mongodb_uri=uri,
            mongodb_db=os.environ.get("MONGODB_DB") or "kalshi_control",
            totp_enc_key=os.environ.get("TOTP_ENC_KEY") or None,
            jwt_secret=os.environ.get("JWT_SECRET") or None,
            allowed_origins=[],
        )


async def _get_store(settings: Any) -> Any:
    from control_api.db import get_store  # lazy: owned by the database agent
    store = get_store(settings)
    if inspect.isawaitable(store):
        store = await store
    return store


def print_enrollment(secret: str, username: str, out=sys.stdout) -> None:
    uri = sec.totp_uri(secret, username)
    print("\nAdd this account to your authenticator app (shown ONCE; do not save it):", file=out)
    print(f"  otpauth URI: {uri}", file=out)
    print(f"  manual key:  {secret}\n", file=out)
    try:
        import qrcode  # optional
        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.make(fit=True)
        try:
            qr.print_ascii(out=out, invert=True)
        except UnicodeEncodeError:  # legacy Windows console code page: plain-ASCII fallback
            for row in qr.get_matrix():
                print("".join("##" if cell else "  " for cell in row), file=out)
    except ImportError:
        print("  (install `qrcode` for an ASCII QR code)", file=out)
    except Exception:
        print("  (could not render QR code; use the URI or manual key)", file=out)


def _prompt_password() -> str:
    for _ in range(3):
        p1 = getpass.getpass(f"Password (min {sec.MIN_PASSWORD_LEN} chars): ")
        p2 = getpass.getpass("Repeat password: ")
        if p1 != p2:
            print("Passwords do not match.", file=sys.stderr)
            continue
        try:
            sec.validate_password_policy(p1)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            continue
        return p1
    raise SystemExit("Too many attempts.")


def _confirm_totp(secret: str) -> int:
    for _ in range(3):
        code = input("Enter the 6-digit code from your app to confirm: ").strip()
        step = sec.verify_totp(secret, code)
        if step is not None:
            return step
        print("Code did not verify (check the phone's clock).", file=sys.stderr)
    raise SystemExit("TOTP not confirmed; nothing was saved.")


async def _enroll(args: argparse.Namespace) -> int:
    username = args.username
    if not _USERNAME_RE.match(username):
        raise SystemExit("username must be 1-64 chars of letters, digits, _ . @ -")
    settings = _load_settings()
    if not getattr(settings, "mongodb_uri", None) and not args.allow_memory:
        raise SystemExit("MONGODB_URI is not set (the user must be stored in Atlas).")
    key = getattr(settings, "totp_enc_key", None)
    if not key:
        raise SystemExit("TOTP_ENC_KEY is not set (use the same value as on Render).")
    try:
        Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        raise SystemExit("TOTP_ENC_KEY is not a valid Fernet key (see --gen-keys).") from None

    store = await _get_store(settings)
    try:
        existing = await store.get_user(username)
        now = sec.utcnow()
        if args.revoke_sessions:
            if not existing:
                raise SystemExit(f"user {username!r} does not exist.")
            await store.save_user({"username": username, SESSIONS_FIELD: now, "updated_utc": now})
            print(f"\nAll sessions for {username!r} revoked "
                  "(tokens issued before now are invalid).")
            return 0
        if args.reset_2fa:
            if not existing:
                raise SystemExit(f"user {username!r} does not exist.")
            doc = {"username": username}     # field-level: don't write back the read copy
        else:
            if existing:
                ans = input(f"User {username!r} exists. Type OVERWRITE to replace its "
                            "password and 2FA: ").strip()
                if ans != "OVERWRITE":
                    raise SystemExit("Aborted; nothing changed.")
            doc = {"username": username, "created_utc": now}
            doc["password_hash"] = sec.hash_password(_prompt_password())

        secret = sec.new_totp_secret()
        print_enrollment(secret, username)
        step = _confirm_totp(secret)
        doc.update({
            "totp_secret_enc": sec.encrypt_totp_secret(settings, secret),
            "last_totp_step": step,          # the confirming code can't be reused
            "failed_logins": 0,
            "locked_until": None,
            "updated_utc": now,
            SESSIONS_FIELD: now,             # S-15: kill every token issued before the reset
        })
        doc.pop("_id", None)
        await store.save_user(doc)
        print(f"\nSaved user {username!r}." + (" 2FA re-enrolled." if args.reset_2fa else "")
              + " Previously issued sessions are now invalid.")
        return 0
    finally:
        close = getattr(store, "close", None)
        if close:
            try:
                r = close()
                if inspect.isawaitable(r):
                    await r
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m control_api.auth.bootstrap",
                                 description="Create the owner account / generate secrets.")
    ap.add_argument("--gen-keys", action="store_true",
                    help="print fresh JWT_SECRET, TOTP_ENC_KEY, TICK_TOKEN and exit")
    ap.add_argument("--username", help="create (or overwrite) the owner account")
    ap.add_argument("--reset-2fa", action="store_true",
                    help="with --username: re-enroll TOTP, keep the password")
    ap.add_argument("--revoke-sessions", action="store_true",
                    help="with --username: log out every session, keep password and 2FA")
    ap.add_argument("--allow-memory", action="store_true", help=argparse.SUPPRESS)  # dev only
    args = ap.parse_args(argv)

    if args.gen_keys:
        print("# Paste into the Render dashboard (Environment). Never commit these.")
        for k, v in gen_keys().items():
            print(f"{k}={v}")
        return 0
    if not args.username:
        ap.error("--username is required (or use --gen-keys)")
    if args.revoke_sessions and args.reset_2fa:
        ap.error("--revoke-sessions and --reset-2fa are exclusive (--reset-2fa also revokes)")
    return asyncio.run(_enroll(args))


if __name__ == "__main__":
    sys.exit(main())
