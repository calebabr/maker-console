# Maker Console

A phone-first control system for running an automated Kalshi market-making bot 24/7 on free cloud tiers: a FastAPI
control API with an in-process bot runner, and a React PWA to start, stop and supervise it from a phone.

## What is in this repository

```
control_api/   FastAPI control API + bot runner (keep-awake, alerts, analytics, Stop that always works)
control_app/   React + Vite PWA (phone app), deployed to Vercel
kalshi_bot/    PUBLIC STAND-IN for the private trading bot: generic Kalshi client + a demo placeholder strategy
deploy/        Render / Vercel / Atlas / cron-job.org notes, env template, optional VM files
scripts/       overlay_private.py (installs the private strategy at deploy time) and helpers
docs/          architecture, API, deploy and phone guides
```

The trading strategy is **not** in this repository. `kalshi_bot/` ships a placeholder with the same interface, so the
app, the API and every test run without it. While the placeholder is installed, LIVE (real-money) mode is refused
with `strategy_not_installed`; demo mode and Stop work as normal. See `kalshi_bot/README.md` for the overlay
contract.

## Tests

```
python -m pytest -q -n auto control_api/tests
cd kalshi_bot && python -m pytest -q tests
cd control_app && npm ci && npm test && npm run build
```
