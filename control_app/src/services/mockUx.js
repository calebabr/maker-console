// Mock endpoints for the FE-Settings pages (UX contract section 5): alerts + alert settings, config undo,
// push, account positions, and source-tagged Activity rows. Called from mock.js route(); returns a reply or null.
// Nothing here is real: no subscription is stored anywhere, "push" just flips a flag.

const MIN = 60 * 1000;

const ALERTS_SEED = (now, iso) =>
  [
    { type: 'fill', title: 'Fill: KXDEMOA-26OCT15-T83', body: 'Bought 7 YES at 41c on Demo series A.', ago: 14 },
    { type: 'daily', title: 'Daily Summary', body: '12 fills, 3 markets settled. P&L +$1.40 today.', ago: 60 },
    { type: 'settled', title: 'Market Settled', body: 'KXDEMOB-26OCT14-B67.5 settled in your favor: +$0.62.', ago: 3 * 60 },
    { type: 'error', title: 'Trading Pass Error', body: 'Kalshi returned HTTP 503 for one series; the pass retried.', ago: 7 * 60 },
    { type: 'stop', title: 'Bot Stopped', body: 'Stopped from the app: 3 resting orders cancelled.', ago: 26 * 60 },
    { type: 'breaker', title: 'Arbitrage Stopped: Kill Switch', body: 'Legging risk detected on a bracket set; arbitrage paused.', ago: 2 * 24 * 60 },
    { type: 'test', title: 'Test Notification', body: 'Prior notifications are working.', ago: 3 * 24 * 60 },
  ].map((a, i) => ({ id: `al${i + 1}`, ts: iso(now - a.ago * MIN), type: a.type, title: a.title, body: a.body }));

// Extra Activity rows so day grouping and the source filters have something to show.
function extraActivity(now, iso) {
  const day = 24 * 60 * MIN;
  return [
    { utc: iso(now - 20 * MIN), event: 'fill', ticker: 'KXFEDDECISION-26DEC-H25', side: 'yes', price: 0.31, count: 20, cost: 6.2, source: 'you', strategy: null, note: 'manual trade' },
    { utc: iso(now - day - 2 * 60 * MIN), event: 'fill', ticker: 'KXDEMOB-26OCT14-B67.5', side: 'yes', price: 0.38, count: 8, cost: 3.04, source: 'bot', strategy: 'maker' },
    { utc: iso(now - day - 3 * 60 * MIN), event: 'fill', ticker: 'KXDEMOC-26OCT14-B88.5', side: 'yes', price: 0.46, count: 5, cost: 2.3, source: 'bot', strategy: 'arb', note: 'arb leg' },
    { utc: iso(now - day - 4 * 60 * MIN), event: 'settle', ticker: 'KXNBAGAME-26OCT13NYKBOS-NYK', side: 'yes', price: 1, count: 10, cost: 5.8, source: 'you', strategy: null, note: 'manual trade settled: +$4.20' },
    { utc: iso(now - 2 * day - 60 * MIN), event: 'quote', ticker: 'KXDEMOA-26OCT13-T83', side: 'yes', price: 0.41, count: 16.2, cost: 6.64, source: 'bot', strategy: 'maker' },
    { utc: iso(now - 2 * day - 90 * MIN), event: 'error', ticker: 'KXDEMOD-26OCT13-B88.5', note: 'Kalshi returned HTTP 503', source: 'bot', strategy: 'maker' },
  ];
}

export function decorateActivity(items, now, iso) {
  const base = (items || []).map((r, i) => ({
    source: 'bot',
    strategy: r.ticker && /^KXDEMO(C|E)/.test(r.ticker) && i % 4 === 0 ? 'arb' : 'maker',
    ...r,
  }));
  return [...base, ...extraActivity(now, iso)].sort((a, b) => String(b.utc || b.ts).localeCompare(String(a.utc || a.ts)));
}

const DEFAULT_ALERT_SETTINGS = () => ({
  daily_summary_time: '08:00',
  mode: 'problems',
  quiet_hours: { enabled: false, start: '22:00', end: '07:00' },
  tz: 'America/Chicago',
});

// Registered push devices: only the push-service host and when it was added (never the endpoint or keys).
function seedDevices() {
  const now = Date.now();
  return [
    { host: 'web.push.apple.com', created_utc: new Date(now - 9 * 24 * 60 * MIN).toISOString() },
    { host: 'fcm.googleapis.com', created_utc: new Date(now - 2 * 24 * 60 * MIN).toISOString() },
  ];
}

const U = { alertSettings: DEFAULT_ALERT_SETTINGS(), push: { subscribed: false, devices: seedDevices() }, alerts: null };

export function resetMockUx() {
  U.alertSettings = DEFAULT_ALERT_SETTINGS();
  U.push = { subscribed: false, devices: seedDevices() };
  U.alerts = null;
}

function flat(o, pre = '') {
  const out = {};
  for (const [k, v] of Object.entries(o)) {
    if (v && typeof v === 'object') Object.assign(out, flat(v, `${pre}${k}.`));
    else out[`${pre}${k}`] = v;
  }
  return out;
}

export function uxRoute(method, p, q, headers, body, ctx) {
  const { reply, check2fa, state, now, iso, audit, validateConfig } = ctx;
  const S = state();
  if (!U.alerts) U.alerts = ALERTS_SEED(now(), iso);

  if (method === 'GET' && p === '/api/alerts') {
    const type = q.get('type');
    const limit = Math.min(200, parseInt(q.get('limit') || '50', 10) || 50);
    const before = q.get('before');
    let rows = U.alerts;
    if (type) rows = rows.filter((a) => a.type === type);
    if (before) rows = rows.filter((a) => a.ts < before);
    return reply(200, rows.slice(0, limit));
  }
  if (method === 'GET' && p === '/api/alerts/settings') return reply(200, U.alertSettings);
  if (method === 'PUT' && p === '/api/alerts/settings') {
    const bad = check2fa(headers);
    if (bad) return bad;
    const b = body || {};
    const next = { ...U.alertSettings, ...b, quiet_hours: { ...U.alertSettings.quiet_hours, ...(b.quiet_hours || {}) } };
    const hhmm = /^([01]\d|2[0-3]):[0-5]\d$/;
    const errs = [];
    if (!hhmm.test(next.daily_summary_time)) errs.push('daily_summary_time: must be HH:MM');
    if (!['problems', 'every_fill'].includes(next.mode)) errs.push('mode: must be problems or every_fill');
    if (!hhmm.test(next.quiet_hours.start) || !hhmm.test(next.quiet_hours.end)) errs.push('quiet_hours: start and end must be HH:MM');
    if (errs.length) return reply(400, { detail: errs });
    const a = flat(U.alertSettings);
    const z = flat(next);
    const changes = Object.keys(z).filter((k) => a[k] !== z[k]).map((k) => ({ key: k, from: a[k], to: z[k] }));
    U.alertSettings = next;
    audit('alerts.settings', true, { changes });
    return reply(200, { ...next, settings: next, changes });
  }

  if (method === 'GET' && p === '/api/config/versions') {
    const last = S.audit.find((e) => e.ok && (e.action === 'config_save' || e.action === 'config_undo'));
    if (!last || last.action !== 'config_save') return reply(200, { versions: [], undo: null });
    const d = last.details || {};
    const changes = Object.keys(d.before || {}).map((k) => ({ key: k, from: d.before[k], to: S.config[k] }));
    const view = { version: S.version, ts: last.ts, by: 'owner', changes, cells_changed: false, undoable: changes.length > 0 };
    return reply(200, { versions: [view], undo: view.undoable ? view : null });
  }
  if (method === 'POST' && p === '/api/config/undo') {
    const bad = check2fa(headers);
    if (bad) return bad;
    const last = S.audit.find((e) => e.ok && (e.action === 'config_save' || e.action === 'config_undo'));
    if (!last || last.action !== 'config_save') return reply(409, { detail: 'nothing_to_undo' });
    if (body && body.version != null && body.version !== S.version) return reply(409, { detail: 'stale_version' });
    const before = (last.details || {}).before || {};
    const next = { ...S.config, ...before };
    const errs = validateConfig(next);
    if (errs.length) return reply(400, { detail: errs });
    const undone = S.version;
    S.config = next;
    S.version += 1;
    audit('config_undo', true, { version: S.version, undone });
    const pub = Object.fromEntries(Object.entries(S.config).filter(([k]) => k !== 'cells'));
    return reply(200, { config: pub, version: S.version, undone_version: undone });
  }

  if (method === 'GET' && p === '/api/push/public-key') {
    return reply(200, { public_key: 'BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U', enabled: true });
  }
  if (method === 'GET' && p === '/api/push/subscriptions') {
    return reply(200, { count: U.push.devices.length, subscriptions: U.push.devices });
  }
  if (method === 'DELETE' && p === '/api/push/subscriptions') {
    const removed = U.push.devices.length;
    U.push.devices = [];
    U.push.subscribed = false;
    return reply(200, { removed });
  }
  if (method === 'POST' && p === '/api/push/subscribe') {
    if (!body || typeof body.endpoint !== 'string') return reply(400, { detail: ['body: must be a PushSubscription'] });
    U.push.subscribed = true;
    return reply(200, { subscribed: true });
  }
  if (method === 'DELETE' && p === '/api/push/subscribe') {
    U.push.subscribed = false;
    return reply(200, { subscribed: false });
  }
  if (method === 'POST' && p === '/api/push/test') {
    if (!U.push.subscribed) return reply(200, { sent: 0, failed: 0, removed: 0 });
    U.alerts.unshift({ id: `al${U.alerts.length + 1}`, ts: iso(now()), type: 'test', title: 'Test Notification', body: 'Prior notifications are working.' });
    return reply(200, { sent: 1, failed: 0, removed: 0 });
  }

  // GET /api/account/positions lives in mock.js (FE-Overview's block); Account reads the same data.
  return null;
}
