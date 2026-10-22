import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CollapsibleList from '../components/CollapsibleList.jsx';
import { setToken } from '../services/api.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => {
  setToken(null);
  window.localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

const N = (n) => Array.from({ length: n }, (_, i) => i + 1);
const list = (items, props = {}) => (
  <CollapsibleList items={items} pageKey="t" {...props}>
    {(vis) => (
      <ul>
        {vis.map((x) => (
          <li key={x.id ?? x}>{x.id ?? x}</li>
        ))}
      </ul>
    )}
  </CollapsibleList>
);

describe('CollapsibleList', () => {
  it('shows 5 rows by default, expands and hides, with aria state', async () => {
    const user = userEvent.setup();
    render(list(N(12)));
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    const btn = screen.getByRole('button', { name: /show all 12/i });
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    expect(document.getElementById(btn.getAttribute('aria-controls'))).not.toBeNull();
    await user.click(btn);
    expect(screen.getAllByRole('listitem')).toHaveLength(12);
    expect(screen.getByRole('button', { name: /hide/i })).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('button', { name: /hide/i }));
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
  });

  it('has no toggle when the list fits, and honors a custom limit', () => {
    const { rerender } = render(list(N(5)));
    expect(screen.queryByRole('button')).toBeNull();
    rerender(list(N(8), { limit: 3 }));
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
  });

  it('keeps pinned rows visible while collapsed', () => {
    const items = N(9).map((id) => ({ id, bad: id === 8 }));
    render(list(items, { pinned: (x) => x.bad }));
    expect(screen.getAllByRole('listitem')).toHaveLength(6);
    expect(screen.getByText('8')).toBeInTheDocument();
    expect(screen.queryByText('7')).toBeNull();
  });

  it('remembers the choice per page key and works when storage throws', async () => {
    const user = userEvent.setup();
    const first = render(list(N(9)));
    await user.click(screen.getByRole('button', { name: /show all/i }));
    first.unmount();
    render(list(N(9)));
    expect(screen.getAllByRole('listitem')).toHaveLength(9);
    cleanupStorage();
  });

  it('falls back to collapsed when localStorage throws', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    render(list(N(9)));
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    await user.click(screen.getByRole('button', { name: /show all/i }));
    expect(screen.getAllByRole('listitem')).toHaveLength(9);
    spy.mockRestore();
    set.mockRestore();
  });
});

function cleanupStorage() {
  window.localStorage.clear();
}

describe('CollapsibleList on pages', () => {
  it('Activity collapses a long day to 5 rows', async () => {
    const day = '2026-10-20T12:00:00Z';
    const rows = N(8).map((i) => ({ utc: day, event: 'fill', ticker: `KXT-${i}`, side: 'yes', price: 0.9, count: 1, cost: 0.9, row_hash: `h${i}`, source: 'bot' }));
    stubApi({ 'GET /api/status': STATUS, 'GET /api/activity': { items: rows }, 'GET /api/strategies': [200, []] });
    renderApp('/activity');
    expect(await screen.findByRole('button', { name: /show all 8/i })).toBeInTheDocument();
    expect(screen.getAllByText(/^KXT-/)).toHaveLength(5);
  });

  it('Alerts history keeps error alerts visible when collapsed', async () => {
    const items = N(8).map((i) => ({ id: `a${i}`, ts: `2026-10-20T10:0${i}:00Z`, type: i === 8 ? 'error' : 'fill', title: `Alert ${i}`, body: '' }));
    stubApi({ 'GET /api/status': STATUS, 'GET /api/alerts': [200, items], 'GET /api/alerts/settings': {} });
    renderApp('/alerts');
    expect(await screen.findByText('Alert 8')).toBeInTheDocument();
    expect(screen.queryByText('Alert 7')).toBeNull();
    expect(screen.getByRole('button', { name: /show all 8/i })).toBeInTheDocument();
  });

  it('Logs audit keeps failed entries visible and collapses the rest', async () => {
    const items = N(8).map((i) => ({ ts: `2026-10-20T10:0${i}:00Z`, action: `act_${i}`, ok: i !== 8, user: 'me' }));
    stubApi({ 'GET /api/status': STATUS, 'GET /api/audit': { items }, 'GET /api/logs': { lines: [] } });
    const user = userEvent.setup();
    renderApp('/logs');
    await user.click(await screen.findByRole('tab', { name: /audit/i }));
    expect(await screen.findByText('Act 8')).toBeInTheDocument();
    expect(screen.queryByText('Act 7')).toBeNull();
    await user.click(screen.getByRole('button', { name: /show all 8/i }));
    expect(screen.getByText('Act 7')).toBeInTheDocument();
  });

  it('Arbitrage recent opportunities collapse to 5', async () => {
    const items = N(7).map((i) => ({ ts: `2026-10-20T10:0${i}:00Z`, event: `EV-${i}`, kind: 'YES', contracts: 1, profit: 0.1, cost: 5, executed: false }));
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/arb': { settings: {}, status: { state: 'stopped' }, schema: {}, pnl: null },
      'GET /api/arb/opportunities': { items },
    });
    renderApp('/arb');
    expect(await screen.findByRole('button', { name: /show all 7/i })).toBeInTheDocument();
    expect(screen.getAllByText(/^EV-/)).toHaveLength(5);
  });

  it('Account open positions collapse to 5', async () => {
    const rows = N(6).map((i) => ({ kind: 'position', ticker: `KXP-${i}`, title: `Pos ${i}`, side: 'yes', count: 1, price: 0.9, value: 1, payout_if_wins: 1, source: 'bot' }));
    stubApi({ 'GET /api/status': STATUS, 'GET /api/account/positions': [200, rows], 'GET /api/strategies': [200, []], 'GET /api/periods': { periods: [] }, 'GET /api/cashflows': { items: [] } });
    renderApp('/account');
    const btn = await screen.findByRole('button', { name: /show all 6/i });
    expect(within(btn.closest('section, div')).getAllByText(/^Pos /).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/^Pos \d$/)).toHaveLength(5);
  });

  it('Shadow per-series table collapses to 5 rows', async () => {
    const R = (a, b, c) => ({ lo: a, mid: b, hi: c });
    const by_series = N(7).map((i) => ({ series: `KXS${i}`, episodes: 3, fills_mid: 1, pnl: R(0, 1, 2) }));
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/shadow/summary': {
        enabled: true,
        standin: false,
        status: { days_covered: 10, settled_episodes: 40, central_fills: 22, too_early: false },
        headline: { pnl: R(1, 5, 9), roi: R(0, 0.01, 0.02), fill_rate: R(0.2, 0.3, 0.4), peak_capital: 300 },
        by_series,
        by_cell: [],
        daily: [],
        caveats: [],
      },
    });
    renderApp('/shadow');
    expect(await screen.findByRole('button', { name: /show all 7/i })).toBeInTheDocument();
    expect(screen.getAllByText(/^KXS\d$/)).toHaveLength(5);
  });
});
