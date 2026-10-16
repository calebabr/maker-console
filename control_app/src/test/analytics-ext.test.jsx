// Sprint extension (docs/ANALYTICS_CONTRACT.md items 1-4 and 7): verdicts, clustered CIs,
// calibration, fill rate and the CSV download.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { filenameFrom, setToken } from '../services/api.js';
import { analyticsSummary, analyticsTrades, analyticsTradesCsv, csvCell, mockMyTrades, wilson } from '../services/mockAnalytics.js';
import WinRateCiBar from '../components/analytics/WinRateCiBar.jsx';
import { EdgeRow } from '../components/analytics/EdgeBreakdown.jsx';
import EdgeHolding, { edgeHeadline } from '../components/analytics/EdgeHolding.jsx';
import Calibration from '../components/analytics/Calibration.jsx';
import FillRate, { minutes } from '../components/analytics/FillRate.jsx';
import { checkSummary } from './contractShape.js';
import { renderApp, STATUS } from './helpers.jsx';

const NOW = Date.parse('2026-10-13T18:00:00Z');
const qs = (path) => new URL(path, 'http://x').searchParams;
// Serve the rich 180-day mock for whatever window is asked (keeps `days` consistent).
const richSummary = (path) => {
  const q = qs(path);
  const days = Number(q.get('days') || 30);
  const [, body] = analyticsSummary(new URLSearchParams('days=180'), { mode: 'demo', nowMs: NOW });
  return { ...body, days, env: q.get('env') || 'demo' };
};

// fetch stub with response headers (CSV needs Content-Disposition / X-Truncated / Retry-After).
function stubFetch(handlers) {
  const calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url, init = {}) => {
      const path = String(url).replace('http://api.test', '');
      const key = `${init.method || 'GET'} ${path.split('?')[0]}`;
      calls.push({ key, path, q: qs(path), headers: init.headers || {} });
      const h = handlers[key];
      const [status, body, headers = {}] = h ? h(path) : [404, { detail: 'not found' }];
      const text = typeof body === 'string' ? body : JSON.stringify(body);
      const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
      return Promise.resolve({
        ok: status < 300,
        status,
        text: () => Promise.resolve(text),
        headers: { get: (n) => lower[String(n).toLowerCase()] ?? null },
      });
    }),
  );
  return calls;
}

const ROUTES = {
  'GET /api/status': () => [200, STATUS],
  'GET /api/analytics/summary': (p) => [200, richSummary(p)],
  'GET /api/analytics/trades': (p) => analyticsTrades(qs(p), { mode: 'demo', nowMs: NOW }),
  'GET /api/analytics/my-trades': (p) => mockMyTrades(qs(p), { nowMs: NOW }),
  'GET /api/analytics/trades.csv': (p) => analyticsTradesCsv(qs(p), { mode: 'demo', nowMs: NOW }),
};

let created;
beforeEach(() => {
  setToken(null);
  created = [];
  URL.createObjectURL = vi.fn((b) => {
    created.push(b);
    return 'blob:mock';
  });
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

const inRouter = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

function cellRow(over = {}) {
  const [lo, hi] = wilson(38, 64);
  return {
    key: '0.35-0.50|3-9h|yes', cell: '0.35-0.50|3-9h|yes', in_config: true, n: 64, wins: 38, contracts: 300, cost: 280, pnl: 4,
    roi: 0.014, avg_price: 0.53, win_rate: 38 / 64, win_ci_low: lo, win_ci_high: hi,
    edge: 38 / 64 - 0.53, edge_ci_low: lo - 0.53, edge_ci_high: hi - 0.53,
    cluster_ci_low: 0.48, cluster_ci_high: 0.7, edge_cluster_ci_low: -0.05, edge_cluster_ci_high: 0.06,
    roi_ci_low: -0.02, roi_ci_high: 0.04, markets: 40, expected_win_rate: 0.6, expected_roi: 0.015,
    verdict: 'on_track', verdict_reason: 'Won 59.4% against an expected 60.0%; the difference is within normal noise.',
    ...over,
  };
}

// ---------------------------------------------------------------- 1. verdicts
describe('edge verdicts (item 1)', () => {
  it('headline: too early / below (singular and plural) / holding', () => {
    const c = (verdict) => ({ verdict });
    expect(edgeHeadline([c('too_early'), c('too_early')]).text).toMatch(/^Too early to tell/);
    expect(edgeHeadline([c('below'), c('on_track'), c('too_early')]).text).toBe('Not everywhere. 1 of 2 judged market types is below.');
    expect(edgeHeadline([c('below'), c('below')]).text).toBe('Not everywhere. 2 of 2 judged market types are below.');
    expect(edgeHeadline([c('on_track'), c('above')]).text).toBe('Yes, so far. All 2 judged market types are on track or better.');
    expect(edgeHeadline([c('above')]).text).toBe('Yes, so far. The 1 judged market type is on track or better.');
  });

  it('summary card lists below/above cells with their reasons, and notes a missing benchmark', () => {
    const s = {
      by_cell: [
        cellRow({ verdict: 'below', verdict_reason: 'Server text.' }),
        cellRow({ key: '0.50-0.62|9-30h|no', cell: '0.50-0.62|9-30h|no', verdict: 'too_early', verdict_reason: 'Only 5 markets.' }),
      ],
      expectations: { source: 'default', loaded: false },
    };
    inRouter(<EdgeHolding summary={s} />);
    const card = screen.getByTestId('edge-holding');
    expect(within(card).getByText('Not everywhere. 1 of 1 judged market type is below.')).toBeInTheDocument();
    expect(within(card).getByText('Won 59%; expected 60%')).toBeInTheDocument();
    expect(within(card).queryByText('Only 5 markets.')).toBeNull(); // too-early cells are counted, not listed
    expect(within(card).getByText(/No research benchmark is loaded/)).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: /what is/i })).toHaveAttribute('data-term', 'edge-verdict');
  });

  it('summary card is not shown when no row carries a verdict (older server)', () => {
    const { container } = inRouter(<EdgeHolding summary={{ by_cell: [{ key: 'x', n: 3 }] }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('cell row: folded summary has the chip, P&L and markets; opening shows reason, numbers and edge range', async () => {
    const user = userEvent.setup();
    const domain = [0.4, 0.8];
    const { rerender } = inRouter(<ul><EdgeRow tab="cell" g={cellRow()} domain={domain} /></ul>);
    let row = screen.getByTestId('edge-row');
    expect(within(row).getByText('On Track')).toHaveAttribute('data-verdict', 'on_track');
    expect(within(row).getByText('+$4.00')).toBeInTheDocument();
    expect(within(row).getByText('40')).toBeInTheDocument(); // markets
    expect(row).toHaveAttribute('data-thin', 'false');
    expect(within(row).queryByText(/Edge Range/)).toBeNull(); // folded
    await user.click(within(row).getByRole('button', { expanded: false }));
    expect(within(row).getByText('Won 59%; expected 60%')).toBeInTheDocument();
    expect(within(row).getByText(/Edge Range \(Win Rate Minus Break-Even\): −5\.0% to \+6\.0%/)).toBeInTheDocument();
    // The % axis and the key only exist inside an open row.
    expect(within(row).getByTestId('ci-scale')).toBeInTheDocument();
    expect(within(row).getByTestId('bar-legend')).toHaveTextContent('Break-Even (Price Paid 53¢)');
    expect(within(row).getByTestId('bar-ends')).toBeInTheDocument();
    expect(within(row).getByText('60.0%')).toBeInTheDocument(); // expected
    expect(row.textContent).not.toMatch(/\bpts\b/);
    rerender(<MemoryRouter><ul><EdgeRow tab="cell" g={cellRow({ verdict: 'too_early', markets: 7 })} domain={domain} /></ul></MemoryRouter>);
    row = screen.getByTestId('edge-row');
    expect(row).toHaveAttribute('data-thin', 'true');
    expect(within(row).getByText('Too Early')).toHaveAttribute('title', 'Not enough markets yet to judge.');
    rerender(<MemoryRouter><ul><EdgeRow tab="cell" g={cellRow({ verdict: 'below' })} domain={domain} /></ul></MemoryRouter>);
    expect(within(screen.getByTestId('edge-row')).getByText('Below')).toHaveAttribute('title', 'Clearly worse than expected. Worth a look.');
  });

  it('page: card at the top, verdict badges on cell rows (rich mock)', async () => {
    stubFetch(ROUTES);
    renderApp('/analytics');
    const card = await screen.findByTestId('edge-holding');
    expect(within(card).getByText(/judged market type/)).toBeInTheDocument();
    // The card comes before the totals.
    const totals = screen.getByTestId('totals');
    expect(card.compareDocumentPosition(totals) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Edge' }));
    const rows = await screen.findAllByTestId('edge-row');
    expect(rows.some((r) => within(r).queryByText('Below'))).toBe(true);
  });
});

// ---------------------------------------------------------------- 2. clustered CI
describe('clustered CI bar (item 2)', () => {
  const X = (v, [d0, d1] = [0.4, 0.6]) => 4 + ((v - d0) / (d1 - d0)) * 292;

  it('main bar is the clustered range; Wilson is a thin line; expected is a dashed mark; price tick stays last', () => {
    const { container } = render(
      <WinRateCiBar winRate={0.5} low={0.45} high={0.54} clusterLow={0.42} clusterHigh={0.57} expected={0.52} avgPrice={0.48} domain={[0.4, 0.6]} />,
    );
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('data-clustered', 'true');
    const rect = svg.querySelector('rect');
    expect(Number(rect.getAttribute('x'))).toBeCloseTo(X(0.42), 3);
    expect(Number(rect.getAttribute('width'))).toBeCloseTo(X(0.57) - X(0.42), 3);
    const wil = svg.querySelector('[data-mark="wilson"]');
    expect(Number(wil.getAttribute('x1'))).toBeCloseTo(X(0.45), 3);
    expect(Number(wil.getAttribute('x2'))).toBeCloseTo(X(0.54), 3);
    expect(Number(svg.querySelector('[data-mark="expected"]').getAttribute('x1'))).toBeCloseTo(X(0.52), 3);
    const lines = svg.querySelectorAll('line');
    expect(Number(lines[lines.length - 1].getAttribute('x1'))).toBeCloseTo(X(0.48), 3);
    expect(svg.getAttribute('aria-label')).toMatch(/clustered likely range 42\.0% to 57\.0% \(per-fill 45\.0% to 54\.0%\).*expected 52\.0%/);
    // Verdict colour follows the clustered range: 0.42 < 0.48 paid -> straddles.
    expect(svg).toHaveAttribute('data-verdict', 'straddles');
  });

  it('without clustered bounds it falls back to Wilson and draws no thin line', () => {
    const { container } = render(<WinRateCiBar winRate={0.5} low={0.49} high={0.54} avgPrice={0.48} domain={[0.4, 0.6]} />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('data-clustered', 'false');
    expect(svg.querySelector('[data-mark="wilson"]')).toBeNull();
    expect(svg.querySelector('[data-mark="expected"]')).toBeNull();
    expect(svg).toHaveAttribute('data-verdict', 'above');
  });

  it('the explainer carries a (?) for clustered ranges', async () => {
    stubFetch(ROUTES);
    renderApp('/analytics?tab=edge');
    const note = await screen.findByTestId('edge-legend');
    
    expect(note.querySelector('[data-term="clustered-ci"]')).not.toBeNull();
  });

  it('mock: clustered CI brackets the win rate and is never narrower than Wilson', () => {
    const [, s] = analyticsSummary(new URLSearchParams('days=180'), { nowMs: NOW });
    for (const g of [...s.by_cell, ...s.by_series]) {
      expect(g.cluster_ci_low).toBeLessThanOrEqual(g.win_rate + 1e-9);
      expect(g.cluster_ci_high).toBeGreaterThanOrEqual(g.win_rate - 1e-9);
      expect(g.cluster_ci_low).toBeLessThanOrEqual(g.win_ci_low + 1e-9);
      expect(g.cluster_ci_high).toBeGreaterThanOrEqual(g.win_ci_high - 1e-9);
    }
    for (const g of s.by_cell) if (g.markets < 20) expect(g.verdict).toBe('too_early');
    expect(s.by_cell.some((g) => g.verdict === 'below')).toBe(true);
    expect(checkSummary(s, { days: 180 })).toEqual([]);
  });
});

// ---------------------------------------------------------------- 3. calibration
describe('calibration chart (item 3)', () => {
  const b = (lo, n, wr, ci = [wr - 0.05, Math.min(1, wr + 0.03)], extra = {}) => ({
    bucket: `${lo.toFixed(2)}-${(lo + 0.01).toFixed(2)}`, lo, hi: lo + 0.02, n, markets: n, wins: Math.round(n * wr),
    win_rate: n ? wr : null, avg_price: n ? lo + 0.01 : null, ci_low: n ? ci[0] : null, ci_high: n ? ci[1] : null, ...extra,
  });

  it('draws one dot per non-empty bucket, sized by n, with the fair-price diagonal and tones', () => {
    const buckets = [b(0.4, 10, 0.55, [0.5, 0.59]), b(0.42, 0, 0), b(0.5, 200, 0.4, [0.35, 0.45]), b(0.56, 50, 0.57)];
    inRouter(<Calibration buckets={buckets} />);
    const box = screen.getByTestId('calibration');
    const svg = within(box).getByRole('img');
    expect(svg.querySelector('[data-mark="fair"]')).not.toBeNull();
    const groups = svg.querySelectorAll('[data-bucket]');
    expect(groups).toHaveLength(3); // the empty bucket has no dot
    const r = (bucket) => Number(svg.querySelector(`[data-bucket="${bucket}"] circle`).getAttribute('r'));
    expect(r('0.50-0.51')).toBeGreaterThan(r('0.40-0.41'));
    expect(svg.querySelector('[data-bucket="0.40-0.41"]')).toHaveAttribute('data-tone', 'above');
    expect(svg.querySelector('[data-bucket="0.50-0.51"]')).toHaveAttribute('data-tone', 'below');
    expect(svg.querySelector('[data-bucket="0.56-0.57"]')).toHaveAttribute('data-tone', 'level');
    expect(svg.getAttribute('aria-label')).toMatch(/3 price buckets.*1 clearly above.*1 clearly below/);
    // The numbers table lists every bucket, including the empty one.
    expect(within(box).getByRole('table', { name: 'Calibration buckets' }).querySelectorAll('tbody tr')).toHaveLength(4);
  });

  it('shows a message instead of a chart when no bucket has fills', () => {
    inRouter(<Calibration buckets={[b(0.4, 0, 0)]} />);
    expect(screen.getByText(/No settled maker fills to compare/)).toBeInTheDocument();
    expect(screen.queryByTestId('calibration')).toBeNull();
  });

  it('mock: 2-cent buckets over the example cells (0.35 to 0.62) with the last one inclusive', () => {
    const [, s] = analyticsSummary(new URLSearchParams('days=180'), { nowMs: NOW });
    const want = [];
    for (let c = 35; c <= 61; c += 2) want.push(`${(c / 100).toFixed(2)}-${((c + 1) / 100).toFixed(2)}`);
    expect(s.calibration.map((x) => x.bucket)).toEqual(want);
    expect(s.calibration[s.calibration.length - 1].bucket).toBe('0.61-0.62');
    expect(s.calibration.reduce((t, x) => t + x.n, 0)).toBe(s.by_band.filter((g) => g.key !== 'other').reduce((t, g) => t + g.n, 0));
  });
});

// ---------------------------------------------------------------- 4. fill rate
describe('fill rate (item 4)', () => {
  const FR = {
    totals: { quotes: 100, filled: 40, partial: 5, unfilled: 60, fill_rate: 0.4, median_rest_minutes: 130, median_minutes_to_fill: 45.2 },
    by_cell: [{ key: '0.50-0.62|9-30h|no', quotes: 60, filled: 30, partial: 2, unfilled: 30, fill_rate: 0.5, median_rest_minutes: 20, median_minutes_to_fill: 12 }],
    by_series: [{ key: 'KXDEMOB', quotes: 100, filled: 40, partial: 5, unfilled: 60, fill_rate: 0.4, median_rest_minutes: 130, median_minutes_to_fill: 45 }],
  };

  it('minutes(): minutes under 90, hours above, dash for null', () => {
    expect(minutes(45.2)).toBe('45 min');
    expect(minutes(130)).toBe('2.2 h');
    expect(minutes(null)).toBe('—');
  });

  it('shows totals and switches between by-cell and by-series tables', async () => {
    const user = userEvent.setup();
    inRouter(<FillRate fr={FR} />);
    const box = screen.getByTestId('fill-rate');
    expect(within(box).getByText('40%')).toBeInTheDocument();
    expect(within(box).getByText('2.2 h')).toBeInTheDocument();
    expect(within(box).getByText('45 min')).toBeInTheDocument();
    expect(within(box).getByRole('table', { name: 'Fill rate by cell' })).toHaveTextContent('50-62¢ 9-30 h NO');
    await user.click(within(box).getByRole('button', { name: 'By series' }));
    expect(within(box).getByRole('table', { name: 'Fill rate by series' })).toHaveTextContent('Demo series B');
    expect(within(box).getAllByRole('button', { name: /what is/i }).map((a) => a.getAttribute('data-term'))).toEqual(
      expect.arrayContaining(['missed-fill', 'rest-time']),
    );
  });

  it('no closed quotes -> a short message', () => {
    inRouter(<FillRate fr={{ totals: { ...FR.totals, quotes: 0 }, by_cell: [], by_series: [] }} />);
    expect(screen.getByText('No closed quotes in this window.')).toBeInTheDocument();
  });

  it('page shows the fill-rate card from the summary', async () => {
    stubFetch(ROUTES);
    renderApp('/analytics?tab=execution');
    expect(await screen.findByTestId('fill-rate')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------- 7. CSV
describe('CSV download (item 7)', () => {
  async function openAndDownload(user) {
    await screen.findAllByTestId('trade-row');
    await user.click(screen.getByRole('button', { name: 'Download CSV' }));
  }

  it('fetches trades.csv with auth and the current filters (no limit/offset) and saves the file', async () => {
    const user = userEvent.setup();
    const calls = stubFetch(ROUTES);
    renderApp('/analytics?tab=trades');
    await screen.findAllByTestId('trade-row');
    await user.click(screen.getByText(/^Filters/));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Result' }), 'loss');
    await user.click(screen.getByRole('button', { name: 'Download CSV' }));
    expect(await screen.findByText(/^Saved trades-2026-10-13\.csv\.$/)).toBeInTheDocument();
    const call = calls.find((c) => c.key === 'GET /api/analytics/trades.csv');
    expect(call.headers.Authorization).toBe('Bearer tok');
    expect(call.q.get('result')).toBe('loss');
    expect(call.q.get('days')).toBe('30');
    expect(call.q.has('limit')).toBe(false);
    expect(call.q.has('offset')).toBe(false);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    const text = await created[0].text();
    expect(text.split('\r\n')[0]).toBe('fill_id,ts,ticker,series,side,kind,price,count,fee,cost,band,ttc_hours,ttc_band,cell,status,result,settled_utc,pnl,fill_quality');
    expect(text).toContain(',loss,');
    expect(text).not.toContain(',win,');
  });

  it('X-Truncated: true tells the owner only the first 5,000 were saved', async () => {
    const user = userEvent.setup();
    stubFetch({
      ...ROUTES,
      'GET /api/analytics/trades.csv': () => [200, 'fill_id\r\n', { 'Content-Disposition': 'attachment; filename="t.csv"', 'X-Truncated': 'true' }],
    });
    renderApp('/analytics?tab=trades');
    await openAndDownload(user);
    expect(await screen.findByText(/Saved t\.csv\. It holds the first 5,000 trades only/)).toBeInTheDocument();
  });

  it('headers not exposed by CORS: builds the filename locally and infers truncation from the 5,000-row cap', async () => {
    const user = userEvent.setup();
    const header = 'fill_id,ts';
    let rows = 3;
    stubFetch({
      ...ROUTES,
      // No Content-Disposition / X-Truncated readable (no CORS expose_headers).
      'GET /api/analytics/trades.csv': () => [200, [header, ...Array.from({ length: rows }, (_, i) => `f${i},t`)].join('\r\n') + '\r\n'],
    });
    renderApp('/analytics?tab=trades');
    await openAndDownload(user);
    expect(await screen.findByText(/^Saved trades-\d{4}-\d{2}-\d{2}\.csv\.$/)).toBeInTheDocument();
    rows = 5000;
    await user.click(screen.getByRole('button', { name: 'Download CSV' }));
    expect(await screen.findByText(/It holds the first 5,000 trades only/)).toBeInTheDocument();
  });

  it('429 shows "Too many requests" with Retry-After and saves nothing', async () => {
    const user = userEvent.setup();
    stubFetch({ ...ROUTES, 'GET /api/analytics/trades.csv': () => [429, { detail: 'rate_limited' }, { 'Retry-After': '30' }] });
    renderApp('/analytics?tab=trades');
    await openAndDownload(user);
    expect(await screen.findByText('Too many requests — try again in 30 s.')).toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeEnabled();
  });

  it('401 signs the user out', async () => {
    const user = userEvent.setup();
    stubFetch({ ...ROUTES, 'GET /api/analytics/trades.csv': () => [401, { detail: 'not_authenticated' }] });
    renderApp('/analytics?tab=trades');
    await openAndDownload(user);
    expect(await screen.findByLabelText('Username')).toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('filenameFrom: quoted, RFC 5987 and unsafe names; fallback when missing', () => {
    expect(filenameFrom('attachment; filename="a.csv"', 'x.csv')).toBe('a.csv');
    expect(filenameFrom("attachment; filename*=UTF-8''b%20c.csv", 'x.csv')).toBe('b c.csv');
    expect(filenameFrom('attachment; filename="../../etc/p.csv"', 'x.csv')).toBe('.._.._etc_p.csv');
    expect(filenameFrom(null, 'x.csv')).toBe('x.csv');
  });

  it('mock CSV: filters honoured, injection-safe cells, filename and truncation header', () => {
    const [st, text, h] = analyticsTradesCsv(new URLSearchParams('days=180&series=KXDEMOB&kind=maker'), { nowMs: NOW });
    expect(st).toBe(200);
    const lines = text.trim().split('\r\n');
    expect(lines.length).toBeGreaterThan(10);
    lines.slice(1).forEach((l) => expect(l.split(',')[3]).toBe('KXDEMOB'));
    expect(h['Content-Disposition']).toBe('attachment; filename="trades-2026-10-13.csv"');
    expect(h['X-Truncated']).toBeUndefined();
    expect(csvCell('=HYPERLINK("x")')).toBe(`"'=HYPERLINK(""x"")"`);
    expect(csvCell('@SUM')).toBe("'@SUM");
    expect(csvCell(-1.5)).toBe('-1.5');
    expect(csvCell(null)).toBe('');
    expect(analyticsTradesCsv(new URLSearchParams('side=up'), { nowMs: NOW })[0]).toBe(422);
  });
});
