import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp, stubApi, STATUS } from './helpers.jsx';
import { dayLabel, groupByDay, matchesSource, sourceChips } from '../utils/activity.js';

const STRATEGIES = [
  { id: 'maker', source: 'bot', name: 'Bot · Maker', color: 'var(--accent)', prefix: 'mm-' },
  { id: 'arb', source: 'bot', name: 'Bot · Arb', color: 'var(--chart2)', prefix: 'arb-' },
  { id: 'you', source: 'you', name: 'You', color: 'var(--muted)' },
];

const CONFIG = {
  config: { run_budget: 25, max_order: 8, poll_minutes: 10, cells: [[0.3, 0.45, 6, 12, "no"]] },
  schema: { run_budget: { type: 'number', min: 1, max: 500 }, max_order: { type: 'number', min: 1 }, poll_minutes: { type: 'integer', min: 1 } },
  ceilings: {},
};

afterEach(() => {
  vi.unstubAllGlobals();
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});

async function enterCode(user, label) {
  await user.type(await screen.findByLabelText('6-digit code'), '123456');
  const btns = screen.getAllByRole('button', { name: label });
  await user.click(btns[btns.length - 1]);
}

// ---------------------------------------------------------------- Settings: preview diff + undo (item 40)

describe('Settings change preview and undo', () => {
  it('shows what will change, with money formatted, before any 2FA prompt', async () => {
    const user = userEvent.setup();
    const calls = stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG });
    renderApp('/settings');
    const field = await screen.findByLabelText(/run budget/i);
    expect(screen.queryByText('Changes To Review')).toBeNull();
    await user.clear(field);
    await user.type(field, '50');
    const card = (await screen.findByText('Changes To Review')).closest('section');
    expect(within(card).getByText('Run Budget')).toBeInTheDocument();
    expect(within(card).getByText('$25.00')).toBeInTheDocument();
    expect(within(card).getByText('$50.00')).toBeInTheDocument();
    // nothing has been sent and no code prompt is open yet
    expect(calls.some((c) => c.key === 'PUT /api/config')).toBe(false);
    expect(screen.queryByLabelText('6-digit code')).toBeNull();
  });

  it('Undo Last Change asks for a fresh code, sends the version and never cells', async () => {
    const user = userEvent.setup();
    let undone = false;
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': () => ({ ...CONFIG, config: { ...CONFIG.config, run_budget: undone ? 25 : 50 } }),
      'GET /api/config/versions': () =>
        undone
          ? { versions: [], undo: null }
          : { versions: [], undo: { version: 8, ts: '2026-10-15T15:00:00Z', undoable: true, changes: [{ key: 'run_budget', from: 25, to: 50 }] } },
      'POST /api/config/undo': () => {
        undone = true;
        return { config: { run_budget: 25 }, version: 9, undone_version: 8 };
      },
    });
    renderApp('/settings');
    const undo = await screen.findByRole('button', { name: /undo last change/i });
    await user.click(undo);
    // the prompt shows the reverse: current value -> value it returns to
    expect(await screen.findByText('Undo Last Change?')).toBeInTheDocument();
    await enterCode(user, 'Undo');
    await screen.findByText('Change Undone');
    const post = calls.find((c) => c.key === 'POST /api/config/undo');
    expect(post.headers['X-2FA-Code']).toBe('123456');
    expect(post.body).toEqual({ version: 8 });
    expect(JSON.stringify(post.body)).not.toContain('cells');
    await waitFor(() => expect(screen.queryByRole('button', { name: /undo last change/i })).toBeNull());
  });

  it('a stale undo (409) says the settings changed again', async () => {
    const user = userEvent.setup();
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': CONFIG,
      'GET /api/config/versions': { versions: [], undo: { version: 8, undoable: true, changes: [{ key: 'run_budget', from: 25, to: 50 }] } },
      'POST /api/config/undo': [409, { detail: 'stale_version' }],
    });
    renderApp('/settings');
    await user.click(await screen.findByRole('button', { name: /undo last change/i }));
    await enterCode(user, 'Undo');
    expect(await screen.findByText('Not Undone')).toBeInTheDocument();
    expect(screen.getByText(/changed again/i)).toBeInTheDocument();
  });

  it('shows no undo card when there is nothing to undo', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG, 'GET /api/config/versions': { versions: [], undo: null } });
    renderApp('/settings');
    await screen.findByLabelText(/run budget/i);
    expect(screen.queryByRole('button', { name: /undo last change/i })).toBeNull();
  });
});

// ---------------------------------------------------------------- Display prefs

describe('Display settings', () => {
  it('theme and text size are per-device (localStorage), need no 2FA and send nothing', async () => {
    const user = userEvent.setup();
    const calls = stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG });
    renderApp('/settings');
    await screen.findByLabelText(/run budget/i);
    const before = calls.length;
    await user.click(screen.getByRole('button', { name: /deep teal/i }));
    await user.click(screen.getByRole('button', { name: 'Large' }));
    expect(localStorage.getItem('prior.theme')).toBe('teal');
    expect(localStorage.getItem('prior.textSize')).toBe('large');
    expect(screen.getByRole('button', { name: /deep teal/i })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByLabelText('6-digit code')).toBeNull();
    expect(calls.length).toBe(before);
  });
});

// ---------------------------------------------------------------- Push (item 45)

function stubInstall({ installed }) {
  vi.stubGlobal('matchMedia', (q) => ({ matches: installed && /standalone/.test(q), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
}

describe('Push notifications card', () => {
  it('outside the installed app: no Enable button, only add-to-Home-Screen steps for Chrome on iPhone', async () => {
    stubInstall({ installed: false });
    stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG });
    renderApp('/settings');
    await screen.findByLabelText(/run budget/i);
    expect(screen.queryByRole('button', { name: /enable notifications/i })).toBeNull();
    const steps = screen.getByTestId('push-install-steps');
    expect(steps).toHaveTextContent(/Chrome on iPhone/);
    expect(steps).toHaveTextContent(/Add to Home Screen/);
  });

  it('as the installed app: Enable subscribes, posts the subscription and offers test and turn off', async () => {
    stubInstall({ installed: true });
    const sub = { endpoint: 'https://push.example/abc', toJSON: () => ({ endpoint: 'https://push.example/abc', keys: { p256dh: 'p', auth: 'a' } }), unsubscribe: vi.fn(async () => true) };
    let current = null;
    const reg = { pushManager: { getSubscription: vi.fn(async () => current), subscribe: vi.fn(async () => (current = sub)) } };
    vi.stubGlobal('navigator', { ...navigator, serviceWorker: { ready: Promise.resolve(reg) } });
    vi.stubGlobal('PushManager', function PushManager() {});
    vi.stubGlobal('Notification', { requestPermission: vi.fn(async () => 'granted') });
    window.PushManager = globalThis.PushManager;
    window.Notification = globalThis.Notification;
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': CONFIG,
      'GET /api/push/public-key': { public_key: 'BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U', enabled: true },
      'POST /api/push/subscribe': { subscribed: true },
      'POST /api/push/test': { sent: 1, failed: 0, removed: 0 },
      'DELETE /api/push/subscribe': { subscribed: false },
    });
    renderApp('/settings');
    await user.click(await screen.findByRole('button', { name: /enable notifications/i }));
    await screen.findByRole('button', { name: 'Send Test' });
    expect(reg.pushManager.subscribe).toHaveBeenCalledWith(expect.objectContaining({ userVisibleOnly: true }));
    expect(calls.find((c) => c.key === 'POST /api/push/subscribe').body.endpoint).toBe('https://push.example/abc');
    // the endpoint is never printed on the page
    expect(document.body.textContent).not.toContain('push.example');
    await user.click(screen.getByRole('button', { name: 'Send Test' }));
    await screen.findByText('Test Sent');
    await user.click(screen.getByRole('button', { name: 'Turn Off' }));
    await screen.findByRole('button', { name: /enable notifications/i });
    expect(sub.unsubscribe).toHaveBeenCalled();
    expect(calls.find((c) => c.key === 'DELETE /api/push/subscribe').body).toEqual({ endpoint: 'https://push.example/abc' });
  });
});

describe('push-sw.js notification click', () => {
  function load() {
    const listeners = {};
    const opened = [];
    const self = {
      location: { origin: 'https://keel.example' },
      addEventListener: (n, f) => (listeners[n] = f),
      clients: { matchAll: async () => [], openWindow: async (u) => opened.push(u) },
      registration: { showNotification: vi.fn() },
    };
    // eslint-disable-next-line no-new-func
    new Function('self', 'URL', readFileSync('public/push-sw.js', 'utf8'))(self, URL);
    return { listeners, opened, self };
  }
  const click = async (url) => {
    const { listeners, opened } = load();
    let p;
    listeners.notificationclick({ notification: { close() {}, data: { url } }, waitUntil: (x) => (p = x) });
    await p;
    return opened;
  };

  it('deep-links to an in-app path', async () => {
    expect(await click('/alerts')).toEqual(['https://keel.example/alerts']);
  });
  it('never follows an off-site or protocol-relative url', async () => {
    expect(await click('//evil.example/x')).toEqual(['https://keel.example/']);
    expect(await click('https://evil.example/x')).toEqual(['https://keel.example/']);
  });
  it('shows the pushed title and body', () => {
    const { listeners, self } = load();
    listeners.push({ data: { json: () => ({ title: 'Fill', body: 'Bought 7', type: 'fill', url: '/activity' }) }, waitUntil() {} });
    expect(self.registration.showNotification).toHaveBeenCalledWith('Fill', expect.objectContaining({ body: 'Bought 7', data: { url: '/activity' } }));
  });
});

// ---------------------------------------------------------------- Activity (items 41, Y4)

const HOUR = 3600 * 1000;
function row(msAgo, over) {
  return { utc: new Date(Date.now() - msAgo).toISOString(), event: 'fill', ticker: 'KXDEMOA-26OCT15-T83', side: 'yes', price: 0.41, count: 7, cost: 2.87, source: 'bot', strategy: 'maker', ...over };
}

describe('Activity', () => {
  let ITEMS;
  beforeEach(() => {
    // a fixed clock (mid-afternoon local time) so the day headings do not depend on when the suite runs
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 14, 14, 0, 0));
    ITEMS = [
      row(10 * 60 * 1000, { ticker: 'KXFEDDECISION-26DEC-H25', source: 'you', strategy: null, note: 'manual' }),
      row(2 * HOUR, { event: 'quote' }),
      row(26 * HOUR, { strategy: 'arb', ticker: 'KXDEMOC-26OCT14-B88.5' }),
      row(27 * HOUR, { event: 'settle', source: 'you', strategy: null, ticker: 'KXNBAGAME-26OCT13NYKBOS-NYK', note: 'manual trade settled' }),
      row(75 * HOUR, { event: 'cancel' }),
    ];
  });
  afterEach(() => { vi.useRealTimers(); });
  const routes = () => ({ 'GET /api/status': STATUS, 'GET /api/activity': { items: ITEMS }, 'GET /api/strategies': [200, STRATEGIES] });

  it('groups rows by day: Today, Yesterday, then a dated heading', async () => {
    stubApi(routes());
    renderApp('/activity');
    await screen.findByRole('heading', { name: 'Today' });
    expect(screen.getByRole('heading', { name: 'Yesterday' })).toBeInTheDocument();
    const dated = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
    expect(dated.length).toBe(3);
    expect(dated[2]).toMatch(/^[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2}$/);
    const today = screen.getByRole('region', { name: 'Today' });
    expect(within(today).getAllByRole('listitem')).toHaveLength(2);
  });

  it('source chips filter to Bot, You and Arb and keep manual settlements visible', async () => {
    const user = userEvent.setup();
    stubApi(routes());
    renderApp('/activity');
    await screen.findByRole('heading', { name: 'Today' });
    const src = screen.getByRole('group', { name: 'Filter by source' });
    expect(within(src).getAllByRole('button').map((b) => b.textContent)).toEqual(['All', 'Bot', 'You', 'Arb']);
    await user.click(within(src).getByRole('button', { name: 'You' }));
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(screen.getByText('manual trade settled')).toBeInTheDocument();
    await user.click(within(src).getByRole('button', { name: 'Arb' }));
    expect(screen.getAllByRole('listitem')).toHaveLength(1);
    await user.click(within(src).getByRole('button', { name: 'Bot' }));
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
  });

  it('type chips filter by event kind', async () => {
    const user = userEvent.setup();
    stubApi(routes());
    renderApp('/activity');
    await screen.findByRole('heading', { name: 'Today' });
    const kinds = screen.getByRole('group', { name: 'Filter by type' });
    await user.click(within(kinds).getByRole('button', { name: 'Cancels' }));
    expect(screen.getAllByRole('listitem')).toHaveLength(1);
  });

  it('every row has a source badge from the registry and an Open in Kalshi link to the market', async () => {
    stubApi(routes());
    renderApp('/activity');
    await screen.findByRole('heading', { name: 'Today' });
    const links = screen.getAllByRole('link', { name: /open in kalshi/i });
    expect(links).toHaveLength(5);
    expect(links[0]).toHaveAttribute('href', 'https://kalshi.com/markets/kxfeddecision-26dec-h25');
    expect(links[0]).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(links[0]).toHaveAttribute('target', '_blank');
    expect(document.querySelectorAll('.source-badge[data-source="you"]').length).toBe(2);
    expect(document.querySelectorAll('.source-badge[data-source="arb"]').length).toBe(1);
    expect(screen.getAllByText('Bot · Maker').length).toBeGreaterThan(0);
  });

  it('still renders with defaults when the strategy registry is unavailable', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/activity': { items: ITEMS } });
    renderApp('/activity');
    await screen.findByRole('heading', { name: 'Today' });
    expect(screen.getAllByText('You').length).toBeGreaterThan(0);
  });
});

describe('activity helpers', () => {
  const now = new Date(2026, 9, 15, 12, 0, 0);
  it('labels days', () => {
    expect(dayLabel(new Date(2026, 9, 15, 1), now)).toBe('Today');
    expect(dayLabel(new Date(2026, 9, 14, 23), now)).toBe('Yesterday');
    expect(dayLabel(new Date(2026, 9, 12, 9), now)).toBe('Mon Oct 12');
    expect(dayLabel(null, now)).toBe('Unknown Date');
  });
  it('groups consecutive rows and keeps order', () => {
    const g = groupByDay([{ t: new Date(2026, 9, 15, 9) }, { t: new Date(2026, 9, 15, 8) }, { t: new Date(2026, 9, 14, 8) }], (r) => r.t, now);
    expect(g.map((x) => [x.label, x.items.length])).toEqual([['Today', 2], ['Yesterday', 1]]);
  });
  it('source chips come from the registry', () => {
    expect(sourceChips(STRATEGIES).map((c) => c.label)).toEqual(['All', 'Bot', 'You', 'Arb']);
    expect(sourceChips([...STRATEGIES, { id: 'news', source: 'bot', name: 'Bot · News' }]).map((c) => c.label)).toContain('News');
    expect(matchesSource({ source: 'bot', strategy: 'arb' }, 'strategy:arb')).toBe(true);
    expect(matchesSource({ source: 'bot', strategy: 'maker' }, 'you')).toBe(false);
  });
});

// ---------------------------------------------------------------- Alerts (items 37, 38)

describe('Alerts page', () => {
  const ALERTS = [
    { id: 'a1', ts: '2026-10-15T15:00:00Z', type: 'fill', title: 'Fill: KXDEMOA', body: 'Bought 7 YES at 41c' },
    { id: 'a2', ts: '2026-10-14T15:00:00Z', type: 'stop', title: 'Bot Stopped', body: 'Stopped from the app' },
  ];
  const SETTINGS = { daily_summary_time: '08:00', mode: 'problems', quiet_hours: { enabled: false, start: '22:00', end: '07:00' }, tz: 'America/Chicago' };

  it('lists history and the type filter asks the API for that type', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/alerts': (c) => (c.path.includes('type=stop') ? [200, [ALERTS[1]]] : [200, ALERTS]),
      'GET /api/alerts/settings': SETTINGS,
    });
    renderApp('/alerts');
    expect(await screen.findByText('Fill: KXDEMOA')).toBeInTheDocument();
    expect(screen.getByText('Bot Stopped')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(screen.queryByText('Fill: KXDEMOA')).toBeNull());
    expect(calls.some((c) => c.path.includes('type=stop'))).toBe(true);
  });

  it('alert settings: shows the diff, needs a fresh code, sends only what changed', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/alerts': [200, ALERTS],
      'GET /api/alerts/settings': SETTINGS,
      'PUT /api/alerts/settings': (c) => ({ ...SETTINGS, ...c.body, settings: { ...SETTINGS, ...c.body }, changes: [] }),
    });
    renderApp('/alerts');
    await user.click(await screen.findByRole('button', { name: /alert settings/i }));
    await user.click(await screen.findByRole('button', { name: 'Every Fill' }));
    await user.click(screen.getByLabelText('Quiet Hours'));
    const review = (await screen.findByText('Changes To Review')).parentElement;
    expect(within(review).getByText('Only Problems')).toBeInTheDocument();
    expect(within(review).getByText('Every Fill')).toBeInTheDocument();
    expect(calls.some((c) => c.key === 'PUT /api/alerts/settings')).toBe(false);
    await user.click(screen.getByRole('button', { name: /save alert settings/i }));
    await enterCode(user, 'Save');
    await screen.findByText('Alert Settings Saved');
    const put = calls.find((c) => c.key === 'PUT /api/alerts/settings');
    expect(put.headers['X-2FA-Code']).toBe('123456');
    expect(put.body).toEqual({ mode: 'every_fill', quiet_hours: { enabled: true } });
  });
});

// ---------------------------------------------------------------- Account (Y3)

describe('Account', () => {
  const POS = [
    { kind: 'position', ticker: 'KXDEMOA-26OCT15-T83', title: 'Demo A above 83?', side: 'yes', count: 12, price: 0.41, value: 5.4, payout_if_wins: 12, source: 'bot', strategy: 'maker' },
    { kind: 'position', ticker: 'KXFEDDECISION-26DEC-H25', title: 'Fed holds in December?', side: 'yes', count: 20, price: 0.31, value: 6.8, payout_if_wins: 20, source: 'you', strategy: null },
    { kind: 'order', ticker: 'KXNBAGAME-26OCT16LALGSW-LAL', title: 'Lakers win?', side: 'yes', count: 10, price: 0.42, value: 4.2, payout_if_wins: 10, source: 'you', strategy: null },
  ];

  it('shows positions and resting orders tagged by source, read-only, with Open in Kalshi links', async () => {
    const user = userEvent.setup();
    const calls = stubApi({ 'GET /api/status': STATUS, 'GET /api/account/positions': [200, POS], 'GET /api/strategies': [200, STRATEGIES] });
    renderApp('/account');
    expect(await screen.findByText('Demo A above 83?')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Open Positions' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Resting Orders' })).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /open in kalshi/i })).toHaveLength(3);
    expect(screen.getByText('Pays $12.00 if it wins')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'You' }));
    expect(screen.queryByText('Demo A above 83?')).toBeNull();
    expect(screen.getByText('Fed holds in December?')).toBeInTheDocument();
    // read-only: only GETs
    expect(calls.filter((c) => c.method !== 'GET')).toHaveLength(0);
  });

  it('explains a server without Kalshi keys in plain words', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/account/positions': [409, { detail: 'kalshi_not_configured' }] });
    renderApp('/account');
    expect(await screen.findByText(/keys are not set on the server/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------- Health (item 27)

describe('Health', () => {
  const SUMMARY = {
    generated_utc: new Date(Date.now() - 3 * HOUR).toISOString(),
    window_days: 30,
    expected_roi: 0.015,
    quoted_dollars: 4120.5,
    filled_dollars: 800,
    fill_rate: 0.194,
    overall: { fills: 220, cost: 1900, pnl: 24.5, roi: 0.0129, ci_low: 0.001, ci_high: 0.019 },
    per_series: {
      KXDEMOA: { fills: 41, pnl: 6.2, roi: 0.018, ci_low: -0.01, ci_high: 0.04 },
      KXDEMOD: { fills: 22, pnl: -0.9, roi: -0.004, ci_low: -0.05, ci_high: 0.04 },
    },
    actions: { pause: ['KXDEMOD'], keep: ['KXDEMOA'], need_more: { KXDEMOA: 78 }, collateral_return: 'not yet: 200 more settled fills needed' },
    last_account: { cash: 212.4, exposure: 37.6 },
    mystery_key: 'visible somewhere',
  };
  const REPORT = 'Maker monitor 2026-10-15 window: last 30 days\nOVERALL fills 220 ...';

  it('reads as a summary: formatted money, At Risk, one row per market, pause list, collateral status', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/health': { summary: SUMMARY, report_text: REPORT } });
    renderApp('/health');
    await screen.findByText('By Market');
    expect(screen.getByText('$212.40')).toBeInTheDocument();
    expect(screen.getByText('$37.60')).toBeInTheDocument();
    expect(screen.getByText('At Risk')).toBeInTheDocument();
    expect(screen.queryByText(/exposure/i)).toBeNull();
    expect(screen.getByText('Demo Series A')).toBeInTheDocument();
    expect(screen.getAllByText('Demo Series D').length).toBe(2); // the market row and the paused list
    expect(screen.getByText('+$6.20')).toBeInTheDocument();
    expect(screen.getByText('78 More Fills Needed')).toBeInTheDocument();
    expect(screen.getByText('not yet: 200 more settled fills needed')).toBeInTheDocument();
    expect(screen.getByText(/220 fills · \+\$24\.50 P&L/)).toBeInTheDocument();
    // no raw numbers like 212.4
    expect(document.body.textContent).not.toMatch(/\b212\.4\b(?!0)/);
  });

  it('keeps the raw report behind Show Raw Report and unknown keys under More Details', async () => {
    const user = userEvent.setup();
    stubApi({ 'GET /api/status': STATUS, 'GET /api/health': { summary: SUMMARY, report_text: REPORT } });
    renderApp('/health');
    await screen.findByText('By Market');
    expect(screen.queryByText(/Maker monitor 2026/)).toBeNull();
    await user.click(screen.getByRole('button', { name: /show raw report/i }));
    const pre = screen.getByText(/Maker monitor 2026/);
    expect(pre.tagName).toBe('PRE');
    expect(pre.className).toContain('report-wrap');
    await user.click(screen.getByRole('button', { name: /more details/i }));
    expect(screen.getByText('visible somewhere')).toBeInTheDocument();
  });
});

describe('no sideways scrolling at 375 px', () => {
  const css = readFileSync('src/styles/settings.css', 'utf8');
  it('the raw report wraps instead of scrolling', () => {
    const rule = /\.report-wrap\s*\{([^}]*)\}/.exec(css)[1];
    expect(rule).toMatch(/white-space:\s*pre-wrap/);
    expect(rule).toMatch(/overflow-wrap:\s*anywhere/);
    expect(rule).not.toMatch(/overflow-x:\s*(auto|scroll)/);
  });
  it('page styles use no fixed pixel widths beyond small marks', () => {
    const widths = [...css.matchAll(/(?<![-\w])width:\s*(\d+)px/g)].map((m) => Number(m[1]));
    expect(widths.every((w) => w <= 20)).toBe(true);
  });
  it('checkbox rows: centred inline-flex label, 1.1rem box in rem, 44px tap target on the label', () => {
    const label = /label\.ux-check\s*\{([^}]*)\}/.exec(css)[1];
    expect(label).toMatch(/display:\s*inline-flex/);
    expect(label).toMatch(/align-items:\s*center/);
    expect(label).toMatch(/gap:\s*0\.6rem/);
    expect(label).toMatch(/min-height:\s*44px/);
    const box = /label\.ux-check > input\[type='checkbox'\]\s*\{([^}]*)\}/.exec(css)[1];
    expect(box).toMatch(/width:\s*1\.1rem/);
    expect(box).toMatch(/height:\s*1\.1rem/);
    expect(box).toMatch(/margin:\s*0/);
  });
  it('the Quiet Hours checkbox uses that row', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/alerts': [200, []], 'GET /api/alerts/settings': { daily_summary_time: '08:00', mode: 'problems', quiet_hours: { enabled: false, start: '22:00', end: '07:00' } } });
    renderApp('/alerts');
    await userEvent.setup().click(await screen.findByRole('button', { name: /alert settings/i }));
    expect((await screen.findByLabelText('Quiet Hours')).closest('label')).toHaveClass('ux-check');
  });
  it('rows with long tickers and titles can break', () => {
    expect(css).toMatch(/\.pos-title\s*\{[^}]*overflow-wrap:\s*anywhere/);
    expect(css).toMatch(/\.alert-body\s*\{[^}]*overflow-wrap:\s*anywhere/);
    expect(css).toMatch(/\.diff-list li\s*\{[^}]*flex-wrap:\s*wrap/);
  });
});
