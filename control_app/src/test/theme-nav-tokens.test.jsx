import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { THEME_IDS, THEMES } from '../utils/theme.js';

// UI-1a stage 2 tokens: --border-strong is the visible edge of inputs, selects and secondary buttons, so as a
// non-text mark it needs 3:1 (WCAG 1.4.11) on every ground it sits on. The --off (stopped / unknown) text rule
// uses --muted, which already has a 4.5:1 test in theme.test.jsx; the warn state must not rely on colour alone.
function lum(hex) {
  const n = parseInt(hex.slice(1), 16);
  const f = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f((n >> 16) & 255) + 0.7152 * f((n >> 8) & 255) + 0.0722 * f(n & 255);
}
const contrast = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);

describe.each(THEME_IDS)('stage 2 tokens: %s', (id) => {
  const c = THEMES[id].colors;
  it('defines borderStrong as a #rrggbb colour', () => {
    expect(c.borderStrong).toMatch(/^#[0-9a-f]{6}$/i);
  });
  for (const ground of ['bg', 'surface']) {
    it(`borderStrong on ${ground} >= 3`, () => {
      expect(contrast(c.borderStrong, c[ground])).toBeGreaterThanOrEqual(3);
    });
  }
  it('borderStrong on surface2 >= 3', () => {
    expect(contrast(c.borderStrong, c.surface2)).toBeGreaterThanOrEqual(3);
  });
  it('borderStrong is stronger than the plain hairline border', () => {
    expect(contrast(c.borderStrong, c.bg)).toBeGreaterThan(contrast(c.border, c.bg));
  });
  it('muted text (the --off rule) is >= 4.5 on every ground', () => {
    for (const g of ['bg', 'surface', 'surface2']) expect(contrast(c.muted, c[g])).toBeGreaterThanOrEqual(4.5);
  });
});

describe('warn state is not colour alone on Graphite', () => {
  it('nav.css switches the warn edge to dashed on the graphite theme', () => {
    const css = fs.readFileSync(path.resolve(__dirname, '../styles/nav.css'), 'utf8');
    expect(css).toMatch(/\[data-theme='graphite'\]\s*\{\s*--warn-edge:\s*dashed/);
    expect(css).toMatch(/\.tag-warn[^{]*\{[^}]*border-style:\s*var\(--warn-edge/);
  });
});
