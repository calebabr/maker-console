import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setToken } from '../services/api.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => setToken(null));
afterEach(() => vi.unstubAllGlobals());

async function fillLogin(user) {
  await user.type(screen.getByLabelText('Username'), 'demo-user');
  await user.type(screen.getByLabelText('Password'), 'hunter2');
  await user.click(screen.getByRole('button', { name: 'Continue' }));
}

describe('login -> 2FA', () => {
  it('password then code signs in and lands on Overview', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'POST /api/auth/login': { mfa_token: 'm1', expires_in: 300 },
      'POST /api/auth/2fa': { access_token: 'acc' },
      'GET /api/status': STATUS,
      'GET /api/history': { snapshots: [] },
    });
    renderApp('/', { signedIn: false });
    await fillLogin(user);
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    // Header + Overview Stop buttons. waitFor (not findAll): findAll resolves on the first
    // match, which can be the header's button while /login is still redirecting to /.
    await waitFor(() => expect(screen.getAllByRole('button', { name: /stop the bot/i })).toHaveLength(2));
    const twofa = calls.find((c) => c.key === 'POST /api/auth/2fa');
    expect(twofa.body).toEqual({ mfa_token: 'm1', code: '123456' });
  });

  it('wrong password shows an error and stays on the password step', async () => {
    const user = userEvent.setup();
    stubApi({ 'POST /api/auth/login': [401, { detail: 'bad' }] });
    renderApp('/', { signedIn: false });
    await fillLogin(user);
    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong username or password.');
    expect(screen.getByLabelText('Username')).toBeInTheDocument();
  });

  it('429 account_locked shows the lockout message', async () => {
    const user = userEvent.setup();
    stubApi({ 'POST /api/auth/login': [429, { detail: 'account_locked' }] });
    renderApp('/', { signedIn: false });
    await fillLogin(user);
    expect(await screen.findByRole('alert')).toHaveTextContent(/account locked.*15 minutes/i);
  });

  it('429 account_locked on the code step also shows lockout', async () => {
    const user = userEvent.setup();
    stubApi({
      'POST /api/auth/login': { mfa_token: 'm1', expires_in: 300 },
      'POST /api/auth/2fa': [429, { detail: 'account_locked' }],
    });
    renderApp('/', { signedIn: false });
    await fillLogin(user);
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/account locked/i);
  });

  it('rejected code shows "wait for the next code" and clears the field', async () => {
    const user = userEvent.setup();
    stubApi({
      'POST /api/auth/login': { mfa_token: 'm1', expires_in: 300 },
      'POST /api/auth/2fa': [401, { detail: 'bad code' }],
    });
    renderApp('/', { signedIn: false });
    await fillLogin(user);
    const input = await screen.findByLabelText('6-digit code');
    await user.type(input, '654321');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/wait for the next code/i);
    expect(input).toHaveValue('');
  });

  it('Sign in stays disabled until 6 digits are typed; non-digits are dropped', async () => {
    const user = userEvent.setup();
    stubApi({ 'POST /api/auth/login': { mfa_token: 'm1', expires_in: 300 } });
    renderApp('/', { signedIn: false });
    await fillLogin(user);
    const input = await screen.findByLabelText('6-digit code');
    await user.type(input, '12a34');
    expect(input).toHaveValue('1234');
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled();
  });
});

describe('Stop', () => {
  async function setup(stopRes) {
    const calls = stubApi({
      'GET /api/status': { ...STATUS, desired: 'running', kill_switch: false },
      'GET /api/history': { snapshots: [] },
      'POST /api/bot/stop': stopRes,
    });
    renderApp('/');
    await waitFor(() => expect(calls.some((c) => c.key === 'GET /api/status')).toBe(true));
    return calls;
  }

  it('is in the header and on Overview', async () => {
    await setup({});
    const header = document.querySelector('header.appbar');
    expect(within(header).getByRole('button', { name: /stop the bot/i })).toBeInTheDocument();
    const main = document.querySelector('main');
    expect(within(main).getByRole('button', { name: /stop the bot/i })).toBeInTheDocument();
  });

  it('header Stop confirms, calls stop with no 2FA header, shows result', async () => {
    const user = userEvent.setup();
    const calls = await setup({ desired: 'stopped', kill_switch: true, cancelled: 3, errors: [] });
    const header = document.querySelector('header.appbar');
    await user.click(within(header).getByRole('button', { name: /stop the bot/i }));
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    expect(await screen.findByText('Stopped. Kill switch set.')).toBeInTheDocument();
    expect(screen.getByText(/cancelled 3 resting order/)).toBeInTheDocument();
    const stop = calls.find((c) => c.key === 'POST /api/bot/stop');
    expect(stop.headers['X-2FA-Code']).toBeUndefined();
    expect(screen.queryByLabelText('6-digit code')).toBeNull();
  });

  it('Overview Stop works too and shows partial errors', async () => {
    const user = userEvent.setup();
    await setup({ desired: 'stopped', kill_switch: true, cancelled: 1, errors: ['order X'] });
    const main = document.querySelector('main');
    await user.click(within(main).getByRole('button', { name: /stop the bot/i }));
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    expect(await screen.findByText(/Not done: order X/)).toBeInTheDocument();
  });

  it('a failed stop is reported as NOT reaching the server', async () => {
    const user = userEvent.setup();
    await setup([500, { detail: 'boom' }]);
    const main = document.querySelector('main');
    await user.click(within(main).getByRole('button', { name: /stop the bot/i }));
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    expect(await screen.findByText('Stop did NOT reach the server')).toBeInTheDocument();
  });

  it('cancelling the confirm sends nothing', async () => {
    const user = userEvent.setup();
    const calls = await setup({});
    const main = document.querySelector('main');
    await user.click(within(main).getByRole('button', { name: /stop the bot/i }));
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.key === 'POST /api/bot/stop')).toBe(false);
  });
});

describe('2FA actions', () => {
  it('Start opens the prompt, sends the code, and a 403 2fa_invalid keeps it open', async () => {
    const user = userEvent.setup();
    let n = 0;
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/history': { snapshots: [] },
      'POST /api/bot/start': () => (++n === 1 ? [403, { detail: '2fa_invalid' }] : { desired: 'running' }),
    });
    renderApp('/');
    const start = await screen.findByRole('button', { name: /start/i });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);
    const input = await screen.findByLabelText('6-digit code');
    await user.type(input, '111111');
    await user.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/wait for the next code/i);
    expect(screen.getByLabelText('6-digit code')).toBeInTheDocument(); // still open
    expect(calls.filter((c) => c.key === 'POST /api/bot/start')[0].headers['X-2FA-Code']).toBe('111111');

    await user.type(screen.getByLabelText('6-digit code'), '222222');
    await user.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByText('Started')).toBeInTheDocument();
    expect(screen.queryByLabelText('6-digit code')).toBeNull();
    expect(calls.filter((c) => c.key === 'POST /api/bot/start')[1].headers['X-2FA-Code']).toBe('222222');
  });

  it('Cancel closes the prompt without calling the API', async () => {
    const user = userEvent.setup();
    const calls = stubApi({ 'GET /api/status': STATUS, 'GET /api/history': { snapshots: [] } });
    renderApp('/');
    const start = await screen.findByRole('button', { name: /start/i });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(screen.queryByLabelText('6-digit code')).toBeNull();
    expect(calls.some((c) => c.key === 'POST /api/bot/start')).toBe(false);
  });
});

describe('Settings', () => {
  const CONFIG = {
    config: { run_budget: 50, poll_minutes: 10, cells: [[0.3, 0.45, 6, 12, "no"]] },
    schema: { run_budget: { type: 'integer', min: 1, max: 500 }, poll_minutes: { type: 'integer', min: 1 } },
    ceilings: {},
  };

  it('Save sends the code and shows the 400 detail list', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': CONFIG,
      'PUT /api/config': [400, { detail: ['run_budget too high', 'bad series'] }],
    });
    renderApp('/settings');
    const field = await screen.findByLabelText(/run budget/i);
    await user.clear(field);
    await user.type(field, '60');
    await user.click(screen.getByRole('button', { name: /save/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getAllByRole('button', { name: 'Save' }).pop());
    expect(await screen.findByText('run_budget too high')).toBeInTheDocument();
    expect(screen.getByText('bad series')).toBeInTheDocument();
    const put = calls.find((c) => c.key === 'PUT /api/config');
    expect(put.headers['X-2FA-Code']).toBe('123456');
    expect(put.body).toEqual({ run_budget: 60 });
  });

  it('poll_minutes is capped at 14 client-side', async () => {
    const user = userEvent.setup();
    stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG });
    renderApp('/settings');
    const field = await screen.findByLabelText(/poll minutes/i);
    await user.clear(field);
    await user.type(field, '15');
    expect(await screen.findByText('Maximum 14')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
  });

  it('cells are read-only and never sent', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': CONFIG,
      'PUT /api/config': { config: CONFIG.config, version: 7 },
    });
    renderApp('/settings');
    const field = await screen.findByLabelText(/run budget/i);
    await user.clear(field);
    await user.type(field, '70');
    await user.click(screen.getByRole('button', { name: /save/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getAllByRole('button', { name: 'Save' }).pop());
    await screen.findByText(/Saved as version 7/);
    expect(Object.keys(calls.find((c) => c.key === 'PUT /api/config').body)).not.toContain('cells');
  });
});

describe('Server page', () => {
  const CLOUD = { settings: { keepalive_minutes: 10, pinger_minutes: 5, pinger_enabled: true }, sleep_timeout_minutes: 15 };

  it('caps both intervals at 1-14', async () => {
    const user = userEvent.setup();
    stubApi({ 'GET /api/status': STATUS, 'GET /api/cloud': CLOUD });
    renderApp('/server');
    const ka = await screen.findByLabelText(/keepalive minutes/i);
    const pg = screen.getByLabelText(/pinger minutes/i);
    expect(ka).toHaveAttribute('min', '1');
    expect(ka).toHaveAttribute('max', '14');
    expect(pg).toHaveAttribute('max', '14');
    await user.clear(ka);
    await user.type(ka, '15');
    expect(await screen.findByText('Maximum 14')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
    await user.clear(ka);
    await user.type(ka, '0');
    expect(await screen.findByText('Minimum 1')).toBeInTheDocument();
    await user.clear(ka);
    await user.type(ka, '14');
    expect(screen.queryByText('Maximum 14')).toBeNull();
    expect(screen.getByRole('button', { name: /save/i })).toBeEnabled();
  });
});

describe('401 handling', () => {
  it('a 401 on a signed-in call returns to login with a session-ended notice', async () => {
    stubApi({ 'GET /api/status': [401, { detail: 'expired' }] });
    renderApp('/');
    expect(await screen.findByLabelText('Username')).toBeInTheDocument();
    expect(screen.getByText(/session ended/i)).toBeInTheDocument();
    expect(sessionStorage.getItem('km.token')).toBeNull();
  });
});
