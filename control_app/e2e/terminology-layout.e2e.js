import { test, expect } from '@playwright/test';
import { login, nav, noHorizontalScroll } from './helpers.js';

// Terminology layout at 375 px (owner bug 2026-10-13): a deep-linked entry must land just BELOW the
// sticky filter (not under it), the filter must be opaque and above the entries, and the highlight
// must stay inside the 16 px page gutter.
// Reduced motion makes the hash scroll instant, so we measure the final position.
test.use({ reducedMotion: 'reduce' });

const GUTTER = 16;

async function measure(page, id) {
  return page.evaluate((termId) => {
    const r = (el) => el.getBoundingClientRect();
    const filter = document.querySelector('.term-search');
    const entry = document.getElementById(termId);
    const header = document.querySelector('header.appbar');
    const cs = getComputedStyle(filter);
    const bg = cs.backgroundColor;
    const alpha = /rgba\(/.test(bg) ? Number(bg.split(',')[3].replace(')', '')) : bg === 'transparent' ? 0 : 1;
    const f = r(filter);
    const e = r(entry);
    const hit = (x, y) => document.elementFromPoint(x, y);
    return {
      vw: document.documentElement.clientWidth,
      headerBottom: r(header).bottom,
      filterTop: f.top,
      filterBottom: f.bottom,
      filterLeft: f.left,
      filterRight: f.right,
      filterZ: Number(cs.zIndex) || 0,
      filterAlpha: alpha,
      entryTop: e.top,
      entryLeft: e.left,
      entryRight: e.right,
      highlighted: entry.classList.contains('highlight'),
      // Nothing (header, page-title bar) covers the filter, and the filter does not cover the entry's top.
      filterVisible: filter.contains(hit(f.left + f.width / 2, f.top + 4)),
      entryTopVisible: entry.contains(hit(e.left + 24, e.top + 4)),
    };
  }, id);
}

function expectLayout(m) {
  expect(m.highlighted, 'target entry is highlighted').toBe(true);
  expect(m.filterAlpha, 'sticky filter background is opaque').toBe(1);
  expect(m.filterZ, 'sticky filter is stacked above entries').toBeGreaterThan(0);
  expect(m.filterTop, 'filter sticks below the header').toBeGreaterThanOrEqual(m.headerBottom - 0.5);
  expect(m.filterVisible, `filter is not covered by the header / page title: ${JSON.stringify(m)}`).toBe(true);
  // Filter background spans the full width (count line included), so nothing peeks past it at the gutters.
  expect(m.filterLeft).toBeLessThanOrEqual(0.5);
  expect(m.filterRight).toBeGreaterThanOrEqual(m.vw - 0.5);
  // The entry's top sits at or just below the filter's bottom, never under it.
  expect(m.entryTop, 'entry top is below the sticky filter').toBeGreaterThanOrEqual(m.filterBottom - 0.5);
  expect(m.entryTop - m.filterBottom, 'entry lands close to the filter').toBeLessThanOrEqual(64);
  expect(m.entryTopVisible, 'entry top is not covered').toBe(true);
  // Highlight stays inside the page gutter.
  expect(m.entryLeft).toBeGreaterThanOrEqual(GUTTER - 0.5);
  expect(m.entryRight).toBeLessThanOrEqual(m.vw - GUTTER + 0.5);
}

test('a (?) icon deep link lands the entry just below the sticky filter, inside the gutter', async ({ page }) => {
  await login(page);
  await nav(page, 'Analytics');
  await page.locator('button[data-term="edge-verdict"]').first().click();
  const icon = page.locator('a[href$="#edge-verdict"]').first();
  await expect(icon).toBeVisible();
  await icon.click();
  await expect(page).toHaveURL(/\/terminology#edge-verdict$/);
  await expect(page.locator('#edge-verdict')).toHaveClass(/highlight/);
  await page.waitForTimeout(250); // let the sticky stack settle (page-title bar may auto-hide on scroll)
  expectLayout(await measure(page, 'edge-verdict'));
  await noHorizontalScroll(page);
  if (process.env.TERM_SHOT) await page.screenshot({ path: process.env.TERM_SHOT });

  // When the sticky page title auto-hides (scroll idle), the filter moves up to the header and the
  // entry is still not under it.
  const bar = page.getByTestId('page-title');
  if (await bar.count()) {
    await expect(bar).toHaveAttribute('data-state', 'hidden', { timeout: 6000 });
    await page.waitForTimeout(100);
    const m = await measure(page, 'edge-verdict');
    expect(m.filterTop).toBeLessThanOrEqual(m.headerBottom + 0.5);
    expect(m.filterVisible).toBe(true);
    expect(m.entryTop).toBeGreaterThanOrEqual(m.filterBottom - 0.5);
  }
});

test('a "See also" link inside Terminology also lands below the filter', async ({ page }) => {
  await login(page);
  await nav(page, 'Terminology');
  await page.locator('#fill .term-related a').first().click();
  await expect(page).toHaveURL(/\/terminology#[\w-]+$/);
  const id = decodeURIComponent(new URL(page.url()).hash.slice(1));
  await expect(page.locator(`[id="${id}"]`)).toHaveClass(/highlight/);
  await page.waitForTimeout(250);
  expectLayout(await measure(page, id));
});
