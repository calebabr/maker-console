import { defineConfig, devices } from '@playwright/test';

// Full-stack end-to-end: the REAL control API (control_api/tools/dev_server.py: in-memory store, fake data,
// runner disabled, every KALSHI_* variable stripped, nothing trades) + the production build of the PWA served by
// `vite preview`. No Atlas, Kalshi, Render or Vercel. Run: npm run e2e:real  (see e2e/README.md)
//
// The dev server only allows CORS from http://127.0.0.1:5173 / http://localhost:5173, so the PWA must use port 5173.
const API_PORT = Number(process.env.E2E_API_PORT || 8765);
const WEB_PORT = 5173;
const REPO = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const PY = process.env.E2E_PYTHON || 'python';

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.real.js',
  timeout: 120_000,            // some steps wait for the next 30 s TOTP window (single-use codes)
  expect: { timeout: 10_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  outputDir: process.env.E2E_OUTPUT_DIR || 'test-results',
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    ...devices['Pixel 5'],
    viewport: { width: 375, height: 812 },
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'real-api-375', use: { browserName: 'chromium' } }],
  webServer: [
    {
      // fresh owner credentials + TOTP secret are written to local_dev/dev_credentials.json on every start
      command: `${PY} -m control_api.tools.dev_server --port ${API_PORT}`,
      cwd: REPO,
      url: `http://127.0.0.1:${API_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 90_000,
    },
    {
      command: `npx vite build --outDir dist-e2e --emptyOutDir && npx vite preview --outDir dist-e2e --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      env: { VITE_API_BASE_URL: `http://127.0.0.1:${API_PORT}`, VITE_MOCK: '' },
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
