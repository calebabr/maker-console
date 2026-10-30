import { test, expect } from '@playwright/test';
import { login, nav } from './helpers.js';

// UI-1a pages at 375 px: Analytics (Ledger, Scorecard), Arbitrage, Shadow, Account (cash-flow form), Settings.
// Same measure as phone-fit.e2e.js, plus clipped-text detection for elements that truncate their own content.
async function audit(page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const sw = document.documentElement.scrollWidth;
    const bad = [];
    const clipped = [];
    const scroller = (el) => {
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const ox = getComputedStyle(p).overflowX;
        if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') return true;
      }
      return false;
    };
    for (const el of document.body.querySelectorAll('*')) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.position === 'fixed') continue;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      if ((r.right > vw + 0.5 || r.left < -0.5) && !scroller(el)) bad.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)} ${Math.round(r.left)}..${Math.round(r.right)}`);
      if (el.children.length === 0 && el.textContent.trim() && (cs.overflowX === 'hidden' || cs.textOverflow === 'ellipsis') && el.scrollWidth > el.clientWidth + 1) {
        clipped.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 30)} "${el.textContent.trim().slice(0, 30)}"`);
      }
    }
    return { vw, sw, bad: bad.slice(0, 8), clipped: clipped.slice(0, 8) };
  });
}

async function fits(page, label) {
  const a = await audit(page);
  expect(a.sw, `${label}: scrollWidth ${a.sw} > ${a.vw}`).toBeLessThanOrEqual(a.vw);
  expect(a.bad, `${label}: elements outside the viewport`).toEqual([]);
  expect(a.clipped, `${label}: clipped text`).toEqual([]);
}

test('UI-1a pages fit 375 px with no clipped text', async ({ page }) => {
  await login(page);
  await nav(page, 'Analytics');
  for (const tab of ['Summary', 'Ledger', 'Scorecard']) {
    await page.getByRole('tab', { name: tab, exact: true }).click();
    await page.waitForTimeout(300);
    await fits(page, `Analytics/${tab}`);
    await page.screenshot({ path: `test-results/ui1a-analytics-${tab.toLowerCase()}.png`, fullPage: true });
  }
  for (const label of ['Arbitrage', 'Shadow Estimate', 'Settings']) {
    await nav(page, label);
    await page.waitForTimeout(300);
    await fits(page, label);
    await page.screenshot({ path: `test-results/ui1a-${label.split(' ')[0].toLowerCase()}.png`, fullPage: true });
  }
  await expect(page.getByText('Shadow Capital').first()).toBeVisible();
  await nav(page, 'Account');
  await page.waitForTimeout(300);
  await fits(page, 'Account');
  await page.screenshot({ path: 'test-results/ui1a-account.png', fullPage: true });
});
