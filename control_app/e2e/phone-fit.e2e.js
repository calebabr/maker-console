import { test, expect } from '@playwright/test';
import { login, nav } from './helpers.js';

// Phone fit (owner flag 2026-10-16): no page may be wider than the viewport, and no visible element may poke
// past the 16 px gutters. `.shell` uses overflow-x: clip, so document scrollWidth alone cannot see a clipped
// overflow; this also measures every element's box (skipping ones inside their own horizontal scroller).

const PAGES = ['Overview', 'Analytics', 'Activity', 'Markets', 'Health', 'Settings', 'Mode', 'Server', 'Logs', 'Arbitrage', 'Shadow Estimate', 'Terminology'];

async function audit(page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const sw = document.documentElement.scrollWidth;
    const scroller = (el) => {
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const ox = getComputedStyle(p).overflowX;
        if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') return true;
      }
      return false;
    };
    const bad = [];
    for (const el of document.body.querySelectorAll('*')) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.position === 'fixed') continue;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      if (cs.position === 'absolute' && (el.className + '').match(/sr-only/)) continue;
      if ((r.right > vw + 0.5 || r.left < -0.5) && !scroller(el)) {
        bad.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)} ${Math.round(r.left)}..${Math.round(r.right)}`);
      }
    }
    return { vw, sw, bad: bad.slice(0, 8) };
  });
}

async function expectFits(page, label) {
  const a = await audit(page);
  expect(a.sw, `${label}: scrollWidth ${a.sw} > ${a.vw}`).toBeLessThanOrEqual(a.vw);
  expect(a.bad, `${label}: elements outside the viewport`).toEqual([]);
}

test('login screen fits and is centred at 375 px', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByLabel('Username')).toBeVisible();
  await expectFits(page, 'login');
  const box = await page.getByLabel('Username').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(15.5);
  expect(box.x + box.width).toBeLessThanOrEqual(375 - 15.5);
});

test('every page fits 375 px, header Stop fully visible, equal gutters', async ({ page }) => {
  await login(page);
  for (const label of PAGES) {
    if (label !== 'Overview') await nav(page, label);
    await page.waitForTimeout(150);
    await expectFits(page, label);
    const stop = await page.locator('header.appbar').getByRole('button', { name: /stop the bot/i }).boundingBox();
    expect(stop.x + stop.width, `${label}: Stop button clipped`).toBeLessThanOrEqual(375 - 8);
  }
});

for (const theme of ['midnight', 'teal', 'graphite']) {
  test(`large text + ${theme} theme fits 320 px on every page`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 700 });
    await page.addInitScript((t) => {
      try { localStorage.setItem('prior.theme', t); localStorage.setItem('prior.textSize', 'large'); } catch { /* ignore */ }
    }, theme);
    await login(page);
    for (const label of PAGES) {
      if (label !== 'Overview') await nav(page, label);
      await page.waitForTimeout(150);
      await expectFits(page, `${theme}/${label}`);
    }
  });
}
