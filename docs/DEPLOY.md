# Deploy runbook (owner)

Everything runs on free tiers. Follow the steps in order. Never paste a key into chat, git or a screenshot; keep keys in a password manager.

**Safety first.** Starting the bot or going live is always reversible with **Stop**:
1. Best: the app's red **Stop** button (one tap, no 2FA).
2. Last resort: see [Emergency stop without the app](#emergency-stop-without-the-app).
3. Desktop fallback only: `stop_maker.ps1`.

Start in **demo** mode. Do not create a production Kalshi key until step 15 is done.

## Steps

1. **Push to GitHub.** Push the repo and confirm the "tests" workflow is green (Actions tab).
2. **Generate secrets** on your PC (repo root; install with `pip install --require-hashes -r control_api/requirements-dev.txt`): `python -m control_api.auth.bootstrap --gen-keys`. It prints `JWT_SECRET`, `TOTP_ENC_KEY` and `TICK_TOKEN`. Save all three in your password manager.
3. **MongoDB Atlas M0** (see `deploy/atlas.md`): create a free M0 cluster (AWS us-east-1), create a database user with readWrite, allow network access `0.0.0.0/0` (Render's free IPs change), copy the connection string as `MONGODB_URI`. Also create a **second, read-only** user (only for the local data tool, see "Local data tool" below); never reuse the Render read/write URI on your PC.
4. **Create your owner account + 2FA** from your PC. Set `MONGODB_URI` and `TOTP_ENC_KEY` in the terminal session, then run `python -m control_api.auth.bootstrap --username <YOUR_USERNAME>`. Choose a password, scan the QR code with an authenticator app, and type one code to confirm.
5. **Kalshi demo key.** Create a key on Kalshi's demo site. You get a key ID (`KALSHI_DEMO_API_KEY_ID`) and a private key (`KALSHI_DEMO_PRIVATE_KEY`). Do NOT create a production key yet.
6. **ntfy.** Install the ntfy app, subscribe to a long random topic name (`NTFY_TOPIC`). Anyone who knows the name can read alerts, so make it unguessable.
7. **Render.** New > Blueprint > pick your GitHub repo (it reads `render.yaml`, service `kalshi-control`). Enter when asked: `MONGODB_URI`, `JWT_SECRET`, `TOTP_ENC_KEY`, `TICK_TOKEN`, `KALSHI_DEMO_API_KEY_ID`, `KALSHI_DEMO_PRIVATE_KEY`, `NTFY_TOPIC`. Leave empty: `PUBLIC_URL`, `CRONJOB_API_KEY`, `KALSHI_PROD_*`, `KALSHI_LIVE_CONFIRM`. Prefilled: `MAX_ARB_CONTRACTS=25` (API ceiling for contracts per arbitrage leg). **Never set `DEV_MODE=true` on Render** (it only enables local dev CORS and is ignored on Render, but leave it unset). The build installs from the hash-pinned lock (`--require-hashes`, wheels only). When deployed, open `https://<service>.onrender.com/healthz`; it should show `{"ok":true}`.
7b. **Push notifications (VAPID keys, optional).** Needed only for notifications on your phone.
    1. On your PC run `npx web-push generate-vapid-keys` (or any tool that makes a P-256 VAPID pair). Copy the **private** key.
    2. In Render > Environment add `VAPID_PRIVATE_KEY` (secret; PEM or the raw base64url key) and `VAPID_SUBJECT` (`mailto:<YOUR_EMAIL>` or your https URL; if empty, an https `PUBLIC_URL` is used). Save (redeploys). The public key is derived and served by the API; do not put either key in git or Vercel.
    3. **iPhone acceptance test (you do this during the demo weeks).** Push on iPhone only works from the Home Screen app. In **Chrome on iPhone** use Share > **Add to Home Screen** (Safari: Share > Add to Home Screen). Open the app from that icon, log in, go to More > Alerts (or Account), tap **Enable notifications** and allow, then tap **Send test**. A notification should arrive within seconds. Then check: a daily summary at your chosen time, and that Quiet Hours holds back everyday alerts but never Stop or breaker alerts.
    3b. **Check and clear registered devices.** In the app go to More > Settings and scroll to **Push Devices**, just under Notifications. Each phone or browser you enabled shows as a push service name (for example Apple or FCM) with the time it was added. Tap **Clear All**, confirm, and every device is removed (no 2FA needed); on each phone tap **Enable notifications** again to re-register. Do this if you re-test from scratch, lose a phone, or see a device you do not recognise. Up to 10 devices can be registered.
    4. No key set = push is simply off; ntfy keeps working. To stop everything, use the app's **Stop** button; last resort, suspend the service in Render, or `stop_maker.ps1` on the desktop.
8. **Vercel.** New Project > import the repo. Root Directory `control_app`. Environment variable `VITE_API_BASE_URL` = your Render URL (no trailing slash). Never set `VITE_MOCK`. Deploy and note the Vercel URL.
   - **Set the exact API origin in the CSP (S-14).** Edit `control_app/vercel.json` and replace `https://your-api-origin.invalid` in `connect-src` with your API origin (for example `https://<service>.onrender.com`; no wildcard, no path). Check it with `node deploy/check_csp.mjs https://<service>.onrender.com`, then commit and push. Until you do, the deployed app cannot reach the API (it fails closed). Details: `deploy/vercel.md`. Same edit for a VM or custom domain.
9. **ALLOWED_ORIGIN.** In Render > Environment set `ALLOWED_ORIGIN` = the Vercel URL exactly (https, no trailing slash). Save (redeploys). A mismatch locks the app out.
10. **cron-job.org** (see `deploy/cronjob.md`): sign up, Settings > API > create an API key (leave unrestricted). Set it in Render as `CRONJOB_API_KEY`. The app creates and manages the ping job itself.
11. **Phone.** Open the Vercel URL, Add to Home Screen, log in (see `docs/PHONE.md`).
12. **Server page.** Set keep-awake 10 min and pinger 5 min and save (needs a 2FA code). Confirm a cron job ID appears and it says synced.
13. **Demo run.** Press Start (2FA), then Run once. Check Activity and Logs. Press Stop and confirm the kill switch shows on. Check ntfy alerts arrive.
13b. **Arbitrage (optional, log-only first).** In the app open More > Arbitrage and turn it on in Log only mode (2FA). It only records opportunities. Going Live needs typing `LIVE`, a fresh code, prod keys and `KALSHI_LIVE_CONFIRM`; keep it log-only until you understand the legging risk. **Stop** stops the maker and arbitrage together.
14. **Verify on demo:** the Kalshi order, fill, settlement and trades field names look right (P&L and fill quality show numbers rather than dashes once fills settle) and "cash" excludes money tied up in resting bids. Fill quality on demo reflects demo liquidity and is not representative.
15. **Watch for 24 hours:** heartbeat age stays under 15 min, the service never sleeps, Atlas storage is far below 0.5 GB.
16. **Go live (only when happy).**
    1. Create a Kalshi production key.
    2. In Render set `KALSHI_PROD_API_KEY_ID`, `KALSHI_PROD_PRIVATE_KEY` and `KALSHI_LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY`.
    3. In the app: Mode page > Live, enter a fresh 2FA code and type `LIVE`.
    4. **Stop** at any time with the red button. To back out: switch to demo and clear `KALSHI_LIVE_CONFIRM` in Render.
    5. **After every restart or redeploy you must re-arm Live** (S-21): the bot always comes back in demo and sends an urgent ntfy alert "LIVE not restored: re-arm from the phone". Go to Mode > Live again (2FA + type `LIVE`). Same for arbitrage live.

    **Preconditions from security review pass 4 (do these before step 16.2):**
    1. `control_app/vercel.json` `connect-src` is your exact Render origin and `node deploy/check_csp.mjs https://<service>.onrender.com` passes (step 8).
    2. `MONGODB_URI` has a strong, unique password (Atlas network access is `0.0.0.0/0`, so the URI is effectively a credential), and the data tool uses a separate **read-only** Atlas user (S-13).
    3. `KALSHI_PROD_*` and `KALSHI_LIVE_CONFIRM` are set in Render only when you mean to go live.
    4. ntfy alerts are on, and you tested both the Stop button and `RUNNER_ENABLED=false` on the deployed demo.
    5. You accept the residual risks: someone with Atlas write access could resume **demo** or log-only trading at a restart (you get an alert); a stolen login token can force extra passes (not more often than every 60 s), but only within the limits you armed.
    6. You have read your strategy's validation (kept in the private repo): run demo first, then live at a small size you can afford to lose entirely in a day, and judge by the **fill quality** block (Overview and Markets) before P&L. See `docs/FEATURES.md`, "Before you go live".
17. **Maintenance.**
    - Pushes to GitHub auto-deploy. To roll back, redeploy an earlier deploy in Render (Events tab).
    - After rotating `TICK_TOKEN`, press Sync on the Server page.
    - **Adding or upgrading a Python package:** edit `control_api/requirements.in`, regenerate both locks with the command in the header of the `.in` files (needs `uv`; Python 3.11 target), commit `requirements.txt` and `requirements-dev.txt`. Never hand-edit them. Installs always use `pip install --require-hashes --only-binary :all: --no-deps -r ...`.
    - Atlas pauses after 30 days inactive: log in and Resume.

## Emergency stop without the app

Use if the app, Vercel or your phone login is unavailable.
- **Render dashboard > `kalshi-control` > Suspend service.** Nothing runs, so no orders are placed. Resting orders already on Kalshi remain until you cancel them in Kalshi's own site; do that too.
- **Also available (S-10 fixed 2026-10-13):** set `RUNNER_ENABLED=false` in Render > Environment and save. It is now a hard stop: tick, run-once, Start and arbitrage are refused, and the runner runs Stop once at startup (kill switches plus cancelling its resting `mm-` orders). Best used together with Suspend service and cancelling on Kalshi.
- Resume later with Resume service (and set `RUNNER_ENABLED=true`), then Start from the app.
- Desktop fallback only: `stop_maker.ps1`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| App says "Waking up the server" | Render free was asleep; wait up to a minute. If it happens often, check the Server page pinger/keep-awake and cron-job.org job. |
| Login fails with a network/CORS error | `ALLOWED_ORIGIN` does not exactly match the Vercel URL (step 9). |
| "2fa_invalid" right after login | Each code works once and login used the current one. Wait for the next code (up to 30 s). |
| Account locked (429) | 5 wrong codes lock for 15 min. Wait. Stop still works with a valid session. |
| Lost phone | End every session: from another logged-in device use **Server > Log out all sessions** (2FA; API `POST /api/auth/logout-all`), or from the PC with `MONGODB_URI` and `TOTP_ENC_KEY` set: `python -m control_api.auth.bootstrap --username <YOU> --revoke-sessions` (keeps password and 2FA; live within about 10 s). If the 2FA device is gone too: `--reset-2fa` (also logs out everything). Then press Stop if the bot should not run. |
| Login says "session expired" (401 `session_expired`) | All sessions were logged out or credentials were reset; log in again. |
| "Server can't reach the database" banner / 503 `store_unavailable` | Atlas paused or unreachable. Every endpoint except Stop fails closed. Resume it in Atlas. Stop still works. |
| Start/arbitrage refused with `runner_disabled` | `RUNNER_ENABLED=false` is set in Render; set it back to `true`. |
| Start refused with `stop_in_progress` / `stopped_during_start` | A Stop was running or happened during Start (Stop always wins); wait a few seconds and Start again. |
| App cannot reach the API after a fresh Vercel deploy | The CSP in `control_app/vercel.json` still has the placeholder origin (step 8). |
| Start refused | Check that the store is up (Start fails safe without it). |
| Live mode refused ("live_not_allowed") | Prod key or `KALSHI_LIVE_CONFIRM` missing in Render. |
| Heartbeat age grows | Service asleep or crashed; check Render logs and the cron-job.org execution history. |
| Render region has no free plan | Edit `region` in `render.yaml` to `ohio` or `oregon`. |

Not yet verified: whether cron-job.org or Atlas ask for a card at signup; Atlas M0 connection limits and backups.

## Optional: analytics edge benchmark file

The Analytics page can compare each cell with an optional private benchmark file (`cell_expectations.json`, also the source of `expected_roi` and the fill-quality baseline). Nothing to do for the demo. The file is never in this repo; supply it to Render as a **secret file**: in Render > Environment > Secret Files add `cell_expectations.json`, then set the optional env var `CELL_EXPECTATIONS_PATH=/etc/secrets/cell_expectations.json`. If the file is missing the page still works; there is simply no comparison ("expectations not loaded"). Never put this file in a public repo.

## Local data tool (read-only) and OneDrive warning

`python -m control_api.tools.pull_data` copies data from Atlas into git-ignored `local_data/` for analysis (project skill `update-local-data`). It reads `MONGODB_URI` from `.env.local`.
- Use a **separate read-only Atlas user**, never the Render read/write URI (S-12/S-13).
- **This repo lives in OneDrive, which uploads every file, `.env.local` and `local_data/` included.** Keep `.env.local` outside the synced folder (set `MONGODB_URI` in the terminal session instead), or make sure OneDrive excludes it; and move or exclude `local_data/` too.
- `--seed-demo` runs it with fake data and needs no Atlas.

### Scheduled "Update Local Data" (optional, Windows)

`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\schedule_pull_data.ps1 -Register` creates a daily Task Scheduler job (default 07:00, option `-Time 06:30`) that runs `python -m control_api.tools.pull_data --days 180` from the repo folder and appends output to `local_data\pull.log`. `-Status` shows state, last result and the log tail; `-Unregister` removes it. Details:
- It runs **only while you are logged in** (no password is stored) and catches up after a missed run at the next logon or wake.
- It is read-only toward Atlas and uses the read-only URI in `.env.local`.
- Each run keeps `local_data/history/` (append-only CSVs) and dated snapshots in `local_data/archive/` (newest 90).
- **OneDrive caveat (S-13):** the task runs code from wherever the repo sits and writes `local_data/` there. In a OneDrive folder that uploads your data (including `state.json`, which holds the real strategy cells) and `.env.local`. Recommended: clone the repo outside OneDrive (for example `C:\dev\trading`), keep `.env.local` there, and register the task from that copy.
- Not registered by default; registering it is your decision. Nothing here can start the bot; to stop trading use the app's Stop button (last resort: suspend the service in Render, or `stop_maker.ps1` for the desktop fallback).
