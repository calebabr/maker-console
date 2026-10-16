import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

// Z5: the dev mock backend must not drift, freeze or look wrong as time passes or after Stop / Start / mode
// switches (the demo once showed a false "Needs Attention" because the next run froze).
let mockFetch;
let resetMock;
beforeAll(async () => {
  process.env.VITE_MOCK_DELAY_MS = '0';
  ({ mockFetch, resetMock } = await import('../services/mock.js'));
});

const MIN = 60 * 1000;
const auth = { headers: { Authorization: 'Bearer mock-test' } };
let codeN = 100000;
const fresh2fa = () => ({ headers: { Authorization: 'Bearer mock-test', 'X-2FA-Code': String(++codeN) } });
const call = async (method, p, init = auth) => {
  const r = await mockFetch(method, p, init);
  return { status: r.status, body: JSON.parse((await r.text()) || 'null') };
};
const advance = (min) => vi.setSystemTime(Date.now() + min * MIN);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-16T12:00:00Z'));
  resetMock();
});
afterEach(() => vi.useRealTimers());

describe('running bot over time', () => {
  it('keeps the heartbeat fresh and the next run in the future for 6 hours of polling', async () => {
    for (let i = 0; i < 12 * 6; i++) {
      advance(5);
      const { body } = await call('GET', '/api/status');
      expect(body.desired).toBe('running');
      expect(body.heartbeat_age_s).toBeLessThanOrEqual(10 * 60);
      expect(Date.parse(body.next_due_utc)).toBeGreaterThan(Date.now());
      expect(Date.parse(body.next_due_utc) - Date.parse(body.last_run_utc)).toBe(10 * MIN);
    }
  });

  it('adds a journal row for passes that elapsed, newest first, without unbounded growth', async () => {
    const before = (await call('GET', '/api/activity?limit=500')).body.items.length;
    advance(35);
    const after = (await call('GET', '/api/activity?limit=500')).body.items;
    expect(after.length).toBeGreaterThan(before);
    const times = after.map((r) => Date.parse(r.utc));
    expect(times[0]).toBeGreaterThanOrEqual(times[1]);
    for (let i = 0; i < 200; i++) {
      advance(60);
      await call('GET', '/api/status');
    }
    const all = (await call('GET', '/api/activity?limit=500')).body.items;
    expect(all.length).toBeLessThanOrEqual(500);
    expect(Date.parse(all[0].utc)).toBeGreaterThan(Date.now() - 2 * 60 * MIN);
  }, 30000);

  it('moves the keep-awake last ping forward instead of ageing for ever', async () => {
    advance(5 * 60);
    const { body } = await call('GET', '/api/cloud');
    const age = Date.now() - Date.parse(body.keepalive.last_ping_utc);
    expect(age).toBeGreaterThanOrEqual(0);
    expect(age).toBeLessThan(body.settings.keepalive_minutes * MIN);
  });
});

describe('Stop, Start and mode switches', () => {
  it('a stopped bot has no next run; Start after a long stop is fresh, not overdue', async () => {
    await call('POST', '/api/bot/stop');
    advance(3 * 60);
    let { body } = await call('GET', '/api/status');
    expect(body.desired).toBe('stopped');
    expect(body.next_due_utc).toBeNull();

    const start = await call('POST', '/api/bot/start', fresh2fa());
    expect(start.status).toBe(200);
    ({ body } = await call('GET', '/api/status'));
    expect(body.desired).toBe('running');
    expect(body.kill_switch).toBe(false);
    expect(body.heartbeat_age_s).toBeLessThan(60);
    expect(Date.parse(body.next_due_utc)).toBeGreaterThan(Date.now());
    expect(Date.parse(body.next_due_utc) - Date.now()).toBeLessThanOrEqual(10 * MIN);
  });

  it('stays healthy over time after Stop then Start', async () => {
    await call('POST', '/api/bot/stop');
    advance(90);
    await call('POST', '/api/bot/start', fresh2fa());
    for (let i = 0; i < 24; i++) {
      advance(5);
      const { body } = await call('GET', '/api/status');
      expect(body.heartbeat_age_s).toBeLessThanOrEqual(10 * 60);
      expect(Date.parse(body.next_due_utc)).toBeGreaterThan(Date.now());
    }
  });

  it('switching mode does not freeze or reset the schedule', async () => {
    advance(7);
    const a = (await call('GET', '/api/status')).body;
    const sw = await call('PUT', '/api/mode', { headers: fresh2fa().headers, body: JSON.stringify({ mode: 'demo' }) });
    expect(sw.status).toBe(200);
    advance(25);
    const b = (await call('GET', '/api/status')).body;
    expect(b.desired).toBe('running');
    expect(Date.parse(b.last_run_utc)).toBeGreaterThan(Date.parse(a.last_run_utc));
    expect(Date.parse(b.next_due_utc)).toBeGreaterThan(Date.now());
  });

  it('changing poll_minutes while running keeps the next run in the future', async () => {
    const put = await call('PUT', '/api/config', { headers: fresh2fa().headers, body: JSON.stringify({ poll_minutes: 2 }) });
    expect(put.status).toBe(200);
    advance(9);
    const { body } = await call('GET', '/api/status');
    expect(Date.parse(body.next_due_utc)).toBeGreaterThan(Date.now());
    expect(body.heartbeat_age_s).toBeLessThanOrEqual(2 * 60);
  });

  it('run-once while stopped does not move the heartbeat', async () => {
    await call('POST', '/api/bot/stop');
    const a = (await call('GET', '/api/status')).body.last_run_utc;
    advance(20);
    const r = await call('POST', '/api/bot/run-once');
    expect(r.body.ran).toBe(false);
    expect((await call('GET', '/api/status')).body.last_run_utc).toBe(a);
  });
});
