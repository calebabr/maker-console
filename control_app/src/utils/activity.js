// Helpers for the Activity page (items 41, Y4): day grouping, event groups and source filters.
import { parseDate } from './format.js';

const DAY_FMT = { weekday: 'short', month: 'short', day: 'numeric' };

/** Local calendar day key "YYYY-MM-DD" for a timestamp, or "" when it has none. */
export function dayKey(v) {
  const d = parseDate(v);
  if (!d) return '';
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

/** "Today", "Yesterday" or "Mon Oct 12" (device time zone). */
export function dayLabel(v, now = new Date()) {
  const d = parseDate(v);
  if (!d) return 'Unknown Date';
  const key = dayKey(d);
  if (key === dayKey(now)) return 'Today';
  const y = new Date(now);
  y.setDate(y.getDate() - 1);
  if (key === dayKey(y)) return 'Yesterday';
  return d.toLocaleDateString('en-US', DAY_FMT).replace(',', '');
}

/** Rows (newest first) -> [{key, label, items}] in the same order. */
export function groupByDay(items, getTs, now = new Date()) {
  const groups = [];
  let cur = null;
  for (const it of items) {
    const ts = getTs(it);
    const key = dayKey(ts) || 'unknown';
    if (!cur || cur.key !== key) {
      cur = { key, label: dayLabel(ts, now), items: [] };
      groups.push(cur);
    }
    cur.items.push(it);
  }
  return groups;
}

// Journal events -> the filter chip they belong to.
export const EVENT_GROUPS = [
  { id: 'fills', label: 'Fills', events: ['fill'] },
  { id: 'quotes', label: 'Quotes', events: ['quote', 'requote', 're-quote'] },
  { id: 'cancels', label: 'Cancels', events: ['cancel'] },
  { id: 'settled', label: 'Settled', events: ['settle', 'settled', 'settlement'] },
  { id: 'errors', label: 'Errors', events: ['error', 'fail', 'failed'] },
];

export function eventGroupOf(event) {
  const e = String(event || '').toLowerCase();
  const g = EVENT_GROUPS.find((x) => x.events.includes(e) || (x.id === 'errors' && e.includes('error')));
  return g ? g.id : 'other';
}

export function eventLabel(event) {
  const e = String(event || '').toLowerCase();
  if (e === 'settle') return 'Settled';
  if (!e) return 'Event';
  return e.charAt(0).toUpperCase() + e.slice(1);
}

// Fallback registry when /api/strategies is unavailable (matches control_api/services/strategies.py).
export const FALLBACK_STRATEGIES = [
  { id: 'maker', source: 'bot', name: 'Bot · Maker', color: 'var(--accent)', prefix: 'mm-' },
  { id: 'arb', source: 'bot', name: 'Bot · Arb', color: 'var(--chart2)', prefix: 'arb-' },
  { id: 'you', source: 'you', name: 'You', color: 'var(--muted)' },
];

/** The registry entry for a row/position: {source, strategy} -> {name, color}. */
export function sourceInfo(row, strategies) {
  const list = strategies && strategies.length ? strategies : FALLBACK_STRATEGIES;
  const source = row && row.source === 'you' ? 'you' : 'bot';
  if (source === 'you') return list.find((s) => s.id === 'you') || FALLBACK_STRATEGIES[2];
  const hit = row && row.strategy ? list.find((s) => s.id === row.strategy) : null;
  return hit || { id: 'bot', source: 'bot', name: 'Bot', color: 'var(--accent)' };
}

/** Source filter chips: All, Bot, You, then one chip per extra strategy (Arb, ...). `maker` is the default Bot. */
export function sourceChips(strategies) {
  const list = strategies && strategies.length ? strategies : FALLBACK_STRATEGIES;
  const chips = [
    { id: 'all', label: 'All' },
    { id: 'bot', label: 'Bot' },
    { id: 'you', label: 'You' },
  ];
  for (const s of list) {
    if (s.source === 'bot' && s.id !== 'maker') chips.push({ id: `strategy:${s.id}`, label: String(s.name || s.id).replace(/^Bot\s*[·-]\s*/, '') });
  }
  return chips;
}

export function matchesSource(row, chipId) {
  if (chipId === 'all') return true;
  const source = row.source === 'you' ? 'you' : 'bot';
  if (chipId === 'bot') return source === 'bot';
  if (chipId === 'you') return source === 'you';
  if (chipId.startsWith('strategy:')) return source === 'bot' && row.strategy === chipId.slice(9);
  return true;
}
