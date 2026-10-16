import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

// Static guard for the Terminology sticky-filter layout (owner bug 2026-10-13). jsdom has no layout
// engine, so geometry is checked in e2e/terminology-layout.e2e.js; this pins the CSS contract.
const css = fs.readFileSync(path.resolve(process.cwd(), 'src/styles/help.css'), 'utf8');

function rule(selector) {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = css.match(new RegExp(`(^|\\n)${esc}\\s*\\{([^}]*)\\}`));
  expect(m, `rule ${selector} exists`).toBeTruthy();
  return m[2];
}

describe('Terminology sticky filter CSS', () => {
  it('filter is sticky below the shared sticky stack, opaque, full-bleed and above the entries', () => {
    const r = rule('.term-search');
    expect(r).toMatch(/position:\s*sticky/);
    expect(r).toMatch(/top:\s*var\(--sticky-top,\s*\d+px\)/);
    expect(r).toMatch(/background:\s*var\(--bg\)/);
    expect(r).toMatch(/margin:\s*0 calc\(-1 \* var\(--gutter\)\)/);
    const z = Number((r.match(/z-index:\s*(\d+)/) || [])[1]);
    expect(z).toBeGreaterThan(0);
    expect(z).toBeLessThan(15); // below the page title (15) and the app header (20)
  });

  it('entries scroll to just below header + page title + filter and never use negative margins', () => {
    const r = rule('.term');
    expect(r).toMatch(/scroll-margin-top:\s*calc\(var\(--sticky-top[^)]*\)\s*\+\s*var\(--term-filter-h[^)]*\)\s*\+\s*\d+px\)/);
    expect(r).not.toMatch(/margin:[^;]*-\d/);
  });

  it('the highlight is drawn inside the entry (inset bar), not with a border that needs negative margins', () => {
    const r = rule('.term.highlight');
    expect(r).toMatch(/box-shadow:\s*inset/);
  });
});
