import { vi } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AppShell } from '../App.jsx';
import { setToken } from '../services/api.js';

export const STATUS = {
  desired: 'stopped',
  mode: 'demo',
  kill_switch: true,
  live_allowed: false,
  heartbeat_age_s: 30,
  account: null,
  pnl: null,
};

/** routes: { 'METHOD /path': (call, calls) => [status, body] | body }. Unmatched -> 404. */
export function stubApi(routes) {
  const calls = [];
  const fn = vi.fn((url, init = {}) => {
    const path = String(url).replace('http://api.test', '');
    const key = `${init.method || 'GET'} ${path.split('?')[0]}`;
    const call = {
      key,
      path,
      method: init.method || 'GET',
      headers: init.headers || {},
      body: init.body ? JSON.parse(init.body) : undefined,
    };
    calls.push(call);
    const h = routes[key];
    let res = h ? (typeof h === 'function' ? h(call, calls) : h) : [404, { detail: 'not found' }];
    if (!Array.isArray(res)) res = [200, res];
    const [status, body] = res;
    return Promise.resolve({ ok: status < 300, status, text: () => Promise.resolve(JSON.stringify(body)) });
  });
  vi.stubGlobal('fetch', fn);
  return calls;
}

export function renderApp(path = '/', { signedIn = true } = {}) {
  setToken(signedIn ? 'tok' : null);
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppShell />
    </MemoryRouter>,
  );
}
