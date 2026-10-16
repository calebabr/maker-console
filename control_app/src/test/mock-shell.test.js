import { beforeAll, describe, expect, it } from 'vitest';
const OLD_NAME = ['Long', 'shot'].join('');   // the retired app name, never spelled out in the repo

// The dev mock backend (VITE_MOCK=1) must follow docs/UX_CONTRACT.md section 5 for the endpoints FE-Shell owns.
let mockFetch;
beforeAll(async () => {
  ({ mockFetch } = await import('../services/mock.js'));
  process.env.VITE_MOCK_DELAY_MS = '0';
});

const auth = { headers: { Authorization: 'Bearer mock-test' } };
const get = async (p) => {
  const r = await mockFetch('GET', p, auth);
  return { status: r.status, body: JSON.parse((await r.text()) || 'null') };
};

describe('mock /api/status additions', () => {
  it('adds pnl_today, recent_passes, last_run_at and next_run_at without removing old fields', async () => {
    const { status, body } = await get('/api/status');
    expect(status).toBe(200);
    // existing fields untouched
    for (const k of ['desired', 'mode', 'kill_switch', 'heartbeat_age_s', 'account', 'pnl', 'expected_roi', 'next_due_utc', 'last_run_utc']) {
      expect(body).toHaveProperty(k);
    }
    expect(Object.keys(body.pnl_today).sort()).toEqual(['arb', 'bot', 'maker', 'total', 'you']);
    expect(body.recent_passes).toHaveLength(12);
    for (const p of body.recent_passes) {
      expect(typeof p.ts).toBe('string');
      expect(typeof p.ok).toBe('boolean');
      expect(typeof p.late).toBe('boolean');
      expect(Number.isInteger(p.errors)).toBe(true);
    }
    // newest last
    const ts = body.recent_passes.map((p) => Date.parse(p.ts));
    expect([...ts].sort((a, b) => a - b)).toEqual(ts);
    expect(body.last_run_at).toBe(body.last_run_utc);
    expect(body.next_run_at === null || typeof body.next_run_at === 'string').toBe(true);
  });
});

describe('mock /api/strategies and /api/changelog', () => {
  it('returns the registry with a "you" entry', async () => {
    const { status, body } = await get('/api/strategies');
    expect(status).toBe(200);
    expect(body.map((s) => s.id)).toEqual(['maker', 'arb', 'you']);
    expect(body.find((s) => s.id === 'maker')).toMatchObject({ name: 'Bot · Maker', prefix: 'mm-' });
    expect(body.find((s) => s.id === 'you').prefix).toBeNull();
    for (const s of body) expect(s.color).toMatch(/^var\(--/);
  });

  it('returns the changelog newest first as [{version, date, items}]', async () => {
    const { status, body } = await get('/api/changelog');
    expect(status).toBe(200);
    expect(body.length).toBeGreaterThan(1);
    for (const e of body) {
      expect(e.version).toMatch(/^\d+\.\d+\.\d+$/);
      expect(e.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(e.items.length).toBeGreaterThan(0);
    }
    expect(body[0].version > body[1].version).toBe(true);
    expect(JSON.stringify(body)).not.toMatch(new RegExp(OLD_NAME));
  });

  it('both need a session', async () => {
    for (const p of ['/api/strategies', '/api/changelog', '/api/status']) {
      const r = await mockFetch('GET', p, {});
      expect(r.status).toBe(401);
    }
  });
});
