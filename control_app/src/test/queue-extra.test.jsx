// Queue-position display, extra: payloads exactly as the API sends them (all five buckets, nulls), junk values,
// and the invariant that nothing queue-related ever changes the Stop button.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { clearToken } from '../services/api.js';
import QueuePosition, { BUCKETS } from '../components/QueuePosition.jsx';
import { analyticsSummary, mockMyTrades } from '../services/mockAnalytics.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => clearToken());
afterEach(() => vi.unstubAllGlobals());

const bucket = (b, o = {}) => ({ bucket: b, episodes: 0, fills: 0, fill_rate: null, trade_through_share: null, ...o });
// What the API returns on a fresh install: queue_stats exists, nothing measured, every bucket null.
const EMPTY = {
  episodes: 0, fills: 0, median_queue_ahead_at_join: null, requote_reset_share: null, front_fill_share: null,
  by_queue: BUCKETS.map((b) => bucket(b)), updated_utc: '2026-10-13T12:00:00+00:00',
};
// Episodes exist but nothing has filled/closed yet: shares null, fill_rate 0 or null per bucket.
const NO_FILLS = {
  ...EMPTY, episodes: 30, median_queue_ahead_at_join: 12.5,
  by_queue: [bucket('0', { episodes: 5, fill_rate: 0 }), bucket('1-50', { episodes: 25, fill_rate: 0 }),
    bucket('51-200'), bucket('201-1000'), bucket('>1000')],
};

describe('QueuePosition with API-shaped payloads', () => {
  it('fresh install: zero episodes shows the empty state, not a table of dashes', () => {
    render(<QueuePosition qs={EMPTY} />);
    expect(screen.getByText(/no quotes measured yet/i)).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('all five buckets render in order even when every rate is null or zero, with no NaN widths', () => {
    const { container } = render(<QueuePosition qs={NO_FILLS} />);
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.querySelector('th').textContent)).toEqual(BUCKETS);
    expect(container.innerHTML).not.toMatch(/NaN|undefined|Infinity/);
    expect(screen.getByText('Too few fills to judge (0/50).')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0); // null shares and rates
  });

  it('a summary line with null shares never prints "null" or "NaN"', () => {
    const { container } = render(<QueuePosition qs={NO_FILLS} variant="summary" />);
    expect(container.textContent).toMatch(/typically 1[23](\.5)? ahead of us/);
    expect(container.textContent).not.toMatch(/null|NaN|undefined/);
  });

  it('junk values (strings, NaN, missing by_queue, unknown buckets) never crash', () => {
    const junk = {
      episodes: 'many', fills: NaN, median_queue_ahead_at_join: 'x', requote_reset_share: undefined,
      front_fill_share: 'half', by_queue: 'nope', updated_utc: 5,
    };
    expect(() => render(<QueuePosition qs={junk} />)).not.toThrow();
    const odd = { ...NO_FILLS, by_queue: [bucket('>1000', { episodes: 1 }), null, { episodes: 3 }, bucket('9999', { episodes: 2 }), bucket('0')] };
    const { container } = render(<QueuePosition qs={odd} />);
    const heads = Array.from(container.querySelectorAll('tbody th')).map((e) => e.textContent);
    expect(heads).toEqual(['0', '>1000', '9999']); // known order first, unknown last, bad rows skipped
  });

  it('bucket order is independent of the order the server sends', () => {
    const shuffled = { ...NO_FILLS, by_queue: [...NO_FILLS.by_queue].reverse() };
    render(<QueuePosition qs={shuffled} />);
    const heads = Array.from(screen.getByRole('table').querySelectorAll('tbody th')).map((e) => e.textContent);
    expect(heads).toEqual(BUCKETS);
  });

  it('exactly 50 fills drops the "too few" note, 49 keeps it', () => {
    const base = { ...NO_FILLS, fills: 50, front_fill_share: 0.4, requote_reset_share: 0.1 };
    const { unmount } = render(<QueuePosition qs={base} />);
    expect(screen.queryByText(/Too few fills/)).toBeNull();
    unmount();
    render(<QueuePosition qs={{ ...base, fills: 49 }} />);
    expect(screen.getByText('Too few fills to judge (49/50).')).toBeInTheDocument();
  });
});

describe('queue stats on pages never disturb Stop', () => {
  it('Overview: Stop is in main, confirms and calls the API without 2FA, with queue_stats present, null or garbage', async () => {
    for (const qs of [NO_FILLS, null, 'garbage', { episodes: 'x' }]) {
      const user = userEvent.setup();
      const calls = stubApi({
        'GET /api/status': { ...STATUS, desired: 'running', kill_switch: false, queue_stats: qs },
        'GET /api/history': { snapshots: [] },
        'POST /api/bot/stop': { desired: 'stopped', kill_switch: true, cancelled: 0, errors: [] },
      });
      const { unmount } = renderApp('/');
      await waitFor(() => expect(calls.some((c) => c.key === 'GET /api/status')).toBe(true));
      const main = document.querySelector('main');
      await user.click(await within(main).findByRole('button', { name: /stop the bot/i }));
      await user.click(await screen.findByRole('button', { name: 'Stop now' }));
      await waitFor(() => expect(calls.some((c) => c.key === 'POST /api/bot/stop')).toBe(true));
      expect(calls.find((c) => c.key === 'POST /api/bot/stop').headers['X-2FA-Code']).toBeUndefined();
      unmount();
      vi.unstubAllGlobals();
    }
  });

  it('Overview: a status response without queue_stats at all (older API) renders without queue text', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/history': { snapshots: [] } });
    renderApp('/');
    await waitFor(() => expect(document.querySelector('main button')).not.toBeNull());
    await screen.findAllByRole('button', { name: /stop the bot/i });
    expect(screen.queryByTestId('queue-summary')).toBeNull();
    expect(screen.queryByText(/Queue/)).toBeNull();
  });

  it('Execution tab: empty install (all buckets null) shows the empty note and the all-series card', async () => {
    const NOW = Date.parse('2026-10-13T18:00:00Z');
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/markets': { markets: [{ series: 'KXDEMOA', enabled: true, queue_stats: EMPTY }], queue_stats: EMPTY },
      'GET /api/analytics/summary': (c) => analyticsSummary(new URL(c.path, 'http://x').searchParams, { nowMs: NOW }),
      'GET /api/analytics/my-trades': (c) => mockMyTrades(new URL(c.path, 'http://x').searchParams, { nowMs: NOW }),
    });
    renderApp('/analytics?tab=execution');
    expect(await screen.findByText('Queue Position, All Series')).toBeInTheDocument();
    expect((await screen.findAllByText(/no quotes measured yet/i)).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('queue_stats')).toBeNull();
  });
});
