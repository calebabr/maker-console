# End-to-end checks (Playwright, 375 px)

Two suites. Both use `@playwright/test` (a devDependency) and a 375x812 phone viewport. Neither touches Kalshi, Atlas, Render or Vercel.

    npm install
    npx playwright install chromium     # once, about 150 MB, stored outside the repo

## 1. Mock backend: `npm run e2e` (about 15 s, runs in CI)

Specs `app.e2e.js`. Starts `npm run dev:mock` on port 5199 (the fake backend in `src/services/mock.js`).
The mock is in-memory and resets on a full page load, so specs click through the nav instead of using `page.goto`.

## 2. Real API, full stack: `npm run e2e:real` (about 4 minutes, local only)

Spec `full-stack.real.js`. Starts, via `playwright.real.config.js`:

1. `python -m control_api.tools.dev_server --port 8765` (repo root): the real FastAPI app with an in-memory store, fake
   data, the runner disabled and every `KALSHI_*` variable stripped, so nothing trades. It writes throwaway owner
   credentials and a TOTP secret to `local_dev/dev_credentials.json` (git-ignored); the specs read that file and
   compute real TOTP codes (`real-helpers.js`, RFC 6238, no dependency).
2. `vite build --outDir dist-e2e` with `VITE_API_BASE_URL=http://127.0.0.1:8765`, then `vite preview` on port 5173
   (the dev server only allows CORS from `127.0.0.1:5173` / `localhost:5173`, so that port is fixed and must be free).

What it proves against the real server: login + 2FA, single-use codes (a replayed code is `2fa_invalid`), a missing
code is `2fa_required`, a settings 400 shown in the app, cells cannot be edited, Start / LIVE / arbitrage gates
(`runner_disabled`, `live_not_allowed`, typed LIVE), Stop from four pages hits the real `/api/bot/stop` with no code and
the state reads stopped with both kill-switch files on disk and an audit row, CORS, and no token or secret in
`/api/logs` or `/api/audit`.

The server accepts a TOTP step only if it is strictly newer than the last one used (window +-1), so after the first three
codes the specs wait for the next 30 s window. That is why it takes minutes. Do not run it while the dev server is
already running on port 8765 or the Vite dev server on 5173.

Environment: `E2E_PYTHON` (python executable), `E2E_API_PORT`, `E2E_OUTPUT_DIR` (put failure screenshots outside the repo).
Specs are named `*.e2e.js` / `*.real.js` because Vitest would pick up `*.spec.js`.
