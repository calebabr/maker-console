import { test, expect } from '@playwright/test';
import { login, nav, nextCode, noHorizontalScroll, stopFromHeader } from './helpers.js';

const header = (page) => page.locator('header.appbar');

test.describe('login + 2FA', () => {
  test('wrong password shows an error and stays on the password step', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel('Username').fill('owner');
    await page.getByLabel('Password', { exact: true }).fill('wrong');
    await page.getByRole('button', { name: 'Continue' }).click();
    await expect(page.getByRole('alert')).toContainText('Wrong username or password');
    await expect(page.getByLabel('Username')).toBeVisible();
  });

  test('a rejected code shows an error; a valid code signs in and lands on Overview', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel('Username').fill('owner');
    await page.getByLabel('Password', { exact: true }).fill('correct-horse');
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.getByLabel('6-digit code').fill('000000');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(header(page).getByRole('button', { name: /stop the bot/i })).toHaveCount(0);
    await page.getByLabel('6-digit code').fill(nextCode());
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(header(page).getByRole('button', { name: /stop the bot/i })).toBeVisible();
  });

  test('signed-out deep links go to login, not to app content', async ({ page }) => {
    await page.goto('/arb');
    await expect(page.getByLabel('Username')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Arbitrage' })).toHaveCount(0);
  });
});

test.describe('Stop from several pages (no 2FA)', () => {
  for (const label of ['Overview', 'Markets', 'Activity', 'Health', 'Settings', 'Mode', 'Server', 'Logs', 'Arbitrage']) {
    test(`Stop from ${label}`, async ({ page }) => {
      await login(page);
      if (label !== 'Overview') await nav(page, label);
      await stopFromHeader(page);
      await expect(page.getByText(/Stopped at /).first()).toBeVisible();
      await expect(page.getByLabel('6-digit code')).toHaveCount(0);
    });
  }

  test('Stop on the Arbitrage page flips arbitrage to Off with the kill switch shown', async ({ page }) => {
    await login(page);
    await nav(page, 'Arbitrage');
    await expect(page.getByRole('img', { name: /arbitrage is log only/i })).toBeVisible();
    await stopFromHeader(page);
    await expect(page.getByText(/Stopped at /).first()).toBeVisible();
    await expect(page.getByRole('img', { name: /arbitrage is off/i })).toBeVisible();
    await expect(page.getByText(/Kill switch set/).first()).toBeVisible();
  });

  test('cancelling the Stop confirm does not stop anything', async ({ page }) => {
    await login(page);
    await header(page).getByRole('button', { name: /stop the bot/i }).click();
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByText(/Stopped at /).first()).toHaveCount(0);
  });
});

test.describe('Arbitrage go live', () => {
  async function toLiveConfirm(page) {
    await login(page);
    await nav(page, 'Arbitrage');
    await page.getByRole('button', { name: /go live/i }).click();
  }

  test('needs typed LIVE (case-sensitive) and then a 2FA code', async ({ page }) => {
    await toLiveConfirm(page);
    const cont = page.getByRole('button', { name: 'Continue' });
    await expect(cont).toBeDisabled();
    await page.getByLabel('Type LIVE to confirm').fill('live');
    await expect(cont).toBeDisabled();
    await page.getByLabel('Type LIVE to confirm').fill('LIVE');
    await expect(cont).toBeEnabled();
    await cont.click();
    await page.getByLabel('6-digit code').fill(nextCode());
    await page.getByRole('button', { name: 'Go LIVE' }).click();
    await expect(page.getByText('Arbitrage is LIVE').first()).toBeVisible();
    await expect(page.getByRole('img', { name: /arbitrage is live/i })).toBeVisible();
  });

  test('an invalid code keeps the prompt open and does not go live', async ({ page }) => {
    await toLiveConfirm(page);
    await page.getByLabel('Type LIVE to confirm').fill('LIVE');
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.getByLabel('6-digit code').fill('000000');
    await page.getByRole('button', { name: 'Go LIVE' }).click();
    await expect(page.getByText(/Code Rejected/)).toBeVisible();
    await expect(page.getByRole('img', { name: /arbitrage is live/i })).toHaveCount(0);
  });

  test('Stop works after the Go-live flow was abandoned', async ({ page }) => {
    await toLiveConfirm(page);
    await page.getByRole('button', { name: 'Cancel' }).click();
    await stopFromHeader(page);
    await expect(page.getByText(/Stopped at /).first()).toBeVisible();
  });
});

test.describe('Settings', () => {
  test('a reused 2FA code is rejected and the error is shown in the prompt', async ({ page }) => {
    await login(page);
    await nav(page, 'Settings');
    const budget = page.getByLabel(/run budget/i);
    await budget.fill('45');
    const code = nextCode();
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await page.getByLabel('6-digit code').fill(code);
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByText(/Saved as version/)).toBeVisible();
    // the form is locked while it reloads, so the next fill waits for it and is never wiped
    // the same code again: single-use, so it is rejected
    await budget.fill('46');
    await expect(budget).toHaveValue('46');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await page.getByLabel('6-digit code').fill(code);
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByText(/Code Rejected/)).toBeVisible();
  });

  test('cells are shown read-only and Save is disabled with no changes', async ({ page }) => {
    await login(page);
    await nav(page, 'Settings');
    await expect(page.getByText('read-only').first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  });

  test('an out-of-range budget blocks Save before any request', async ({ page }) => {
    await login(page);
    await nav(page, 'Settings');
    await page.getByLabel(/run budget/i).fill('501');
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  });
});

test.describe('375px viewport', () => {
  const PAGES = ['Overview', 'Markets', 'Activity', 'Health', 'Settings', 'Mode', 'Server', 'Logs', 'Arbitrage'];

  test('no horizontal scroll and a tappable Stop on every page', async ({ page }) => {
    await login(page);
    for (const label of PAGES) {
      if (label !== 'Overview') await nav(page, label);
      const stop = header(page).getByRole('button', { name: /stop the bot/i });
      await expect(stop).toBeVisible();
      const box = await stop.boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(375);
      expect(box.height).toBeGreaterThanOrEqual(30);
      await noHorizontalScroll(page);
    }
  });

  test('Server page rejects intervals above 14', async ({ page }) => {
    await login(page);
    await nav(page, 'Server');
    const input = page.getByLabel(/self-ping|keepalive/i).first();
    await input.fill('15');
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
    await input.fill('14');
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
  });
});
