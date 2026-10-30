import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setToken } from '../services/api.js';
import { configScope, isMakerKey } from '../utils/strategyConfig.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

// Maker and Arbitrage Controls: every write keeps its PUT payload and its fresh-2FA requirement
// (the old single pages were split into Overview | Results | Controls).

beforeEach(() => setToken(null));
afterEach(() => vi.unstubAllGlobals());

async function enterCode(user, label) {
  await user.type(await screen.findByLabelText('6-digit code'), '123456');
  const btns = screen.getAllByRole('button', { name: label });
  await user.click(btns[btns.length - 1]);
}

const ARB = {
  settings: { enabled: true, live: false, arb_max_contracts: 100, arb_min_profit: 0.05, arb_series: ['KXDEMOA', 'KXDEMOB'], arb_poll_seconds: 15 },
  schema: {
    arb_poll_seconds: { type: 'integer', unit: 'seconds', min: 5, max: 60 },
    arb_max_contracts: { type: 'number', min: 1, max: 100 },
  },
  status: { state: 'log-only', running: true, kill_switch: false, live_allowed: true, scans: 1, found: 0 },
  pnl: null,
};

const MARKETS = { markets: [{ series: 'KXDEMOA', enabled: true, fills: 1, cost: 1, pnl: 0, roi: 0 }] };

const CONFIG = {
  config: { run_budget: 25, max_order: 5, max_open_frac: 0.2, series: ['KXDEMOA'], poll_minutes: 10, cells: [[0.35, 0.5, 3, 9, 'yes']] },
  schema: {
    run_budget: { type: 'number', unit: 'USD', min: 1, max: 1000 },
    max_order: { type: 'number', unit: 'USD', min: 1, max: 100 },
    max_open_frac: { type: 'number', min: 0.05, max: 0.9 },
    poll_minutes: { type: 'integer', unit: 'minutes', min: 2, max: 60 },
    series: { type: 'list', options: ['KXDEMOA', 'KXDEMOB'] },
  },
  ceilings: {},
};

describe('strategy config key map', () => {
  it('maps maker keys, arbitrage keys and treats everything else as global', () => {
    expect(configScope('run_budget')).toBe('maker');
    expect(configScope('arb_day_cap')).toBe('arb');
    expect(configScope('poll_minutes')).toBe('global');
    expect(configScope('some_new_backend_key')).toBe('global');
    expect(isMakerKey('some_new_backend_key')).toBe(false);
  });
});

describe('Arbitrage Controls writes', () => {
  const base = (extra = {}) =>
    stubApi({ 'GET /api/status': STATUS, 'GET /api/arb': ARB, 'GET /api/arb/opportunities': { items: [] }, ...extra });

  it('Save sends only the changed field with a fresh code, and nothing before the code', async () => {
    const user = userEvent.setup();
    const calls = base({ 'PUT /api/arb': () => ARB });
    renderApp('/strategies/arb/controls');
    const field = await screen.findByLabelText(/poll seconds/i);
    await user.clear(field);
    await user.type(field, '20');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    expect(calls.some((c) => c.key === 'PUT /api/arb')).toBe(false);
    await enterCode(user, 'Save');
    await waitFor(() => expect(calls.some((c) => c.key === 'PUT /api/arb')).toBe(true));
    const put = calls.find((c) => c.key === 'PUT /api/arb');
    expect(put.body).toEqual({ arb_poll_seconds: 20 });
    expect(put.headers['X-2FA-Code']).toBe('123456');
  });

  it('shows the 400 detail list when the server rejects a save', async () => {
    const user = userEvent.setup();
    base({ 'PUT /api/arb': [400, { detail: ['first problem', 'second problem'] }] });
    renderApp('/strategies/arb/controls');
    const field = await screen.findByLabelText(/poll seconds/i);
    await user.clear(field);
    await user.type(field, '20');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await enterCode(user, 'Save');
    expect(await screen.findByText('first problem')).toBeInTheDocument();
    expect(screen.getByText('second problem')).toBeInTheDocument();
  });

  it('Turn off on Controls needs a code and sends {enabled:false}', async () => {
    const user = userEvent.setup();
    const calls = base({ 'PUT /api/arb': () => ARB });
    renderApp('/strategies/arb/controls');
    await user.click(await screen.findByRole('button', { name: /turn off/i }));
    expect(calls.some((c) => c.key === 'PUT /api/arb')).toBe(false);
    await enterCode(user, /^turn off$/i);
    await waitFor(() => expect(calls.some((c) => c.key === 'PUT /api/arb')).toBe(true));
    const put = calls.find((c) => c.key === 'PUT /api/arb');
    expect(put.body).toEqual({ enabled: false });
    expect(put.headers['X-2FA-Code']).toBe('123456');
  });

  it('Go live on Controls needs typed LIVE, then a code, and sends confirm', async () => {
    const user = userEvent.setup();
    const calls = base({ 'PUT /api/arb': () => ARB });
    renderApp('/strategies/arb/controls');
    await user.click(await screen.findByRole('button', { name: /go live/i }));
    const typed = await screen.findByRole('textbox');
    const cont = screen.getByRole('button', { name: /continue/i });
    expect(cont).toBeDisabled();
    await user.type(typed, 'LIVE');
    await user.click(cont);
    await enterCode(user, /go live/i);
    await waitFor(() => expect(calls.some((c) => c.key === 'PUT /api/arb')).toBe(true));
    const put = calls.find((c) => c.key === 'PUT /api/arb');
    expect(put.body).toEqual({ live: true, confirm: 'LIVE' });
    expect(put.headers['X-2FA-Code']).toBe('123456');
  });

  it('keeps the Stop line and the Stop button on the Overview', async () => {
    base();
    renderApp('/strategies/arb');
    expect(await screen.findByText(/Stop turns arbitrage off too, no code needed/)).toBeInTheDocument();
    expect(within(document.querySelector('main')).getByRole('button', { name: /stop the bot/i })).toBeInTheDocument();
  });
});

describe('Maker Controls writes', () => {
  it('Pause needs a code and sends {enabled:false} to the series path', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/markets': MARKETS,
      'GET /api/config': CONFIG,
      'PUT /api/markets/KXDEMOA': { version: 3 },
    });
    renderApp('/strategies/maker/controls');
    await user.click(await screen.findByRole('button', { name: /pause demo series a/i }));
    expect(calls.some((c) => c.key === 'PUT /api/markets/KXDEMOA')).toBe(false);
    await enterCode(user, 'Pause');
    await waitFor(() => expect(calls.some((c) => c.key === 'PUT /api/markets/KXDEMOA')).toBe(true));
    const put = calls.find((c) => c.key === 'PUT /api/markets/KXDEMOA');
    expect(put.body).toEqual({ enabled: false });
    expect(put.headers['X-2FA-Code']).toBe('123456');
  });

  it('shows only the Maker settings, saves only changed keys with a code, and keeps global keys in Settings', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/markets': MARKETS,
      'GET /api/config': CONFIG,
      'GET /api/config/versions': { versions: [], undo: null },
      'PUT /api/config': () => ({ config: { ...CONFIG.config, run_budget: 40 }, version: 4 }),
    });
    renderApp('/strategies/maker/controls');
    const field = await screen.findByLabelText(/run budget/i);
    expect(screen.queryByLabelText(/poll minutes/i)).toBeNull(); // global: Settings only
    await user.clear(field);
    await user.type(field, '40');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    expect(calls.some((c) => c.key === 'PUT /api/config')).toBe(false);
    await enterCode(user, 'Save');
    await waitFor(() => expect(calls.some((c) => c.key === 'PUT /api/config')).toBe(true));
    const put = calls.find((c) => c.key === 'PUT /api/config');
    expect(put.body).toEqual({ run_budget: 40 });
    expect(put.headers['X-2FA-Code']).toBe('123456');
  });

  it('Settings still shows the full form, global keys included', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG, 'GET /api/config/versions': { versions: [], undo: null } });
    renderApp('/settings');
    expect(await screen.findByLabelText(/run budget/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/poll minutes/i)).toBeInTheDocument();
  });
});
