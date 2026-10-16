import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AppShell } from '../App.jsx';
import { api, setToken, getToken } from '../services/api.js';
import { badgeKind } from '../utils/status.js';
import { money, pct, detailList } from '../utils/format.js';

function okJson(data, status = 200) {
  return Promise.resolve({ ok: status < 300, status, text: () => Promise.resolve(JSON.stringify(data)) });
}

describe('smoke', () => {
  beforeEach(() => {
    setToken(null);
    vi.restoreAllMocks();
  });

  it('shows the login screen when signed out', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <AppShell />
      </MemoryRouter>,
    );
    expect(screen.getByLabelText('Username')).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
  });

  it('shows the Stop button on Overview when signed in', async () => {
    setToken('t');
    vi.stubGlobal('fetch', vi.fn(() =>
      okJson({ desired: 'running', mode: 'demo', kill_switch: false, heartbeat_age_s: 60, account: null, pnl: null }),
    ));
    render(
      <MemoryRouter initialEntries={['/']}>
        <AppShell />
      </MemoryRouter>,
    );
    // Header + Overview both carry a Stop control.
    expect(screen.getAllByRole('button', { name: /stop the bot/i }).length).toBe(2);
    await waitFor(() => expect(screen.getAllByText('Demo Running').length).toBeGreaterThan(0));
    vi.unstubAllGlobals();
  });
});

describe('api client', () => {
  it('sends Authorization and X-2FA-Code headers', async () => {
    setToken('abc');
    const fetchMock = vi.fn(() => okJson({ desired: 'running' }));
    vi.stubGlobal('fetch', fetchMock);
    await api.start('123456');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/api/bot/start');
    expect(init.method).toBe('POST');
    expect(init.headers.Authorization).toBe('Bearer abc');
    expect(init.headers['X-2FA-Code']).toBe('123456');
    vi.unstubAllGlobals();
  });

  it('Stop never sends a 2FA code', async () => {
    setToken('abc');
    const fetchMock = vi.fn(() => okJson({ desired: 'stopped', kill_switch: true, cancelled: 0, errors: [] }));
    vi.stubGlobal('fetch', fetchMock);
    await api.stop();
    expect(fetchMock.mock.calls[0][1].headers['X-2FA-Code']).toBeUndefined();
    vi.unstubAllGlobals();
  });

  it('clears the token on 401', async () => {
    setToken('abc');
    vi.stubGlobal('fetch', vi.fn(() => okJson({ detail: 'expired' }, 401)));
    await expect(api.status()).rejects.toMatchObject({ status: 401 });
    expect(getToken()).toBeNull();
    vi.unstubAllGlobals();
  });

  it('exposes the 400 detail list', async () => {
    setToken('abc');
    vi.stubGlobal('fetch', vi.fn(() => okJson({ detail: ['run_budget too high', 'bad series'] }, 400)));
    await expect(api.putConfig({ run_budget: 9999 }, '111111')).rejects.toMatchObject({
      details: ['run_budget too high', 'bad series'],
    });
    vi.unstubAllGlobals();
  });
});

describe('utils', () => {
  it('derives the badge', () => {
    expect(badgeKind({ desired: 'running', mode: 'live', heartbeat_age_s: 10 }).kind).toBe('live');
    expect(badgeKind({ desired: 'running', mode: 'demo', heartbeat_age_s: 10 }).kind).toBe('demo');
    expect(badgeKind({ desired: 'running', mode: 'demo', heartbeat_age_s: 3600 }).kind).toBe('error');
    expect(badgeKind({ desired: 'stopped', mode: 'demo' }).kind).toBe('stopped');
    expect(badgeKind({ desired: 'stopped', last_error: 'boom' }).kind).toBe('error');
  });
  it('formats', () => {
    expect(money(1234.5)).toBe('$1,234.50');
    expect(money(-2)).toBe('-$2.00');
    expect(pct(0.015)).toBe('+1.50%');
    expect(detailList([{ loc: ['body', 'x'], msg: 'bad' }])).toEqual(['x: bad']);
  });
});
