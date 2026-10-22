import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setToken } from '../services/api.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => {
  setToken(null);
  window.localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

const PERIODS = {
  periods: [
    { id: 'p1', name: 'Demo Period 1', current: false, started_at: '2026-10-01T00:00:00Z' },
    { id: 'p2', name: 'Demo Period 2', current: true, started_at: '2026-10-21T00:00:00Z' },
  ],
  cash_flows: [],
};

const R = (a, b, c) => ({ lo: a, mid: b, hi: c });
const N = (n) => Array.from({ length: n }, (_, i) => i + 1);

describe('Overview rolling P&L period selector', () => {
  it('defaults to the current period, shows the API window labels and re-queries on change', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': (call) => {
        const p = new URLSearchParams(call.path.split('?')[1] || '').get('period');
        if (p === 'p2') {
          return {
            ...STATUS,
            pnl: {
              d7: 1.5,
              d30: 1.5,
              settled_fills: 3,
              basis: 'fill_date',
              window: { d7: { label: 'Demo Period 2 so far (since 2026-10-21, under 7 days)' }, d30: { label: 'Demo Period 2 so far (since 2026-10-21, under 30 days)' } },
            },
          };
        }
        if (p === 'all') {
          return { ...STATUS, pnl: { d7: 9, d30: 20, settled_fills: 9, basis: 'fill_date', window: { d7: { label: 'Last 7 days (all periods)' }, d30: { label: 'Last 30 days (all periods)' } } } };
        }
        return STATUS;
      },
      'GET /api/periods': PERIODS,
    });
    renderApp('/');
    expect(await screen.findByText(/Demo Period 2 so far \(since 2026-10-21, under 7 days\)/)).toBeInTheDocument();
    expect(calls.some((c) => c.path.includes('/api/status?period=p2'))).toBe(true);
    expect(screen.getByTestId('pnl-basis')).toBeInTheDocument();
    await user.selectOptions(document.getElementById('pnl-period-select'), 'all');
    expect(await screen.findByText('Last 7 days (all periods)')).toBeInTheDocument();
  });
});

describe('Period summary basis caveat', () => {
  it('shows the fill-date basis and the equity change label', async () => {
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/account/positions': [200, []],
      'GET /api/strategies': [200, []],
      'GET /api/periods': PERIODS,
      'GET /api/periods/p2/summary': {
        fills: 3,
        settled: 1,
        wins: 1,
        pnl: 1.5,
        pnl_basis: 'fill_date',
        cash_flows: { equity_change_ex_flows: 4, equity_change_label: 'Also counts settlements of earlier-period positions.' },
      },
    });
    renderApp('/account');
    expect(await screen.findByTestId('ps-basis')).toHaveTextContent(/fill date/i);
    expect(screen.getByTestId('ps-equity-change')).toHaveTextContent(/earlier-period positions/);
  });
});

const SHADOW = (over = {}) => ({
  enabled: true,
  standin: false,
  status: { days_covered: 10, settled_episodes: 40, central_fills: 22, too_early: false },
  headline: { pnl: R(1, 5, 9), roi: R(0.004, 0.01, 0.016), fill_rate: R(0.2, 0.3, 0.4), peak_capital: 300 },
  headline_zero_fee: null,
  by_series: [],
  by_cell: [],
  daily: [],
  caveats: [],
  period: { id: 'p2', name: 'Demo Period 2', label: 'Demo Period 2 (current)', current: true },
  period_ok: true,
  by_event_day: N(7).map((i) => ({
    day: `2026-10-0${i}`,
    episodes: 4,
    filled_episodes: 2,
    cost: R(1, 2, 3),
    pnl: R(0, 0.1, 0.2),
    return: R(0.01, 0.02, 0.03),
  })),
  day_stats: {
    event_days: 7,
    days_with_fills: 6,
    filled_episodes: 14,
    mean_return_mid: 0.02,
    sd_return_mid: 0.01,
    worst_day: { day: '2026-10-01', return_mid: -0.01, pnl_mid: -0.1 },
    best_day: { day: '2026-10-07', return_mid: 0.05, pnl_mid: 0.5 },
    best_day_pnl_share: 0.4,
    top3_pnl_share: 0.8,
  },
  reliability_note: 'Too early to estimate. So far: 6 of 7 days with a fill.',
  arb: { enabled: false },
  ...over,
});

const shadowApi = (summary) =>
  stubApi({ 'GET /api/status': STATUS, 'GET /api/periods': PERIODS, 'GET /api/shadow/summary': summary });

describe('Shadow period selector, event days and arbitrage', () => {
  it('asks for the current period by default and re-queries when another is chosen', async () => {
    const user = userEvent.setup();
    const calls = shadowApi(SHADOW());
    renderApp('/shadow');
    expect(await screen.findByTestId('shadow-period-label')).toHaveTextContent('Demo Period 2 (current)');
    await waitFor(() => expect(calls.some((c) => c.path.includes('/api/shadow/summary?period=p2'))).toBe(true));
    await user.selectOptions(document.getElementById('shadow-period-select'), 'all');
    await waitFor(() => expect(calls.some((c) => c.path.includes('/api/shadow/summary?period=all'))).toBe(true));
  });

  it('shows the per-event-day table collapsed with stats and the reliability note', async () => {
    const user = userEvent.setup();
    shadowApi(SHADOW());
    renderApp('/shadow');
    expect(await screen.findByTestId('reliability-note')).toHaveTextContent(/6 of 7 days/);
    expect(screen.getByTestId('day-stats')).toHaveTextContent(/Top 3 days share/);
    const table = screen.getByTestId('event-day-table');
    expect(table.querySelectorAll('tbody tr')).toHaveLength(5);
    await user.click(screen.getByRole('button', { name: /show all 7/i }));
    expect(screen.getByTestId('event-day-table').querySelectorAll('tbody tr')).toHaveLength(7);
  });

  it('shows a short off note when the arbitrage scan is disabled', async () => {
    shadowApi(SHADOW());
    renderApp('/shadow');
    expect(await screen.findByTestId('arb-off')).toHaveTextContent(/scan is off/i);
    expect(screen.queryByTestId('arb-section')).toBeNull();
  });

  it('shows the arbitrage windows and last scan when enabled', async () => {
    const w = { opportunities: 4, yes: 3, no: 1, profit: 2.5, median_cost: 12, gate_pass_share: 0.75, median_duration_s: 600, clipped_to_period: true };
    shadowApi(
      SHADOW({
        arb: {
          enabled: true,
          days: [{ day: '2026-10-21', opportunities: 4, yes: 3, no: 1, profit: 2.5 }],
          rolling: { '7d': w, '30d': { ...w, clipped_to_period: false } },
          last_scan: { ts: new Date().toISOString(), skipped: 'time_budget', opportunities: 0 },
        },
      }),
    );
    renderApp('/shadow');
    expect(await screen.findByTestId('arb-section')).toBeInTheDocument();
    expect(screen.getByText('Last 7 days within Demo Period 2')).toBeInTheDocument();
    expect(screen.getByText('Last 30 days')).toBeInTheDocument();
    expect(screen.getByTestId('arb-last-scan')).toHaveTextContent(/skipped \(time budget\)/);
    expect(screen.queryByTestId('arb-off')).toBeNull();
  });
});
