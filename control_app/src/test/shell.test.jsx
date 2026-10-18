import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fs from 'node:fs';
import path from 'node:path';
import { setToken } from '../services/api.js';
import { titleCase } from '../utils/format.js';
import { APP_NAME, MORE_ROUTES, ROUTE_TITLES, TAB_ROUTES } from '../utils/routes.js';
import { badgeStyles } from '../utils/theme.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';
const OLD_NAME = ['Long', 'shot'].join('');   // the retired app name, never spelled out in the repo

const realMatchMedia = window.matchMedia;

// matchMedia stub: `wide` makes (min-width: 1024px) match, like a desktop browser window.
function setViewport({ wide }) {
  window.matchMedia = (query) => ({
    matches: /min-width:\s*(\d+)px/.test(query) ? wide && Number(RegExp.$1) <= 1280 : false,
    media: query,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const base = () =>
  stubApi({
    'GET /api/status': STATUS,
    'GET /api/history': { snapshots: [] },
    'GET /api/changelog': [200, []],
  });

beforeEach(() => {
  setToken(null);
  localStorage.clear();
});
afterEach(() => {
  window.matchMedia = realMatchMedia;
  vi.unstubAllGlobals();
});

describe('bottom tabs (phones)', () => {
  it('are Overview, Analytics, Activity, Markets, More in that order; Health is not a tab', async () => {
    setViewport({ wide: false });
    base();
    renderApp('/');
    const main = screen.getByRole('navigation', { name: 'Main' });
    expect(main).toHaveClass('tabbar');
    const links = within(main).getAllByRole('link');
    expect(links.map((l) => l.textContent)).toEqual(['Overview', 'Analytics', 'Activity', 'Markets', 'More']);
    expect(links.map((l) => l.getAttribute('href'))).toEqual(['/', '/analytics', '/activity', '/markets', '/more']);
    expect(within(main).queryByText('Health')).toBeNull();
    expect(TAB_ROUTES.map((t) => t.label)).toEqual(['Overview', 'Analytics', 'Activity', 'Markets', 'More']);
  });

  it('marks the current tab, and More while on a page that lives under More', async () => {
    setViewport({ wide: false });
    base();
    renderApp('/health');
    const main = screen.getByRole('navigation', { name: 'Main' });
    expect(within(main).getByRole('link', { name: 'More' })).toHaveClass('active');
    expect(within(main).getByRole('link', { name: 'Overview' })).not.toHaveClass('active');
  });

  it('has no sidebar below 1024 px', () => {
    setViewport({ wide: false });
    base();
    renderApp('/');
    expect(document.querySelector('.sidebar')).toBeNull();
  });
});

describe('More page', () => {
  it('lists every page that is not a tab, in the contract order, plus Sign Out', async () => {
    setViewport({ wide: false });
    base();
    renderApp('/more');
    const nav = await screen.findByRole('navigation', { name: 'More pages' });
    const links = within(nav).getAllByRole('link');
    expect(links.map((l) => l.getAttribute('href'))).toEqual([
      '/health', '/settings', '/account', '/alerts', '/mode', '/server', '/logs', '/arb', '/shadow', '/terminology',
    ]);
    expect(links.map((l) => within(l).getByText(/./, { selector: '.menu-label' }).textContent)).toEqual(
      MORE_ROUTES.map((r) => r.label),
    );
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument();
  });

  it('a menu row opens its page', async () => {
    setViewport({ wide: false });
    base();
    const user = userEvent.setup();
    renderApp('/more');
    await user.click(await screen.findByRole('link', { name: 'Alerts' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Alerts' })).toBeInTheDocument();
  });

  it.each(['/more', '/account', '/alerts'])('%s renders and keeps the header Stop', async (p) => {
    setViewport({ wide: false });
    base();
    renderApp(p);
    const header = document.querySelector('header.appbar');
    expect(within(header).getByRole('button', { name: /stop the bot/i })).toBeVisible();
    expect(await screen.findAllByRole('heading', { level: 1 })).toHaveLength(1);
  });
});

describe('sidebar (>= 1024 px)', () => {
  it('replaces the tabs with a sidebar holding the same entries', async () => {
    setViewport({ wide: true });
    base();
    renderApp('/');
    const side = screen.getByRole('navigation', { name: 'Main' });
    expect(side).toHaveClass('sidebar');
    expect(document.querySelector('.tabbar')).toBeNull();
    const links = within(side).getAllByRole('link');
    expect(links.map((l) => l.textContent)).toEqual([
      'Overview', 'Analytics', 'Activity', 'Markets',
      'Health', 'Settings', 'Account', 'Alerts', 'Mode', 'Server', 'Logs', 'Arbitrage', 'Shadow Estimate', 'Terminology',
    ]);
    expect(within(side).getByRole('link', { name: 'Overview' })).toHaveClass('active');
    expect(within(side).getByRole('button', { name: /sign out/i })).toBeInTheDocument();
  });

  it('navigates, and Stop stays in the header and works', async () => {
    setViewport({ wide: true });
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/history': { snapshots: [] },
      'GET /api/changelog': [200, []],
      'GET /api/markets': { markets: [] },
      'POST /api/bot/stop': { desired: 'stopped', kill_switch: true, cancelled: 2, errors: [] },
    });
    const user = userEvent.setup();
    renderApp('/');
    await user.click(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Markets' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Markets' })).toBeInTheDocument();
    const header = document.querySelector('header.appbar');
    await user.click(within(header).getByRole('button', { name: /stop the bot/i }));
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    expect(await screen.findByText('Stopped. Kill switch set.')).toBeInTheDocument();
  });
});

describe('header', () => {
  it('shows the Keel lockup with the logo and no old name', async () => {
    setViewport({ wide: false });
    base();
    renderApp('/');
    const header = document.querySelector('header.appbar');
    expect(within(header).getByText(APP_NAME)).toBeInTheDocument();
    expect(within(header).getByTestId('keel-logo')).toBeInTheDocument();
    expect(header.textContent).not.toMatch(new RegExp(OLD_NAME));
    await waitFor(() => expect(within(header).getByRole('button', { name: /stop the bot/i })).toBeEnabled());
  });

  it('page title bar carries the matching nav icon', async () => {
    setViewport({ wide: false });
    base();
    renderApp('/markets');
    const bar = screen.getByTestId('page-title');
    expect(bar.querySelector('.page-title-icon svg')).not.toBeNull();
    expect(bar.querySelector('.page-title-accent')).not.toBeNull();
  });
});

describe('Title Case labels (item 21) across the shell', () => {
  it('every tab, sidebar, More, route and badge label is already Title Case', async () => {
    const labels = [
      ...TAB_ROUTES.map((t) => t.label),
      ...MORE_ROUTES.map((r) => r.label),
      ...ROUTE_TITLES.map((r) => r.title),
      ...Object.values(badgeStyles).map((b) => b.label),
      'Show Password',
      'Got It',
      'Sign Out',
      '2FA Code',
    ];
    for (const l of labels) expect(titleCase(l), l).toBe(l);
    // No label starts with a lowercase letter.
    for (const l of labels) expect(l[0], l).toBe(l[0].toUpperCase());
  });

  it('rendered tab and menu labels start with a capital', async () => {
    setViewport({ wide: false });
    base();
    renderApp('/more');
    await screen.findByRole('navigation', { name: 'More pages' });
    const rendered = [
      ...[...document.querySelectorAll('.tab-label')].map((n) => n.textContent),
      ...[...document.querySelectorAll('.menu-label')].map((n) => n.textContent),
    ];
    expect(rendered.length).toBeGreaterThanOrEqual(14);
    for (const t of rendered) expect(/^[A-Z]/.test(t), t).toBe(true);
  });
});

// Walk src/ (not tests) for text files.
function sourceFiles(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name !== 'test') out.push(...sourceFiles(p));
    } else if (/\.(jsx?|css|html|json|svg)$/.test(e.name)) out.push(p);
  }
  return out;
}

describe('rename to Keel (item 28)', () => {
  const root = process.cwd();
  it('the retired app name is not left in src/', () => {
    const hits = sourceFiles(path.join(root, 'src'))
      .filter((f) => new RegExp(`\\b${OLD_NAME}\\b`).test(fs.readFileSync(f, 'utf8')))
      .map((f) => path.relative(root, f));
    expect(hits).toEqual([]);
  });

  it('manifest, html title and apple title say Keel', () => {
    const cfg = fs.readFileSync(path.join(root, 'vite.config.js'), 'utf8');
    expect(cfg).toMatch(/name:\s*'Keel'/);
    expect(cfg).toMatch(/short_name:\s*'Keel'/);
    expect(cfg).not.toMatch(new RegExp(OLD_NAME));
    expect(cfg).toMatch(/display:\s*'standalone'/);
    expect(cfg).toMatch(/icon-192\.png/);
    expect(cfg).toMatch(/icon-512\.png/);
    expect(cfg).toMatch(/purpose:\s*'maskable'/);
    const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
    expect(html).toMatch(/<title>Keel<\/title>/);
    expect(html).toMatch(/apple-mobile-web-app-title"\s+content="Keel"/);
    expect(html).not.toMatch(new RegExp(OLD_NAME));
    expect(html).toMatch(/theme-color" content="#0f141b"/);
  });

  it('icon files exist and the SVG icons use the Keel G colours', () => {
    const pub = path.join(root, 'public');
    for (const f of ['icon-192.png', 'icon-512.png', 'maskable-512.png', 'apple-touch-icon.png', 'icon.svg', 'favicon.svg']) {
      expect(fs.existsSync(path.join(pub, f)), f).toBe(true);
    }
    for (const f of ['icon.svg', 'favicon.svg']) {
      const svg = fs.readFileSync(path.join(pub, f), 'utf8');
      expect(svg).toContain('M9 24 H39'); // horizon
      expect(svg).toContain('M18 24 L24 38 L30 24 Z'); // keel
      expect(svg).toContain('#4ea1ff'); // accent
      expect(svg).toContain('#3fb97a'); // success
    }
    const png = fs.readFileSync(path.join(pub, 'icon-512.png'));
    expect(png.readUInt32BE(16)).toBe(512); // IHDR width
    expect(png.readUInt32BE(20)).toBe(512);
  });

  it('document.title is "<Page> · Keel"', async () => {
    setViewport({ wide: false });
    base();
    renderApp('/more');
    await screen.findByRole('navigation', { name: 'More pages' });
    expect(document.title).toBe('More · Keel');
  });
});
