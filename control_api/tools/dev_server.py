"""Local preview of the real control API with fake data -- no Atlas, no Kalshi, no real bot files.

    python -m control_api.tools.dev_server            # http://localhost:8000
    cd control_app && set VITE_API_BASE_URL=http://localhost:8000 && npm run dev   # http://localhost:5173

- In-memory store filled by control_api.db.seed (realistic fake journal, snapshots, fills, monitor summary).
- A throwaway test owner (random password + TOTP secret) is written to local_dev/dev_credentials.json
  (git-ignored). Add the otpauth URI to an authenticator app, or print the current code with --code.
- The runner is disabled (RUNNER_ENABLED=false, a hard stop since S-10: Start/Run once return runner_disabled), every
  KALSHI_* variable is removed from this process's env at startup, and the Kalshi factories are fakes that return
  no client (S-11), so nothing trades and nothing calls Kalshi even if your shell has Kalshi keys.
- maker_config.json, the journal and the kill switch point at copies in local_dev/, so the real bot files are
  never touched.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "local_dev"
CREDS = DEV / "dev_credentials.json"


def _strip_kalshi_env() -> list[str]:
    """S-11: the runner reads Kalshi keys from os.environ; make sure the dev server can never see any."""
    import os
    gone = [k for k in list(os.environ) if k.upper().startswith("KALSHI_") or k.upper() == "ARB_LIVE"]
    for k in gone:
        os.environ.pop(k, None)
    return gone


def _no_client(_mode: str):
    return None


class _NoPublicData:
    """Public-data stand-in for arb scans in dev: no markets, so nothing is ever found or sent."""

    def get_markets(self, **_kw):
        return []

    def get_orderbook(self, _ticker, depth=None):
        return {}


def _point_bot_files_at_copies() -> None:
    from control_api.services import botfiles
    ms = botfiles.strategy()
    bot = DEV / "kalshi_bot"
    (bot / "logs").mkdir(parents=True, exist_ok=True)
    cfg = bot / "maker_config.json"
    if not cfg.exists():
        shutil.copy(ROOT / "kalshi_bot" / "maker_config.json", cfg)
    ms.CONFIG_PATH, ms.LOG, ms.KILL = cfg, bot / "logs" / "maker_journal.csv", bot / "MAKER_KILL_SWITCH"
    from control_api.services import arb as arbsvc
    sc = arbsvc.scanner()
    sc.KILL_PATH, sc.LOG_PATH = bot / "ARB_KILL_SWITCH", bot / "logs" / "arb_opportunities.csv"
    import maker_monitor as mm  # "Run health check" must not touch the real files either
    mm.HERE, mm.LOGS, mm.JOURNAL = bot, bot / "logs", bot / "logs" / "maker_journal.csv"
    mm.CONFIG, mm.KILL = cfg, bot / "MAKER_KILL_SWITCH"


async def _build(port: int):
    import pyotp
    from control_api.auth import security as sec
    from control_api.db import MemoryStore
    from control_api.db.seed import seed
    from control_api.main import create_app
    from control_api.settings import Settings

    settings = Settings.from_env({
        "STORE": "memory", "RUNNER_ENABLED": "false",
        "ALLOWED_ORIGIN": "http://localhost:5173,http://127.0.0.1:5173",
        "TICK_TOKEN": secrets.token_urlsafe(24), "PUBLIC_URL": f"http://localhost:{port}",
    })
    store = MemoryStore()
    await seed(store)
    password, secret = secrets.token_urlsafe(12), pyotp.random_base32()
    await store.save_user({"username": "owner", "password_hash": sec.hash_password(password),
                           "totp_secret_enc": sec.encrypt_totp_secret(settings, secret),
                           "last_totp_step": None, "failed_logins": 0})
    DEV.mkdir(exist_ok=True)
    CREDS.write_text(json.dumps({
        "note": "LOCAL TEST CREDENTIALS for the in-memory dev server only. Regenerated on every start.",
        "username": "owner", "password": password, "totp_secret": secret,
        "otpauth_uri": pyotp.TOTP(secret).provisioning_uri("owner", issuer_name="Kalshi Maker (dev)"),
    }, indent=2), encoding="utf-8")
    app = create_app(settings, store=store, kalshi_factory=_no_client, public_factory=lambda env: _NoPublicData())
    await app.state.runner.rehydrate()
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--code", action="store_true", help="print the current 2FA code for the running dev server")
    a = ap.parse_args()
    if a.code:
        import pyotp
        print(pyotp.TOTP(json.loads(CREDS.read_text())["totp_secret"]).now())
        return
    gone = _strip_kalshi_env()
    if gone:
        print(f"removed {len(gone)} KALSHI_* variable(s) from this process's env (dev server never uses keys)")
    _point_bot_files_at_copies()
    import uvicorn
    app = asyncio.run(_build(a.port))
    print(f"dev API on http://localhost:{a.port}  (test credentials: {CREDS.relative_to(ROOT)})")
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
