import { defineConfig, devices } from '@playwright/test';

// End-to-end checks against the fake backend (src/services/mock.js) served by `npm run dev:mock`.
// No real API, Kalshi, Atlas or Render is touched. Run: npm run e2e  (see e2e/README.md)
const PORT = Number(process.env.E2E_PORT || 5199);

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.js', // not *.spec.js: vitest would try to run those
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: true,
  retries: 0,
  reporter: [['list']],
  outputDir: process.env.E2E_OUTPUT_DIR || 'test-results',
  use: {
    baseURL: `http://localhost:${PORT}`,
    ...devices['Pixel 5'],
    viewport: { width: 375, height: 812 },
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium-375', use: { browserName: 'chromium' } }],
  webServer: {
    command: `npm run dev:mock -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    // Set the mock-mode flags here, not only in .env.mock: `.env.*` is git-ignored, so a fresh clone (CI) has no
    // .env.mock and the app would call a real API base URL that does not exist. Process env wins over .env files in Vite.
    env: { VITE_MOCK: '1', VITE_API_BASE_URL: 'http://mock.local' },
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
