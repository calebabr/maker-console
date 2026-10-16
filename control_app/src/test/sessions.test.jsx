// S-15 follow-ups: 401 session_expired, 503 store_unavailable banner, Log out all sessions.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { clearToken, isStoreDown } from '../services/api.js';
import { writeCache, readCache } from '../services/cache.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => clearToken());
afterEach(() => vi.unstubAllGlobals());

const DB_DOWN = [503, { detail: 'store_unavailable' }];

describe('401 session_expired', () => {
  it('returns to login, clears the token and explains why', async () => {
    stubApi({ 'GET /api/status': [401, { detail: 'session_expired' }], 'GET /api/history': { snapshots: [] } });
    renderApp('/');
    expect(await screen.findByLabelText('Username')).toBeInTheDocument();
    expect(screen.getByText(/all sessions were logged out/i)).toBeInTheDocument();
    expect(sessionStorage.getItem('km.token')).toBeNull();
  });
});

describe('503 store_unavailable', () => {
  it('shows the database banner, keeps the cached data and Stop still works', async () => {
    const user = userEvent.setup();
    writeCache('/api/status', { ...STATUS, desired: 'running', kill_switch: false, mode: 'demo' });
    const calls = stubApi({
      'GET /api/status': DB_DOWN,
      'GET /api/history': DB_DOWN,
      'POST /api/bot/stop': { desired: 'stopped', kill_switch: true, arb_kill_switch: true, cancelled: 2, errors: [] },
    });
    renderApp('/');
    expect(await screen.findByText(/can.t reach the database\. Stop still works/i)).toBeInTheDocument();
    expect(readCache('/api/status')).not.toBeNull(); // offline copy not wiped
    expect(screen.getAllByText('Demo').length).toBeGreaterThan(0); // cached status still rendered

    const main = document.querySelector('main');
    const stop = within(main).getByRole('button', { name: /stop the bot/i });
    expect(stop).toBeEnabled();
    await user.click(stop);
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    expect(await screen.findByText('Stopped. Kill switch set.')).toBeInTheDocument();
    expect(calls.find((c) => c.key === 'POST /api/bot/stop').headers['X-2FA-Code']).toBeUndefined();
    // A successful Stop does not mean the database is back.
    expect(isStoreDown()).toBe(true);
  });

  it('the banner clears after the next successful call', async () => {
    let down = true;
    stubApi({
      'GET /api/status': () => (down ? DB_DOWN : STATUS),
      'GET /api/history': { snapshots: [] },
      'GET /api/markets': { markets: [] },
    });
    const user = userEvent.setup();
    renderApp('/');
    expect(await screen.findByText(/can.t reach the database/i)).toBeInTheDocument();
    down = false;
    await user.click(screen.getByRole('link', { name: /markets/i }));
    await waitFor(() => expect(screen.queryByText(/can.t reach the database/i)).toBeNull());
  });

  it('error messages are readable, not the raw code', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/markets': DB_DOWN });
    renderApp('/markets');
    expect((await screen.findAllByText("The server can't reach its database. Stop still works.")).length).toBeGreaterThan(0);
  });
});

describe('Log out all sessions', () => {
  const CLOUD = { settings: { keepalive_minutes: 10, pinger_minutes: 5 }, sleep_timeout_minutes: 15 };

  it('needs a 2FA code, calls logout-all, then signs out locally', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/cloud': CLOUD,
      'POST /api/auth/logout-all': { ok: true, sessions_valid_after: '2026-10-13T12:00:00Z' },
    });
    renderApp('/server');
    await user.click(await screen.findByRole('button', { name: /log out all sessions/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Log out all' }));
    expect(await screen.findByLabelText('Username')).toBeInTheDocument();
    expect(screen.getByText(/all sessions were logged out, on every device/i)).toBeInTheDocument();
    const call = calls.find((c) => c.key === 'POST /api/auth/logout-all');
    expect(call.headers['X-2FA-Code']).toBe('123456');
    expect(sessionStorage.getItem('km.token')).toBeNull();
    expect(calls.some((c) => c.key === 'POST /api/auth/logout')).toBe(false);
  });

  it('a failure keeps the session and says so', async () => {
    const user = userEvent.setup();
    stubApi({ 'GET /api/status': STATUS, 'GET /api/cloud': CLOUD, 'POST /api/auth/logout-all': DB_DOWN });
    renderApp('/server');
    await user.click(await screen.findByRole('button', { name: /log out all sessions/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Log out all' }));
    expect(await screen.findByText('Sessions NOT logged out')).toBeInTheDocument();
    expect(sessionStorage.getItem('km.token')).toBe('tok');
  });
});

describe('fill quality', () => {
  const FQ = { n: 60, trade_through_share: 0.52, avg_move_60m_c: -0.9, baseline: { trade_through_share: 0.3, avg_move_60m_c: -0.2 } };   // made-up numbers

  it('Overview shows fill quality vs baseline and flags worse-than-tape', async () => {
    stubApi({ 'GET /api/status': { ...STATUS, fill_quality: FQ }, 'GET /api/history': { snapshots: [] } });
    renderApp('/');
    await userEvent.setup().click(await waitFor(() => { const b = document.querySelector('#fill-quality-fold .fold-toggle'); expect(b).not.toBeNull(); return b; }));
    const fq = await screen.findByTestId('fill-quality');
    expect(within(fq).getByText('52%')).toBeInTheDocument();
    expect(within(fq).getByText(/back of the queue/)).toBeInTheDocument();
  });

  it('under 50 fills shows numbers but no warning', async () => {
    stubApi({ 'GET /api/status': { ...STATUS, fill_quality: { ...FQ, n: 30 } }, 'GET /api/history': { snapshots: [] } });
    renderApp('/');
    await userEvent.setup().click(await waitFor(() => { const b = document.querySelector('#fill-quality-fold .fold-toggle'); expect(b).not.toBeNull(); return b; }));
    const fq = await screen.findByTestId('fill-quality');
    expect(within(fq).getByText('52%')).toBeInTheDocument();
    expect(within(fq).getByText(/Too few fills to compare \(30\/50\)/)).toBeInTheDocument();
    expect(within(fq).queryByText(/back of the queue/)).toBeNull();
  });

  it('n = 0 shows a note, not numbers', async () => {
    stubApi({
      'GET /api/status': { ...STATUS, fill_quality: { n: 0, trade_through_share: null, avg_move_60m_c: null, baseline: FQ.baseline } },
      'GET /api/history': { snapshots: [] },
    });
    renderApp('/');
    await userEvent.setup().click(await waitFor(() => { const b = document.querySelector('#fill-quality-fold .fold-toggle'); expect(b).not.toBeNull(); return b; }));
    expect(await screen.findByText('Fill Quality: No fills measured yet.')).toBeInTheDocument();
  });

  it('Markets shows the top-level block and keeps row fill_quality out of More stats', async () => {
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/markets': { markets: [{ series: 'KXDEMOA', enabled: true, fill_quality: { ...FQ, trade_through_share: 0.3, avg_move_60m_c: 0 } }], fill_quality: FQ },
    });
    renderApp('/markets');
    expect(await screen.findByText('Fill Quality, All Series')).toBeInTheDocument();
    expect(screen.getAllByTestId('fill-quality').length).toBe(1); // market rows are folded
    await userEvent.setup().click(screen.getByRole('button', { name: /KXDEMOA/ }));
    expect(screen.getAllByTestId('fill-quality').length).toBe(2);
    expect(screen.getByText(/In line with the tape/)).toBeInTheDocument();
  });
});
