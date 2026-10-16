# Sprint 1 contract: release process + CI

_Written 2026-10-16 by the lead. This is the single shared source for every agent in Sprint 1. The design and the owner decisions (A2 signed handover, B2 manual approved release workflow, C = 120 s shutdown delay) are in `docs/RELEASE_PROCESS.md`; this file fixes the interfaces. If something here is unclear, build the simplest thing that satisfies it and note the choice in your report. Never change this file; ask the lead._

Scope: R1-R6, C1-C7, S-25..S-27. Demo only (Kalshi demo keys); no real money. **Rules that outrank everything:** Stop works on any instance with no lease, no 2FA, no Atlas and no network; Stop beats the lease; a stopped bot stays stopped across deploys. Fail safe: if the lease cannot be confirmed, no orders. Never edit the strategy (`kalshi_bot/maker_strategy.py`, `cells`), quoting, sizing or order-building code; the changes here only decide WHETHER the runner may call it. No secrets in git, logs, responses or the DB. Agents never commit.

## File ownership (no overlaps)

| Agent | Owns |
|---|---|
| Backend-Release (Opus medium) | `control_api/runner.py`, `control_api/main.py` (lifespan, healthz), `control_api/settings.py`, NEW `control_api/version.py`, `services/lease.py`, `services/handover.py`, `routes/release.py`, `routes/deploy_status.py`, their tests |
| Database (Sonnet medium) | `control_api/db/*` (store.py, mongo.py, schemas.py, seed.py), store tests, `control_api/tests/fixtures/schema_v1/` |
| Infra (Sonnet medium) | `.github/**`, `render.yaml`, `scripts/**` (except `overlay_private.py`), `.githooks/**`, `CHANGELOG.md`, `CONTRIBUTING.md`, `deploy/**` env template |
| Frontend (Sonnet medium, wave 2) | `control_app/**` |
| Tests (Sonnet medium, wave 3) | gap tests only, plus the single full run |
| Docs (Sonnet low, wave 4) | `docs/**` except this file |

## 1. Store additions (Database implements; Backend calls these exact signatures)

All on the `Store` ABC with a `MemoryStore` and a `MongoStore` implementation. Times are timezone-aware UTC `datetime`. Every method takes an optional `now: datetime | None = None` (tests inject the clock; production passes nothing).

```
async def acquire_lease(self, holder: str, version: str, ttl_s: float, *, now=None) -> dict
    # atomic. Succeeds if there is no lease, it is expired, or holder already owns it (then it is a renew).
    # returns {"acquired": bool, "generation": int, "holder": str, "expires_at": datetime}
    # generation is a monotonically increasing int; it increases ONLY when the holder changes (or a new lease is created).
async def renew_lease(self, holder: str, generation: int, ttl_s: float, *, now=None) -> bool
    # True only if holder AND generation still match and the lease has not been taken by someone else.
async def release_lease(self, holder: str, generation: int, *, handover: dict | None = None, now=None) -> bool
    # expires the lease immediately (expires_at = now) if holder+generation match; stores `handover` on the doc.
async def get_lease(self, *, now=None) -> dict | None
    # {"holder","version","generation","expires_at","handover": dict|None}; None if never created.
async def consume_handover(self, nonce: str, *, now=None) -> bool
    # atomic single use: True the first time a handover nonce is consumed, False afterwards (replay).
async def get_schema_version(self) -> int | None
async def set_schema_version(self, version: int) -> None
async def save_pending_release(self, doc: dict) -> dict      # doc: {version, commit, summary, ci}; returns doc with id, status "pending", created_at, expires_at (now+30 min)
async def get_pending_release(self, release_id: str) -> dict | None
async def latest_pending_release(self) -> dict | None
async def resolve_pending_release(self, release_id: str, decision: str, by: str, *, now=None) -> dict | None
    # decision in {"approved","rejected"}; only from "pending" and not expired; else returns None. Records by + at.
```

- Mongo collections: `runner_lease` (single doc `_id: "leader"`; acquire/renew via `find_one_and_update` with a filter on holder/expiry, never read-modify-write), `handover_nonces` (unique index on `nonce`, TTL 1 h), `meta` (`_id: "schema"`), `releases` (TTL 30 days). Add to `ensure_indexes`.
- `SCHEMA_VERSION = 1` for everything that exists today. New docs/fields are additive only.
- Compatibility fixtures: `control_api/tests/fixtures/schema_v1/` holds small JSON docs of every collection as written by the current release; a test loads them into each store and checks the new code reads them.
- S-26 (Database + Backend): `refresh_queue_stats` and fill quality must use the existing `fields=` projection instead of reading up to 100,000 unprojected fills.

## 2. Runner behaviour (Backend-Release)

- Constants (settings, env-overridable, defaults): `LEASE_TTL_S = 60`, `LEASE_RENEW_S = 20`, `PASS_BUDGET_S = 25`, `SHUTDOWN_DELAY_S = 120` (Render side), `HANDOVER_MAX_AGE_S = 300`.
- `instance_id`: random per process (`uuid4().hex`) + hostname for logs.
- Roles: **standby** (serves API and the app, never trades) and **leader** (holds the lease). Every instance starts as standby and tries to acquire the lease on start and every `LEASE_RENEW_S`. A failed or unreachable store means standby (no trading). `state` exposes `role`, `lease_generation`, `lease_expires_at`.
- **Fencing:** `_fence_ok()` = role is leader, the lease was renewed within `LEASE_TTL_S` by the local clock, and the generation still matches the last renew result. Check it immediately before (a) any maker pass sends orders, (b) any arbitrage set executes, (c) any non-Stop cancel. If false: send nothing, journal `fence_blocked`, and keep standby. **Stop and the persisted stop never check the fence.**
- **Stop beats lease:** a persisted `stopped` state must be honoured by a new leader on start (it stays stopped). Existing `rehydrate` rules stay.
- **SIGTERM / lifespan shutdown (R2):** stop scheduling passes; let a running pass reach the end of its current order step (never cut mid-order; hard `PASS_BUDGET_S`); persist state; if still leader, write a handover (below) and `release_lease`. Total must finish well inside 120 s. If anything fails, still release the lease best-effort and exit.
- **Signed handover (R3, decision A2):** only for a planned shutdown while running in LIVE. Token = `{v:1, nonce, from_instance, mode, desired, config_hash, issued_at, stop_seq}` + `sig = HMAC-SHA256(HANDOVER_HMAC_KEY, canonical-json)`; stored on the lease doc. The new leader keeps LIVE only if: key present, signature valid (constant-time compare), `issued_at` < `HANDOVER_MAX_AGE_S` ago, `config_hash` equals the hash of the config it loaded, no Stop since (`stop_seq` unchanged), `consume_handover(nonce)` returned True (single use), and the stand-in gate / `live_allowed` still pass. Anything else: drop to demo exactly as today (S-21). `HANDOVER_HMAC_KEY` missing or shorter than 32 bytes disables handover (always drop to demo). Never log the key or the token.
- **R4 version handshake:** NEW `control_api/version.py`: `API_VERSION` (string, from env `APP_VERSION` or `0.0.0-dev`), `MIN_APP_VERSION`, `SCHEMA_VERSION`. `/healthz` (unchanged shape plus fields) and `/api/status` add `api_version`, `min_app_version`, `schema_version`, `role`, `lease_generation`. On start the runner reads the stored schema version: if it is greater than its own `SCHEMA_VERSION`, it stays standby and alerts (newer data written by a newer release); if lower or missing it writes its own.
- **S-27:** move the two startup cache refreshes out of `rehydrate` into a background task (so the API and Stop come up sooner). **S-25 remainder:** run the queue-position, fill-quality and fills-sync collection in a dedicated small executor with a short client (as close time and analytics already do), not the default executor that Stop's cancel uses. Stop latency tests must still pass.
- Do not call the Kalshi API from lease code. Lease code only talks to the store, with `store_call` timeouts.

## 3. New endpoints (Backend-Release)

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /api/deploy/status` | header `X-Deploy-Status-Key` (env `DEPLOY_STATUS_KEY`, constant-time compare, rate-limited, 401 when unset) | returns exactly `{version, schema_version, lease_holder, generation, role, running, stopped, mode, arb_executing, healthy}` and nothing else |
| `POST /api/release/pending` | header `X-Release-Token` (env `RELEASE_TOKEN`) | the workflow registers `{version, commit, summary, ci}`; returns `{id, status}` |
| `GET /api/release/pending/{id}` | `X-Release-Token` | the workflow polls `{status}` (pending/approved/rejected/expired) |
| `GET /api/release/pending` | login | the app shows the latest pending release |
| `POST /api/release/pending/{id}/approve` | **fresh 2FA** (like Start) | approve; audited; agents can never call it |
| `POST /api/release/pending/{id}/reject` | login | reject; audited |

All audited; no secrets in responses or logs; input size limits; reuse the existing auth deps and rate limiter. 404 (not 401) when `RELEASE_TOKEN` is unset so the feature is invisible.

## 4. Release pipeline and CI (Infra)

- **`render.yaml` (B2/C):** `autoDeployTrigger: off`; `maxShutdownDelaySeconds: 120`; new env names only (no values): `APP_VERSION`, `HANDOVER_HMAC_KEY`, `DEPLOY_STATUS_KEY`, `RELEASE_TOKEN`. Keep everything else, including the overlay build script.
- **`.github/workflows/release.yml`** (`workflow_dispatch`, input: commit or tag): 1) confirm CI is green on that exact commit; 2) run `scripts/release_gate.py` against `GET /api/deploy/status` (rules in `RELEASE_PROCESS.md` section 6: CI green, running server healthy, no arbitrage set executing, handover-capable version else the bot must be stopped); 3) `POST /api/release/pending`, then poll until approved/rejected/expired (30 min timeout, no deploy on anything but approved); 4) backend deploy hook, then verify (`/healthz` new version, new leader, heartbeat); 5) frontend deploy hook, then verify; 6) ntfy "Release vX healthy"; 7) tag `vYYYY.MM.DD-n` and add notes from `scripts/changelog.py`. Secrets as GitHub secrets (`RENDER_DEPLOY_HOOK`, `VERCEL_DEPLOY_HOOK`, `DEPLOY_STATUS_KEY`, `RELEASE_TOKEN`, `NTFY_TOPIC`, `API_BASE_URL`); read-only permissions; actions pinned by SHA; never `pull_request_target`. The workflow cannot be run end to end until the owner has a deploy; build it and unit-test the gate and changelog scripts.
- **C1:** make `tests.yml` green on every push. The python job fails only on Linux (root cause unknown; see `docs/TASKS.md` backlog and the earlier agent reports); diagnose by reproducing on Linux (the Dockerfile or WSL/`act` if available), fix the real cause, never weaken or skip tests. Also fix the e2e job.
- **C2:** gitleaks already present; add the commit hook step to `.githooks` and keep the pinned checksum. **C3:** `semgrep.yml` (OSS rulesets for Python + JS, required check, triage baseline file). **C4:** `.github/dependabot.yml` (pip, npm, github-actions) plus `docs`-agent-owned monthly routine. **C5:** `scripts/changelog.py` generating release notes from conventional commits (groups by type), used by `release.yml`. **C6:** `.github/pull_request_template.md` and `CODEOWNERS` with a security-review checklist for strategy/orders, Stop/auth/security, schema and release changes. **C7:** `.githooks/commit-msg` plus the same regex check in CI on pushed commits: `^(feat|fix|perf|refactor|security|test|docs|ci|chore)(\((app|api|bot|db|auth|deploy|tasks|research)\))?!?: .{1,72}$`.
- **Cells check:** the public repo has example cells only; do NOT add a cells-unchanged job here (the real cells are guarded by a private test).
- CI must not need any private secret and must run only against the stand-in.

## 5. App (Frontend, wave 2, after the API exists)

- Server page: show role (leader/standby), lease holder, generation, API and schema version, and a Pending Release card (version, commit, summary, CI result) with **Approve** (fresh 2FA, reuse the existing 2FA confirm component) and **Reject**. Hidden when there is no pending release or the endpoint answers 404.
- Version handshake: if the app version is older than `min_app_version`, show a dismissible-by-reopening banner "Update available, reopen the app"; keep showing data; **Stop always works**.
- Add the endpoints to the mock API with realistic data (standby/leader, a pending release). Phone width, 3 themes, no horizontal overflow (the phone-fit e2e must still pass). No strategy values anywhere.

## 6. Test scenarios (Tests agent builds the gaps; backend agents write their own unit tests)

1. Two instances acquire at once: exactly one wins (both stores).
2. Holder renews; a stale generation cannot renew; lease expiry after TTL lets the other instance take it with a higher generation.
3. A killed leader (no release): standby takes over after expiry; no overlap window.
4. Fencing: a pass whose lease was lost mid-way sends no orders; same for arbitrage and non-Stop cancel.
5. Atlas flapping / store timeouts: nobody trades; Stop still works.
6. Clock jump forward/back: no double leader.
7. SIGTERM mid-pass: pass finishes its order step, state persisted, lease released, handover written, within budget.
8. Handover in demo and in LIVE; valid token keeps LIVE; expired, wrong config hash, Stop since, replay (second consume), bad signature, missing key: all drop to demo.
9. Stop during handover always ends stopped; a persisted stop survives any number of restarts/handovers.
10. Standby never trades and `/api/status` says so.
11. Schema version newer than ours: standby + alert; older/missing: written.
12. Old data (`schema_v1` fixtures) read by new code; old app requests against the new API still answer.
13. Deploy-status key: wrong/missing key 401, rate limit, exact fields only, no secrets.
14. Release endpoints: token required, approve needs fresh 2FA, agents/no-2FA cannot approve, expiry after 30 min, 404 when unset.
15. `release_gate.py` truth table (each refusal reason) and `changelog.py` grouping.
16. Commit-msg hook accepts/rejects the regex cases.
17. S-25/S-26/S-27: Stop latency unchanged under the new executor; projection used; startup does not block on cache refresh.
18. Frontend: Server page states, Approve with 2FA, banner, Stop unaffected, phone-fit e2e.

## 7. Done means

Each agent: own tests green (`python -m pytest -q -n auto -m "not slow" <files>` / `npm test -- <files>`), no strategy values, `bash scripts/scan_tree.sh` clean after `python tools/make_denylist.py` in the private root, a short report in the private repo's `docs/agent-reports/2026-10-16-sprint1-<area>.md`, and a 3-line reply. The lead runs the full suites (including the overlaid copy), gitleaks and the push.
