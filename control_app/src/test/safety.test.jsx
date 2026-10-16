import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setToken, request, subscribeSlow, SLOW_MS } from '../services/api.js';
import { writeCache, readCache } from '../services/cache.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => setToken(null));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const LIVE_OK = { ...STATUS, live_allowed: true };

const MARKETS = {
  report_utc: '2026-10-13T10:00:00Z',
  markets: [
    { series: 'KXDEMOA', enabled: true, fills: 20, cost: 100, pnl: 3.2, roi: 0.03, ci_low: -0.02, ci_high: 0.08, verdict: 'keep', need_more: null },
    { series: 'KXDEMOF', enabled: false, fills: 0, cost: 0, pnl: 0, roi: 0, verdict: null, need_more: 30 },
  ],
};

const jsonRes = (status, body) => ({ ok: status < 300, status, text: () => Promise.resolve(JSON.stringify(body)) });

// ------------------------------------------------------------------ Mode page

describe('Mode page: LIVE typed confirmation', () => {
  async function openLiveConfirm(user, extra = {}) {
    const calls = stubApi({ 'GET /api/status': LIVE_OK, ...extra });
    renderApp('/mode');
    await user.click(await screen.findByRole('button', { name: /switch to live/i }));
    return calls;
  }

  it('Continue stays disabled until exactly LIVE is typed (case-sensitive)', async () => {
    const user = userEvent.setup();
    await openLiveConfirm(user);
    const cont = await screen.findByRole('button', { name: 'Continue' });
    const input = screen.getByLabelText('Type LIVE to confirm');
    expect(cont).toBeDisabled();
    await user.type(input, 'live');
    expect(cont).toBeDisabled();
    await user.clear(input);
    await user.type(input, 'LIVE ');
    expect(cont).toBeDisabled();
    await user.clear(input);
    await user.type(input, 'LIVE');
    expect(cont).toBeEnabled();
  });

  it('cancelling the typed confirm sends nothing and opens no 2FA prompt', async () => {
    const user = userEvent.setup();
    const calls = await openLiveConfirm(user);
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(screen.queryByLabelText('6-digit code')).toBeNull();
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
  });

  it('typed LIVE then a code sends {mode:"live",confirm:"LIVE"} with X-2FA-Code', async () => {
    const user = userEvent.setup();
    const calls = await openLiveConfirm(user, { 'PUT /api/mode': { mode: 'live' } });
    await user.type(await screen.findByLabelText('Type LIVE to confirm'), 'LIVE');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.type(await screen.findByLabelText('6-digit code'), '654321');
    await user.click(screen.getByRole('button', { name: 'Go LIVE' }));
    expect(await screen.findByText('Mode is now LIVE')).toBeInTheDocument();
    const put = calls.find((c) => c.key === 'PUT /api/mode');
    expect(put.body).toEqual({ mode: 'live', confirm: 'LIVE' });
    expect(put.headers['X-2FA-Code']).toBe('654321');
  });

  it('409 from the server explains the env requirement', async () => {
    const user = userEvent.setup();
    await openLiveConfirm(user, { 'PUT /api/mode': [409, { detail: 'live_not_allowed' }] });
    await user.type(await screen.findByLabelText('Type LIVE to confirm'), 'LIVE');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.type(await screen.findByLabelText('6-digit code'), '111111');
    await user.click(screen.getByRole('button', { name: 'Go LIVE' }));
    expect(await screen.findByText(/not allowed by the server/i)).toBeInTheDocument();
    expect(screen.getByText(/missing the prod key or KALSHI_LIVE_CONFIRM/)).toBeInTheDocument();
  });

  it('a rejected code keeps the prompt open and a retry succeeds', async () => {
    const user = userEvent.setup();
    let n = 0;
    await openLiveConfirm(user, {
      'PUT /api/mode': () => (++n === 1 ? [403, { detail: '2fa_invalid' }] : { mode: 'live' }),
    });
    await user.type(await screen.findByLabelText('Type LIVE to confirm'), 'LIVE');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.type(await screen.findByLabelText('6-digit code'), '111111');
    await user.click(screen.getByRole('button', { name: 'Go LIVE' }));
    expect(await screen.findByText(/Code Rejected/)).toBeInTheDocument();
    await user.type(screen.getByLabelText('6-digit code'), '222222');
    await user.click(screen.getByRole('button', { name: 'Go LIVE' }));
    expect(await screen.findByText('Mode is now LIVE')).toBeInTheDocument();
  });

  it('LIVE button is disabled with an explanation when the server disallows live', async () => {
    stubApi({ 'GET /api/status': STATUS });
    renderApp('/mode');
    expect(await screen.findByRole('button', { name: /switch to live/i })).toBeDisabled();
    expect(screen.getByText(/server does not allow live trading/i)).toBeInTheDocument();
  });

  it('switching to DEMO needs a code but no typed confirmation and sends no confirm', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': { ...LIVE_OK, mode: 'live' },
      'PUT /api/mode': { mode: 'demo' },
    });
    renderApp('/mode');
    await user.click(await screen.findByRole('button', { name: /switch to demo/i }));
    expect(screen.queryByLabelText('Type LIVE to confirm')).toBeNull();
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Switch to demo' }));
    await screen.findByText('Mode is now DEMO');
    const put = calls.find((c) => c.key === 'PUT /api/mode');
    expect(put.body.mode).toBe('demo');
    expect(put.body.confirm).toBeUndefined();
  });
});

// ---------------------------------------------------------------- Markets page

describe('Markets toggle', () => {
  it('Pause asks for a code and sends {enabled:false} to the series path', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/markets': MARKETS,
      'PUT /api/markets/KXDEMOA': { version: 7 },
    });
    renderApp('/markets');
    await user.click(await screen.findByRole('button', { name: /pause/i }));
    expect(screen.getByText(/Pause KXDEMOA\?/)).toBeInTheDocument();
    await user.type(screen.getByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Pause' }));
    expect(await screen.findByText('KXDEMOA paused')).toBeInTheDocument();
    expect(screen.getByText('Settings saved as version 7.')).toBeInTheDocument();
    const put = calls.find((c) => c.key === 'PUT /api/markets/KXDEMOA');
    expect(put.body).toEqual({ enabled: false });
    expect(put.headers['X-2FA-Code']).toBe('123456');
  });

  it('Resume sends {enabled:true}', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/markets': MARKETS,
      'PUT /api/markets/KXDEMOF': { version: 8 },
    });
    renderApp('/markets');
    await user.click(await screen.findByRole('button', { name: /resume/i }));
    await user.type(screen.getByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Resume' }));
    await screen.findByText('KXDEMOF resumed');
    expect(calls.find((c) => c.key === 'PUT /api/markets/KXDEMOF').body).toEqual({ enabled: true });
  });

  it('pausing the last market is rejected by the server and its 400 detail is shown', async () => {
    const user = userEvent.setup();
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/markets': { markets: [MARKETS.markets[0]] },
      'PUT /api/markets/KXDEMOA': [400, { detail: ['cannot pause the last enabled market'] }],
    });
    renderApp('/markets');
    await user.click(await screen.findByRole('button', { name: /pause/i }));
    await user.type(screen.getByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Pause' }));
    expect(await screen.findByText('Change not saved')).toBeInTheDocument();
    expect((await screen.findAllByText(/cannot pause the last enabled market/)).length).toBeGreaterThan(0);
  });

  it('cancelling the code prompt sends nothing', async () => {
    const user = userEvent.setup();
    const calls = stubApi({ 'GET /api/status': STATUS, 'GET /api/markets': MARKETS });
    renderApp('/markets');
    await user.click(await screen.findByRole('button', { name: /pause/i }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
  });
});

// ---------------------------------------------------------------- offline cache

describe('offline cache', () => {
  it('GET responses are cached; token and 2FA codes are never cached', async () => {
    stubApi({ 'GET /api/markets': MARKETS });
    setToken('secret-token');
    await request('GET', '/api/markets', { code: '123456' });
    const c = readCache('/api/markets');
    expect(c.data.markets).toHaveLength(2);
    const dump = JSON.stringify(Object.entries(sessionStorage).filter(([k]) => k.startsWith('km.cache:')));
    expect(dump).not.toContain('secret-token');
    expect(dump).not.toContain('123456');
  });

  it('POST responses are not cached', async () => {
    stubApi({ 'POST /api/bot/stop': { kill_switch: true } });
    setToken('t');
    await request('POST', '/api/bot/stop');
    expect(readCache('/api/bot/stop')).toBeNull();
  });

  it('Markets shows the cached copy with an offline notice when the network fails', async () => {
    writeCache('/api/markets', MARKETS);
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))));
    renderApp('/markets');
    expect(await screen.findByText('KXDEMOA')).toBeInTheDocument();
    expect(await screen.findByText(/Offline copy from/)).toBeInTheDocument();
    expect((await screen.findAllByRole('alert'))[0]).toHaveTextContent(/Cannot reach the server/);
  });

  it('with no cache and no network, shows the error and Retry recovers', async () => {
    const user = userEvent.setup();
    let up = false;
    vi.stubGlobal(
      'fetch',
      vi.fn((url) => {
        if (!up) return Promise.reject(new TypeError('down'));
        return Promise.resolve(jsonRes(200, String(url).includes('/api/markets') ? MARKETS : STATUS));
      }),
    );
    renderApp('/markets');
    const retry = await screen.findAllByRole('button', { name: 'Retry' });
    up = true;
    await user.click(retry[retry.length - 1]);
    expect(await screen.findByText('KXDEMOA')).toBeInTheDocument();
    expect(screen.queryByText(/Offline copy from/)).toBeNull();
  });

  it('a server error (not a network error) also falls back to the cached copy', async () => {
    writeCache('/api/markets', MARKETS);
    stubApi({ 'GET /api/status': STATUS, 'GET /api/markets': [500, { detail: 'boom' }] });
    renderApp('/markets');
    expect(await screen.findByText('KXDEMOA')).toBeInTheDocument();
    expect(await screen.findByText(/Offline copy from/)).toBeInTheDocument();
  });

  it('a 401 clears all cached data (shared phone does not leak it)', async () => {
    writeCache('/api/markets', MARKETS);
    stubApi({ 'GET /api/markets': [401, { detail: 'expired' }] });
    setToken('t');
    await expect(request('GET', '/api/markets')).rejects.toMatchObject({ status: 401 });
    expect(readCache('/api/markets')).toBeNull();
  });
});

// ---------------------------------------------------------------- waking banner

describe('waking banner (Render cold start)', () => {
  it('slow listeners fire after SLOW_MS of a pending request and clear when it ends', async () => {
    vi.useFakeTimers();
    let release;
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise((resolve) => { release = () => resolve(jsonRes(200, {})); })),
    );
    const seen = [];
    const unsub = subscribeSlow((s) => seen.push(s));
    const p = request('GET', '/api/status');
    await vi.advanceTimersByTimeAsync(SLOW_MS - 100);
    expect(seen.at(-1)).toBe(false);
    await vi.advanceTimersByTimeAsync(200);
    expect(seen.at(-1)).toBe(true);
    release();
    await p;
    expect(seen.at(-1)).toBe(false);
    unsub();
  });

  it('a fast request never raises the banner', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonRes(200, {}))));
    const seen = [];
    const unsub = subscribeSlow((s) => seen.push(s));
    await request('GET', '/api/status');
    await vi.advanceTimersByTimeAsync(SLOW_MS + 1000);
    expect(seen.every((s) => s === false)).toBe(true);
    unsub();
  });

  it('the banner renders in the app while a request is pending > 5 s, then disappears', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const releases = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise((resolve) => { releases.push(() => resolve(jsonRes(200, STATUS))); })),
    );
    renderApp('/');
    expect(screen.queryByText(/Waking the server/)).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SLOW_MS + 500);
    });
    expect(screen.getByText(/Waking the server/)).toBeInTheDocument();
    await act(async () => {
      releases.forEach((r) => r());
      await vi.advanceTimersByTimeAsync(10);
    });
    await waitFor(() => expect(screen.queryByText(/Waking the server/)).toBeNull());
  });

  it('offline/online events toggle the offline banner', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/history': { snapshots: [] } });
    renderApp('/');
    await act(async () => {
      window.dispatchEvent(new Event('offline'));
    });
    expect(screen.getByText(/You are offline/)).toBeInTheDocument();
    await act(async () => {
      window.dispatchEvent(new Event('online'));
    });
    expect(screen.queryByText(/You are offline/)).toBeNull();
  });

  it('a request that never answers times out with a clear error', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (url, init) =>
          new Promise((_, reject) => {
            init.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
          }),
      ),
    );
    const assertion = expect(request('GET', '/api/status')).rejects.toMatchObject({
      status: 0,
      message: expect.stringMatching(/did not answer in time/),
    });
    await vi.advanceTimersByTimeAsync(91000);
    await assertion;
  });
});

// ------------------------------------------------- Stop on every page, hostile API

const PAGES = ['/', '/markets', '/activity', '/health', '/settings', '/mode', '/server', '/logs', '/arb'];

/** Every GET misbehaves per `mode`; POST /api/bot/stop is controlled separately. */
function hostileApi(mode, stopHandler) {
  const calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url, init = {}) => {
      const path = String(url).replace('http://api.test', '');
      const method = init.method || 'GET';
      calls.push({ method, path, headers: init.headers || {}, body: init.body });
      if (method === 'POST' && path === '/api/bot/stop') return stopHandler();
      if (mode === 'hang') return new Promise(() => {});
      if (mode === 'network') return Promise.reject(new TypeError('Failed to fetch'));
      return Promise.resolve(jsonRes(500, { detail: 'boom' }));
    }),
  );
  return calls;
}

const okStop = () =>
  Promise.resolve(jsonRes(200, { desired: 'stopped', kill_switch: true, cancelled: 2, errors: [], arb_kill_switch: true }));

describe('Stop is always tappable and never needs 2FA', () => {
  for (const mode of ['hang', 'network', 'error500']) {
    for (const page of PAGES) {
      it(`${page} with GETs ${mode}: header Stop works with no 2FA`, async () => {
        const user = userEvent.setup();
        const calls = hostileApi(mode, okStop);
        renderApp(page);
        const header = document.querySelector('header.appbar');
        const stop = within(header).getByRole('button', { name: /stop the bot/i });
        expect(stop).toBeEnabled();
        await user.click(stop);
        await user.click(await screen.findByRole('button', { name: 'Stop now' }));
        expect(await screen.findByText('Stopped. Kill switch set.')).toBeInTheDocument();
        expect(screen.queryByLabelText('6-digit code')).toBeNull();
        const stopCalls = calls.filter((c) => c.path === '/api/bot/stop');
        expect(stopCalls).toHaveLength(1);
        expect(stopCalls[0].method).toBe('POST');
        expect(stopCalls[0].headers['X-2FA-Code']).toBeUndefined();
      });
    }
  }

  it('a network failure on Stop is reported loudly and Stop can be tapped again', async () => {
    const user = userEvent.setup();
    let n = 0;
    hostileApi('network', () => (++n === 1 ? Promise.reject(new TypeError('down')) : okStop()));
    renderApp('/');
    const header = document.querySelector('header.appbar');
    await user.click(within(header).getByRole('button', { name: /stop the bot/i }));
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    expect(await screen.findByText('Stop did NOT reach the server')).toBeInTheDocument();
    const again = within(header).getByRole('button', { name: /stop the bot/i });
    await waitFor(() => expect(again).toBeEnabled());
    await user.click(again);
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    expect(await screen.findByText('Stopped. Kill switch set.')).toBeInTheDocument();
  });

  it('Stop returning 500 is not shown as success', async () => {
    const user = userEvent.setup();
    hostileApi('error500', () => Promise.resolve(jsonRes(500, { detail: 'x' })));
    renderApp('/mode');
    const header = document.querySelector('header.appbar');
    await user.click(within(header).getByRole('button', { name: /stop the bot/i }));
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    expect(await screen.findByText('Stop did NOT reach the server')).toBeInTheDocument();
    expect(screen.queryByText('Stopped. Kill switch set.')).toBeNull();
  });

  it('a slow Stop disables the button while pending so it is not double-sent', async () => {
    const user = userEvent.setup();
    let release;
    const calls = hostileApi('hang', () => new Promise((r) => { release = () => r(jsonRes(200, { kill_switch: true, desired: 'stopped', cancelled: 0 })); }));
    renderApp('/');
    const header = document.querySelector('header.appbar');
    const stop = within(header).getByRole('button', { name: /stop the bot/i });
    await user.click(stop);
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    await waitFor(() => expect(stop).toBeDisabled());
    expect(calls.filter((c) => c.path === '/api/bot/stop')).toHaveLength(1);
    release();
    expect(await screen.findByText('Stopped. Kill switch set.')).toBeInTheDocument();
  });

  it('Stop reports "Arbitrage kill switch NOT confirmed" when the server says arb is not stopped', async () => {
    const user = userEvent.setup();
    hostileApi('hang', () => Promise.resolve(jsonRes(200, { kill_switch: true, desired: 'stopped', cancelled: 0, arb_kill_switch: false })));
    renderApp('/');
    const header = document.querySelector('header.appbar');
    await user.click(within(header).getByRole('button', { name: /stop the bot/i }));
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    expect(await screen.findByText(/Arbitrage kill switch NOT confirmed/)).toBeInTheDocument();
  });

  it('Stop with an expired session (401) sends the user to login', async () => {
    const user = userEvent.setup();
    hostileApi('hang', () => Promise.resolve(jsonRes(401, { detail: 'expired' })));
    renderApp('/');
    const header = document.querySelector('header.appbar');
    await user.click(within(header).getByRole('button', { name: /stop the bot/i }));
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    expect(await screen.findByLabelText('Username')).toBeInTheDocument();
  });
});
