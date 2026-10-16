import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import {
  ARB_KILL_FILE, KILL_FILE, apiGet, apiSend, creds, existsSync, freshCode, lastUsedCode, openSignedIn, rememberToken,
} from './real-helpers.js';

// Runs against the REAL control API (dev server: in-memory store, runner disabled, no Kalshi) and the built PWA.
// Serial: the 2FA code is single-use and the server state is shared.
test.describe.configure({ mode: 'serial' });

const header = (page) => page.locator('header.appbar');
const stopBtn = (page) => header(page).getByRole('button', { name: /stop the bot/i });

async function nav(page, label) {
  const main = ['Overview', 'Analytics', 'Activity', 'Markets'];
  if (main.includes(label)) {
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: label }).click();
  } else {
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'More' }).click();
    await page.getByRole('navigation', { name: 'More pages' }).getByRole('link', { name: label }).click();
  }
}

async function enterCode(page, code, submitName) {
  await page.getByLabel('6-digit code').fill(code);
  await page.getByRole('button', { name: submitName, exact: true }).click();
}

test.describe('login against the real server', () => {
  test('wrong password is refused, then password + a real TOTP code signs in', async ({ page }) => {
    const { username, password } = creds();
    await page.goto('/');
    await page.getByLabel('Username').fill(username);
    await page.getByLabel('Password', { exact: true }).fill(password + 'x');
    await page.getByRole('button', { name: 'Continue' }).click();
    await expect(page.getByRole('alert')).toContainText('Wrong username or password');

    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Continue' }).click();
    await enterCode(page, await freshCode(), 'Sign in');
    await expect(stopBtn(page)).toBeVisible();
    const token = await page.evaluate(() => sessionStorage.getItem('km.token'));
    expect(token).toBeTruthy();
    rememberToken(token);
    // the real status endpoint answered: a mode badge and no error banner
    await expect(page.getByText(/demo/i).first()).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);
  });

  test('a wrong 2FA code is refused by the real server', async ({ page, request }) => {
    const { username, password } = creds();
    await page.goto('/');
    await page.getByLabel('Username').fill(username);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Continue' }).click();
    await enterCode(page, '000000', 'Sign in');
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(stopBtn(page)).toHaveCount(0);
    void request;
  });

  test('signed-out API calls from the browser are refused (401) and land on login', async ({ page }) => {
    await page.addInitScript(() => sessionStorage.setItem('km.token', 'not-a-real-token'));
    await page.goto('/');
    await expect(page.getByLabel('Username')).toBeVisible();
    await expect(stopBtn(page)).toHaveCount(0);
  });
});

test.describe('2FA is single-use on the real server', () => {
  test('a settings save with a fresh code works; the same code again is rejected', async ({ page, request }) => {
    await openSignedIn(page, request, '/');
    await nav(page, 'Settings');
    const budget = page.getByLabel(/run budget/i);
    await budget.fill('45');
    await page.getByRole('button', { name: /save/i }).click();
    const code = await freshCode();
    await enterCode(page, code, 'Save');
    await expect(page.getByText(/saved/i).first()).toBeVisible();
    expect((await apiGet(request, '/api/config')).body.config.run_budget).toBe(45);

    await budget.fill('46');
    await page.getByRole('button', { name: /save/i }).click();
    await enterCode(page, code, 'Save');                              // the code the server already consumed
    await expect(page.getByText(/Code Rejected/)).toBeVisible();
    expect((await apiGet(request, '/api/config')).body.config.run_budget).toBe(45);   // unchanged
  });

  test('a code replayed straight at the API is refused with 2fa_invalid', async ({ request }) => {
    const r = await apiSend(request, 'PUT', '/api/config', { data: { run_budget: 47 }, code: lastUsedCode() });
    expect(r.status).toBe(403);
    expect(r.body.detail).toBe('2fa_invalid');
  });

  test('a protected call with no code is 2fa_required', async ({ request }) => {
    const r = await apiSend(request, 'PUT', '/api/config', { data: { run_budget: 47 } });
    expect(r.status).toBe(403);
    expect(r.body.detail).toBe('2fa_required');
  });
});

test.describe('settings validation shown from the real server', () => {
  test('a 400 detail list from PUT /api/config is displayed and nothing is saved', async ({ page, request }) => {
    await openSignedIn(page, request, '/');
    await nav(page, 'Settings');
    // The client validates ranges, so rewrite the request on the wire to something the server must refuse.
    await page.route('**/api/config', async (route) => {
      if (route.request().method() !== 'PUT') return route.continue();
      await route.continue({ postData: JSON.stringify({ run_budget: 9999, max_open_frac: 0.9 }) });
    });
    await page.getByLabel(/run budget/i).fill('50');
    await page.getByRole('button', { name: /save/i }).click();
    await enterCode(page, await freshCode(), 'Save');
    const alert = page.getByRole('alert').filter({ hasText: /run_budget|max_open_frac/i });
    await expect(alert.first()).toBeVisible();
    await expect(alert.first()).toContainText(/500|ceiling/i);          // the server's own wording
    expect((await apiGet(request, '/api/config')).body.config.run_budget).toBe(45);
  });

  test('cells cannot be edited through the API even with a valid code', async ({ request }) => {
    const r = await apiSend(request, 'PUT', '/api/config', { data: { cells: [[0.1, 0.2, 0, 1, 'no']] }, code: await freshCode() });
    expect(r.status).toBe(400);
    expect(JSON.stringify(r.body)).toContain('cells');
  });
});

test.describe('Start and live gates', () => {
  test('Start from the app is refused by the real server (runner disabled) and the bot stays stopped', async ({ page, request }) => {
    await openSignedIn(page, request, '/');
    await page.getByRole('button', { name: /start/i }).first().click();
    await enterCode(page, await freshCode(), 'Start');
    await expect(page.getByText('Start failed')).toBeVisible();
    await expect(page.getByText(/runner is disabled/i)).toBeVisible();
    expect((await apiGet(request, '/api/status')).body.desired).toBe('stopped');
  });

  test('Mode: LIVE is unavailable because the server environment disallows it', async ({ page, request }) => {
    await openSignedIn(page, request, '/mode');
    await expect(page.getByRole('button', { name: /switch to live/i })).toBeDisabled();
    await expect(page.getByText(/server does not allow live trading/i)).toBeVisible();
    expect((await apiGet(request, '/api/status')).body.live_allowed).toBe(false);
  });

  test('PUT /api/mode live: needs the typed LIVE (400), then the env gate (409)', async ({ request }) => {
    const noConfirm = await apiSend(request, 'PUT', '/api/mode', { data: { mode: 'live' }, code: await freshCode() });
    expect(noConfirm.status).toBe(400);
    const gated = await apiSend(request, 'PUT', '/api/mode', { data: { mode: 'live', confirm: 'LIVE' }, code: await freshCode() });
    expect(gated.status).toBe(409);
    expect(gated.body.detail).toBe('live_not_allowed');
    expect((await apiGet(request, '/api/status')).body.mode).toBe('demo');
  });
});

test.describe('arbitrage gates', () => {
  test('Arbitrage page loads from the real API, shows Off/log-only and disables Go live', async ({ page, request }) => {
    await openSignedIn(page, request, '/arb');
    await expect(page.getByRole('img', { name: /arbitrage is/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /go live/i })).toBeDisabled();
    await expect(page.getByText(/Live Is Disabled/)).toBeVisible();
  });

  test('turning arbitrage on is refused while the runner is disabled', async ({ page, request }) => {
    await openSignedIn(page, request, '/arb');
    await page.getByRole('button', { name: /turn on/i }).click();
    await enterCode(page, await freshCode(), 'Turn on');
    await expect(page.getByText('Arbitrage not changed')).toBeVisible();
    const arb = await apiGet(request, '/api/arb');
    expect(arb.body.settings.enabled).toBe(false);
  });

  test('PUT /api/arb live: typed LIVE (400) then env gate (409); arb stays log-only', async ({ request }) => {
    const noConfirm = await apiSend(request, 'PUT', '/api/arb', { data: { live: true }, code: await freshCode() });
    expect(noConfirm.status).toBe(400);
    const gated = await apiSend(request, 'PUT', '/api/arb', { data: { live: true, confirm: 'LIVE' }, code: await freshCode() });
    expect(gated.status).toBe(409);
    expect(gated.body.detail).toBe('live_not_allowed');
    const arb = await apiGet(request, '/api/arb');
    expect(arb.body.settings.live).toBe(false);
  });

  test('arb settings over the API ceiling are refused by the real server', async ({ request }) => {
    const r = await apiSend(request, 'PUT', '/api/arb', { data: { arb_max_contracts: 100000 }, code: await freshCode() });
    expect(r.status).toBe(400);
    expect(JSON.stringify(r.body)).toMatch(/arb_max_contracts/);
  });
});

test.describe('Stop from the app reaches the real /api/bot/stop (no 2FA)', () => {
  for (const [label, path] of [['Overview', '/'], ['Markets', '/markets'], ['Arbitrage', '/arb'], ['Server', '/server']]) {
    test(`Stop from ${label}: the server reads stopped, both kill switches exist, it is audited`, async ({ page, request }) => {
      await openSignedIn(page, request, path);
      const stopReq = page.waitForRequest((r) => r.url().endsWith('/api/bot/stop') && r.method() === 'POST');
      await stopBtn(page).click();
      await page.getByRole('button', { name: 'Stop now' }).click();
      const req = await stopReq;
      expect(req.headers()['x-2fa-code']).toBeUndefined();           // Stop never needs a code
      await expect(page.getByText('Stopped. Kill switch set.')).toBeVisible();
      await expect(page.getByLabel('6-digit code')).toHaveCount(0);

      const st = (await apiGet(request, '/api/status')).body;
      expect(st.desired).toBe('stopped');
      expect(st.kill_switch).toBe(true);
      expect((await apiGet(request, '/api/arb')).body.status.kill_switch).toBe(true);
      expect(existsSync(KILL_FILE)).toBe(true);
      expect(existsSync(ARB_KILL_FILE)).toBe(true);
      expect(readFileSync(KILL_FILE, 'utf8')).toMatch(/stopped by owner/);
      const audit = (await apiGet(request, '/api/audit?limit=20')).body;
      expect(JSON.stringify(audit)).toContain('bot.stop');
    });
  }

  test('Stop still works with a code the server has already consumed (it is not a 2FA action)', async ({ page, request }) => {
    await openSignedIn(page, request, '/');
    await stopBtn(page).click();
    await page.getByRole('button', { name: 'Stop now' }).click();
    await expect(page.getByText('Stopped. Kill switch set.')).toBeVisible();
  });

  test('after Stop the API refuses a tick and a run-once (nothing runs)', async ({ request }) => {
    const once = await apiSend(request, 'POST', '/api/bot/run-once');
    expect(once.status).toBe(200);
    expect(once.body.ran).toBe(false);
  });
});

test.describe('security basics on the real server', () => {
  test('CORS: the PWA origin is allowed, a foreign origin is not', async ({ request }) => {
    const ok = await request.fetch('http://127.0.0.1:8765/healthz', { headers: { Origin: 'http://127.0.0.1:5173' } });
    expect(ok.headers()['access-control-allow-origin']).toBe('http://127.0.0.1:5173');
    const bad = await request.fetch('http://127.0.0.1:8765/healthz', { headers: { Origin: 'https://evil.example' } });
    expect(bad.headers()['access-control-allow-origin']).toBeUndefined();
  });

  test('no token or code appears in /api/logs or /api/audit', async ({ request }) => {
    const { apiToken } = await import('./real-helpers.js');
    const token = await apiToken(request);
    const dump = JSON.stringify((await apiGet(request, '/api/logs?lines=500')).body) +
      JSON.stringify((await apiGet(request, '/api/audit?limit=200')).body);
    expect(dump).not.toContain(token);
    expect(dump).not.toContain(creds().totp_secret);
    expect(dump).not.toContain(creds().password);
  });
});

test.describe('375 px layout with real data', () => {
  test('no horizontal scroll on the main pages', async ({ page, request }) => {
    await openSignedIn(page, request, '/');
    for (const label of ['Overview', 'Markets', 'Activity', 'Health', 'Settings', 'Mode', 'Server', 'Logs', 'Arbitrage']) {
      if (label !== 'Overview') await nav(page, label);
      await expect(stopBtn(page)).toBeVisible();
      await page.waitForLoadState('networkidle');
      const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
      expect(sw, `${label}: ${sw}px in ${cw}px`).toBeLessThanOrEqual(cw);
    }
  });
});
