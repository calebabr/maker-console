import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setToken } from '../services/api.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => setToken(null));
afterEach(() => vi.unstubAllGlobals());

const R = (lo, mid, hi, ci90) => ({ lo, mid, hi, ...(ci90 ? { ci90 } : {}) });
const SUMMARY = (over = {}) => ({
  enabled: true,
  standin: false,
  status: { days_covered: 10, settled_episodes: 40, central_fills: 22.5, too_early: false, last_pass_ts: null, pass_ok_pct: 1, truncated: false },
  headline: { pnl: R(1, 5, 9, [0.5, 9.5]), roi: R(0.004, 0.01, 0.016, [0.003, 0.017]), fill_rate: R(0.2, 0.3, 0.4), peak_capital: 300 },
  headline_zero_fee: { pnl: R(2, 6, 10, [1, 11]), roi: R(0.005, 0.011, 0.017, [0.004, 0.018]), fill_rate: R(0.2, 0.3, 0.4), peak_capital: 300 },
  by_series: [{ series: 'KXDEMOA', episodes: 12, fills_mid: 7, pnl: R(1, 2, 3) }],
  by_cell: [{ cell: ['a', 'b'], episodes: 12, fills_mid: 7, pnl: R(1, 2, 3) }],
  daily: [
    { day: '2026-10-01', cum_lo: 0.1, cum_mid: 0.4, cum_hi: 0.9 },
    { day: '2026-10-02', cum_lo: 0.3, cum_mid: 0.9, cum_hi: 1.8 },
  ],
  fee_mode: 'maker',
  caveats: [],
  ...over,
});

const base = (summary) => stubApi({ 'GET /api/status': STATUS, 'GET /api/shadow/summary': summary });

describe('Shadow estimate page', () => {
  it('shows the banner, a range, the chart and the caveats, and switches to zero fees', async () => {
    base(SUMMARY());
    const user = userEvent.setup();
    renderApp('/shadow');
    expect(await screen.findByText(/Simulated\. No orders were placed/)).toBeInTheDocument();
    expect(screen.getByText(/after maker fees/i)).toBeInTheDocument();
    expect(screen.getByTestId('shadow-band')).toBeInTheDocument();
    expect(screen.getByText(/what it cannot capture/i)).toBeInTheDocument();
    expect(screen.queryByText(/placeholder/i)).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Zero fees' }));
    expect(screen.getByText(/zero fees/i, { selector: 'h3' })).toBeInTheDocument();
  });

  it('warns on the stand-in and explains the too-early state', async () => {
    base(SUMMARY({ standin: true, status: { ...SUMMARY().status, too_early: true } }));
    renderApp('/shadow');
    expect(await screen.findByText(/uses a placeholder/i)).toBeInTheDocument();
    expect(screen.getByText(/too early to estimate/i)).toBeInTheDocument();
    expect(screen.queryByTestId('shadow-band')).toBeNull();
  });

  it('explains how to turn shadow on when disabled', async () => {
    base(SUMMARY({ enabled: false, headline: null }));
    renderApp('/shadow');
    expect(await screen.findByText(/shadow estimate is off/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Server' })).toHaveAttribute('href', '/server');
  });
});
