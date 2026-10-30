import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setToken } from '../services/api.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => setToken(null));
afterEach(() => vi.unstubAllGlobals());

const ARB = (over = {}) => ({
  settings: {
    enabled: true, live: false, arb_max_contracts: 100, arb_min_profit: 0.05,
    arb_series: ['KXDEMOA', 'KXDEMOB'], arb_poll_seconds: 15, ...(over.settings || {}),
  },
  schema: {
    arb_poll_seconds: { type: 'integer', unit: 'seconds', min: 5, max: 60 },
    arb_max_contracts: { type: 'number', min: 1, max: 100 },
  },
  status: {
    state: 'log-only', running: true, kill_switch: false, kill_reason: null, last_scan_utc: null,
    last_error: null, live_allowed: true, scans: 10, found: 1, ...(over.status || {}),
  },
  pnl: over.pnl ?? null,
});

const OPPS = {
  items: [
    { ts: '2026-10-13T12:00:00Z', event: 'KXDEMOA-26OCT13', kind: 'NO', contracts: 100, profit: 1.26, cost: 508.7, executed: 'log-only' },
    { ts: '2026-10-13T11:00:00Z', event: 'KXDEMOB-26OCT13', kind: 'NO', contracts: 10, profit: 0.2, cost: 50, executed: 'PARTIAL {}' },
  ],
};

function base(extra = {}) {
  return stubApi({
    'GET /api/status': STATUS,
    'GET /api/arb': ARB(),
    'GET /api/arb/opportunities': OPPS,
    ...extra,
  });
}

describe('Arbitrage page', () => {
  it('Results: P&L null renders the empty text', async () => {
    base();
    renderApp('/strategies/arb/results');
    expect(await screen.findByText('No executed sets yet.')).toBeInTheDocument();
    expect(await screen.findByText('KXDEMOA-26OCT13')).toBeInTheDocument();
  });

  it('shows state, opportunities and a Stop button', async () => {
    base();
    renderApp('/arb');
    expect(await screen.findByRole('img', { name: /arbitrage is log only/i })).toBeInTheDocument();
    expect(await screen.findByText('KXDEMOA-26OCT13')).toBeInTheDocument();
    expect(screen.getByText('Partial fill')).toBeInTheDocument();
    const main = document.querySelector('main');
    expect(within(main).getByRole('button', { name: /stop the bot/i })).toBeInTheDocument();
  });

  it('Go live needs typed LIVE, then a 2FA code, and sends confirm', async () => {
    const user = userEvent.setup();
    const calls = base({ 'PUT /api/arb': ARB({ settings: { live: true }, status: { state: 'live' } }) });
    renderApp('/arb');
    await user.click(await screen.findByRole('button', { name: /go live/i }));
    const cont = await screen.findByRole('button', { name: 'Continue' });
    expect(cont).toBeDisabled();
    await user.type(screen.getByLabelText('Type LIVE to confirm'), 'LIVE');
    await user.click(cont);
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Go LIVE' }));
    expect(await screen.findByText('Arbitrage is LIVE')).toBeInTheDocument();
    const put = calls.find((c) => c.key === 'PUT /api/arb');
    expect(put.body).toEqual({ live: true, confirm: 'LIVE' });
    expect(put.headers['X-2FA-Code']).toBe('123456');
  });

  it('409 live_not_allowed is explained', async () => {
    const user = userEvent.setup();
    base({ 'PUT /api/arb': [409, { detail: 'live_not_allowed' }] });
    renderApp('/arb');
    await user.click(await screen.findByRole('button', { name: /go live/i }));
    await user.type(await screen.findByLabelText('Type LIVE to confirm'), 'LIVE');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Go LIVE' }));
    expect(await screen.findByText(/not allowed by the server/i)).toBeInTheDocument();
  });

  it('Go live is disabled when the server does not allow live', async () => {
    base({ 'GET /api/arb': ARB({ status: { live_allowed: false } }) });
    renderApp('/arb');
    expect(await screen.findByRole('button', { name: /go live/i })).toBeDisabled();
  });

  it('settings save shows the server 400 detail list', async () => {
    const user = userEvent.setup();
    const calls = base({ 'PUT /api/arb': [400, { detail: ['arb_poll_seconds too fast', 'other'] }] });
    renderApp('/strategies/arb/controls');
    const field = await screen.findByLabelText(/poll seconds/i);
    await user.clear(field);
    await user.type(field, '4');
    expect(await screen.findByText('Minimum 5')).toBeInTheDocument();
    await user.clear(field);
    await user.type(field, '20');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getAllByRole('button', { name: 'Save' }).pop());
    expect(await screen.findByText('arb_poll_seconds too fast')).toBeInTheDocument();
    expect(calls.find((c) => c.key === 'PUT /api/arb').body).toEqual({ arb_poll_seconds: 20 });
  });

  it('turning on after a legging kill asks to acknowledge and sends ack_legging', async () => {
    const user = userEvent.setup();
    const calls = base({
      'GET /api/arb': ARB({ settings: { enabled: false }, status: { state: 'stopped', kill_switch: true, kill_reason: 'legging' } }),
      'PUT /api/arb': ARB(),
    });
    renderApp('/arb');
    expect(await screen.findByText(/unmatched leg/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /turn on/i }));
    await user.click(await screen.findByRole('button', { name: 'I have checked' }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Turn on' }));
    await screen.findByText('Arbitrage is on');
    expect(calls.find((c) => c.key === 'PUT /api/arb').body).toEqual({ enabled: true, ack_legging: true });
  });

  it('Stop from the arb page sends no 2FA and reloads arb data', async () => {
    const user = userEvent.setup();
    const calls = base({ 'POST /api/bot/stop': { desired: 'stopped', kill_switch: true, arb_kill_switch: true, cancelled: 0, errors: [] } });
    renderApp('/arb');
    const main = document.querySelector('main');
    await screen.findByText('KXDEMOA-26OCT13');
    const before = calls.filter((c) => c.key === 'GET /api/arb').length;
    await user.click(within(main).getByRole('button', { name: /stop the bot/i }));
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    expect(await screen.findByText(/Arbitrage stopped/)).toBeInTheDocument();
    expect(calls.find((c) => c.key === 'POST /api/bot/stop').headers['X-2FA-Code']).toBeUndefined();
    await waitFor(() => expect(calls.filter((c) => c.key === 'GET /api/arb').length).toBeGreaterThan(before));
  });
});

describe('P&L display', () => {
  // Stage 2: Home shows the ledger total; the 7/30-day status.pnl strip lives on Activity.
  it('the P&L strip renders status.pnl when present', async () => {
    stubApi({ 'GET /api/status': { ...STATUS, pnl: { d7: 4.5, d30: -2.25 } }, 'GET /api/activity': { items: [] } });
    renderApp('/activity');
    const pnl = await screen.findByTestId('pnl');
    expect(within(pnl).getByText('+$4.50')).toBeInTheDocument();
    expect(within(pnl).getByText('-$2.25')).toBeInTheDocument();
  });

  it('the P&L strip shows dashes and a hint when pnl is null', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/activity': { items: [] } });
    renderApp('/activity');
    const pnl = await screen.findByTestId('pnl');
    expect(within(pnl).getAllByText('—').length).toBe(2);
    expect(screen.getByText(/P&L appears once fills settle/)).toBeInTheDocument();
  });

  it('Activity shows the P&L strip', async () => {
    stubApi({ 'GET /api/status': { ...STATUS, pnl: { d7: 1, d30: null } }, 'GET /api/activity': { items: [] } });
    renderApp('/activity');
    const pnl = await screen.findByTestId('pnl');
    expect(within(pnl).getByText('+$1.00')).toBeInTheDocument();
  });
});

describe('contract shapes', () => {
  it('Markets shows backend verdict + need_more from per_series rows', async () => {
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/markets': {
        markets: [{ series: 'KXDEMOA', enabled: true, fills: 20, cost: 100, pnl: 1, events: 9, roi: 0.01, ci_low: -0.02, ci_high: 0.04, verdict: 'keep', need_more: 80 }],
        report_utc: null,
      },
    });
    renderApp('/strategies/maker/results');
    expect(await screen.findByText('Keep (80 More Fills to Judge)')).toBeInTheDocument();
  });

  it('Server PUT /api/cloud 422 (FastAPI type error) shows the field message', async () => {
    const user = userEvent.setup();
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/cloud': { settings: { keepalive_minutes: 10, pinger_minutes: 5 }, sleep_timeout_minutes: 15 },
      'PUT /api/cloud': [422, { detail: [{ loc: ['body', 'pinger_minutes'], msg: 'Input should be a valid integer' }] }],
    });
    renderApp('/server');
    const ka = await screen.findByLabelText(/keepalive minutes/i);
    await user.clear(ka);
    await user.type(ka, '9');
    await user.click(screen.getByRole('button', { name: /save/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getAllByRole('button', { name: 'Save' }).pop());
    expect(await screen.findByText('pinger_minutes: Input should be a valid integer')).toBeInTheDocument();
  });

  it('pinger sync returning the GET /api/cloud shape updates the page', async () => {
    const user = userEvent.setup();
    const cloud = (synced) => ({
      settings: { keepalive_minutes: 10, pinger_minutes: 5 },
      pinger: { configured: true, job_id: 42, synced, last_sync_utc: null, error: null },
      sleep_timeout_minutes: 15,
    });
    stubApi({ 'GET /api/status': STATUS, 'GET /api/cloud': cloud(false), 'POST /api/cloud/pinger/sync': cloud(true) });
    renderApp('/server');
    await user.click(await screen.findByRole('button', { name: /re-sync pinger/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Re-sync' }));
    expect(await screen.findByText('Pinger synced')).toBeInTheDocument();
    expect(await screen.findByText('yes')).toBeInTheDocument();
  });
});
