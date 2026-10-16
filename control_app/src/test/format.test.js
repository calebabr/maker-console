import { describe, expect, it } from 'vitest';
import {
  ago, dateTime, formatAgo, formatMoney, formatPct, formatTime, formatTimeParts, formatUntil, money, pct, time,
  titleCase, until, utcTitle,
} from '../utils/format.js';

const M = '−'; // typographic minus

describe('formatMoney', () => {
  const table = [
    [12.3, undefined, '$12.30'],
    [0, undefined, '$0.00'],
    [1234.5, undefined, '$1,234.50'],
    [17.12, { sign: true }, '+$17.12'],
    [-1.4, { sign: true }, `${M}$1.40`],
    [-1.4, undefined, `${M}$1.40`],
    [0, { sign: true }, '$0.00'],
    [-0.001, { sign: true }, '$0.00'], // rounds to zero: no sign
    [0.004, { sign: true }, '$0.00'],
    [999, { compact: true }, '$999.00'],
    [1200, { compact: true }, '$1.2k'],
    [12000, { compact: true }, '$12k'],
    [-1234, { compact: true, sign: true }, `${M}$1.2k`],
    [2500000, { compact: true }, '$2.5M'],
    [1234.5, { compact: true, sign: true }, '+$1.2k'],
    [null, undefined, '—'],
    [undefined, undefined, '—'],
    [NaN, undefined, '—'],
    ['12', undefined, '—'],
  ];
  it.each(table)('formatMoney(%s, %j) -> %s', (v, opts, want) => {
    expect(formatMoney(v, opts)).toBe(want);
  });

  it('the legacy money() alias keeps its old output (ASCII hyphen) so existing pages do not change', () => {
    expect(money(-2)).toBe('-$2.00');
    expect(money(4.83, { sign: true })).toBe('+$4.83');
    expect(money(3, { digits: 0 })).toBe('$3');
    expect(money(null)).toBe('—');
  });
});

describe('formatPct', () => {
  it.each([
    [0.015, undefined, '+1.50%'],
    [-0.0142, undefined, `${M}1.42%`],
    [0.021, { sign: false, digits: 1 }, '2.1%'],
    [0, undefined, '0.00%'],
    [-0.00001, undefined, '0.00%'],
    [null, undefined, '—'],
  ])('formatPct(%s, %j) -> %s', (v, opts, want) => {
    expect(formatPct(v, opts)).toBe(want);
  });
  it('never says "pts" and the legacy pct() alias is unchanged', () => {
    expect(formatPct(0.05)).not.toMatch(/pts/);
    expect(pct(0.015)).toBe('+1.50%');
    expect(pct(-0.08, { digits: 0 })).toBe('-8%');
  });
});

describe('time formatters', () => {
  const iso = '2026-10-15T02:41:07Z';
  it('formats in the device time zone and carries the exact UTC in the title', () => {
    const p = formatTimeParts(iso);
    expect(p.text).toMatch(/^\d{1,2}:\d{2} (AM|PM)$/);
    expect(p.title).toBe('2026-10-15 02:41:07 UTC');
    expect(formatTime(iso)).toBe(p.text);
    expect(utcTitle(new Date(iso))).toBe('2026-10-15 02:41:07 UTC');
  });
  it('withDate adds the day', () => {
    expect(formatTime(iso, { withDate: true })).toMatch(/^[A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2} (AM|PM)$/);
  });
  it('matches the device clock (e.g. 9:41 PM when the zone is US Central)', () => {
    const d = new Date(iso);
    const want = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
    expect(formatTime(d)).toBe(want);
  });
  it('bad input -> dash with no tooltip', () => {
    expect(formatTimeParts(null)).toEqual({ text: '—', title: '' });
    expect(formatTime('nope')).toBe('—');
  });
  it('old names still work', () => {
    expect(time(iso)).toBe(formatTime(iso));
    expect(dateTime(iso)).toBe(formatTime(iso, { withDate: true }));
  });
  it('ago / until read naturally', () => {
    const now = Date.parse('2026-10-15T12:00:00Z');
    expect(formatAgo('2026-10-15T11:56:00Z', now)).toBe('4 min ago');
    expect(formatUntil('2026-10-15T12:05:00Z', now)).toBe('in 5 min');
    expect(formatUntil('2026-10-15T11:00:00Z', now)).toBe('due now');
    expect(ago).toBe(formatAgo);
    expect(until).toBe(formatUntil);
  });
});

describe('titleCase', () => {
  it.each([
    ['Demo series D', 'Demo Series D'],
    ['Median ahead at join', 'Median Ahead at Join'],
    ['Fills from the front', 'Fills From the Front'],
    ['Lost place by re-quoting', 'Lost Place by Re-Quoting'],
    ['Quotes / fills', 'Quotes / Fills'],
    ['Quotes/fills', 'Quotes/Fills'],
    ['collecting data', 'Collecting Data'],
    ['the end of a day', 'The End of a Day'],
    ['run budget for the day', 'Run Budget for the Day'],
    ['Mode (demo / live)', 'Mode (Demo / Live)'],
    ['7-/30-day p&l', '7-/30-Day P&L'],
    ['roi and ci', 'ROI and CI'],
    ['KXDEMOD', 'KXDEMOD'],
    ['KXDEMO-26OCT15 high', 'KXDEMO-26OCT15 High'],
    ['demo KXDEMOD series', 'Demo KXDEMOD Series'],
    ['2FA code', '2FA Code'],
    ['Run budget $40', 'Run Budget $40'],
    ['Plan A', 'Plan A'],
    ["don't stop", "Don't Stop"],
    ['  spaced   out ', '  Spaced   Out '],
    ['', ''],
  ])('%j -> %j', (input, want) => {
    expect(titleCase(input)).toBe(want);
  });
  it('is idempotent and tolerant of null', () => {
    const once = titleCase('lost place by re-quoting');
    expect(titleCase(once)).toBe(once);
    expect(titleCase(null)).toBe('');
    expect(titleCase(undefined)).toBe('');
  });
  it('leaves tokens that already carry capitals alone (iPhone, P&L, USD)', () => {
    expect(titleCase('iPhone USD P&L')).toBe('iPhone USD P&L');
  });
});
