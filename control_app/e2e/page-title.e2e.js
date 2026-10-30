import { test, expect } from '@playwright/test';
import { login, nav, noHorizontalScroll } from './helpers.js';

// Sticky page title bar (components/PageTitle.jsx) against the mock backend, at 375 px.
const bar = (page) => page.getByTestId('page-title');

test('page title: shown at top, pinned under the header when scrolled, hides when idle, back on scroll', async ({ page }) => {
  await login(page);
  await nav(page, 'Logs');
  await expect(bar(page)).toHaveText('Logs');
  await expect(page).toHaveTitle('Logs · Prior');
  await nav(page, 'Markets');
  await expect(bar(page)).toHaveText('Markets');
  await expect(page).toHaveTitle('Markets · Prior');
  await expect(bar(page)).toHaveAttribute('data-state', 'shown');

  // Markets cards are folded now, so that page is too short to scroll. Terminology is static and long
  // (dozens of glossary entries), so the scroll behaviour is checked there.
  await nav(page, 'Terminology');
  await expect(bar(page)).toHaveText('Terminology');
  await expect(page).toHaveTitle('Terminology · Prior');
  await expect(page.locator('.term').first()).toBeVisible();
  // Mobile emulation (Pixel 5) ignores mouse wheel; scroll the window directly (fires a real scroll event).
  await page.evaluate(() => window.scrollBy(0, 600));
  await expect(bar(page)).toHaveClass(/is-stuck/);
  await expect(bar(page)).toHaveAttribute('data-state', 'shown');
  const header = await page.locator('header.appbar').boundingBox();
  const b = await bar(page).boundingBox();
  expect(Math.abs(b.y - (header.y + header.height))).toBeLessThanOrEqual(1); // pinned right under the header

  await expect(bar(page)).toHaveAttribute('data-state', 'hidden', { timeout: 5000 });
  await page.evaluate(() => window.scrollBy(0, 120));
  await expect(bar(page)).toHaveAttribute('data-state', 'shown');
  // --sticky-top = header + bar while shown (other sticky elements stick below it).
  const hb = await page.locator('header.appbar').boundingBox();
  const bb = await bar(page).boundingBox();
  await expect
    .poll(() => page.evaluate(() => parseFloat(document.documentElement.style.getPropertyValue('--sticky-top'))))
    .toBeCloseTo(hb.height + bb.height, 0);
  const stickyTop = await page.evaluate(() => parseFloat(document.documentElement.style.getPropertyValue('--sticky-top')));
  expect(Math.abs(stickyTop - (hb.height + bb.height))).toBeLessThanOrEqual(1);

  // Stop in the header stays visible and on top of the bar.
  const stop = page.locator('header.appbar').getByRole('button', { name: /stop the bot/i });
  await expect(stop).toBeVisible();
  const s = await stop.boundingBox();
  const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest('header.appbar') !== null, [
    s.x + s.width / 2,
    s.y + s.height / 2,
  ]);
  expect(hit).toBe(true);
  await noHorizontalScroll(page);
});
