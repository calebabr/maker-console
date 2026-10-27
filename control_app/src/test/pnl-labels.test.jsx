import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setToken, viewerTimeZone } from '../services/api.js';
import { PeriodSummary } from '../components/PeriodsCard.jsx';
import PnlScope from '../components/PnlScope.jsx';
import { PNL_SCOPES, TODAY_EMPTY, scopeText, zoneLabel } from '../utils/pnlScopes.js';
import { analyticsSummary } from '../services/mockAnalytics.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => setToken(null));
afterEach(() => vi.unstubAllGlobals());

const RUNNING = {
  ...STATUS,
  desired: 'running',
  kill_switch: false,
  mode: 'live',
  heartbeat_age_s: 60,
  account: { cash: 212.4, exposure: 37.6, headroom: 87.4 },
  limits: { run_budget: 25, max_open_frac: 0.4 },
  pnl: { d7: 2.5, d30: 5.5 },
  pnl_arb: { d7: 1.0, d30: 4.5 },
  pnl_total: { d7: 3.5, d30: 10.0 },
  pnl_today: { maker: 1.4, arb: 0.35, you: -0.8, bot: 1.75, total: 0.95 },
  pnl_today_tz: 'America/Chicago',
  recent_passes: [],
};
const NOTHING_TODAY = { ...RUNNING, pnl_today: { maker: null, arb: null, you: null, bot: null, total: null } };
const ZERO_TODAY = { ...RUNNING, pnl_today: { maker: 0, arb: null, you: null, bot: 0, total: 0 } };
const STRATS = [
  { id: 'maker', source: 'bot', name: 'Bot · Maker', color: 'var(--accent)' },
  { id: 'arb', source: 'bot', name: 'Bot · Arb', color: 'var(--chart2)' },
  { id: 'you', source: 'you', name: 'You', color: 'var(--warn)' },
];
const ovr = (status) => ({
  'GET /api/status': status,
  'GET /api/history': { snapshots: [] },
  'GET /api/strategies': [200, STRATS],
});

describe('scope registry', () => {
  it('has a one-line text for every figure and fills in the time zone', () => {
    for (const id of Object.keys(PNL_SCOPES)) expect(scopeText(id, { tz: 'America/Chicago' }).length).toBeGreaterThan(10);
    expect(scopeText('today', { tz: 'America/Chicago' })).toContain('midnight America/Chicago');
    expect(scopeText('today', {})).toContain('midnight UTC');
    expect(scopeText('nope')).toBe('');
    expect(zoneLabel('America/Argentina/Buenos_Aires')).toBe('America/Argentina/Buenos Aires');
    expect(zoneLabel(undefined)).toBe('UTC');
  });

  it('renders nothing for an unknown id and a tagged line for a known one', () => {
    const { container, rerender } = render(<PnlScope id="nope" />);
    expect(container).toBeEmptyDOMElement();
    rerender(<PnlScope id="maker" />);
    expect(screen.getByTestId('scope-maker')).toHaveTextContent('Realized, maker fills only, by settlement date (UTC)');
  });
});

describe('viewer time zone', () => {
  it('is an IANA name from the device and is sent with the plain status call only', async () => {
    expect(viewerTimeZone()).toMatch(/^[A-Za-z0-9_+\-/]+$/);
    const calls = stubApi(ovr(RUNNING));
    renderApp('/');
    await screen.findByTestId('status-tiles');
    const plain = calls.filter((c) => c.key === 'GET /api/status' && !c.path.includes('period='));
    expect(plain.length).toBeGreaterThan(0);
    expect(plain[0].path).toBe(`/api/status?tz=${encodeURIComponent(viewerTimeZone())}`);
    const withPeriod = calls.filter((c) => c.path.includes('period='));
    for (const c of withPeriod) expect(c.path).not.toContain('tz=');
  });

  it('sends no tz when the browser cannot name its zone', async () => {
    const spy = vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(() => ({ resolvedOptions: () => ({}) }));
    const calls = stubApi(ovr(RUNNING));
    renderApp('/');
    await screen.findByTestId('status-tiles');
    spy.mockRestore();
    expect(calls.find((c) => c.key === 'GET /api/status' && !c.path.includes('period=')).path).toBe('/api/status');
  });
});

describe('Overview tiles', () => {
  it('names the 30-day tile Maker P&L, labels every tile and shows the combined total with the split', async () => {
    stubApi(ovr(RUNNING));
    renderApp('/');
    const tiles = await screen.findByTestId('status-tiles');
    expect(within(tiles).getByText('Maker P&L (30 days)')).toBeInTheDocument();
    expect(within(tiles).queryByText('30-Day P&L')).toBeNull();
    expect(within(tiles).getByText('+$5.50')).toBeInTheDocument();
    expect(within(tiles).getByTestId('scope-maker')).toHaveTextContent('Realized, maker fills only, by settlement date (UTC)');
    expect(within(tiles).getByTestId('scope-todayShort')).toHaveTextContent('Settled since midnight America/Chicago');
    const combined = await screen.findByTestId('pnl-combined');
    expect(within(combined).getByTestId('pnl-combined-total')).toHaveTextContent('+$10.00');
    expect(within(combined).getByTestId('pnl-split')).toHaveTextContent('Maker +$5.50 / Arbitrage +$4.50');
    expect(within(combined).getByTestId('scope-makerArb')).toHaveTextContent('maker + arbitrage, by settlement date (UTC)');
  });

  it('says "No settlements yet today" instead of a dash or $0.00 when nothing settled', async () => {
    stubApi(ovr(NOTHING_TODAY));
    renderApp('/');
    const tiles = await screen.findByTestId('status-tiles');
    expect(within(tiles).getByTestId('today-empty')).toHaveTextContent(TODAY_EMPTY);
    expect(TODAY_EMPTY).toBe('No settlements yet today');
    expect(within(tiles).queryByText('$0.00')).toBeNull();
  });

  it('shows $0.00 only when something settled and netted to zero', async () => {
    stubApi(ovr(ZERO_TODAY));
    renderApp('/');
    const tiles = await screen.findByTestId('status-tiles');
    expect(within(tiles).getByText('$0.00')).toBeInTheDocument();
    expect(within(tiles).queryByTestId('today-empty')).toBeNull();
  });

  it('omits the combined line when the API sends no total, and labels the P&L card by its date rule', async () => {
    stubApi(ovr({ ...RUNNING, pnl_total: null, pnl_arb: null }));
    renderApp('/');
    await screen.findByTestId('status-tiles');
    expect(screen.queryByTestId('pnl-combined')).toBeNull();
    expect((await screen.findAllByTestId('scope-maker')).length).toBeGreaterThanOrEqual(2);   // tile + Profit & Loss card
    expect(await screen.findByTestId('scope-bySource')).toHaveTextContent('settled since midnight America/Chicago');
  });
});

describe('Analytics, Account, Arbitrage', () => {
  it('Analytics totals say maker + arbitrage by settlement date', async () => {
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/analytics/summary': (call) => analyticsSummary(new URL(call.path, 'http://x').searchParams, { mode: 'demo', nowMs: Date.parse('2026-10-13T18:00:00Z') }),
    });
    renderApp('/analytics');
    const totals = await screen.findByTestId('totals');
    expect(totals.parentElement.querySelector('[data-testid="scope-analytics"]')).toHaveTextContent(
      'Realized, maker + arbitrage, by settlement date (UTC)',
    );
  });

  it('Period summary says fill date, and the equity line says what it nets', () => {
    render(
      <PeriodSummary
        summary={{ fills: 3, settled: 2, wins: 1, pnl: 1.2, pnl_basis: 'fill_date', cash_flows: { deposits: 100, equity_change_ex_flows: 2.5, equity_change_label: 'balance now minus start minus deposits' } }}
      />,
    );
    expect(screen.getByTestId('scope-period')).toHaveTextContent('by fill date (UTC)');
    expect(screen.getByTestId('scope-equity')).toHaveTextContent('Equity minus deposits, open positions at cost');
  });

  const ARB = {
    settings: { enabled: true, live: true, arb_max_contracts: 10, arb_poll_seconds: 15, arb_series: ['KXDEMOA'] },
    status: { state: 'live', running: true, kill_switch: false, live_allowed: true, scans: 1, found: 1 },
    pnl: { d7: 3.0, d30: 9.0, sets: 4 },
  };

  it('Arbitrage shows guaranteed profit beside realized profit from the status figures, each with its scope', async () => {
    stubApi({ 'GET /api/status': RUNNING, 'GET /api/arb': ARB, 'GET /api/arb/opportunities': { items: [] } });
    renderApp('/arb');
    const realized = await screen.findByTestId('arb-realized');
    expect(screen.getByTestId('scope-arbGuaranteed')).toHaveTextContent('Guaranteed at execution, not yet realized');
    expect(within(realized).getByTestId('scope-arbRealized')).toHaveTextContent('Realized, arbitrage fills only, by settlement date (UTC)');
    expect(within(realized).getByText('+$4.50')).toBeInTheDocument();          // status.pnl_arb.d30
    expect(screen.getByText('+$9.00')).toBeInTheDocument();                    // guaranteed d30
    expect(within(realized).getByText(/cover different sets and dates/)).toBeInTheDocument();
    expect(within(realized).queryByText(/play money/)).toBeNull();              // live mode
  });

  it('Arbitrage says nothing has settled yet (not $0.00) when there is no realized figure, and flags demo play money', async () => {
    stubApi({ 'GET /api/status': { ...RUNNING, mode: 'demo', pnl_arb: null }, 'GET /api/arb': ARB, 'GET /api/arb/opportunities': { items: [] } });
    renderApp('/arb');
    const realized = await screen.findByTestId('arb-realized');
    expect(within(realized).getByText('No arbitrage fills have settled yet.')).toBeInTheDocument();
    expect(within(realized).getByText(/demo mode, so this is play money/)).toBeInTheDocument();
  });
});

describe('Shadow headline', () => {
  const R = (lo, mid, hi) => ({ lo, mid, hi });
  const EMPTY = (over = {}) => ({
    enabled: true,
    standin: false,
    status: { days_covered: 0, settled_episodes: 0, central_fills: 0, too_early: true, headline_status: 'too_early' },
    headline: { pnl: { ...R(0, 0, 0), ci90: null }, roi: { lo: null, mid: null, hi: null }, fill_rate: R(0, 0, 0), peak_capital: 0, headline_status: 'too_early', n_settled_fills: 0 },
    by_series: [],
    by_cell: [],
    daily: [],
    caveats: [],
    ...over,
  });
  const show = (summary) => stubApi({ 'GET /api/status': STATUS, 'GET /api/shadow/summary': summary });

  it('does not present the empty $0.00 as an estimate: it says no estimate yet', async () => {
    show(EMPTY());
    renderApp('/shadow');
    const block = await screen.findByTestId('headline-block');
    expect(within(block).getByTestId('no-estimate')).toBeInTheDocument();
    expect(within(block).getAllByTestId('range-empty')).toHaveLength(2);        // P&L and fill rate
    const pnlRange = within(block).getByText('Estimated P&L, after maker fees').nextElementSibling;
    expect(pnlRange).toHaveAttribute('data-testid', 'range-empty');           // the zeroed P&L is not drawn as a range
    expect(pnlRange).toHaveTextContent('No estimate yet');
    expect(within(block).getByTestId('scope-shadow')).toHaveTextContent('not real P&L');
  });

  it('still shows the range, with its scope line, once something has settled', async () => {
    show(EMPTY({
      status: { days_covered: 3, settled_episodes: 5, central_fills: 4, too_early: true, headline_status: 'too_early' },
      headline: { pnl: { ...R(-1, 0.5, 2), ci90: null }, roi: R(0.001, 0.002, 0.003), fill_rate: R(0.1, 0.2, 0.3), peak_capital: 20, headline_status: 'too_early', n_settled_fills: 5 },
    }));
    renderApp('/shadow');
    const block = await screen.findByTestId('headline-block');
    expect(within(block).queryByTestId('no-estimate')).toBeNull();
    expect(within(block).getByText('+$0.50')).toBeInTheDocument();
    expect(within(block).getByTestId('scope-shadow')).toBeInTheDocument();
  });

  it('shows no headline figure at all while the simulated ceiling is full', async () => {
    show(EMPTY({ status: { days_covered: 5, settled_episodes: 9, too_early: false, ceiling_full: true, held_cost: 99, ceiling: 75 } }));
    renderApp('/shadow');
    expect(await screen.findByTestId('ceiling-full')).toHaveTextContent('Shadow is not estimating');
    expect(screen.queryByTestId('headline-block')).toBeNull();
    expect(screen.queryByText('$0.00')).toBeNull();
  });
});
