import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp, stubApi, STATUS } from './helpers.jsx';
import { LAST_VISIT_KEY, abnormalities, edgeSummary, headlineParts, settingsToastTitle, sinceParts, stopToastTitle } from '../utils/overview.js';

const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString();
const MIN = 60 * 1000;

const RUNNING = {
  ...STATUS,
  desired: 'running',
  kill_switch: false,
  mode: 'demo',
  heartbeat_age_s: 60,
  last_run_at: iso(4 * MIN),
  next_run_at: new Date(Date.now() + 5 * MIN).toISOString(),
  account: { cash: 212.4, exposure: 37.6, headroom: 87.4 },
  limits: { run_budget: 25, max_open_frac: 0.4 },
  pnl: { d7: 4.83, d30: 17.12 },
  pnl_today: { maker: 1.4, arb: 0.35, you: -0.8, total: 0.95 },
  recent_passes: Array.from({ length: 12 }, (_, i) => ({ ts: iso((12 - i) * 5 * MIN), ok: true, late: i === 8, errors: 0 })),
};

const STRATS = [
  { id: 'maker', source: 'bot', name: 'Bot · Maker', color: 'var(--accent)' },
  { id: 'arb', source: 'bot', name: 'Bot · Arb', color: 'var(--chart2)' },
  { id: 'you', source: 'you', name: 'You', color: 'var(--warn)' },
];

const base = (extra = {}) => ({
  'GET /api/status': RUNNING,
  'GET /api/history': { snapshots: [] },
  'GET /api/strategies': [200, STRATS],
  ...extra,
});

function mediaQuery(wide) {
  window.matchMedia = vi.fn((q) => ({
    matches: /min-width:\s*768px/.test(q) ? wide : false,
    media: q,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  }));
}

// IntersectionObserver stub: tests call fireOffscreen() to say the status card scrolled away.
let observers = [];
function stubObserver() {
  observers = [];
  window.IntersectionObserver = class {
    constructor(cb) {
      this.cb = cb;
      observers.push(this);
    }
    observe() {}
    disconnect() {}
  };
}
function fireOffscreen(off = true) {
  act(() => {
    observers.forEach((o) => o.cb([{ isIntersecting: !off, boundingClientRect: { top: off ? -200 : 100 } }]));
  });
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete window.matchMedia;
  delete window.IntersectionObserver;
});

describe('Overview headline and status card', () => {
  it('running in demo: one headline line with the 30-day P&L', async () => {
    stubApi(base());
    renderApp('/');
    const hl = await screen.findByTestId('headline');
    expect(hl).toHaveTextContent('Demo Running');
    expect(hl).toHaveTextContent('+$17.12 Over 30 Days');
    expect(screen.getByTestId('state-label')).toHaveTextContent('Running');
    expect(screen.getByTestId('status-card')).toHaveAttribute('data-state', 'demo');
  });

  it('stopped and live states read correctly', async () => {
    stubApi(base({ 'GET /api/status': { ...STATUS, mode: 'live' } }));
    const { unmount } = renderApp('/');
    expect(await screen.findByTestId('headline')).toHaveTextContent('Live Stopped');
    unmount();
    vi.unstubAllGlobals();
    stubApi(base({ 'GET /api/status': { ...RUNNING, mode: 'live' } }));
    renderApp('/');
    await waitFor(() => expect(screen.getByTestId('headline')).toHaveTextContent('Live Running'));
    expect(screen.getByTestId('status-card')).toHaveAttribute('data-state', 'live');
  });

  it('an error turns the headline and card to Needs Attention and lists the problem in red', async () => {
    stubApi(base({ 'GET /api/status': { ...RUNNING, last_error: 'kalshi 500 on order' } }));
    renderApp('/');
    const hl = await screen.findByTestId('headline');
    await waitFor(() => expect(hl).toHaveTextContent('Needs Attention'));
    expect(hl.querySelector('.ov-hl-danger')).not.toBeNull();
    const bad = screen.getByTestId('abnormal');
    expect(within(bad).getByText(/kalshi 500 on order/)).toHaveClass('error-text');
    expect(screen.getByTestId('status-card')).toHaveAttribute('data-state', 'error');
  });

  it('a stale heartbeat and an overdue run show in red; a healthy status shows none', async () => {
    stubApi(base({ 'GET /api/status': { ...RUNNING, heartbeat_age_s: 45 * 60, next_run_at: iso(10 * MIN) } }));
    const { unmount } = renderApp('/');
    const bad = await screen.findByTestId('abnormal');
    expect(within(bad).getByText(/No heartbeat for 45 min/)).toHaveClass('error-text');
    expect(within(bad).getByText(/Next run is overdue/)).toHaveClass('error-text');
    expect(screen.getByTestId('timing-line').querySelector('.error-text')).not.toBeNull();
    unmount();
    vi.unstubAllGlobals();
    stubApi(base());
    renderApp('/');
    await screen.findByTestId('headline');
    expect(screen.queryByTestId('abnormal')).toBeNull();
  });

  it('three tiles, a timing line and the heartbeat strip replace the old Bot card', async () => {
    stubApi(base());
    renderApp('/');
    const tiles = await screen.findByTestId('status-tiles');
    expect(within(tiles).getByText("Today's P&L")).toBeInTheDocument();
    expect(within(tiles).getByText('+$0.95')).toBeInTheDocument();
    expect(within(tiles).getByText('Maker P&L (30 days)')).toBeInTheDocument();
    expect(within(tiles).getByText('+$17.12')).toBeInTheDocument();
    expect(within(tiles).getByText('At Risk')).toBeInTheDocument();
    expect(within(tiles).getByText('$37.60')).toBeInTheDocument();
    const strip = screen.getByTestId('heartbeat-strip');
    expect(strip.querySelectorAll('.hb-bar')).toHaveLength(12);
    expect(strip.querySelectorAll('.hb-warn')).toHaveLength(1);
    expect(screen.getByTestId('timing-line')).toHaveTextContent(/Last Run 4 min ago · Next in \d+ min/);
    const text = document.querySelector('main').textContent;
    for (const gone of ['Heartbeat', 'Kill switch', 'Live allowed', 'Pass running', 'Expected return', 'Run budget', 'headroom']) {
      expect(text).not.toContain(gone);
    }
  });

  it('Run Once moves into the more-actions menu and still confirms first', async () => {
    const user = userEvent.setup();
    const calls = stubApi(base({ 'POST /api/bot/run-once': { ran: true } }));
    renderApp('/');
    await user.click(await screen.findByRole('button', { name: 'More actions' }));
    await user.click(screen.getByRole('menuitem', { name: 'Run Once' }));
    await user.click(await screen.findByRole('button', { name: 'Run once' }));
    await waitFor(() => expect(calls.some((c) => c.key === 'POST /api/bot/run-once')).toBe(true));
  });

  it('stopped shows Start (with the lock) as the primary action and Stop is still on the card', async () => {
    stubApi(base({ 'GET /api/status': STATUS }));
    renderApp('/');
    await screen.findByTestId('status-card');
    const card = screen.getByTestId('status-card');
    expect(within(card).getByRole('button', { name: /Start/ })).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: /stop the bot/i })).toBeInTheDocument();
  });

  it('shows one bar and one line: "$37.60 at risk of $100" (limit = max_open_frac of balance + positions)', async () => {
    stubApi(base({ 'GET /api/status': { ...RUNNING, account: { cash: 212.4, exposure: 37.6 }, limits: { max_open_frac: 0.4 } } }));
    renderApp('/');
    const line = await screen.findByTestId('risk-line');
    expect(line).toHaveTextContent('$37.60 at risk of $100');
    expect(document.querySelector('main').textContent).not.toMatch(/headroom|limit \(/);
  });

  it('one demo banner in demo mode, none in live', async () => {
    stubApi(base());
    const { unmount } = renderApp('/');
    await screen.findByTestId('headline');
    expect(screen.getAllByTestId('demo-banner')).toHaveLength(1);
    expect(document.querySelector('main').textContent.match(/not representative/g)).toHaveLength(1);
    unmount();
    vi.unstubAllGlobals();
    stubApi(base({ 'GET /api/status': { ...RUNNING, mode: 'live' } }));
    renderApp('/');
    await screen.findByTestId('headline');
    expect(screen.queryByTestId('demo-banner')).toBeNull();
  });

  it('edge verdict joins the headline when analytics has cells', async () => {
    stubApi(base({ 'GET /api/analytics/summary': { by_cell: [{ cell: 'a', verdict: 'on_track' }, { cell: 'b', verdict: 'too_early' }] } }));
    renderApp('/');
    await waitFor(() => expect(screen.getByTestId('headline')).toHaveTextContent('Edge On Track'));
  });
});

describe('Overview helpers', () => {
  it('edge summary: below wins, all too early, on track', () => {
    expect(edgeSummary({ by_cell: [{ verdict: 'below' }, { verdict: 'on_track' }] }).text).toBe('Edge Below Expected');
    expect(edgeSummary({ by_cell: [{ verdict: 'too_early' }] }).text).toBe('Edge Too Early');
    expect(edgeSummary({ by_cell: [{ verdict: 'above' }] }).text).toBe('Edge On Track');
    expect(edgeSummary({ by_cell: [] })).toBeNull();
    expect(edgeSummary(null)).toBeNull();
  });

  it('headline omits the P&L part when there is none', () => {
    expect(headlineParts(RUNNING, null).map((p) => p.text)).toEqual(['Demo Running', '+$17.12 Over 30 Days']);
    expect(headlineParts({ ...STATUS, pnl: null }, null).map((p) => p.text)).toEqual(['Demo Stopped']);
  });

  it('abnormalities: kill switch while desired running, failed last pass, fill sync error', () => {
    const keys = abnormalities({ ...RUNNING, kill_switch: true, recent_passes: [{ ts: 'x', ok: false, errors: 1 }], fills_sync: { error: 'x' } }).map((b) => b.key);
    expect(keys).toEqual(expect.arrayContaining(['kill', 'pass', 'sync']));
    expect(abnormalities(null)).toEqual([]);
  });

  it('toast texts are specific, and absent fields fall back', () => {
    expect(stopToastTitle({ stopped_at: '2026-10-15T21:41:00Z', orders_cancelled: 3 })).toMatch(/^Stopped at .* · 3 Orders Cancelled$/);
    expect(stopToastTitle({ stopped_at: '2026-10-15T21:41:00Z', orders_cancelled: 1 })).toMatch(/1 Order Cancelled$/);
    expect(stopToastTitle({ cancelled: 3 })).toBeNull();
    expect(settingsToastTitle({ changes: [{ key: 'run_budget', from: 25, to: 50 }] })).toBe('Settings Saved · Run Budget $25 → $50');
    expect(settingsToastTitle({ changes: [] })).toBeNull();
    expect(settingsToastTitle({})).toBeNull();
    expect(sinceParts({ fills: 0, settled: 0, pnl_change: 0 }, 'x')).toEqual([]);
  });
});

describe('Since You Last Checked', () => {
  it('first visit shows nothing and records the visit', async () => {
    const calls = stubApi(base());
    renderApp('/');
    await screen.findByTestId('headline');
    expect(screen.queryByTestId('since-card')).toBeNull();
    expect(calls.some((c) => c.key === 'GET /api/since')).toBe(false);
    expect(window.localStorage.getItem(LAST_VISIT_KEY)).not.toBeNull();
  });

  it('a returning visit shows fills, settled markets and P&L change from /api/since', async () => {
    window.localStorage.setItem(LAST_VISIT_KEY, iso(3 * 60 * MIN));
    const calls = stubApi(base({ 'GET /api/since': { fills: 3, settled: 2, pnl_change: 1.4 } }));
    renderApp('/');
    const card = await screen.findByTestId('since-card');
    expect(card).toHaveTextContent('Since You Last Checked');
    expect(card).toHaveTextContent(/3 New Fills · 2 Markets Settled · P&L \+\$1\.40 Since /);
    expect(calls.find((c) => c.key === 'GET /api/since').path).toMatch(/ts=/);
    await userEvent.setup().click(within(card).getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByTestId('since-card')).toBeNull();
  });

  it('is hidden when nothing changed or the request fails', async () => {
    window.localStorage.setItem(LAST_VISIT_KEY, iso(60 * MIN));
    stubApi(base({ 'GET /api/since': { fills: 0, settled: 0, pnl_change: 0 } }));
    const { unmount } = renderApp('/');
    await screen.findByTestId('headline');
    expect(screen.queryByTestId('since-card')).toBeNull();
    unmount();
    vi.unstubAllGlobals();
    stubApi(base({ 'GET /api/since': [500, { detail: 'boom' }] }));
    renderApp('/');
    await screen.findByTestId('headline');
    expect(screen.queryByTestId('since-card')).toBeNull();
  });
});

describe('P&L by source', () => {
  it('lists strategies by registry name with today P&L, and splits $ at risk into Bot and You', async () => {
    stubApi(
      base({
        'GET /api/account/positions': [
          200,
          [
            { ticker: 'A', value: 30, source: 'bot', strategy: 'maker' },
            { ticker: 'B', value: 10, source: 'you', strategy: null },
          ],
        ],
      }),
    );
    renderApp('/');
    const box = await screen.findByTestId('source-pnl');
    expect(within(box).getByText('Bot · Maker').closest('li')).toHaveTextContent('+$1.40');
    expect(within(box).getByText('Bot · Arb').closest('li')).toHaveTextContent('+$0.35');
    expect(within(box).getByText('You').closest('li')).toHaveTextContent('−$0.80');
    expect(within(box).getByText('Total Today').closest('li')).toHaveTextContent('+$0.95');
    const split = await screen.findByTestId('risk-split');
    expect(split).toHaveTextContent('Bot $30.00');
    expect(split).toHaveTextContent('You $10.00');
  });

  it('resting order rows (kind: order) do not change the Bot/You at-risk split', async () => {
    stubApi(
      base({
        'GET /api/account/positions': [
          200,
          [
            { ticker: 'A', kind: 'position', value: 30, source: 'bot', strategy: 'maker' },
            { ticker: 'B', value: 10, source: 'you', strategy: null },
            { ticker: 'C', kind: 'order', value: 8.1, source: 'bot', strategy: 'maker' },
          ],
        ],
      }),
    );
    renderApp('/');
    const split = await screen.findByTestId('risk-split');
    expect(split).toHaveTextContent('Bot $30.00');
    expect(split).toHaveTextContent('You $10.00');
    expect(split).toHaveTextContent('$40.00');
  });

  it('a new strategy in the registry shows up with no code change; no positions hides the bar', async () => {
    stubApi(
      base({
        'GET /api/strategies': [200, [...STRATS, { id: 'news', source: 'bot', name: 'Bot · News', color: 'red' }]],
        'GET /api/status': { ...RUNNING, pnl_today: { maker: 1, arb: 0, you: 0, news: 2.5, total: 3.5 } },
      }),
    );
    renderApp('/');
    const box = await screen.findByTestId('source-pnl');
    await waitFor(() => expect(within(box).getByText('Bot · News').closest('li')).toHaveTextContent('+$2.50'));
    expect(screen.queryByTestId('risk-split')).toBeNull();
  });
});

describe('Floating status widget', () => {
  async function setupWide(extra = {}) {
    mediaQuery(true);
    stubObserver();
    const calls = stubApi(base(extra));
    renderApp('/');
    await screen.findByTestId('status-card');
    return calls;
  }

  it('appears at 768 px and up only after the status card scrolls away, and goes back when it returns', async () => {
    await setupWide();
    expect(screen.queryByTestId('status-widget')).toBeNull();
    fireOffscreen(true);
    const w = await screen.findByTestId('status-widget');
    expect(w).toHaveTextContent('Running');
    expect(w).toHaveTextContent('Demo');
    expect(w).toHaveTextContent('+$0.95');
    fireOffscreen(false);
    await waitFor(() => expect(screen.queryByTestId('status-widget')).toBeNull());
  });

  it('never appears on phone widths, even when the card is off screen', async () => {
    mediaQuery(false);
    stubObserver();
    stubApi(base());
    renderApp('/');
    await screen.findByTestId('status-card');
    fireOffscreen(true);
    expect(screen.queryByTestId('status-widget')).toBeNull();
    expect(observers).toHaveLength(0);
  });

  it('Stop from the widget: one confirm, no 2FA, specific toast', async () => {
    const user = userEvent.setup();
    const calls = await setupWide({
      'POST /api/bot/stop': { desired: 'stopped', kill_switch: true, cancelled: 3, errors: [], stopped_at: '2026-10-15T21:41:00Z', orders_cancelled: 3 },
    });
    fireOffscreen(true);
    const w = await screen.findByTestId('status-widget');
    await user.click(within(w).getByRole('button', { name: /stop the bot/i }));
    expect(screen.getByRole('button', { name: 'Stop now' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Stop now' }));
    expect(await screen.findByText(/^Stopped at .* · 3 Orders Cancelled$/)).toBeInTheDocument();
    const stop = calls.find((c) => c.key === 'POST /api/bot/stop');
    expect(stop.headers['X-2FA-Code']).toBeUndefined();
    expect(screen.queryByLabelText('6-digit code')).toBeNull();
  });

  it('the widget does not replace the header Stop or the card Stop', async () => {
    await setupWide();
    fireOffscreen(true);
    await screen.findByTestId('status-widget');
    const header = document.querySelector('header.appbar');
    expect(within(header).getByRole('button', { name: /stop the bot/i })).toBeInTheDocument();
    expect(within(screen.getByTestId('status-card')).getByRole('button', { name: /stop the bot/i })).toBeInTheDocument();
    expect(document.querySelector('nav')).not.toBeNull();
    expect(screen.getByTestId('status-widget').closest('header, nav')).toBeNull();
  });

  it('tap expands it with a Back to Status button', async () => {
    const user = userEvent.setup();
    await setupWide();
    fireOffscreen(true);
    const w = await screen.findByTestId('status-widget');
    const scroll = vi.fn();
    screen.getByTestId('status-card').scrollIntoView = scroll;
    await user.click(within(w).getByRole('button', { name: /Running/ }));
    await user.click(within(w).getByRole('button', { name: 'Back to Status' }));
    expect(scroll).toHaveBeenCalled();
  });
});
