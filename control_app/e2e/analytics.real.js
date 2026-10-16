import { test, expect } from '@playwright/test';
import { apiGet, openSignedIn } from './real-helpers.js';

// Frontend <-> backend wiring for /api/analytics/* against the REAL control API (dev server: in-memory seeded store,
// runner disabled, no Kalshi) and the built PWA. Read-only; nothing here starts, stops or changes the bot.
// The analytics routes are rate limited per user (30 summary / 60 trades per minute), so this spec stays well below that
// and fails loudly if the app ever receives a 429.
test.describe.configure({ mode: 'serial' });

const fmtInt = (n) => n.toLocaleString('en-US');
const fmtMoney = (v) => `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function watchAnalytics(page) {
  const seen = [];
  page.on('response', (r) => {
    if (r.url().includes('/api/analytics/')) seen.push({ url: r.url(), status: r.status() });
  });
  return seen;
}

test.describe('analytics page against the real API', () => {
  test('totals shown on /analytics are the numbers the API returns for the seeded data', async ({ page, request }) => {
    const seen = watchAnalytics(page);
    const { status, body: api } = await apiGet(request, '/api/analytics/summary?days=90');
    expect(status).toBe(200);
    expect(api.totals.settled_fills, 'seed must produce settled fills').toBeGreaterThan(20);

    await openSignedIn(page, request, '/analytics');
    await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible();
    await page.getByRole('button', { name: '90 days' }).click();
    await expect(page.getByText(/settled in the last 90 days/)).toBeVisible();

    const totals = page.getByTestId('totals');
    await expect(totals).toContainText(fmtInt(api.totals.settled_fills));
    await expect(totals).toContainText(fmtMoney(api.totals.pnl));
    await expect(totals).toContainText(`${(api.totals.win_rate * 100).toFixed(1)}%`);
    await expect(totals).not.toContainText(/NaN|undefined|null/);
    await expect(page.getByTestId('pnl-curve')).toBeVisible();
    await expect(page.getByTestId('edge-row').first()).toBeVisible();
    // each edge tab renders rows from the real by_* arrays
    for (const [tab, field] of [['Series', 'by_series'], ['Price', 'by_band'], ['Time left', 'by_ttc'], ['Cell', 'by_cell']]) {
      await page.getByRole('tab', { name: tab }).click();
      await expect(page.getByTestId('edge-row')).toHaveCount(api[field].length);
    }
    await expect(page.getByRole('table', { name: /fill quality by week/i })).toBeVisible();
    expect(seen.filter((r) => r.status === 429), '429 from analytics routes').toEqual([]);
    expect(seen.filter((r) => r.status >= 400), JSON.stringify(seen)).toEqual([]);
  });

  test('"Demo + live" sends env=all and the page shows the all-accounts label', async ({ page, request }) => {
    const seen = watchAnalytics(page);
    await openSignedIn(page, request, '/analytics');
    await expect(page.getByTestId('totals')).toBeVisible();
    await page.getByRole('button', { name: 'Demo + live' }).click();
    await expect(page.getByText(/Demo and live accounts/)).toBeVisible();
    expect(seen.some((r) => r.url.includes('/summary') && r.url.includes('env=all'))).toBe(true);
    // the trade list refetches after a 300 ms debounce, so poll instead of asserting right away
    await expect.poll(() => seen.some((r) => r.url.includes('/trades') && r.url.includes('env=all'))).toBe(true);
    expect(seen.filter((r) => r.status >= 400)).toEqual([]);
  });

  test('trades list: first page, filters and Load more come from /api/analytics/trades', async ({ page, request }) => {
    const seen = watchAnalytics(page);
    const { body: all } = await apiGet(request, '/api/analytics/trades?days=90&limit=50');
    expect(all.total).toBeGreaterThan(50);       // seed has ~90 days of fills: enough to paginate
    const { body: losses } = await apiGet(request, '/api/analytics/trades?days=90&result=loss&limit=50');

    await openSignedIn(page, request, '/analytics');
    await page.getByRole('button', { name: '90 days' }).click();
    const rows = page.getByTestId('trade-row');
    await expect(rows).toHaveCount(50);
    await expect(page.getByText(`Showing 50 of ${fmtInt(all.total)}`)).toBeVisible();
    await expect(rows.first()).toContainText(all.trades[0].ticker);

    await page.getByRole('button', { name: 'Load more' }).click();
    const expectedTwo = Math.min(100, all.total);
    await expect(rows).toHaveCount(expectedTwo);
    const tickers = await rows.locator('.row-ticker').allInnerTexts();
    expect(tickers.length).toBe(expectedTwo);

    // server-side filter: only losses, count agrees with the API's own total
    await page.getByText(/^Filters/).click();
    await page.getByRole('combobox', { name: 'Result' }).selectOption('loss');
    await expect(page.getByText('Filters (1 on)')).toBeVisible();
    if (losses.total > 0) {
      await expect(rows).toHaveCount(Math.min(50, losses.total));
      for (const t of await rows.all()) await expect(t).toContainText('Lost');
      await expect(page.getByText(`of ${fmtInt(losses.total)}`)).toBeVisible();
    } else {
      await expect(page.getByText('No trades match these filters.')).toBeVisible();
    }
    // a filter that matches nothing is an empty state, not an error
    await page.getByRole('combobox', { name: 'Status' }).selectOption('open');
    await page.getByRole('combobox', { name: 'Result' }).selectOption('win');
    await expect(page.getByText('No trades match these filters.')).toBeVisible();
    await page.getByRole('button', { name: 'Clear filters' }).click();
    await expect(rows).toHaveCount(50);

    const trades = seen.filter((r) => r.url.includes('/trades'));
    expect(trades.some((r) => r.url.includes('offset=50')), 'Load more requested offset 50').toBe(true);
    expect(trades.some((r) => r.url.includes('result=loss'))).toBe(true);
    expect(seen.filter((r) => r.status === 429), '429 from analytics routes').toEqual([]);
    expect(seen.filter((r) => r.status >= 400)).toEqual([]);
  });

  test('Stop stays in the header on /analytics (not clicked here: the real kill switch is shared state)', async ({ page, request }) => {
    await openSignedIn(page, request, '/analytics');
    await expect(page.locator('header.appbar').getByRole('button', { name: /stop the bot/i })).toBeEnabled();
  });
});

test.describe('sprint extension against the real API (edge verdicts, calibration, fill rate, CSV)', () => {
  test('edge card, calibration and fill rate render the numbers the API returns', async ({ page, request }) => {
    const seen = watchAnalytics(page);
    const { status, body: api } = await apiGet(request, '/api/analytics/summary?days=90');
    expect(status).toBe(200);
    expect(api.by_cell.length, 'seed must produce cells').toBeGreaterThan(0);
    expect(api.calibration.some((b) => b.n > 0), 'seed must fill some calibration buckets').toBe(true);
    expect(api.fill_rate.totals.quotes, 'seed must produce closed quote episodes').toBeGreaterThan(0);

    await openSignedIn(page, request, '/analytics');
    await page.getByRole('button', { name: '90 days' }).click();

    // "Is the edge holding?" card: one verdict count per verdict, adding up to the number of cells with a verdict
    const edge = page.getByTestId('edge-holding');
    await expect(edge).toBeVisible();
    await expect(edge).toContainText('Is the edge holding?');
    const counts = edge.getByRole('list', { name: 'Cells by verdict' });
    const verdicts = api.by_cell.filter((c) => ['too_early', 'on_track', 'below', 'above'].includes(c.verdict));
    const shown = (await counts.locator('li b').allInnerTexts()).map(Number);
    expect(shown.reduce((a, b) => a + b, 0)).toBe(verdicts.length);
    const judged = verdicts.filter((c) => c.verdict !== 'too_early').length;
    if (!judged) await expect(edge).toContainText('Too early to tell');
    else if (verdicts.some((c) => c.verdict === 'below')) await expect(edge).toContainText('Not everywhere');
    else await expect(edge).toContainText('Yes, so far');
    await expect(edge).not.toContainText(/NaN|undefined|null/);

    // calibration: chart + one table row per bucket (empty buckets included)
    const calib = page.getByTestId('calibration');
    await expect(calib).toBeVisible();
    await expect(calib.getByRole('img')).toHaveAttribute('aria-label', /price buckets with fills/);
    await calib.getByText('Bucket numbers').click();
    await expect(calib.getByRole('table', { name: 'Calibration buckets' }).locator('tbody tr')).toHaveCount(api.calibration.length);

    // fill rate: totals and the by-cell / by-series toggle
    const fr = page.getByTestId('fill-rate');
    await expect(fr).toBeVisible();
    await expect(fr).toContainText(fmtInt(api.fill_rate.totals.quotes));
    await expect(fr).toContainText(`${(api.fill_rate.totals.fill_rate * 100).toFixed(0)}%`);
    await expect(fr).not.toContainText(/NaN|undefined|null/);
    await expect(fr.getByRole('table', { name: 'Fill rate by cell' }).locator('tbody tr')).toHaveCount(api.fill_rate.by_cell.length);
    await fr.getByRole('button', { name: 'By series' }).click();
    await expect(fr.getByRole('table', { name: 'Fill rate by series' }).locator('tbody tr')).toHaveCount(api.fill_rate.by_series.length);

    expect(seen.filter((r) => r.status >= 400), JSON.stringify(seen)).toEqual([]);
  });

  test('Download CSV saves the trades the API returns (filters respected)', async ({ page, request }) => {
    const seen = watchAnalytics(page);
    const { body: all } = await apiGet(request, '/api/analytics/trades?days=90&limit=1');
    const { body: losses } = await apiGet(request, '/api/analytics/trades?days=90&result=loss&limit=1');

    await openSignedIn(page, request, '/analytics');
    await page.getByRole('button', { name: '90 days' }).click();
    await expect(page.getByTestId('trade-row').first()).toBeVisible();

    const read = async (dl) => {
      const stream = await dl.createReadStream();
      let text = '';
      for await (const chunk of stream) text += chunk.toString('utf8');
      return text;
    };
    let [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Download CSV' }).click()]);
    expect(dl.suggestedFilename()).toMatch(/^trades-\d{4}-\d{2}-\d{2}\.csv$/);
    let lines = (await read(dl)).trim().split(/\r?\n/);
    expect(lines[0]).toBe('fill_id,ts,ticker,series,side,kind,price,count,fee,cost,band,ttc_hours,ttc_band,cell,status,result,settled_utc,pnl,fill_quality');
    expect(lines.length - 1).toBe(Math.min(all.total, 5000));
    await expect(page.getByText(/Saved trades-/)).toBeVisible();

    // with a filter on, the file holds only the matching rows
    await page.getByText(/^Filters/).click();
    await page.getByRole('combobox', { name: 'Result' }).selectOption('loss');
    await expect(page.getByText('Filters (1 on)')).toBeVisible();
    [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Download CSV' }).click()]);
    lines = (await read(dl)).trim().split(/\r?\n/);
    expect(lines.length - 1).toBe(Math.min(losses.total, 5000));
    expect(seen.filter((r) => r.status === 429), '429 from analytics routes').toEqual([]);
    expect(seen.filter((r) => r.status >= 400), JSON.stringify(seen)).toEqual([]);
  });
});

test.describe('terminology page against the built app', () => {
  test('help icon on /analytics opens the definition and highlights it', async ({ page, request }) => {
    await openSignedIn(page, request, '/analytics');
    await expect(page.getByTestId('totals')).toBeVisible();
    await page.getByTestId('totals').getByRole('link', { name: /what is win rate\?/i }).click();
    await expect(page).toHaveURL(/\/terminology#win-rate$/);
    await expect(page.getByRole('heading', { name: /terminology/i })).toBeVisible();
    const entry = page.locator('#win-rate');
    await expect(entry).toBeVisible();
    await expect(entry).toBeInViewport();
  });

  test('deep link, unknown hash and filter work; no console errors', async ({ page, request }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await openSignedIn(page, request, '/terminology#drawdown');
    await expect(page.locator('#drawdown')).toBeInViewport();

    await page.goto('/terminology#no-such-term');
    await expect(page.getByRole('heading', { name: /terminology/i })).toBeVisible();

    const box = page.getByRole('searchbox').or(page.getByPlaceholder(/search|filter/i)).first();
    await box.fill('kill switch');
    await expect(page.locator('#kill-switch')).toBeVisible();
    await box.fill('zzzzqqq');
    await expect(page.getByText(/no (terms|matches|results)|nothing/i).first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('Stop is in the header on /terminology', async ({ page, request }) => {
    await openSignedIn(page, request, '/terminology');
    await expect(page.locator('header.appbar').getByRole('button', { name: /stop the bot/i })).toBeEnabled();
  });
});
