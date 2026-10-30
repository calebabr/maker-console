// Sprint UI-1a stage 1: correct data and honest labels inside the current pages.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setToken } from '../services/api.js';
import { analyticsSummary, mockLedger, mockScorecard } from '../services/mockAnalytics.js';
import { checkSummary } from './contractShape.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';
import { todayKind } from '../utils/pnlScopes.js';

const NOW = Date.parse('2026-10-13T18:00:00Z');
const qs = (call) => new URL(call.path, 'http://x').searchParams;
const VIEWER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

beforeEach(() => setToken(null));
afterEach(() => vi.unstubAllGlobals());

function summary(call, over = {}) {
  const [, data] = analyticsSummary(qs(call), { mode: 'demo', nowMs: NOW });
  return { ...data, ...over };
}

const WR = {
  maker: { unit: 'fill', n: 40, wins: 36, losses: 4, win_rate: 0.9, ci_low: 0.77, ci_high: 0.96, method: 'wilson95' },
  arb: { unit: 'set', n: 3, wins: 2, losses: 1, win_rate: 0.6667, ci_low: 0.21, ci_high: 0.94, method: 'wilson95' },
};

describe('mock stays in the contract', () => {
  it('the mock summary passes the strict checker including the UI-1a keys', () => {
    const [, data] = analyticsSummary(new URLSearchParams('days=30'), { mode: 'demo', nowMs: NOW });
    expect(checkSummary(data)).toEqual([]);
    expect(data.tz_used).toBe('UTC');
  });
});

describe('local dates and win rate per strategy', () => {
  it('sends the viewer time zone on the summary and names it in the scope label', async () => {
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/analytics/summary': (c) => summary(c, { tz_used: 'America/Chicago' }),
    });
    renderApp('/analytics');
    const totals = await screen.findByTestId('totals');
    const summaryCall = calls.find((c) => c.key === 'GET /api/analytics/summary');
    expect(qs(summaryCall).get('tz')).toBe(VIEWER_TZ);
    const scope = totals.parentElement.querySelector('[data-testid="scope-analytics"]');
    expect(scope).toHaveTextContent('by settlement date (America/Chicago)');
    expect(scope).not.toHaveTextContent('UTC');
  });

  it('shows maker per fill and arbitrage per set with intervals, and never the pooled rate', async () => {
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/analytics/summary': (c) => summary(c, { win_rate_by_strategy: WR }),
    });
    renderApp('/analytics');
    const maker = await screen.findByTestId('wr-maker');
    expect(maker).toHaveTextContent('Per Fill');
    expect(maker).toHaveTextContent('90.0%');
    expect(maker).toHaveTextContent('36 of 40 fills won');
    expect(maker).toHaveTextContent('95% range 77.0% to 96.0%');
    const arb = screen.getByTestId('wr-arb');
    expect(arb).toHaveTextContent('Per Set');
    expect(arb).toHaveTextContent('2 of 3 sets won');
    const totals = screen.getByTestId('totals');
    expect(within(totals).queryByText('Win Rate')).toBeNull();
  });

  it('says no settled sets instead of 0% for an empty strategy', async () => {
    const empty = { ...WR, arb: { ...WR.arb, n: 0, wins: 0, losses: 0, win_rate: null, ci_low: null, ci_high: null } };
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/analytics/summary': (c) => summary(c, { win_rate_by_strategy: empty }),
    });
    renderApp('/analytics');
    expect(await screen.findByTestId('wr-arb')).toHaveTextContent('No settled yet');
  });
});

const ARB_WINDOW = (o = {}) => ({
  since: '2026-10-13T00:00:00Z', sets: 3, events: 3, sightings: 20,
  by_outcome: { filled: 1, partial: 1, error: 0, skipped: 1, log_only: 0 }, skipped_by_rule: { series_cap: 1 }, ...o,
});
const ARB_SETS = [
  {
    set_id: 's1', event: 'KXDEMOA-26OCT13', kind: 'YES', mode: 'demo-execute', executed_utc: '2026-10-13T12:00:00Z', outcome: 'filled',
    contracts: 5, cost: 4.7, guaranteed_profit: 0.3, status: 'SETTLED', complete: true, realized_pnl: 0.28, settled_utc: '2026-10-14T00:00:00Z', notes: [],
    legs: [
      { ticker: 'KXDEMOA-26OCT13-B1', side: 'yes', price: 0.4, contracts: 5, filled: 5, settled: true, result: 'win', pnl: 3 },
      { ticker: 'KXDEMOA-26OCT13-B2', side: 'yes', price: 0.54, contracts: 5, filled: 5, settled: true, result: 'loss', pnl: -2.72 },
    ],
  },
  {
    set_id: 's2', event: 'KXDEMOB-26OCT13', kind: 'NO', mode: 'demo-execute', executed_utc: '2026-10-13T13:00:00Z', outcome: 'partial',
    contracts: 5, cost: 2, guaranteed_profit: 0.2, status: 'PARTIAL', complete: false, realized_pnl: null, settled_utc: null, notes: ['one leg short'],
    legs: [{ ticker: 'KXDEMOB-26OCT13-B1', side: 'no', price: 0.4, contracts: 5, filled: 2, settled: false, result: null, pnl: null }],
  },
];

describe('Arbitrage page labels', () => {
  const arbBody = {
    settings: { enabled: true, live: false, arb_poll_seconds: 15, arb_series: [] },
    status: {
      state: 'log-only', running: true, kill_switch: false, live_allowed: false, scans: 571, found: 99, last_scan_utc: null,
      since_restart_utc: '2026-10-13T10:00:00Z',
    },
    pnl: null,
    arb_summary: {
      tz: 'UTC', source: 'log', today: ARB_WINDOW(), period: { label: 'last 30 days', ...ARB_WINDOW({ sets: 7, sightings: 60 }) },
      per_set: null, since_restart: { label: 'Since restart', since_utc: '2026-10-13T10:00:00Z', scans: 571, sightings: 99 },
    },
  };
  const routes = () => ({
    'GET /api/status': STATUS,
    'GET /api/arb': arbBody,
    'GET /api/arb/opportunities': { items: [] },
    'GET /api/analytics/summary': (c) => summary(c, { arb_sets: ARB_SETS, arb_sets_total: 2 }),
  });

  it('relabels the tiles: scans since restart with its time, no bare Opportunities', async () => {
    stubApi(routes());
    renderApp('/arb');
    const scans = await screen.findByTestId('scans-since-restart');
    expect(scans).toHaveTextContent('571');
    expect(screen.getByTestId('restart-time')).toHaveTextContent(/^since /);
    expect(screen.getByText(/Scans since restart/)).toBeInTheDocument();
    expect(screen.getByText(/Sightings since restart/)).toBeInTheDocument();
    expect(screen.queryByText(/^Opportunities$/)).toBeNull();
  });

  it('shows distinct sets today and for the period with the gate-outcome breakdown', async () => {
    stubApi(routes());
    renderApp('/arb');
    const today = await screen.findByTestId('arb-today');
    expect(within(today).getByTestId('arb-today-sets')).toHaveTextContent('3');
    expect(today).toHaveTextContent('Skipped by a risk rule');
    expect(today).toHaveTextContent('series cap 1');
    expect(screen.getByTestId('arb-period-sets')).toHaveTextContent('7');
  });

  it('lists one row per set with expandable legs, status and guaranteed vs realized', async () => {
    const user = userEvent.setup();
    stubApi(routes());
    renderApp('/arb');
    const rows = await screen.findAllByTestId('arb-set-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Settled');
    expect(rows[0]).toHaveTextContent('guaranteed $0.30');
    expect(rows[0]).toHaveTextContent('realized +$0.28');
    expect(rows[1]).toHaveTextContent('Partial');
    expect(rows[1]).toHaveTextContent('not available');
    await user.click(within(rows[0]).getByText('KXDEMOA-26OCT13'));
    expect(within(rows[0]).getByText(/KXDEMOA-26OCT13-B2 YES/)).toBeInTheDocument();
    expect(within(rows[1]).getByText('one leg short')).toBeInTheDocument();
  });
});

describe('Ledger and scorecard', () => {
  const LEDGER = (rec = {}) => ({
    ...mockLedger(new URLSearchParams(), 'demo'),
    reconciliation: {
      tolerance: 0.1, flag: false, residual: 0.02, equity_change: 2.37, net_flows: 0, realized_pnl: 2.25, expected_equity_change: 2.35,
      start_exact: true, notes: [], ...rec,
    },
  });
  const routes = (ledger, extra = {}) => ({
    'GET /api/status': STATUS,
    'GET /api/analytics/summary': (c) => summary(c),
    'GET /api/analytics/ledger': ledger,
    'GET /api/analytics/scorecard': (c) => mockScorecard(qs(c), 'demo'),
    ...extra,
  });

  it('shows the strategy table and a within-tolerance residual', async () => {
    const calls = stubApi(routes(() => LEDGER()));
    renderApp('/analytics?tab=ledger');
    const table = await screen.findByTestId('ledger-table');
    expect(within(table).getByText('Maker')).toBeInTheDocument();
    expect(within(table).getByText('Arbitrage')).toBeInTheDocument();
    expect(screen.getByTestId('recon-residual')).toHaveTextContent('+$0.02');
    expect(screen.getByTestId('recon-ok')).toBeInTheDocument();
    expect(screen.queryByTestId('recon-flag')).toBeNull();
    expect(qs(calls.find((c) => c.key === 'GET /api/analytics/ledger')).get('tz')).toBe(VIEWER_TZ);
  });

  it('flags a residual over the tolerance', async () => {
    stubApi(routes(() => LEDGER({ flag: true, residual: 1.5 })));
    renderApp('/analytics?tab=ledger');
    expect(await screen.findByTestId('recon-flag')).toHaveTextContent('Over tolerance');
    expect(screen.getByTestId('reconciliation').className).toMatch(/flag/);
  });

  it('shows null as "not available" with the notes, never $0.00', async () => {
    stubApi(routes(() => LEDGER({ residual: null, equity_change: null, expected_equity_change: null, flag: false, notes: ['Fewer than 2 balance snapshots.'] })));
    renderApp('/analytics?tab=ledger');
    const rec = await screen.findByTestId('reconciliation');
    expect(within(rec).getByTestId('recon-residual')).toHaveTextContent('not available');
    expect(rec).toHaveTextContent('Fewer than 2 balance snapshots.');
    expect(within(rec).getAllByText('not available').length).toBeGreaterThanOrEqual(3);
  });

  it('scorecard: null metrics say "not tracked yet" and the return is labelled demo, with sample size', async () => {
    stubApi(routes(() => LEDGER()));
    renderApp('/analytics?tab=scorecard');
    const sc = await screen.findByTestId('scorecard');
    expect(within(sc).getByTestId('sc-order-errors')).toHaveTextContent('not tracked yet');
    expect(within(sc).getByTestId('sc-cap-maker')).toHaveTextContent('not tracked yet');
    const ret = screen.getByTestId('sc-return');
    expect(within(ret).getByTestId('sc-return-label')).toHaveTextContent('demo, indicative only');
    expect(ret).toHaveTextContent('12 settled fills');
    expect(screen.getByText(/not stored yet/)).toBeInTheDocument();
  });
});

describe('Cash-flow form', () => {
  const PERIODS = { periods: [{ id: 'p2', name: 'Demo Period 2', current: true, started_at: '2026-03-01T00:00:00Z' }], cash_flows: [] };
  async function submit(user) {
    await user.type(await screen.findByLabelText('Amount ($)'), '50');
    await user.click(screen.getByRole('button', { name: /Add Entry/ }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Add Cash Flow', exact: true }));
  }
  const base = (post) => stubApi({ 'GET /api/status': STATUS, 'GET /api/periods': PERIODS, 'GET /api/periods/p2/summary': {}, 'POST /api/cash-flows': post });

  it('requires the date field', async () => {
    const user = userEvent.setup();
    base(PERIODS);
    renderApp('/account');
    await user.type(await screen.findByLabelText('Amount ($)'), '50');
    expect(screen.getByLabelText('When it happened')).toBeRequired();
  });

  it('shows the server 422 messages', async () => {
    const user = userEvent.setup();
    base([422, { detail: ['ts must not be in the future'] }]);
    renderApp('/account');
    await submit(user);
    expect(await screen.findByTestId('flow-422')).toHaveTextContent('ts must not be in the future');
  });

  it('409 duplicate asks "Add anyway" and then resends with confirm_duplicate and a fresh code', async () => {
    const user = userEvent.setup();
    const calls = base((c) => (c.body.confirm_duplicate ? PERIODS : [409, { detail: ['duplicate: same type, amount and date already recorded'] }]));
    renderApp('/account');
    await submit(user);
    expect(await screen.findByText(/duplicate: same type/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add anyway' }));
    await user.type(await screen.findByLabelText('6-digit code'), '654321');
    await user.click(screen.getByRole('button', { name: 'Add Cash Flow', exact: true }));
    await screen.findByText('Cash Flow Recorded');
    const posts = calls.filter((c) => c.key === 'POST /api/cash-flows');
    expect(posts).toHaveLength(2);
    expect(posts[0].body.confirm_duplicate).toBeUndefined();
    expect(posts[1].body.confirm_duplicate).toBe(true);
    expect(posts[1].headers['X-2FA-Code']).toBe('654321');
  });

  it('declining "Add anyway" sends nothing more', async () => {
    const user = userEvent.setup();
    const calls = base([409, { detail: ['duplicate: same'] }]);
    renderApp('/account');
    await submit(user);
    await screen.findByText(/duplicate: same/);
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByText('Possible duplicate')).toBeNull());
    expect(calls.filter((c) => c.key === 'POST /api/cash-flows')).toHaveLength(1);
  });
});

describe('Shadow capital setting', () => {
  const CONFIG = {
    config: { run_budget: 25, poll_minutes: 10 },
    schema: { run_budget: { type: 'number', min: 1, max: 500 }, poll_minutes: { type: 'integer', min: 1 } },
    ceilings: {},
    shadow_capital: { value: 150, default: 150, min: 50, max: 1000, unit: 'USD', help: 'Scales dollar P&L.', editable: true, write_via: 'PUT /api/cloud', needs_fresh_2fa: true },
  };

  it('shows the bounds and help, and writes through PUT /api/cloud with a fresh 2FA code', async () => {
    const user = userEvent.setup();
    const calls = stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG, 'PUT /api/cloud': { settings: { shadow_capital: 300 } } });
    renderApp('/settings');
    const input = await screen.findByLabelText(/Shadow capital \(USD\)/);
    expect(screen.getByText(/Range 50 – 1000/)).toBeInTheDocument();
    expect(screen.getByText('Scales dollar P&L.')).toBeInTheDocument();
    await user.clear(input);
    await user.type(input, '300');
    await user.click(screen.getByRole('button', { name: /Save Shadow Capital/ }));
    expect(calls.some((c) => c.key === 'PUT /api/cloud')).toBe(false); // nothing is sent before the code
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    const saves = screen.getAllByRole('button', { name: 'Save', exact: true });
    await user.click(saves[saves.length - 1]);
    await waitFor(() => expect(calls.some((c) => c.key === 'PUT /api/cloud')).toBe(true));
    const put = calls.find((c) => c.key === 'PUT /api/cloud');
    expect(put.body).toEqual({ shadow_capital: 300 });
    expect(put.headers['X-2FA-Code']).toBe('123456');
  });

  it('blocks a value outside the bounds before any request', async () => {
    const user = userEvent.setup();
    stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG });
    renderApp('/settings');
    const input = await screen.findByLabelText(/Shadow capital \(USD\)/);
    await user.clear(input);
    await user.type(input, '5000');
    expect(screen.getByRole('button', { name: /Save Shadow Capital/ })).toBeDisabled();
    expect(screen.getByText(/from \$50\.00 to \$1,000\.00/)).toBeInTheDocument();
  });
});

describe('Shadow ceiling banner', () => {
  it('names the ceiling and says it clears as positions settle', async () => {
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/shadow/summary': {
        enabled: true, standin: false, headline: null, by_series: [], by_cell: [], daily: [], caveats: [],
        status: { days_covered: 5, settled_episodes: 9, too_early: false, ceiling_full: true, held_cost: 99, ceiling: 75 },
      },
    });
    renderApp('/shadow');
    const note = await screen.findByTestId('ceiling-full');
    expect(note).toHaveTextContent('the simulated open-position ceiling is full; it clears as positions settle');
    expect(screen.queryByText('$0.00')).toBeNull();
  });
});

describe("Today's P&L state", () => {
  const status = (over) => ({
    ...STATUS, desired: 'running', kill_switch: false, pnl: { d7: 1, d30: 2 },
    pnl_today: { maker: 0, arb: 0, you: 0, bot: 0, total: 0 }, pnl_today_tz: 'UTC', ...over,
  });

  it('todayKind maps the three states and falls back for an older server', () => {
    expect(todayKind({ pnl_today_state: 'settled' })).toBe('value');
    expect(todayKind({ pnl_today_state: 'none_yet' })).toBe('none');
    expect(todayKind({ pnl_today_state: 'unavailable' })).toBe('unavailable');
    expect(todayKind({ pnl_today_state: 'whatever' })).toBe('unavailable');
    expect(todayKind({ pnl_today: { total: 1.2 } })).toBe('value');
    expect(todayKind({})).toBe('none');
  });

  it('none_yet shows "No settlements yet today", not $0.00', async () => {
    stubApi({ 'GET /api/status': status({ pnl_today_state: 'none_yet' }) });
    renderApp('/');
    const tiles = await screen.findByTestId('status-tiles');
    expect(within(tiles).getByTestId('today-empty')).toHaveTextContent('No settlements yet today');
    expect(within(tiles).queryByTestId('today-unavailable')).toBeNull();
  });

  it('unavailable shows "Unavailable"', async () => {
    stubApi({ 'GET /api/status': status({ pnl_today_state: 'unavailable', pnl_today: null }) });
    renderApp('/');
    const tiles = await screen.findByTestId('status-tiles');
    expect(within(tiles).getByTestId('today-unavailable')).toHaveTextContent('Unavailable');
    expect(within(tiles).queryByTestId('today-empty')).toBeNull();
  });

  it('settled shows the value, including a real $0.00', async () => {
    stubApi({ 'GET /api/status': status({ pnl_today_state: 'settled' }) });
    renderApp('/');
    const tiles = await screen.findByTestId('status-tiles');
    expect(within(tiles).getByText('$0.00')).toBeInTheDocument();
    expect(within(tiles).queryByTestId('today-empty')).toBeNull();
  });
});
