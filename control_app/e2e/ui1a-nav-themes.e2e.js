import { test, expect } from '@playwright/test';
import { login, nav, noHorizontalScroll } from './helpers.js';

// UI-1a stage 2: Home, the Strategies hub and Arbitrage Controls in all three themes. For each, the theme is
// really applied (data-theme), the new tokens resolve to colours, text/UI contrast on the live computed styles
// meets WCAG (4.5 text, 3 for the strong border), and nothing overflows. Screenshots go to test-results/.

const SCREENS = [
  ['Home', (p) => nav(p, 'Home')],
  ['Strategies', (p) => nav(p, 'Strategies')],
  ['Arbitrage Controls', (p) => nav(p, 'Arbitrage Controls')],
];

for (const theme of ['midnight', 'teal', 'graphite']) {
  test(`${theme}: Home, Strategies hub and Arbitrage Controls render with readable tokens`, async ({ page }) => {
    await page.addInitScript((t) => {
      try { localStorage.setItem('prior.theme', t); } catch { /* ignore */ }
    }, theme);
    await login(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    for (const [label, go] of SCREENS) {
      await go(page);
      await page.waitForTimeout(400);
      const r = await page.evaluate(() => {
        const cs = getComputedStyle(document.documentElement);
        const v = (n) => cs.getPropertyValue(n).trim();
        const probe = document.createElement('span');
        document.body.appendChild(probe);
        const rgb = (c) => {
          probe.style.color = c;
          const m = getComputedStyle(probe).color.match(/[\d.]+/g).map(Number);
          return m.slice(0, 3);
        };
        const out = { vars: {}, rgb: {} };
        for (const n of ['--bg', '--surface', '--text', '--muted', '--border-strong', '--accent', '--danger']) {
          out.vars[n] = v(n);
          out.rgb[n] = v(n) ? rgb(v(n)) : null;
        }
        probe.remove();
        const stop = document.querySelector('header.appbar button[aria-label*="Stop" i]');
        out.stop = stop ? { color: getComputedStyle(stop).color, bg: getComputedStyle(stop).backgroundColor } : null;
        return out;
      });
      const lum = ([R, G, B]) => {
        const f = (c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
        return 0.2126 * f(R) + 0.7152 * f(G) + 0.0722 * f(B);
      };
      const ratio = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
      for (const n of ['--bg', '--text', '--muted', '--border-strong']) expect(r.rgb[n], `${theme}/${label}: ${n} resolves`).toBeTruthy();
      expect(ratio(r.rgb['--text'], r.rgb['--bg']), `${theme}/${label}: text on bg`).toBeGreaterThanOrEqual(4.5);
      expect(ratio(r.rgb['--muted'], r.rgb['--bg']), `${theme}/${label}: muted on bg`).toBeGreaterThanOrEqual(4.5);
      expect(ratio(r.rgb['--muted'], r.rgb['--surface']), `${theme}/${label}: muted on surface`).toBeGreaterThanOrEqual(4.5);
      expect(ratio(r.rgb['--border-strong'], r.rgb['--bg']), `${theme}/${label}: border-strong on bg`).toBeGreaterThanOrEqual(3);
      expect(r.stop, `${theme}/${label}: header Stop present`).toBeTruthy();
      await noHorizontalScroll(page);
      await page.screenshot({ path: `test-results/ui1a-nav-theme-${theme}-${label.replace(/\s+/g, '-').toLowerCase()}.png`, fullPage: true });
    }
  });
}
