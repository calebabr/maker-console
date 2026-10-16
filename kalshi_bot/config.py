"""Generic bot settings for the PUBLIC STAND-IN: Kalshi API base URLs and the env handling the client needs.

Secrets are NEVER stored here. They are read only from the environment (KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY or
KALSHI_PRIVATE_KEY_PATH) by kalshi_client. The private overlay replaces this file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent

BASE_URLS = {
    # https://docs.kalshi.com/getting_started/api_environments
    "prod": "https://external-api.kalshi.com/trade-api/v2",
    "prod_alt": "https://api.elections.kalshi.com/trade-api/v2",
    "demo": "https://external-api.demo.kalshi.co/trade-api/v2",
    "demo_alt": "https://demo-api.kalshi.co/trade-api/v2",
}


@dataclass
class Settings:
    env: str = "demo"
    live_confirmed: bool = False

    @property
    def base_url(self) -> str:
        override = os.environ.get("KALSHI_BASE_URL")
        if override:
            return override.rstrip("/")
        return BASE_URLS["prod" if self.env == "prod" else "demo"]


def load_settings(config_path: str | None = None) -> Settings:  # noqa: ARG001  (same signature as the bot)
    env = os.environ.get("KALSHI_ENV", "demo").strip().lower()
    if env not in {"demo", "prod"}:
        raise ValueError(f"KALSHI_ENV must be 'demo' or 'prod', got {env!r}")
    live_confirmed = os.environ.get("KALSHI_LIVE_CONFIRM", "") == "I_UNDERSTAND_REAL_MONEY"
    return Settings(env=env, live_confirmed=live_confirmed)
