import { test, expect } from '@playwright/test';
import { login, nav } from './helpers.js';

// UI-1a stage 2 phone audit: the new or changed screens at 375x667 (small phone) and 375x812.
// Per screen: no horizontal scroll, nothing outside the 16 px gutters, no clipped text, every interactive
// element in <main> at least 40 px tall (inline text links are allow-listed), and at most two sticky rows under
// the header (title bar + sub-nav). Screenshots go to test-results/ (git-ignored), never into the repo.

const MIN_TAP = 40;

// Screens: [label, how to get there from a signed-in page]. Labels map through helpers.nav().
const SCREENS = [
  ['Home', (p) => nav(p, 'Home')],
  ['Strategies hub', (p) => nav(p, 'Strategies')],
  ['Maker Overview', (p) => nav(p, 'Maker')],
  ['Maker Results', (p) => nav(p, 'Maker Results')],
  ['Maker Controls', (p) => nav(p, 'Maker Controls')],
  ['Arbitrage Overview', (p) => nav(p, 'Arbitrage')],
  ['Arbitrage Results', (p) => nav(p, 'Arbitrage Results')],
  ['Arbitrage Controls', (p) => nav(p, 'Arbitrage Controls')],
  ['Performance hub', (p) => nav(p, 'Performance')],
  ['Periods', (p) => nav(p, 'Periods')],
  ['Analytics Summary', (p) => nav(p, 'Analytics')],
  [
    'Analytics Ledger',
    async (p) => {
      await nav(p, 'Analytics');
      await p.getByRole('tab', { name: 'Ledger', exact: true }).click();
    },
  ],
  [
    'Analytics Scorecard',
    async (p) => {
      await nav(p, 'Analytics');
      await p.getByRole('tab', { name: 'Scorecard', exact: true }).click();
    },
  ],
  ['Activity', (p) => nav(p, 'Activity')],
  ['More', (p) => nav(p, 'More')],
];

async function audit(page) {
  return page.evaluate((MIN) => {
    const vw = document.documentElement.clientWidth;
    const sw = document.documentElement.scrollWidth;
    const inScroller = (el) => {
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const ox = getComputedStyle(p).overflowX;
        if (ox === 'auto' || ox === 'scroll') return true;
      }
      return false;
    };
    const name = (el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 36)} "${(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 24)}"`;
    const outside = [];
    const clipped = [];
    for (const el of document.body.querySelectorAll('*')) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.position === 'fixed') continue;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      if ((r.right > vw + 0.5 || r.left < -0.5) && !inScroller(el)) outside.push(`${name(el)} ${Math.round(r.left)}..${Math.round(r.right)}`);
      if (el.children.length === 0 && el.textContent.trim() && (cs.overflowX === 'hidden' || cs.textOverflow === 'ellipsis') && el.scrollWidth > el.clientWidth + 1) {
        clipped.push(name(el));
      }
    }
    // Tap targets in <main>: links, buttons, form controls, tabs, radios. Inline text links (display: inline
    // inside running text) are allow-listed; so are visually hidden inputs that a styled label covers.
    const small = [];
    const main = document.querySelector('main') || document.body;
    const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=tab], [role=radio], [role=switch], [role=button]';
    for (const el of main.querySelectorAll(sel)) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      const r = el.getBoundingClientRect();
      if (r.width < 2 && r.height < 2) continue; // visually hidden (sr-only / covered input)
      if (el.closest('.sr-only, [aria-hidden="true"]')) continue;
      // Allow-list: the inline "?" help glyph keeps its 16 px look and carries an invisible 34 px pad (styles/help.css).
      if (el.classList.contains('help-icon')) continue;
      if (el.tagName === 'INPUT' && (el.type === 'radio' || el.type === 'checkbox') && el.closest('label')) continue; // the label is the target
      if (el.tagName === 'A' && cs.display === 'inline') continue; // inline text link
      if (r.height < MIN - 0.5) small.push(`${name(el)} ${Math.round(r.height)}px`);
    }
    // Sticky rows under the header (title bar, sub-nav). Bottom bars (sticky save bar) do not count.
    const header = document.querySelector('header.appbar');
    const hb = header ? header.getBoundingClientRect().bottom : 0;
    const rows = new Set();
    for (const el of document.body.querySelectorAll('*')) {
      if (el === header || header?.contains(el) || el.closest('nav.tabbar')) continue;
      const cs = getComputedStyle(el);
      if (cs.position !== 'sticky' && cs.position !== 'fixed') continue;
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      const r = el.getBoundingClientRect();
      if (r.height < 2 || r.width < 2 || r.top > window.innerHeight / 2) continue;
      rows.add(`${Math.round(r.top)}|${Math.round(r.height)}`);
    }
    return { vw, sw, outside: outside.slice(0, 8), clipped: clipped.slice(0, 8), small: small.slice(0, 12), stickyRows: [...rows], headerBottom: hb };
  }, MIN_TAP);
}

for (const size of [
  { width: 375, height: 667 },
  { width: 375, height: 812 },
]) {
  test.describe(`${size.width}x${size.height}`, () => {
    test.use({ viewport: size });

    test('new and changed screens: no overflow, no clipped text, 40 px targets, at most two sticky rows', async ({ page }) => {
      await login(page);
      const problems = [];
      for (const [label, go] of SCREENS) {
        await go(page);
        await page.waitForTimeout(350);
        const a = await audit(page);
        if (a.sw > a.vw) problems.push(`${label}: scrollWidth ${a.sw} > ${a.vw}`);
        if (a.outside.length) problems.push(`${label}: outside the viewport: ${a.outside.join('; ')}`);
        if (a.clipped.length) problems.push(`${label}: clipped text: ${a.clipped.join('; ')}`);
        if (a.small.length) problems.push(`${label}: targets under ${MIN_TAP}px: ${a.small.join('; ')}`);
        // The page title bar and the sub-nav are the only sticky rows allowed under the header.
        const under = a.stickyRows.filter((r) => Number(r.split('|')[0]) >= a.headerBottom - 1);
        if (under.length > 2) problems.push(`${label}: ${under.length} sticky rows under the header (${under.join(', ')})`);
        await page.screenshot({ path: `test-results/ui1a-nav-${size.height}-${label.replace(/\s+/g, '-').toLowerCase()}.png`, fullPage: true });
      }
      expect(problems, problems.join('\n')).toEqual([]);
    });

    test('Arbitrage is two taps from the tab bar and the header Stop stays in view', async ({ page }) => {
      await login(page);
      await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Strategies', exact: true }).click(); // tap 1
      await page.getByTestId('strategy-card-arb').click(); // tap 2
      await expect(page.getByRole('heading', { name: 'Arbitrage', level: 1 })).toBeVisible();
      const stop = await page.locator('header.appbar').getByRole('button', { name: /stop the bot/i }).boundingBox();
      expect(stop.y).toBeGreaterThanOrEqual(0);
      expect(stop.y + stop.height).toBeLessThanOrEqual(size.height);
      expect(stop.x + stop.width).toBeLessThanOrEqual(size.width);
    });
  });
}
