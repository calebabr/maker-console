# cron-job.org: the external pinger (free)

The Render free service sleeps after **15 min without inbound requests**. Two pingers keep it awake
(see `DASHBOARD_PLAN.md` v3, "Keep-awake"):

1. **Self-ping** (built in): the runner calls `RENDER_EXTERNAL_URL/healthz` every `keepalive_minutes`.
2. **External pinger** (this file): a cron-job.org job calls `POST /api/tick` every `pinger_minutes`.
   It is the backup that wakes the service if it ever slept or restarted.

Normally **you do not create the job by hand.** You give the API a cron-job.org API key, and the
backend creates/updates the job itself whenever you change `pinger_minutes` / `pinger_enabled`
on the app's **Server** page. The manual job below is only a fallback.

Facts checked 2026-10-12 against <https://cron-job.org/faq/> and <https://docs.cron-job.org/rest-api.html>:
- Free; jobs can run up to 60×/hour (every minute); no fixed job count (fair use).
- **Each request times out after 30 s.** `/api/tick` must answer quickly (it starts the pass in the
  background and returns `{ran, reason}`), because a full pass takes about a minute.
- Executions may be delayed by a few seconds (jitter). Fine for a ≤14-min interval.
- REST API: `https://api.cron-job.org/`, header `Authorization: Bearer <key>`;
  default quota **100 API requests/day** (the backend only calls it when you change settings or press Sync).

## 1. Create the account and API key (one time)

1. Go to <https://console.cron-job.org/signup> and sign up (free; no card mentioned on the FAQ — *verify on signup*).
   Confirm the e-mail.
2. Console → **Settings** → **API** → **Create API key**. Copy it once.
3. **IP restriction:** leave the key **unrestricted**. Render free has no static outbound IP of its own
   (outbound traffic uses shared per-region ranges), so an IP allow-list would break or be meaningless.
   The key can only manage your cron jobs; it can't touch Kalshi or your data.
4. Render dashboard → `kalshi-control` → **Environment** → set `CRONJOB_API_KEY` = the key → **Save**
   (the service redeploys).
5. Open the app → **Server** → confirm `pinger: configured`, then set `pinger_minutes` (default 5) and
   Save (🔐 2FA). The job id and "synced" should appear. If sync fails the error is shown there; press
   **Sync now** to retry.

## 2. Manual fallback job (only if you don't want to use the API key)

Leave `CRONJOB_API_KEY` empty and create this job in the console (**Cronjobs → Create cronjob**):

| Field | Value |
|---|---|
| Title | `kalshi-control tick` |
| URL | `https://<your-service>.onrender.com/api/tick` |
| Schedule | Every **5** minutes (any value 1–14) |
| Advanced → Request method | **POST** |
| Advanced → Headers | `X-Tick-Token: <your TICK_TOKEN>` (exact value from the Render env) |
| Advanced → Request timeout | 30 s (the maximum) |
| Notifications | On failure (optional) — e-mails you if the service is down |
| Save responses | Off (not needed) |

Then press **Test run**: the response should be HTTP 200 with JSON like `{"ran": false, "reason": "..."}`.
A 401/403 means the header value doesn't match `TICK_TOKEN`.

With a manual job, changing `pinger_minutes` in the app does **not** change the schedule; edit it in the
console instead.

## 3. Rotating the tick token

1. Generate a new value (`python -m control_api.auth.bootstrap --gen-keys`, use only `TICK_TOKEN`).
2. Render → Environment → update `TICK_TOKEN` → Save (redeploys).
3. App → Server → **Sync now** (API-managed job) — or edit the header of the manual job.
