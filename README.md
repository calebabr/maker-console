# Maker Console

A phone-first control system for running an automated Kalshi trading bot 24/7 on free cloud tiers. A FastAPI control
API runs the bot in-process and a React PWA lets the owner start, stop and supervise it from a phone.

> **Not financial advice.** This is a personal engineering project. Trading involves risk of loss, nothing here is a
> recommendation or a promise of returns, and the maintainers are not licensed advisors. The system runs in demo mode
> (Kalshi's demo environment, no real money) unless the owner deliberately arms live mode.

## What it does

- **Run and supervise the bot from a phone.** Start, Stop, mode (demo or live), settings, logs, activity and account
  state, all in an installable PWA (`docs/PHONE.md`).
- **Stop always works.** One tap, no 2FA, and it does not depend on the database, the exchange or the network
  (see Safety model).
- **Stays awake for free.** Render's free tier sleeps when idle, so a self-ping plus an external cron job keep it
  reachable (`docs/ARCHITECTURE.md`).
- **Alerts** to the phone through ntfy.sh and Web Push for fills, errors, restarts and safety events.
- **Analytics and health pages:** fills, quote and fill quality, account snapshots, benchmark comparison and
  per-period results.
- **Shadow mode:** estimates what the strategy would have quoted on public market data without placing orders
  (`docs/SHADOW_CONTRACT.md`).
- **Demo periods:** named, resettable evaluation windows with cash flows tracked per period.
- **Arbitrage scanner** with a log-only default and a gated demo-execute path.
- **Zero-downtime updates:** a manual, owner-approved release workflow with a lease handover so two instances never
  trade at once (`docs/RELEASE_PROCESS.md`).

## Architecture

```
 Phone (PWA) --static files--> Vercel (control_app)
     |
     | HTTPS + Bearer JWT (+ 2FA code for sensitive actions)
     v
 Render free web service (native Python runtime)
   FastAPI control_api  +  in-process bot runner
     |        |             ^               ^
     |        |             | POST /api/tick | self-ping
     |        v             |               |
     |    Kalshi API    cron-job.org    (runner itself)
     |   (demo or prod)
     v
 MongoDB Atlas M0 (users, config, journal, audit, snapshots, desired state)
 ntfy.sh <-- alerts from the runner
```

The phone never talks to Kalshi, and Kalshi keys exist only as Render environment variables. Details and data flows
(status, Stop, Start, config change, keep-awake, restart recovery) are in `docs/ARCHITECTURE.md`.

## Safety model

- **Stop** is a single authenticated tap with no 2FA. It cancels resting orders, sets kill switches and persists the
  stopped state; it keeps working if the database is down.
- **Start, live mode and settings changes** require a fresh TOTP 2FA code. Live mode also needs a typed confirmation
  and a server-side environment gate.
- **Live is never restored automatically** after a restart: the runner comes back in demo and alerts the owner.
- **Live refused without the real strategy:** while the public stand-in is installed, the API rejects live mode with
  `strategy_not_installed`.
- **Account-level risk gate** caps total exposure, per-category exposure and per-scan and per-day spend, and fails
  closed on missing or unreadable inputs.
- **Single leader lease** with signed handover and fencing, so an old and a new instance never trade together
  during a release.
- **Validated settings:** every write passes the strategy's validator plus API ceilings and is saved atomically.
- **Emergency path without the app:** suspend the Render service, or set `RUNNER_ENABLED=false`.

## The strategy is private

The trading strategy is private and installed at deploy time. This repository ships a placeholder `kalshi_bot/`
with the same interface (a generic Kalshi client plus a demo strategy that places no orders), so the app, the API and
every test run without it. The build-time overlay mechanism is described in `kalshi_bot/README.md` and
`deploy/private_overlay.md`.

## Tech stack

| Layer | Choice |
|---|---|
| API and runner | Python 3.11, FastAPI, Uvicorn, Pydantic |
| Phone app | React, Vite, installable PWA, hosted on Vercel (Hobby) |
| Database | MongoDB Atlas M0 (async PyMongo), in-memory store for tests |
| Hosting | Render free web service (native Python runtime) |
| Keep-awake and alerts | self-ping, cron-job.org, ntfy.sh, Web Push |
| Auth | single owner, Argon2 password hash, TOTP 2FA, JWT sessions, rate limiting |
| Tests | pytest, Vitest and Testing Library, Playwright |
| CI and security | GitHub Actions, semgrep, gitleaks, a denylist scan, hash-pinned dependency locks |

Everything runs on free tiers.

## Repository layout

```
control_api/   FastAPI control API + bot runner (routes/, services/, auth/, db/, tests/)
control_app/   React + Vite PWA (pages/, e2e/), deployed to Vercel
kalshi_bot/    Public stand-in bot: generic Kalshi client + demo strategy (see its README)
deploy/        Render / Vercel / Atlas / cron-job.org notes, env template, optional VM files
scripts/       Overlay installer, release gate and control, commit-message check, repo scanners
docs/          Architecture, API, deploy, release and phone guides
.github/       CI (tests, semgrep, gitleaks) and the manual release workflow
```

## Documentation

| Doc | What it covers |
|---|---|
| `docs/DEPLOY.md` | Step-by-step owner runbook: Atlas, secrets, Render, Vercel, cron-job.org, ntfy, first demo run, updating, rollback, troubleshooting |
| `docs/ARCHITECTURE.md` | System diagram and data flows |
| `docs/API.md` | Every endpoint with auth level, request, response and errors |
| `docs/PHONE.md` | Installing the PWA and what each screen does |
| `docs/RELEASE_PROCESS.md` | CI/CD, release workflow, lease handover, compatibility rules |
| `docs/SHADOW_CONTRACT.md`, `docs/ANALYTICS_CONTRACT.md`, `docs/UX_CONTRACT.md`, `docs/RELEASE_CONTRACT.md` | Design contracts for those features |
| `deploy/*.md` | Per-service setup notes (Atlas, Vercel, cron-job.org, release secrets, private overlay) |
| `CONTRIBUTING.md`, `CHANGELOG.md` | Commit conventions and release notes |

## Getting started

To deploy, follow `docs/DEPLOY.md` from the top; it begins in demo mode with no real money. Copy `deploy/env.example`
for the environment variable list. Whenever you start the bot, remember how to stop it: the app's **Stop** button, and
as a last resort suspend the service in the Render dashboard.

Local development uses the in-memory store and fakes; no cloud accounts or real keys are needed.

## Tests

```
python -m pytest -q -n auto control_api/tests
cd kalshi_bot && python -m pytest -q tests
cd control_app && npm ci && npm test && npm run build && npm run e2e
```

Tests are parallel-safe, use fakes and mocks only, and never call Kalshi, Atlas, cron-job.org or ntfy. Slower tests
are marked `slow`; skip them while iterating with `-m "not slow"`, but run everything before pushing.

## CI and releases

- **tests** workflow: Python and frontend suites, end-to-end tests and a commit-message check on every push and pull
  request. CI runs only against the stand-in.
- **semgrep** and **gitleaks** workflows scan for code and secret issues.
- **release** workflow: manual, needs owner approval and a fresh 2FA code, passes a pre-deploy safety gate, then
  hands leadership from the old instance to the new one.

## Contributing

Commits follow Conventional Commits, one logical change each, as described in `CONTRIBUTING.md`. A local git hook
checks the format; install it with `scripts/install_hooks.sh`. Never commit secrets or private strategy files.
