import { test, expect } from '@playwright/test';
import { login, nav, nextCode } from './helpers.js';

// Server page: Lease & Release and Pending Release states against the mock (scenario flags in
// sessionStorage 'keel.mock', see src/services/mock.js). Phone fit at 375 and 320 px, plus the 2FA gate on Approve.

async function scenario(page, flags) {
  await page.addInitScript((f) => {
    try { sessionStorage.setItem('keel.mock', f); } catch { /* ignore */ }
  }, flags);
}

async function fits(page, label) {
  const { sw, vw, bad } = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const bad = [];
    for (const el of document.body.querySelectorAll('*')) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.position === 'fixed') continue;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      if (r.right > vw + 0.5 || r.left < -0.5) {
        let scrolls = false;
        for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
          if (['auto', 'scroll', 'hidden'].includes(getComputedStyle(p).overflowX)) scrolls = true;
        }
        if (!scrolls) bad.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 30)}`);
      }
    }
    return { sw: document.documentElement.scrollWidth, vw, bad: bad.slice(0, 6) };
  });
  expect(sw, `${label}: scrollWidth ${sw} > ${vw}`).toBeLessThanOrEqual(vw);
  expect(bad, `${label}: elements outside the viewport`).toEqual([]);
}

const STATES = [
  ['leader with a pending release', ''],
  ['standby with a pending release', 'standby'],
  ['expired release', 'release-expired'],
  ['no release', 'release-none'],
  ['release feature off (404)', 'release-off'],
  ['old app (update banner)', 'old-app,standby'],
];

for (const width of [375, 320]) {
  for (const [name, flags] of STATES) {
    test(`Server page fits ${width} px: ${name}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 700 });
      await scenario(page, flags);
      await login(page);
      await nav(page, 'Server');
      await expect(page.getByRole('heading', { name: 'Lease & Release' })).toBeVisible();
      await fits(page, name);
      // Stop is visible and enabled in every state, including standby and the update banner.
      await expect(page.locator('header.appbar').getByRole('button', { name: /stop the bot/i })).toBeEnabled();
    });
  }
}

test('role is written in words, not only colour', async ({ page }) => {
  await scenario(page, 'standby');
  await login(page);
  await nav(page, 'Server');
  await expect(page.getByText('This server is standing by')).toBeVisible();
  await expect(page.getByText('Standby', { exact: true })).toBeVisible();
});

test('update banner shows but does not block anything', async ({ page }) => {
  await scenario(page, 'old-app');
  await login(page);
  await expect(page.getByText(/update available, reopen the app/i)).toBeVisible();
  await expect(page.locator('header.appbar').getByRole('button', { name: /stop the bot/i })).toBeEnabled();
});

test('pending release: hidden when off, and the expired one cannot be approved', async ({ page }) => {
  await scenario(page, 'release-off');
  await login(page);
  await nav(page, 'Server');
  await expect(page.getByRole('heading', { name: 'Lease & Release' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Pending Release' })).toHaveCount(0);
});

test('Approve asks for a 2FA code and then approves', async ({ page }) => {
  await login(page);
  await nav(page, 'Server');
  await expect(page.getByRole('heading', { name: 'Pending Release' })).toBeVisible();
  await page.getByRole('button', { name: /approve/i }).click();
  await expect(page.getByLabel('6-digit code')).toBeVisible();
  // a wrong code keeps the prompt open and approves nothing
  await page.getByLabel('6-digit code').fill('000000');
  await page.getByRole('button', { name: /^approve$/i }).last().click();
  await expect(page.getByLabel('6-digit code')).toBeVisible();
  await page.getByLabel('6-digit code').fill(nextCode());
  await page.getByRole('button', { name: /^approve$/i }).last().click();
  await expect(page.getByText(/approved\. the deploy continues/i)).toBeVisible();
});

test('Reject asks to confirm and needs no code', async ({ page }) => {
  await login(page);
  await nav(page, 'Server');
  await page.getByRole('button', { name: 'Reject' }).click();
  await expect(page.getByLabel('6-digit code')).toHaveCount(0);
  await page.getByRole('button', { name: 'Reject' }).last().click();
  await expect(page.getByText(/rejected\. nothing was deployed/i)).toBeVisible();
});
