# Prior (control app)

Prior is the phone app (installable PWA) for the Kalshi maker bot. It's a Vite + React 18 static site deployed to Vercel, and it talks only to the control API. It never calls Kalshi.

## Develop

```bash
npm install
cp .env.example .env.local        # set VITE_API_BASE_URL=http://localhost:8000
npm run dev                       # http://localhost:5173 (backend must allow this origin)
```

No backend? Use the mock:

```bash
npm run dev:mock                  # uses .env.mock: VITE_MOCK=1
```

In mock mode any username and password work. Any 6-digit code works except `000000`, and a code can't be used twice in a row. The password `wrong` simulates a failed login. Set `VITE_MOCK_DELAY_MS=6000` to see the "Waking up the server" banner. The mock lives in `src/services/mock.js` and is left out of production builds.

## Test and build

```bash
npm test        # vitest run (jsdom + Testing Library)
npm run build   # dist/ with service worker + manifest
npm run icons   # regenerate public/ icons (no dependencies)
```

## Deploy (Vercel)

- Set the root directory to `control_app`.
- Set the env var `VITE_API_BASE_URL=https://<your-render-service>.onrender.com`.
- Never set `VITE_MOCK` in production.
- `vercel.json` adds the SPA rewrite and security headers, including a Content-Security-Policy. Its `connect-src` allows `'self'` and `https://*.onrender.com` because the API URL is set per deploy. If the API is hosted anywhere else, add that origin to `connect-src` in `vercel.json` or every API call will be blocked.
- Add the Vercel URL to the backend's `ALLOWED_ORIGIN`.

## Layout

```
src/services/api.js      the only HTTP client (Bearer token, X-2FA-Code, 401 -> login, slow-request banner)
src/services/mock.js     dev-only fake backend (VITE_MOCK=1)
src/services/cache.js    last API data in sessionStorage for offline display
src/context/             AuthContext (login -> 2FA), StatusContext (30 s /api/status poll), DialogContext (confirm, 2FA, toasts)
src/pages/               Login, Overview, Markets, Activity, Health, Settings, Mode, Server, Logs, Arbitrage
src/components/          StatusBadge, StopButton, TwoFactorPrompt, ConfirmDialog, Card, NavBar, Header, CiBar, Sparkline...
src/utils/               theme.js (tokens -> CSS vars, dark/light), format.js, status.js
```

## Safety

- **Stop** sits in the header on every page and is the main control on Overview. It takes one tap and one confirmation, with no 2FA.
- Start, mode, settings, market pause/resume and server settings send a fresh `X-2FA-Code` each time.
- Live mode also requires typing `LIVE`.
- The token is kept in memory and `sessionStorage`, never in `localStorage` or a URL.
- `cells` is shown read-only and is never sent.
