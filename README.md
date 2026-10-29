# Maker Console

This is my personal research project: a way to take trading strategies from the academic literature, implement and
backtest them, test them on Kalshi's demo exchange with mathematical analysis, and only then risk real money. I built
it because I'm interested in probabilistic modeling under uncertainty and in code, and I wanted to see how far careful
AI-assisted development could take a project like this. A FastAPI control API runs the bot in-process, and a React
PWA lets me start, stop and supervise it from my phone. I designed the infrastructure to cost nothing beyond funding
the trading account.

It runs in demo mode (Kalshi's demo environment, no real money) unless I deliberately arm live mode.

**Project status:** running on Kalshi's demo exchange in named evaluation periods; live trading is not enabled.

## Why this project exists

I want to implement strategies from the published literature and find out, with statistics, whether they work before
any real money is involved. My workflow is deliberately slow and honest:

1. **Read and Implement.** Pick a strategy or an effect from the literature and write it down as code.
2. **Backtest.** Write down the hypotheses and pass/fail rules before looking at outcomes, split
   the data into discovery, holdout and forward sets, cluster by event day, correct for multiple tests, and report
   negative results plainly. An independent verifier recomputes the headline numbers.
3. **Run on Demo Data.** The bot trades Kalshi's demo exchange for weeks in named evaluation periods. Demo shows
   that the machinery works (orders, caps, fills, settlement, reconciliation); it does not prove returns, because
   demo fills are not real queue fills.
4. **Estimate Before Risking Real Money.** A Shadow mode records what the strategy would have quoted on real public
   market data, with no orders and no credentials, and scores it with real settlements under pessimistic and
   optimistic queue assumptions.
5. **Go Live.** Live mode needs fresh 2FA, a typed confirmation and a server-side gate. The first
   live phase is a small, supervised execution test with the expectations written down in advance.

## Built to cost nothing

My constraint was to spend as little as possible apart from funding the accounts. Every layer runs on a free tier,
and the design bends around the limits of each:

| Need | Free-tier choice | What the design does about its limits |
|---|---|---|
| API and bot runner | Render free web service | Sleeps after 15 minutes idle, so a self-ping and an external cron job keep it awake (every 14 minutes or less); the runner restores its state from the database after every restart |
| Phone app | Vercel Hobby | Static files only; the app never talks to Kalshi |
| Database | MongoDB Atlas M0 (512 MB) | TTL indexes expire old rows to stay under the size limit; Stop does not depend on it |
| Scheduler and keep-awake | cron-job.org | One job calls the tick endpoint with a shared secret |
| Alerts | ntfy.sh and Web Push | Fire-and-forget, never blocks a pass or Stop |
| CI and releases | GitHub Actions | A manual release workflow that needs my approval and a fresh 2FA code |
| Market-data recorder | Oracle Cloud Always Free VM (1 GB) | A small timer-driven recorder of public market data for offline research; it holds no trading keys |

The only money in the system is the funding of the Kalshi account.

## What it does

- **Run and supervise the bot from a phone.** Start, Stop, mode (demo or live), settings, logs, activity and account
  state, all in an installable PWA (`docs/PHONE.md`).
- **Stop always works.** One tap, no 2FA, and it does not depend on the database, the exchange or the network.
- **Alerts** to the phone through ntfy.sh and Web Push for fills, errors, restarts and safety events.
- **Analytics and health pages:** fills, quote and fill quality, account snapshots, benchmark comparison and
  per-period results.
- **Shadow mode:** estimates what the strategy would have quoted on public market data without placing orders
  (`docs/SHADOW_CONTRACT.md`).
- **Demo periods:** named evaluation windows with cash flows tracked per period, so results are never mixed across
  versions of the system.
- **Arbitrage scanner** with a log-only default and a gated demo-execute path.
- **Zero-downtime updates:** a manual release workflow I approve with fresh 2FA, with a lease handover so two instances never
  trade at once (`docs/RELEASE_PROCESS.md`).

## Architecture

```
 Phone (PWA) --static files--> Vercel (control_app)
     |
     | HTTPS + Bearer JWT (+ fresh 2FA code for sensitive actions)
     v
 Render free web service (native Python runtime)                GitHub Actions
   FastAPI control_api  +  in-process bot runner  <-- release -- (CI, manual
     |        |    |  ^            ^                              release workflow)
     |        |    |  | lease      | POST /api/tick (shared secret)
     |        |    |  | (Atlas)    |
     |        |    |  v            |
     |        |    +--> Kalshi API       cron-job.org + self-ping
     |        |        (demo or prod)         (keep-awake)
     |        v
     |   MongoDB Atlas M0: users, config versions, journal, audit, snapshots,
     |   desired state, shadow episodes, periods, lease
     v
 ntfy.sh / Web Push <-- alerts from the runner

 Oracle Always Free VM (separate, keyless): timer-driven recorder of public market data -> offline research
```

**Components**

- **`control_app` (Vercel).** A mobile-first React + Vite PWA. It talks only to the control API. The header always
  shows the mode and the Stop button.
- **`control_api` (Render).** A FastAPI service with single-user auth (password, TOTP 2FA, short-lived JWTs) and
  the bot runner in the same process. The runner executes a pass on a timer (default 10 minutes) and on external
  ticks, holds a single-leader lease, and writes every action to the journal and audit log.
- **`kalshi_bot`.** The trading code, called by the runner. This repository ships a stand-in with the same
  interface; the real strategy is installed at deploy time.
- **Atlas.** Stores configuration versions (every settings change is versioned and undoable), the journal, audit
  trail, account snapshots, shadow episodes, periods and cash flows, the desired state and the lease document.
- **Keep-awake.** Render sleeps when idle, so a self-ping plus a cron-job.org job call `POST /api/tick`. Both
  intervals stay under the 15-minute sleep limit.
- **Release path.** CI runs the tests and scanners; the release workflow is manual, needs my approval and a
  fresh 2FA code, passes a pre-deploy safety gate, and hands leadership from the old instance to the new one with
  a signed token and fencing so only one instance ever trades.
- **Safety wiring.** Stop sets in-memory flags first, then cancels orders, then persists; it uses its own executor so
  a slow database or exchange cannot delay it.

Data flows (status, Stop, Start, config change, keep-awake, restart recovery) are described step by step in
`docs/ARCHITECTURE.md`.

## API endpoints

All routes live under `/api` and need a signed-in session (Bearer JWT) unless noted. **Fresh 2FA** means a TOTP code
verified within the last few minutes. Request and response details for the periods and cash-flow routes are in `docs/API.md`; the others are described by the code in `control_api/routes/`.

**Auth** (`/api/auth`)

| Method and path | Purpose |
|---|---|
| `POST /login`, `POST /2fa` | Password step, then TOTP step, returning an access token |
| `POST /logout`, `POST /logout-all` | End this session, or every session |
| `GET /me` | Current user and session info |

**Bot control**

| Method and path | Auth | Purpose |
|---|---|---|
| `GET /status` | session | Runner state, account, rolling P&L, today's P&L, recent passes, limits |
| `POST /bot/stop` | session, **no 2FA** | Stop the bot and the arbitrage scanner; always answers |
| `POST /bot/start` | fresh 2FA | Start trading (demo restored on restart; live is never restored) |
| `POST /bot/run-once` | session | Run one pass now |
| `PUT /mode` | fresh 2FA | Switch demo or live (live also needs a typed confirmation and a server gate) |
| `POST /tick` | `X-Tick-Token` | External keep-awake and scheduler tick (no session) |
| `POST /monitor/run` | session | Run the periodic health check |

**Settings**

| Method and path | Auth | Purpose |
|---|---|---|
| `GET /config`, `PUT /config` | session / fresh 2FA | Read, or validate and atomically write, the bot settings |
| `GET /config/versions`, `POST /config/undo` | session / fresh 2FA | List past versions, restore the previous one |
| `GET /markets`, `PUT /markets/{series}` | session / fresh 2FA | Per-series monitoring and pause or resume |
| `GET /cloud`, `PUT /cloud` | session / fresh 2FA | Cloud-side settings (keep-awake, Shadow, alerts timing) |
| `POST /cloud/pinger/sync` | fresh 2FA | Sync the cron-job.org job |
| `GET /alerts/settings`, `PUT /alerts/settings` | session / fresh 2FA | Alert policy |

**Arbitrage**

| Method and path | Auth | Purpose |
|---|---|---|
| `GET /arb`, `PUT /arb` | session / fresh 2FA | Scanner state and settings (log-only, demo-execute, caps) |
| `GET /arb/opportunities` | session | Recent opportunities and what the risk gate did with each |

**Analytics, periods and money**

| Method and path | Purpose |
|---|---|
| `GET /analytics/summary`, `/analytics/trades`, `/analytics/my-trades`, `/analytics/trades.csv` | Results, fills and fill quality; CSV export |
| `GET`/`PUT /trades/{fill_id}/note` | Per-trade notes |
| `GET /periods`, `POST /periods` (fresh 2FA), `GET /periods/{id}/summary` | Named evaluation periods and their summaries |
| `POST /cash-flows`, `DELETE /cash-flows/{id}` (fresh 2FA) | Record deposits and withdrawals beside the results |
| `GET /pnl/by-source` | Realized P&L split by strategy |
| `GET /account/positions` | Read-only view of open positions and resting orders |

**Shadow, activity and health**

| Method and path | Purpose |
|---|---|
| `GET /shadow/summary`, `GET /shadow/episodes` | Shadow estimate and its episodes (period-aware) |
| `GET /activity`, `/history`, `/since` | Time-ordered activity feed, history, "since you last checked" |
| `GET /health`, `/logs`, `/audit` | Health, redacted logs, audit trail |
| `GET /strategies`, `/changelog` | Strategy registry for labels and release notes |

**Alerts and push**

| Method and path | Purpose |
|---|---|
| `GET /alerts` | Alert history |
| `GET /push/public-key`, `POST`/`DELETE /push/subscribe`, `GET`/`DELETE /push/subscriptions`, `POST /push/test` | Web Push device management |

**Release and system**

| Method and path | Auth | Purpose |
|---|---|---|
| `POST /release/pending`, `GET /release/pending/{id}` | `X-Release-Token` (CI) | CI registers a pending release and polls its status |
| `GET /release/pending` | session | I see the latest pending release |
| `POST /release/pending/{id}/approve`, `/reject` | fresh 2FA | My approval for a release |
| `GET /deploy/status` | `X-Deploy-Status-Key` | Which version is running and its handover state (used by the release workflow) |
| `GET /healthz` (no `/api`) | none | Liveness, version handshake and lease role; no secrets |

## Safety model

- **Stop** is a single authenticated tap with no 2FA. It cancels resting orders, sets kill switches and persists the
  stopped state; it keeps working if the database is down.
- **Start, live mode and settings changes** require a fresh TOTP 2FA code. Live mode also needs a typed confirmation
  and a server-side environment gate.
- **Live is never restored automatically** after a restart: the runner comes back in demo and alerts me.
- **Live refused without the real strategy:** while the public stand-in is installed, the API rejects live mode with
  `strategy_not_installed`.
- **Account-level risk gate** caps total exposure, per-category exposure and per-scan and per-day spend, and fails
  closed on missing or unreadable inputs.
- **Single leader lease** with signed handover and fencing, so an old and a new instance never trade together
  during a release.
- **Validated settings:** every write passes the strategy's validator plus API ceilings and is saved atomically.
- **Secrets** live only in Render environment variables, never in git, logs, the database or API responses.
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
| Auth | single user, Argon2 password hash, TOTP 2FA, JWT sessions, rate limiting |
| Tests | pytest, Vitest and Testing Library, Playwright |
| CI and security | GitHub Actions, semgrep, gitleaks, a denylist scan, hash-pinned dependency locks |
| Research recorder | systemd timer on an Oracle Always Free VM |

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
| `docs/DEPLOY.md` | Step-by-step runbook: Atlas, secrets, Render, Vercel, cron-job.org, ntfy, first demo run, updating, rollback, troubleshooting |
| `docs/ARCHITECTURE.md` | System diagram and data flows |
| `docs/API.md` | Request and response details for the periods and cash-flow endpoints; the endpoint list above covers the rest |
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
- **release** workflow: manual, needs my approval and a fresh 2FA code, passes a pre-deploy safety gate, then
  hands leadership from the old instance to the new one.

## Contributing

Commits follow Conventional Commits, one logical change each, as described in `CONTRIBUTING.md`. A local git hook
checks the format; install it with `scripts/install_hooks.sh`. Never commit secrets or private strategy files.
