import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { render } from '@testing-library/react';
import PriorLogo from '../components/PriorLogo.jsx';
import { LAST_VISIT_KEY, readLastVisit, writeLastVisit } from '../utils/overview.js';
import { applyTheme, readTextSize, readTheme, THEME_IDS, TEXT_SIZE_KEY, THEME_KEY } from '../utils/theme.js';

const OLD = ['Ke', 'el'].join('');

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name !== 'test') walk(p, out);
    } else if (/\.(jsx?|css|html)$/.test(e.name)) out.push(p);
  }
  return out;
}

describe('rename to Prior', () => {
  it('no UI string in src contains the old capitalised name', () => {
    const src = path.resolve(__dirname, '..');
    const hits = walk(src).filter((f) => fs.readFileSync(f, 'utf8').includes(OLD));
    expect(hits).toEqual([]);
  });

  it('the logo renders in all three themes with theme variables only', () => {
    expect(THEME_IDS).toHaveLength(3);
    for (const id of THEME_IDS) {
      applyTheme(id);
      const { container, unmount } = render(<PriorLogo size={16} />);
      expect(container.querySelectorAll('path')).toHaveLength(3);
      expect(container.innerHTML).toMatch(/var\(--ok\)/);
      expect(container.innerHTML).not.toMatch(/#[0-9a-f]{3,6}/i);
      unmount();
    }
  });
});

describe('legacy storage keys', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it('reads the pre-rename theme and text size when the new keys are absent', () => {
    localStorage.setItem('keel.theme', 'teal');
    localStorage.setItem('keel.textSize', 'large');
    expect(readTheme()).toBe('teal');
    expect(readTextSize()).toBe('large');
  });

  it('the new key wins, and writes go to the new key', () => {
    localStorage.setItem('keel.theme', 'teal');
    localStorage.setItem(THEME_KEY, 'graphite');
    expect(readTheme()).toBe('graphite');
    applyTheme('midnight', { persist: true });
    expect(localStorage.getItem(THEME_KEY)).toBe('midnight');
    expect(localStorage.getItem(TEXT_SIZE_KEY)).toBeNull();
  });

  it('falls back to the old last-visit time and writes the new key', () => {
    const iso = '2026-10-01T12:00:00.000Z';
    localStorage.setItem('keel.lastVisit', iso);
    expect(readLastVisit()).toBe(iso);
    writeLastVisit('2026-10-02T12:00:00.000Z');
    expect(localStorage.getItem(LAST_VISIT_KEY)).toBe('2026-10-02T12:00:00.000Z');
    expect(readLastVisit()).toBe('2026-10-02T12:00:00.000Z');
  });
});
