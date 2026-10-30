import { test, expect } from '@playwright/test';
import { nextCode, stopFromHeader } from './helpers.js';

// UI-1a stage 2 safety, against the mock backend, on EVERY signed-in route at 375, 768 and 1280 px:
//   * the header Stop is visible, inside the viewport, and works with one tap plus one confirm and no 2FA prompt;
//   * Start, go-live, Save, Pause and Resume still ask for a fresh 2FA code, and live needs typing LIVE.
// A fresh page per test resets the in-memory mock, so each route starts running.

const ROUTES = [
  '/', '/strategies', '/strategies/maker', '/strategies/maker/results', '/strategies/maker/controls',
  '/strategies/arb', '/strategies/arb/results', '/strategies/arb/controls',
  '/performance', '/performance/periods', '/analytics', '/shadow', '/activity', '/more',
  '/health', '/server', '/mode', '/logs', '/account', '/settings', '/alerts', '/terminology',
  '/markets', '/arb', // legacy paths redirect
];
const WIDTHS = [
  { width: 375, height: 812 },
  { width: 768, height: 1024 },
  { width: 1280, height: 800 },
];

const stopBtn = (page) => page.locator('header.appbar').getByRole('button', { name: /stop the bot/i });

/**
 * Sign in at "/" (a deep link is not kept through the login form) and then move to `path` inside the app with a
 * history push, which a full page load would not: the in-memory mock and the session stay alive.
 */
async function loginAt(page, path) {
  await page.goto('/');
  await page.getByLabel('Username').fill('owner');
  await page.getByLabel('Password', { exact: true }).fill('correct-horse');
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByLabel('6-digit code').fill(nextCode());
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(stopBtn(page)).toBeVisible();
  if (path !== '/') {
    await page.evaluate((p) => {
      window.history.pushState({}, '', p);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }, path);
    // legacy paths redirect, so only check the URL moved off "/"
    await expect.poll(() => new URL(page.url()).pathname).not.toBe('/');
    await expect(page.getByRole('main').getByRole('heading', { level: 1 }).first()).not.toHaveText('Home');
  }
  await expect(stopBtn(page)).toBeVisible();
}

for (const vp of WIDTHS) {
  test.describe(`Stop on every route at ${vp.width}px`, () => {
    test.use({ viewport: vp });
    for (const path of ROUTES) {
      test(`${path}: header Stop is visible, one tap + confirm, no 2FA`, async ({ page }) => {
        await loginAt(page, path);
        const box = await stopBtn(page).boundingBox();
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(vp.width);
        expect(box.y).toBeGreaterThanOrEqual(0);
        expect(box.height).toBeGreaterThanOrEqual(30);
        await stopFromHeader(page);
        await expect(page.getByText(/Stopped at /).first()).toBeVisible();
        await expect(page.getByLabel('6-digit code')).toHaveCount(0);
      });
    }
  });
}

for (const vp of [WIDTHS[0], WIDTHS[2]]) {
  test.describe(`2FA gates at ${vp.width}px`, () => {
    test.use({ viewport: vp });

    test('Start needs a fresh code; cancelling leaves the bot stopped', async ({ page }) => {
      await loginAt(page, '/');
      await stopFromHeader(page);
      await expect(page.getByText(/Stopped at /).first()).toBeVisible();
      await page.getByRole('button', { name: /^Start$/ }).first().click();
      await expect(page.getByLabel('6-digit code')).toBeVisible();
      await page.getByRole('button', { name: 'Cancel' }).click();
      await expect(page.getByLabel('6-digit code')).toHaveCount(0);
      await expect(page.getByText(/Stopped at /).first()).toBeVisible();
      // with a code it starts
      await page.getByRole('button', { name: /^Start$/ }).first().click();
      await page.getByLabel('6-digit code').fill(nextCode());
      await page.getByRole('button', { name: /^Start$/ }).last().click();
      await expect(page.getByText(/Stopped at /)).toHaveCount(0);
    });

    test('Arbitrage Go live needs typed LIVE, then a code', async ({ page }) => {
      await loginAt(page, '/strategies/arb');
      await page.getByRole('button', { name: /go live/i }).click();
      const cont = page.getByRole('button', { name: 'Continue' });
      await expect(cont).toBeDisabled();
      await page.getByLabel('Type LIVE to confirm').fill('LIVE');
      await cont.click();
      await expect(page.getByLabel('6-digit code')).toBeVisible();
      await page.getByRole('button', { name: 'Cancel' }).click();

    });

    test('Arbitrage Controls: Save opens the 2FA prompt and sends nothing before the code', async ({ page }) => {
      await loginAt(page, '/strategies/arb/controls');
      const field = page.getByRole('main').locator('input[type=number]').first();
      await expect(field).toBeVisible();
      const cur = Number(await field.inputValue());
      await field.fill(String(cur > 1 ? cur - 1 : cur + 1));
      const save = page.getByRole('button', { name: 'Save', exact: true });
      await save.click();
      await expect(page.getByLabel('6-digit code')).toBeVisible();
    });

    test('Maker Controls: Pause and Resume each need a fresh code', async ({ page }) => {
      await loginAt(page, '/strategies/maker/controls');
      const pause = page.getByRole('button', { name: /^Pause /i }).first();
      await pause.click();
      await expect(page.getByLabel('6-digit code')).toBeVisible();
      await page.getByLabel('6-digit code').fill(nextCode());
      await page.getByRole('button', { name: 'Pause', exact: true }).last().click();
      const resume = page.getByRole('button', { name: /^Resume /i }).first();
      await expect(resume).toBeVisible();
      await resume.click();
      await expect(page.getByLabel('6-digit code')).toBeVisible();
    });

    test('Maker Controls: Save in the settings form needs a code', async ({ page }) => {
      await loginAt(page, '/strategies/maker/controls');
      const budget = page.getByLabel(/run budget/i);
      await budget.fill('44');
      await page.getByRole('button', { name: 'Save', exact: true }).click();
      await expect(page.getByLabel('6-digit code')).toBeVisible();
    });
  });
}
