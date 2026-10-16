import { afterEach, describe, expect, it } from 'vitest';
import { vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

const CONFIG = {
  config: { run_budget: 25, max_order: 8, poll_minutes: 10, cells: [[0.3, 0.45, 6, 12, "no"]] },
  schema: { run_budget: { type: 'number', min: 1, max: 500 }, max_order: { type: 'number', min: 1 }, poll_minutes: { type: 'integer', min: 1 } },
  ceilings: {},
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Settings form lock after Save (Z1)', () => {
  it('locks the fields until the reload finishes, so typing cannot be wiped', async () => {
    const user = userEvent.setup();
    let gets = 0;
    let release;
    const gate = new Promise((r) => {
      release = r;
    });
    const saved = { ...CONFIG.config, run_budget: 50 };
    // stubApi responds synchronously, so gate the second GET /api/config by hand.
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': () => {
        gets += 1;
        return [200, gets > 1 ? { ...CONFIG, config: saved } : CONFIG];
      },
      'PUT /api/config': { config: saved, version: 2 },
    });
    const realFetch = globalThis.fetch;
    vi.stubGlobal('fetch', (url, init) => {
      const p = realFetch(url, init);
      if (String(url).includes('/api/config') && !(init && init.method === 'PUT') && gets >= 1 && calls.some((c) => c.method === 'PUT')) {
        return gate.then(() => p);
      }
      return p;
    });
    renderApp('/settings');
    const field = await screen.findByLabelText(/run budget/i);
    await user.clear(field);
    await user.type(field, '50');
    await user.click(screen.getByRole('button', { name: /save/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    const btns = screen.getAllByRole('button', { name: 'Save' });
    await user.click(btns[btns.length - 1]);
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    await waitFor(() => expect(screen.getByLabelText(/run budget/i)).toBeDisabled());
    expect(document.querySelector('form button[type="submit"]')).toBeDisabled();
    release();
    await waitFor(() => expect(screen.getByLabelText(/run budget/i)).toBeEnabled());
    expect(screen.getByLabelText(/run budget/i)).toHaveValue(50);
  });
});

describe('Push Devices card (Z3)', () => {
  const DEVICES = {
    count: 2,
    subscriptions: [
      { host: 'web.push.apple.com', created_utc: '2026-10-07T12:00:00+00:00' },
      { host: 'fcm.googleapis.com', created_utc: '2026-10-14T09:30:00+00:00' },
    ],
  };

  it('lists hosts and clears all after a confirm (no 2FA)', async () => {
    const user = userEvent.setup();
    let cleared = false;
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': CONFIG,
      'GET /api/push/subscriptions': () => (cleared ? { count: 0, subscriptions: [] } : DEVICES),
      'DELETE /api/push/subscriptions': () => {
        cleared = true;
        return { removed: 2 };
      },
    });
    renderApp('/settings');
    expect(await screen.findByText('web.push.apple.com')).toBeInTheDocument();
    expect(screen.getByText('fcm.googleapis.com')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear all push devices' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    expect(screen.queryByLabelText('6-digit code')).toBeNull();
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Clear All' }));
    expect(await screen.findByText('No devices registered.')).toBeInTheDocument();
    const del = calls.find((c) => c.method === 'DELETE');
    expect(del.headers['X-2FA-Code']).toBeUndefined();
  });

  it('cancel leaves the list alone', async () => {
    const user = userEvent.setup();
    const calls = stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG, 'GET /api/push/subscriptions': DEVICES });
    renderApp('/settings');
    await screen.findByText('web.push.apple.com');
    await user.click(screen.getByRole('button', { name: 'Clear all push devices' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: /cancel/i }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    expect(screen.getByText('web.push.apple.com')).toBeInTheDocument();
  });

  it('empty state disables Clear All', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG, 'GET /api/push/subscriptions': { count: 0, subscriptions: [] } });
    renderApp('/settings');
    expect(await screen.findByText('No devices registered.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Clear all push devices' })).toBeDisabled();
  });
});

describe('push-sw.js safePath hardening (S-34)', () => {
  function clickTarget(url) {
    const listeners = {};
    const opened = [];
    const self = {
      location: { origin: 'https://keel.example' },
      addEventListener: (n, f) => {
        listeners[n] = f;
      },
      clients: { matchAll: async () => [], openWindow: async (u) => opened.push(u) },
      registration: { showNotification() {} },
    };
    // eslint-disable-next-line no-new-func
    new Function('self', 'URL', readFileSync('public/push-sw.js', 'utf8'))(self, URL);
    let p;
    listeners.notificationclick({ notification: { close() {}, data: { url } }, waitUntil: (x) => (p = x) });
    return p.then(() => opened);
  }
  const bad = ['/\t/evil.example', '/\n/evil.example', '/\r/evil.example', '/\u0000/x', '/ok\u007f', '/a b', '/ x'];
  it.each(bad)('rejects %j', async (u) => {
    expect(await clickTarget(u)).toEqual(['https://keel.example/']);
  });
  it('still follows a normal path with a query', async () => {
    expect(await clickTarget('/alerts?type=fill')).toEqual(['https://keel.example/alerts?type=fill']);
  });
});
