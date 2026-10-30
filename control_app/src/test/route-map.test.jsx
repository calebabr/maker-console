import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { AppShell } from '../App.jsx';
import { setToken } from '../services/api.js';
import {
  LEGACY_REDIRECTS, MORE_GROUPS, MORE_ROUTES, PERFORMANCE_ANALYSIS_PATH, ROUTE_TITLES, TAB_ROUTES, redirectTarget,
  strategyPath, tabForPath, titleForPath,
} from '../utils/routes.js';
import { stubApi, STATUS } from './helpers.jsx';

const realMatchMedia = window.matchMedia;
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

let seen = '';
function Probe() {
  const l = useLocation();
  seen = `${l.pathname}${l.search}${l.hash}`;
  return null;
}
function renderAt(entry) {
  setToken('tok');
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Probe />
      <AppShell />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  setToken(null);
  stubApi({ 'GET /api/status': STATUS, 'GET /api/history': { snapshots: [] }, 'GET /api/changelog': [200, []] });
});
afterEach(() => {
  window.matchMedia = realMatchMedia;
  vi.unstubAllGlobals();
});

describe('legacy redirects', () => {
  it('maps /markets and /arb (with or without a trailing slash)', () => {
    expect(LEGACY_REDIRECTS.map((r) => [r.from, r.to])).toEqual([
      ['/markets', '/strategies/maker/controls'],
      ['/arb', '/strategies/arb'],
    ]);
    expect(redirectTarget('/markets')).toBe('/strategies/maker/controls');
    expect(redirectTarget('/markets/')).toBe('/strategies/maker/controls');
    expect(redirectTarget('/arb')).toBe('/strategies/arb');
    expect(redirectTarget('/health')).toBeNull();
  });

  it.each([
    ['/markets', '/strategies/maker/controls'],
    ['/markets?series=KXDEMOA', '/strategies/maker/controls?series=KXDEMOA'],
    ['/markets?series=KXDEMOA#pause', '/strategies/maker/controls?series=KXDEMOA#pause'],
    ['/arb', '/strategies/arb'],
    ['/arb?from=notification#opportunities', '/strategies/arb?from=notification#opportunities'],
    ['/arb#stop', '/strategies/arb#stop'],
  ])('%s -> %s (query and hash kept)', async (from, to) => {
    renderAt(from);
    await waitFor(() => expect(seen).toBe(to));
  });

  it('an unknown strategy id goes to the hub', async () => {
    renderAt('/strategies/nope/controls');
    await waitFor(() => expect(seen).toBe('/strategies'));
  });

  it('the reserved Analysis slot has no page yet and falls back to the Performance hub', async () => {
    expect(PERFORMANCE_ANALYSIS_PATH).toBe('/performance/analysis');
    expect(titleForPath(PERFORMANCE_ANALYSIS_PATH)).toBeNull();
    expect(TAB_ROUTES.some((t) => t.path === PERFORMANCE_ANALYSIS_PATH)).toBe(false);
    renderAt(PERFORMANCE_ANALYSIS_PATH);
    await waitFor(() => expect(seen).toBe('/performance'));
  });

  it('an unknown path goes home', async () => {
    renderAt('/does-not-exist');
    await waitFor(() => expect(seen).toBe('/'));
  });
});

describe('route map', () => {
  it('covers every strategy view for Maker and Arbitrage', () => {
    for (const id of ['maker', 'arb']) {
      for (const v of ['overview', 'results', 'controls']) {
        expect(ROUTE_TITLES.map((r) => r.path)).toContain(strategyPath(id, v));
      }
    }
    expect(strategyPath('maker')).toBe('/strategies/maker');
    expect(strategyPath('arb', 'results')).toBe('/strategies/arb/results');
    expect(titleForPath('/strategies/maker/controls')).toBe('Maker');
    expect(titleForPath('/strategies/arb')).toBe('Arbitrage');
  });

  it('tabForPath lights the right tab and never More for moved pages', () => {
    const cases = {
      '/': 'home',
      '/strategies': 'strategies',
      '/strategies/maker/results': 'strategies',
      '/markets': 'strategies',
      '/arb': 'strategies',
      '/performance': 'performance',
      '/performance/periods': 'performance',
      '/analytics': 'performance',
      '/shadow': 'performance',
      '/activity': 'activity',
      '/more': 'more',
      '/health': 'more',
      '/server': 'more',
      '/mode': 'more',
      '/logs': 'more',
      '/account': 'more',
      '/settings': 'more',
      '/alerts': 'more',
      '/terminology': 'more',
      '/nope': null,
    };
    for (const [p, tab] of Object.entries(cases)) expect(tabForPath(p), p).toBe(tab);
    expect(tabForPath('/analytics/')).toBe('performance');
  });

  it('every ROUTE_TITLES path is reachable (no redirect) and every More item has a title', async () => {
    for (const { path } of ROUTE_TITLES) {
      const { unmount } = renderAt(path);
      await waitFor(() => expect(seen, path).toBe(path));
      unmount();
    }
    for (const r of MORE_ROUTES) expect(titleForPath(r.path), r.path).toBe(r.label);
    expect(MORE_GROUPS.map((g) => g.label)).toEqual(['System', 'You', 'Help']);
  });
});

describe('Stop stays in the header on every route', () => {
  const paths = [...ROUTE_TITLES.map((r) => r.path), '/markets', '/arb', PERFORMANCE_ANALYSIS_PATH, '/login'];
  for (const wide of [false, true]) {
    it(`${wide ? 'wide (sidebar)' : 'phone (tab bar)'}: header Stop is present and outside the nav`, async () => {
      setViewport({ wide });
      for (const p of paths) {
        const { unmount } = renderAt(p);
        const header = document.querySelector('header.appbar');
        expect(header, p).not.toBeNull();
        const stop = await within(header).findByRole('button', { name: /stop the bot/i });
        expect(stop, p).toBeVisible();
        expect(document.querySelector('nav[aria-label="Main"]').contains(stop), p).toBe(false);
        expect(stop.closest('[aria-hidden="true"]'), p).toBeNull();
        unmount();
      }
    });
  }

  it('the nav shows five tabs on a phone', () => {
    setViewport({ wide: false });
    renderAt('/strategies');
    const links = within(screen.getByRole('navigation', { name: 'Main' })).getAllByRole('link');
    expect(links).toHaveLength(5);
    expect(links.find((l) => l.classList.contains('active')).textContent).toBe('Strategies');
  });
});
