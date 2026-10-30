import { describe, expect, it } from 'vitest';
import {
  DASH, NA_TITLE, formatInterval, formatMoney, formatRange, formatReturn, formatShare, formatWhen, sentenceCase,
  strategyShortName, titleCase,
} from '../utils/format.js';

const M = '−'; // typographic minus U+2212

describe('stage 2 number rules', () => {
  it('rendered money never uses an ASCII hyphen as the minus sign', () => {
    for (const v of [-0.5, -12, -1234.5, -99999]) {
      expect(formatMoney(v)).not.toMatch(/-/);
      expect(formatMoney(v, { sign: true })).toMatch(/^−\$/);
    }
  });

  it('zero is neutral: $0.00 with no sign, including values that round to zero', () => {
    expect(formatMoney(0, { sign: true })).toBe('$0.00');
    expect(formatMoney(-0.004, { sign: true })).toBe('$0.00');
    expect(formatReturn(0)).toBe('0.0%');
    expect(formatReturn(-0.00001)).toBe('0.0%');
  });

  it('a missing value is the dash placeholder, never 0 or blank', () => {
    expect(DASH).toBe('—');
    expect(NA_TITLE).toBe('Not available yet');
    for (const f of [formatMoney, formatReturn, formatShare]) {
      expect(f(null)).toBe(DASH);
      expect(f(undefined)).toBe(DASH);
      expect(f(NaN)).toBe(DASH);
    }
  });

  it('returns have one decimal, shares and rates none, intervals read "(lo to hi)"', () => {
    expect(formatReturn(0.046)).toBe('+4.6%');
    expect(formatReturn(-0.004)).toBe(`${M}0.4%`);
    expect(formatReturn(0.046, { sign: false })).toBe('4.6%');
    expect(formatShare(0.914)).toBe('91%');
    expect(formatInterval(-0.004, 0.051)).toBe(`(${M}0.4% to +5.1%)`);
    expect(formatInterval(null, 0.05)).toBe('');
    expect(formatReturn(0.046)).not.toMatch(/pts/);
  });
});

describe('stage 2 date rules (device time zone)', () => {
  const now = new Date(2026, 9, 30, 17, 40).getTime(); // Oct 30, 2026, 5:40 PM local
  const at = (y, mo, d, h = 17, mi = 19) => new Date(y, mo, d, h, mi).toISOString();

  it('today is the time, yesterday says so, this year is month and day, other years add the year', () => {
    expect(formatWhen(at(2026, 9, 30), now)).toMatch(/^5:19\sPM$/);
    expect(formatWhen(at(2026, 9, 29), now)).toMatch(/^Yesterday 5:19\sPM$/);
    expect(formatWhen(at(2026, 9, 20), now)).toBe('Oct 20');
    expect(formatWhen(at(2025, 9, 30), now)).toBe('Oct 30, 2025');
    expect(formatWhen(null, now)).toBe(DASH);
    expect(formatWhen('garbage', now)).toBe(DASH);
  });

  it('yesterday is by calendar day, not 24 hours', () => {
    expect(formatWhen(at(2026, 9, 29, 23, 59), new Date(2026, 9, 30, 0, 5).getTime())).toMatch(/^Yesterday 11:59\sPM$/);
  });

  it('ranges use "to", never a dash', () => {
    const r = formatRange(at(2026, 9, 1), at(2026, 9, 30), now);
    expect(r).toBe('Oct 1 to Oct 30');
    expect(r).not.toMatch(/[-–—]/);
    expect(formatRange(at(2026, 9, 1), null, now)).toBe('Oct 1 to now');
    expect(formatRange(at(2025, 11, 5), at(2026, 0, 3), now)).toBe('Dec 5, 2025 to Jan 3');
  });
});

describe('stage 2 label voice', () => {
  it('Title Case for titles, sentence case for sentences, short strategy names without the Bot prefix', () => {
    expect(titleCase('shadow estimate')).toBe('Shadow Estimate');
    expect(titleCase('all strategies')).toBe('All Strategies');
    expect(sentenceCase('save settings')).toBe('Save settings');
    expect(sentenceCase('')).toBe('');
    expect(strategyShortName('Bot · Maker')).toBe('Maker');
    expect(strategyShortName('Bot · Arb')).toBe('Arb');
    expect(strategyShortName('You')).toBe('You');
    expect(strategyShortName(null)).toBe('');
  });
});
