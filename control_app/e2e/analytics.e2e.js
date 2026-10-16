import { test, expect } from '@playwright/test';
import { login, nav, noHorizontalScroll } from './helpers.js';

// Analytics smoke against the mock backend (src/services/mockAnalytics.js), at 375 px.
test('Analytics: totals, chart, edge, window switch, filters and load more', async ({ page }) => {
  await login(page);
  await nav(page, 'Analytics');
  await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible();
  await expect(page.getByTestId('totals')).toContainText('Win Rate');
  await expect(page.getByTestId('pnl-curve')).toBeVisible();
  await page.getByRole('tab', { name: 'Edge' }).click();
  await expect(page.getByTestId('edge-row').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Trades' }).click();
  await expect(page.getByTestId('trade-row')).toHaveCount(50);

  await page.getByRole('button', { name: '180 days' }).click();
  await expect(page.getByText(/settled in the last 180 days/)).toBeVisible();

  await page.getByRole('tab', { name: 'Edge' }).click();
  await page.locator('select:has(option[value="series"])').selectOption('series');
  await expect(page.getByTestId('edge-row').filter({ hasText: 'Demo series B' })).toBeVisible();

  await page.getByRole('tab', { name: 'Trades' }).click();
  await page.getByText(/^Filters/).click();
  await page.getByRole('combobox', { name: 'Result' }).selectOption('loss');
  await expect(page.getByText('Filters (1 on)')).toBeVisible();
  await expect(page.getByTestId('trade-row').first()).toContainText('Lost');

  // Filter changes apply after a 300 ms debounce (S-28): wait until the unfiltered list replaced the filtered one.
  const filtered = await page.getByText(/^Showing \d+ of/).textContent();
  await page.getByRole('combobox', { name: 'Result' }).selectOption('');
  await expect(page.getByText(/^Showing \d+ of/)).not.toHaveText(filtered);
  await expect(page.getByTestId('trade-row')).toHaveCount(50);
  await page.getByRole('button', { name: 'Load more' }).click();
  await expect(page.getByTestId('trade-row')).toHaveCount(100);

  await noHorizontalScroll(page);
});

test('Analytics extension: edge verdict card, calibration, fill rate and CSV download', async ({ page }) => {
  await login(page);
  await nav(page, 'Analytics');
  await page.getByRole('button', { name: '180 days' }).click();
  const card = page.getByTestId('edge-holding');
  await expect(card).toContainText(/Is the edge holding/i);
  await expect(card).toContainText(/judged market type/);
  await page.getByRole('tab', { name: 'Edge' }).click();
  await expect(page.getByTestId('calibration').getByRole('img')).toBeVisible();
  await page.getByRole('tab', { name: 'Execution' }).click();
  await expect(page.getByTestId('fill-rate')).toContainText(/Fill rate/i);

  await page.getByRole('tab', { name: 'Trades' }).click();
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Download CSV' }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^trades-\d{4}-\d{2}-\d{2}\.csv$/);
  await expect(page.getByText(/^Saved trades-/)).toBeVisible();
  await noHorizontalScroll(page);
});
