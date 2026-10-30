import { expect } from '@playwright/test';

let seq = 100000 + (process.pid % 1000) * 100;
/** A fresh, never-used 6-digit code (the mock rejects a reused code, like the real API). */
export const nextCode = () => String(++seq).padStart(6, '0');

export async function login(page) {
  await page.goto('/');
  await expect(page.getByLabel('Username')).toBeVisible();
  await page.getByLabel('Username').fill('owner');
  await page.getByLabel('Password', { exact: true }).fill('correct-horse');
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByLabel('6-digit code').fill(nextCode());
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.locator('header.appbar').getByRole('button', { name: /stop the bot/i })).toBeVisible();
}

// The five phone tabs (UI-1a stage 2). 'Overview' is kept as an alias of Home for older specs.
const TABS = ['Home', 'Strategies', 'Performance', 'Activity', 'More'];
// Pages reached through a hub: Strategies -> card, Strategies -> card -> view, Performance -> link.
const STRATEGY_CARD = { Maker: 'maker', Markets: 'maker', Arbitrage: 'arb' };
const STRATEGY_VIEW = { 'Maker Results': ['maker', 'Results'], 'Maker Controls': ['maker', 'Controls'], 'Markets Controls': ['maker', 'Controls'], 'Arbitrage Results': ['arb', 'Results'], 'Arbitrage Controls': ['arb', 'Controls'] };
const PERFORMANCE_LINK = { Analytics: /^Analytics/, 'Shadow Estimate': /^Shadow/, Periods: /^Periods/ };

const tab = (page, label) => page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: label, exact: true });

/** Click through the real nav (a full page.goto would reset the in-memory mock and the session). */
export async function nav(page, label) {
  if (label === 'Overview') label = 'Home';
  if (TABS.includes(label)) {
    await tab(page, label).click();
  } else if (STRATEGY_CARD[label]) {
    await tab(page, 'Strategies').click();
    await page.getByTestId(`strategy-card-${STRATEGY_CARD[label]}`).click(); // two taps from the tab bar
  } else if (STRATEGY_VIEW[label]) {
    const [id, view] = STRATEGY_VIEW[label];
    await tab(page, 'Strategies').click();
    await page.getByTestId(`strategy-card-${id}`).click();
    await page.getByTestId('subnav').getByText(view, { exact: true }).click();
  } else if (PERFORMANCE_LINK[label]) {
    await tab(page, 'Performance').click();
    await page.getByRole('main').getByRole('link', { name: PERFORMANCE_LINK[label] }).first().click();
  } else {
    await tab(page, 'More').click();
    await page.getByRole('navigation', { name: 'More pages' }).getByRole('link', { name: label }).click();
  }
}

export async function stopFromHeader(page) {
  await page.locator('header.appbar').getByRole('button', { name: /stop the bot/i }).click();
  await page.getByRole('button', { name: 'Stop now' }).click();
}

export async function noHorizontalScroll(page) {
  const { sw, cw } = await page.evaluate(() => ({
    sw: document.documentElement.scrollWidth,
    cw: document.documentElement.clientWidth,
  }));
  expect(sw, `page is ${sw}px wide in a ${cw}px viewport`).toBeLessThanOrEqual(cw);
}
