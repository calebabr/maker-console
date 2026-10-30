import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setToken } from '../services/api.js';
import { writeCache } from '../services/cache.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => setToken(null));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const SCHEMA = {
  arb_poll_seconds: { type: 'integer', unit: 'seconds', min: 5, max: 60 },
  arb_max_contracts: { type: 'number', min: 1, max: 100 },
  arb_min_profit: { type: 'number', min: 0.01, max: 5 },
};

const ARB = (over = {}) => ({
  settings: {
    enabled: true, live: false, arb_max_contracts: 50, arb_min_profit: 0.05,
    arb_series: ['KXDEMOA', 'KXDEMOB'], arb_poll_seconds: 15, ...(over.settings || {}),
  },
  schema: SCHEMA,
  ceilings: over.ceilings || {},
  status: {
    state: 'log-only', running: true, kill_switch: false, kill_reason: null, last_scan_utc: null,
    last_error: null, live_allowed: true, scans: 3, found: 0, ...(over.status || {}),
  },
  pnl: null,
});

function base(extra = {}, arb = ARB()) {
  return stubApi({
    'GET /api/status': { ...STATUS, live_allowed: true },
    'GET /api/arb': arb,
    'GET /api/arb/opportunities': { items: [] },
    ...extra,
  });
}

async function goLiveTo2fa(user) {
  await user.click(await screen.findByRole('button', { name: /go live/i }));
  await user.type(await screen.findByLabelText('Type LIVE to confirm'), 'LIVE');
  await user.click(screen.getByRole('button', { name: 'Continue' }));
}

describe('Arbitrage go-live confirmation edge cases', () => {
  it('lowercase "live" does not enable Continue', async () => {
    const user = userEvent.setup();
    base();
    renderApp('/arb');
    await user.click(await screen.findByRole('button', { name: /go live/i }));
    await user.type(await screen.findByLabelText('Type LIVE to confirm'), 'live');
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
  });

  it('cancelling the typed confirm sends no PUT and shows no code prompt', async () => {
    const user = userEvent.setup();
    const calls = base();
    renderApp('/arb');
    await user.click(await screen.findByRole('button', { name: /go live/i }));
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(screen.queryByLabelText('6-digit code')).toBeNull();
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
  });

  it('cancelling at the code prompt (after typing LIVE) sends no PUT', async () => {
    const user = userEvent.setup();
    const calls = base();
    renderApp('/arb');
    await goLiveTo2fa(user);
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
    expect(screen.queryByText('Arbitrage is LIVE')).toBeNull();
  });

  it('a rejected code keeps the prompt open; a retry with a new code goes live', async () => {
    const user = userEvent.setup();
    let n = 0;
    const calls = base({
      'PUT /api/arb': () => (++n === 1 ? [403, { detail: '2fa_invalid' }] : ARB({ settings: { live: true }, status: { state: 'live' } })),
    });
    renderApp('/arb');
    await goLiveTo2fa(user);
    await user.type(await screen.findByLabelText('6-digit code'), '111111');
    await user.click(screen.getByRole('button', { name: 'Go LIVE' }));
    expect(await screen.findByText(/Code Rejected/)).toBeInTheDocument();
    await user.type(screen.getByLabelText('6-digit code'), '222222');
    await user.click(screen.getByRole('button', { name: 'Go LIVE' }));
    expect(await screen.findByText('Arbitrage is LIVE')).toBeInTheDocument();
    const puts = calls.filter((c) => c.key === 'PUT /api/arb');
    expect(puts.map((c) => c.headers['X-2FA-Code'])).toEqual(['111111', '222222']);
  });

  it('when live, "Log only" needs a code but no typed LIVE, and sends {live:false}', async () => {
    const user = userEvent.setup();
    const calls = base(
      { 'PUT /api/arb': ARB() },
      ARB({ settings: { live: true }, status: { state: 'live' } }),
    );
    renderApp('/arb');
    await user.click(await screen.findByRole('button', { name: /log only/i }));
    expect(screen.queryByLabelText('Type LIVE to confirm')).toBeNull();
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Log only', exact: true }));
    await screen.findByText('Arbitrage is log-only');
    expect(calls.find((c) => c.key === 'PUT /api/arb').body).toEqual({ live: false });
  });

  it('Turn off sends {enabled:false} with a code', async () => {
    const user = userEvent.setup();
    const calls = base({
      'PUT /api/arb': ARB({ settings: { enabled: false }, status: { state: 'stopped' } }),
    });
    renderApp('/arb');
    await user.click(await screen.findByRole('button', { name: /turn off/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Turn off', exact: true }));
    await screen.findByText('Arbitrage is off');
    const put = calls.find((c) => c.key === 'PUT /api/arb');
    expect(put.body).toEqual({ enabled: false });
    expect(put.headers['X-2FA-Code']).toBe('123456');
  });

  it('a Stop-set kill switch is shown and the toggle offers Turn on', async () => {
    base({}, ARB({ settings: { enabled: false }, status: { state: 'stopped', kill_switch: true, kill_reason: 'stop' } }));
    renderApp('/arb');
    expect(await screen.findByText(/Kill switch set/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /turn on/i })).toBeInTheDocument();
  });

  it('legging: cancelling the acknowledgement sends nothing', async () => {
    const user = userEvent.setup();
    const calls = base({}, ARB({ settings: { enabled: false }, status: { state: 'stopped', kill_switch: true, kill_reason: 'legging' } }));
    renderApp('/arb');
    expect(await screen.findByText(/unmatched leg/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /turn on/i }));
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(screen.queryByLabelText('6-digit code')).toBeNull();
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
  });

  it('409 arb_legging_kill from the server is explained', async () => {
    const user = userEvent.setup();
    base({ 'PUT /api/arb': [409, { detail: 'arb_legging_kill: acknowledge first' }] });
    renderApp('/arb');
    await user.click(await screen.findByRole('button', { name: /turn off/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Turn off', exact: true }));
    expect(await screen.findByText('Legging kill switch is set')).toBeInTheDocument();
  });
});

describe('Arbitrage settings form validation', () => {
  async function setField(user, label, value) {
    const input = await screen.findByLabelText(label);
    await user.clear(input);
    if (value !== '') await user.type(input, String(value));
    return input;
  }
  const saveBtn = () => screen.getByRole('button', { name: /save/i });

  it('Save is disabled until something changes; Undo restores', async () => {
    const user = userEvent.setup();
    base();
    renderApp('/strategies/arb/controls');
    const input = await setField(user, /max contracts/i, 50);
    expect(saveBtn()).toBeDisabled();
    await user.clear(input);
    await user.type(input, '60');
    expect(saveBtn()).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Undo changes' }));
    expect(screen.getByLabelText(/max contracts/i)).toHaveValue(50);
    expect(saveBtn()).toBeDisabled();
  });

  it.each([
    ['max contracts', 101, 'Maximum 100'],
    ['max contracts', 0, 'Minimum 1'],
    ['poll', 61, 'Maximum 60'],
    ['poll', 4, 'Minimum 5'],
    ['poll', 7.5, 'Whole number only'],
    ['min profit', 0, 'Minimum 0.01'],
  ])('%s = %s is rejected client-side (%s) and cannot be saved', async (label, value, msg) => {
    const user = userEvent.setup();
    const calls = base();
    renderApp('/strategies/arb/controls');
    await setField(user, new RegExp(label, 'i'), value);
    expect(await screen.findByText(msg)).toBeInTheDocument();
    expect(saveBtn()).toBeDisabled();
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
  });

  it('an empty field is Required and blocks Save', async () => {
    const user = userEvent.setup();
    base();
    renderApp('/strategies/arb/controls');
    await setField(user, /max contracts/i, '');
    expect(await screen.findByText('Required')).toBeInTheDocument();
    expect(saveBtn()).toBeDisabled();
  });

  it('server ceilings tighten the allowed maximum below the schema max', async () => {
    const user = userEvent.setup();
    base({}, ARB({ ceilings: { arb_max_contracts: 20 } }));
    renderApp('/strategies/arb/controls');
    await setField(user, /max contracts/i, 30);
    expect(await screen.findByText('Maximum 20')).toBeInTheDocument();
    expect(saveBtn()).toBeDisabled();
  });

  it('unchecking every series blocks Save with "Pick at least one"', async () => {
    const user = userEvent.setup();
    base();
    renderApp('/strategies/arb/controls');
    await user.click(await screen.findByRole('checkbox', { name: 'KXDEMOA' }));
    await user.click(screen.getByRole('checkbox', { name: 'KXDEMOB' }));
    expect(await screen.findByText('Pick at least one')).toBeInTheDocument();
    expect(saveBtn()).toBeDisabled();
  });

  it('only changed fields are sent, with the code', async () => {
    const user = userEvent.setup();
    const calls = base({ 'PUT /api/arb': ARB({ settings: { arb_max_contracts: 60 } }) });
    renderApp('/strategies/arb/controls');
    await setField(user, /max contracts/i, 60);
    await user.click(saveBtn());
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }));
    await screen.findByText('Arbitrage settings saved');
    const put = calls.find((c) => c.key === 'PUT /api/arb');
    expect(put.body).toEqual({ arb_max_contracts: 60 });
    expect(put.headers['X-2FA-Code']).toBe('123456');
  });

  it('a 422 FastAPI error list is shown field by field', async () => {
    const user = userEvent.setup();
    base({
      'PUT /api/arb': [422, { detail: [{ loc: ['body', 'arb_max_contracts'], msg: 'Input should be less than or equal to 100' }] }],
    });
    renderApp('/strategies/arb/controls');
    await setField(user, /max contracts/i, 60);
    await user.click(saveBtn());
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }));
    expect(await screen.findByText('Not saved.')).toBeInTheDocument();
    expect(screen.getByText(/less than or equal to 100/)).toBeInTheDocument();
  });
});

describe('Arbitrage data states', () => {
  it('empty opportunities show "None found yet."', async () => {
    base();
    renderApp('/arb');
    expect(await screen.findByText('None found yet.')).toBeInTheDocument();
  });

  it('a failing opportunities endpoint does not hide the arb controls or Stop', async () => {
    base({ 'GET /api/arb/opportunities': [500, { detail: 'boom' }] });
    renderApp('/arb');
    expect(await screen.findByRole('img', { name: /arbitrage is log only/i })).toBeInTheDocument();
    const main = document.querySelector('main');
    expect(within(main).getByRole('button', { name: /stop the bot/i })).toBeEnabled();
    expect((await screen.findAllByRole('alert')).length).toBeGreaterThan(0);
  });

  it('GET /api/arb failing shows cached arb state with an offline notice', async () => {
    writeCache('/api/arb', ARB());
    base({ 'GET /api/arb': [500, { detail: 'boom' }] });
    renderApp('/arb');
    expect(await screen.findByRole('img', { name: /arbitrage is log only/i })).toBeInTheDocument();
    expect(await screen.findByText(/Offline copy from/)).toBeInTheDocument();
  });

  it('GET /api/arb failing with no cache shows an error and still offers Stop', async () => {
    base({ 'GET /api/arb': [500, { detail: 'boom' }] });
    renderApp('/arb');
    expect((await screen.findAllByRole('alert'))[0]).toBeInTheDocument();
    const header = document.querySelector('header.appbar');
    expect(within(header).getByRole('button', { name: /stop the bot/i })).toBeEnabled();
  });

  it('last_error from the scanner is displayed', async () => {
    base({}, ARB({ status: { last_error: 'kalshi 503' } }));
    renderApp('/arb');
    expect(await screen.findByText('kalshi 503')).toBeInTheDocument();
  });

  it('live is disabled with an explanation when live_allowed is false', async () => {
    base({}, ARB({ status: { live_allowed: false } }));
    renderApp('/arb');
    expect(await screen.findByRole('button', { name: /go live/i })).toBeDisabled();
    expect(screen.getByText(/Live Is Disabled/)).toBeInTheDocument();
  });

  it('the page re-polls every 30 s and reloads after a Stop event', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = base();
    renderApp('/arb');
    await screen.findByRole('img', { name: /arbitrage is log only/i });
    const count = () => calls.filter((c) => c.key === 'GET /api/arb').length;
    const before = count();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30500);
    });
    await waitFor(() => expect(count()).toBeGreaterThan(before));
    const mid = count();
    await act(async () => {
      window.dispatchEvent(new Event('km:stopped'));
    });
    await waitFor(() => expect(count()).toBeGreaterThan(mid));
  });
});
