import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp, stubApi, STATUS } from './helpers.jsx';
import { gateReason } from '../pages/Arbitrage.jsx';
import { plainText } from '../components/ChangeDiff.jsx';
import { isComplex } from '../pages/Settings.jsx';

afterEach(() => {
  vi.unstubAllGlobals();
});

const P1 = { id: 'p1', name: 'Demo Period 1', started_at: null, ended_at: '2026-10-25T12:00:00Z', note: null, current: false, implicit: true };
const P2 = { id: 'abc123', name: 'Demo Period 2', started_at: '2026-10-25T12:00:00Z', ended_at: null, note: null, current: true, implicit: false };
const PERIODS = { periods: [P1, P2], cash_flows: [{ id: 'cf1', ts: '2026-10-26T10:00:00Z', kind: 'deposit', amount: 50, note: 'top up', created_by: 'owner' }] };
const SUMMARY = {
  fills: 24, settled: 18, wins: 16, pnl: 6.4, money_spent: 96, settled_cost: 72,
  return_on_settled_cost: 0.0889, return_on_all_cost: 0.0667, open_at_risk: 24,
  cash_flows: { deposits: 50, withdrawals: 0, net: 50, items: [], return_on_capital: 0.03 },
};

async function enterCode(user, label) {
  await user.type(await screen.findByLabelText('6-digit code'), '123456');
  const btns = screen.getAllByRole('button', { name: label });
  await user.click(btns[btns.length - 1]);
}

describe('Periods on the Account page', () => {
  const routes = (extra = {}) => ({
    'GET /api/status': STATUS,
    'GET /api/account/positions': [],
    'GET /api/strategies': [],
    'GET /api/periods': PERIODS,
    'GET /api/periods/abc123/summary': SUMMARY,
    ...extra,
  });

  it('shows the timeline, the current period summary and both return-on-money-spent lines', async () => {
    stubApi(routes());
    renderApp('/account');
    const sum = await screen.findByTestId('period-summary');
    expect(within(sum).getByTestId('ps-spent')).toHaveTextContent('$96.00');
    expect(within(sum).getByTestId('ps-ret-settled')).toHaveTextContent('+8.89%');
    expect(within(sum).getByTestId('ps-ret-all')).toHaveTextContent('+6.67%');
    expect(within(sum).getByTestId('ps-pnl')).toHaveTextContent('+$6.40');
    expect(within(sum).getByTestId('ps-ret-capital')).toHaveTextContent('+3.00%');
    const tl = screen.getByLabelText('Period timeline');
    expect(within(tl).getByText(/Demo Period 1/)).toBeInTheDocument();
    expect(within(tl).getByText(/Demo Period 2 \(current\)/)).toBeInTheDocument();
  });

  it('changing the period selector loads that period summary', async () => {
    const user = userEvent.setup();
    const calls = stubApi(routes({ 'GET /api/periods/p1/summary': { ...SUMMARY, fills: 7 } }));
    renderApp('/account');
    await screen.findByTestId('period-summary');
    await user.selectOptions(screen.getByLabelText('Period'), 'p1');
    await waitFor(() => expect(calls.some((c) => c.key === 'GET /api/periods/p1/summary')).toBe(true));
  });

  it('Mark new period asks for a fresh 2FA code and posts the name', async () => {
    const user = userEvent.setup();
    const calls = stubApi(
      routes({
        'POST /api/periods': () => ({ periods: [P1, { ...P2, current: false }, { id: 'new9', name: 'Demo Period 3', started_at: '2026-10-27T00:00:00Z', ended_at: null, current: true }], cash_flows: [], created: 'new9' }),
        'GET /api/periods/new9/summary': SUMMARY,
      }),
    );
    renderApp('/account');
    await screen.findByTestId('period-summary');
    await user.type(screen.getByLabelText('New Period Name'), 'Demo Period 3');
    await user.click(screen.getByRole('button', { name: /mark new period/i }));
    expect(calls.some((c) => c.method === 'POST')).toBe(false); // nothing sent before the code
    await enterCode(user, /mark new period/i);
    await waitFor(() => expect(calls.some((c) => c.key === 'POST /api/periods')).toBe(true));
    const post = calls.find((c) => c.key === 'POST /api/periods');
    expect(post.body).toEqual({ name: 'Demo Period 3' });
    expect(post.headers['X-2FA-Code']).toBe('123456');
    await waitFor(() => expect(screen.getByLabelText('Period timeline')).toHaveTextContent('Demo Period 3'));
  });

  it('adds a deposit with a 2FA code and deletes one', async () => {
    const user = userEvent.setup();
    const calls = stubApi(
      routes({
        'POST /api/cash-flows': () => ({ ...PERIODS, created: 'cf2' }),
        'DELETE /api/cash-flows/cf1': () => ({ periods: PERIODS.periods, cash_flows: [], deleted: 'cf1' }),
      }),
    );
    renderApp('/account');
    await screen.findByTestId('period-summary');
    await user.type(screen.getByLabelText('Amount ($)'), '25');
    await user.click(screen.getByRole('button', { name: /add entry/i }));
    await enterCode(user, /add cash flow/i);
    await waitFor(() => expect(calls.some((c) => c.key === 'POST /api/cash-flows')).toBe(true));
    expect(calls.find((c) => c.key === 'POST /api/cash-flows').body).toMatchObject({ kind: 'deposit', amount: 25 });

    await user.click(await screen.findByRole('button', { name: /delete deposit/i }));
    await enterCode(user, /^delete$/i);
    await waitFor(() => expect(calls.some((c) => c.key === 'DELETE /api/cash-flows/cf1')).toBe(true));
    await waitFor(() => expect(screen.getByText('No entries yet.')).toBeInTheDocument());
  });
});

describe('Overview By Source period selector', () => {
  it('hides the selector with one period and uses /api/pnl/by-source for a chosen period', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': { ...STATUS, account: { cash: 100, exposure: 10 }, pnl_today: { maker: 1, arb: 0, you: 0, total: 1 } },
      'GET /api/history': { snapshots: [] },
      'GET /api/strategies': [],
      'GET /api/account/positions': [],
      'GET /api/periods': PERIODS,
      'GET /api/pnl/by-source': { period: P1, env: 'demo', by_source: { maker: 3.25, arb: 0, you: 0, total: 3.25 } },
    });
    renderApp('/');
    const sel = await screen.findByLabelText('Period');
    expect(screen.getByText('Total Today')).toBeInTheDocument();
    await user.selectOptions(sel, 'p1');
    await waitFor(() => expect(calls.some((c) => c.key === 'GET /api/pnl/by-source' && c.path.includes('period=p1'))).toBe(true));
    const box = screen.getByTestId('source-pnl');
    await waitFor(() => expect(within(box).getByText('Total').closest('li')).toHaveTextContent('+$3.25'));
  });
});

describe('Settings [object Object] fix', () => {
  it('shows a nested object setting read-only as text, never as [object Object]', async () => {
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': {
        config: { run_budget: 25, extra: { a: 1, b: 'x' }, rules: [{ k: 1 }], cells: [{ lo: 0.3, hi: 0.4 }] },
        schema: { run_budget: { type: 'number', min: 1, max: 500 } },
        ceilings: {},
      },
    });
    const { container } = renderApp('/settings');
    await screen.findByLabelText(/run budget/i);
    expect(container.textContent).not.toContain('[object Object]');
    expect(container.textContent).toContain('{"a":1,"b":"x"}');
    // the object is not an editable text box
    expect(screen.queryByLabelText(/^extra/i)).toBeNull();
  });

  it('plainText and isComplex handle objects', () => {
    expect(plainText({ a: 1 })).toBe('{"a":1}');
    expect(plainText([{ a: 1 }, 'x'])).toBe('{"a":1}, x');
    expect(plainText(null)).toBe('—');
    expect(isComplex({ a: 1 })).toBe(true);
    expect(isComplex([{ a: 1 }])).toBe(true);
    expect(isComplex([[1, 2]])).toBe(false);
    expect(isComplex('x')).toBe(false);
  });
});

describe('At Risk You label refreshes with the status poll', () => {
  it('re-reads the positions whenever the status refreshes', async () => {
    let n = 0;
    const calls = stubApi({
      'GET /api/status': { ...STATUS, account: { cash: 100, exposure: 40 } },
      'GET /api/history': { snapshots: [] },
      'GET /api/strategies': [],
      'GET /api/periods': { periods: [P1], cash_flows: [] },
      'GET /api/account/positions': () => {
        n += 1;
        return [200, [{ ticker: 'A', value: 30, source: 'bot' }, { ticker: 'B', value: n === 1 ? 10 : 25, source: 'you' }]];
      },
    });
    renderApp('/');
    const split = await screen.findByTestId('risk-split');
    await waitFor(() => expect(split).toHaveTextContent('You $25.00'));
    expect(calls.filter((c) => c.key === 'GET /api/account/positions').length).toBeGreaterThanOrEqual(2);
  });
});

describe('Arbitrage demo-execute, caps and gate reasons', () => {
  const ARB = {
    settings: { enabled: true, live: false, arb_max_contracts: 10, arb_min_profit: 0.05, arb_poll_seconds: 15, arb_series: ['KXA'], arb_scan_cap: 10, arb_day_cap: 30, arb_series_cap_frac: 0.35, arb_demo_execute: false },
    schema: {
      arb_scan_cap: { type: 'number', unit: 'USD per scan', min: 1, max: 100 },
      arb_day_cap: { type: 'number', unit: 'USD per day', min: 1, max: 500 },
      arb_series_cap_frac: { type: 'number', min: 0.05, max: 0.9, step: 0.01 },
      arb_demo_execute: { type: 'boolean', label: 'Demo Execute' },
      arb_series: { type: 'list', options: ['KXA'] },
    },
    status: { state: 'log-only', demo_execute: true, live_allowed: false, scans: 3, found: 2 },
    pnl: null,
    pnl_demo_execute: { d7: 1.25, d30: 1.25, sets: 4, demo: true },
  };
  const OPPS = {
    items: [
      { ts: '2026-10-26T10:00:00Z', event: 'EV1', kind: 'YES', contracts: 3, profit: 0.2, cost: 2.5, executed: 'FILLED 3', mode: 'demo-execute' },
      { ts: '2026-10-26T10:01:00Z', event: 'EV2', kind: 'NO', contracts: 5, profit: 0.3, cost: 4, executed: 'skipped: gate total exposure over the ceiling', mode: 'demo-execute' },
      { ts: '2026-10-26T10:02:00Z', event: 'EV3', kind: 'NO', contracts: 1, profit: 0.1, cost: 1, executed: 'log-only' },
    ],
  };

  it('marks demo-execute rows, explains gate skips and shows the new settings', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/arb': ARB,
      'GET /api/arb/opportunities': OPPS,
      'PUT /api/arb': () => ({ ...ARB, settings: { ...ARB.settings, arb_demo_execute: true } }),
    });
    renderApp('/arb');
    await screen.findByText('EV1');
    expect(screen.getAllByTestId('demo-exec-badge')).toHaveLength(2);
    expect(screen.getByTestId('gate-reason')).toHaveTextContent('Not placed: total exposure over the ceiling');
    expect(screen.getByText('Skipped by risk limit')).toBeInTheDocument();
    expect(screen.getByTestId('demo-exec-state')).toHaveTextContent('on');
    expect(screen.getByTestId('demo-pnl')).toHaveTextContent('4 demo sets');
    expect(screen.getByLabelText(/Scan Cap/i)).toHaveValue(10);
    expect(screen.getByLabelText(/Day Cap/i)).toHaveValue(30);
    expect(screen.getByLabelText(/Series Cap Frac/i)).toHaveValue(0.35);

    await user.click(screen.getByRole('checkbox', { name: /demo execute/i }));
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await enterCode(user, /^save$/i);
    await waitFor(() => expect(calls.some((c) => c.key === 'PUT /api/arb')).toBe(true));
    expect(calls.find((c) => c.key === 'PUT /api/arb').body).toEqual({ arb_demo_execute: true });
  });

  it('gateReason reads the reason from the executed text', () => {
    expect(gateReason('skipped: gate scan cap')).toBe('scan cap');
    expect(gateReason('skipped: other')).toBeNull();
    expect(gateReason('FILLED 2')).toBeNull();
  });
});
