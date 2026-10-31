import { readFileSync } from 'node:fs';
import { resetNavTrail } from '../hooks/useNavTrail.js';
import { resolve } from 'node:path';
import { describe, expect, it, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ROUTE_TITLES, TAB_ROUTES, parentForPath } from '../utils/routes.js';
import { setToken } from '../services/api.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

const TAB_PATHS = TAB_ROUTES.map((t) => t.path);
beforeEach(() => { setToken(null); resetNavTrail(); });

function open(path) {
  stubApi({ 'GET /api/status': STATUS, 'GET /api/history': { snapshots: [] } });
  return renderApp(path);
}

describe('route parents', () => {
  it('every ROUTE_TITLES entry has a defined parent or is a bottom tab', () => {
    for (const r of ROUTE_TITLES) {
      if (TAB_PATHS.includes(r.path)) expect(r.parent, r.path).toBeUndefined();
      else expect(ROUTE_TITLES.some((p) => p.path === r.parent), `${r.path} parent`).toBe(true);
    }
  });
  it('maps More pages to More and strategy pages to Strategies', () => {
    expect(parentForPath('/health')).toBe('/more');
    expect(parentForPath('/strategies/maker/results')).toBe('/strategies');
    expect(parentForPath('/more')).toBeNull();
  });
});

describe('back controls', () => {
  const linkTo = (href) => document.querySelector(`main a[href="${href}"]`);
  const waitLink = (href) => waitFor(() => {
    const el = linkTo(href);
    expect(el).not.toBeNull();
    return el;
  });

  for (const p of ['/health', '/settings', '/account', '/alerts', '/mode', '/server', '/logs', '/terminology']) {
    it(`${p}: Back + "Up to More" in the title bar (>= 40 px class), reachable (not aria-hidden)`, () => {
      open(p);
      const back = screen.getByRole('link', { name: 'Back' });
      const up = screen.getByRole('link', { name: 'Up to More' });
      expect(back).toHaveClass('back-btn');
      expect(up).toHaveClass('back-up');
      expect(up).toHaveAttribute('href', '/more');
      expect(back.closest('[aria-hidden="true"]')).toBeNull();
      // direct load: no in-app history, so Back falls back to the parent
      expect(back).toHaveAttribute('href', '/more');
    });
  }
  for (const p of TAB_PATHS) {
    it(`${p}: neither control on a top-level tab`, () => {
      open(p);
      expect(screen.queryByTestId('back-link')).toBeNull();
      expect(screen.queryByTestId('up-link')).toBeNull();
    });
  }
  it('strategy and performance children: breadcrumb goes up to the parent', () => {
    open('/strategies/maker/results');
    expect(screen.getByRole('link', { name: 'Up to Strategies' })).toHaveAttribute('href', '/strategies');
    expect(screen.getAllByTestId('back-link')).toHaveLength(1); // no extra row in the sub-nav
  });
  it('Stop stays visible next to the controls', () => {
    open('/health');
    expect(screen.getByRole('button', { name: /stop the bot/i })).toBeInTheDocument();
  });

  it('Back returns to the previously visited page (history), not the parent', async () => {
    const user = userEvent.setup();
    open('/');
    await user.click(await waitLink('/strategies/maker'));
    const back = await screen.findByRole('link', { name: 'Back' });
    expect(back).toHaveAttribute('href', '/');
    await user.click(back);
    await waitFor(() => expect(screen.queryByTestId('back-link')).toBeNull()); // Home is a tab
    expect(document.title).toMatch(/^Home/);
  });

  it('the breadcrumb goes to the parent even when history points elsewhere', async () => {
    const user = userEvent.setup();
    open('/');
    await user.click(await waitLink('/strategies/maker'));
    await user.click(await screen.findByRole('link', { name: 'Up to Strategies' }));
    await waitFor(() => expect(document.title).toMatch(/^Strategies/));
  });

  it('view switches replace history: Back leaves the strategy page', async () => {
    const user = userEvent.setup();
    open('/strategies');
    await user.click(await waitLink('/strategies/maker'));
    await user.click(await screen.findByRole('tab', { name: 'Results' }));
    await user.click(screen.getByRole('tab', { name: 'Controls' }));
    await user.click(screen.getByRole('link', { name: 'Back' }));
    await waitFor(() => expect(screen.queryByRole('tab', { name: 'Controls' })).toBeNull());
    expect(document.title).toMatch(/^Strategies/);
  });

  it('direct load of a strategy view: Back falls back to the parent; view switches add no entry', async () => {
    const user = userEvent.setup();
    open('/strategies/maker');
    await user.click(await screen.findByRole('tab', { name: 'Results' }));
    await user.click(screen.getByRole('tab', { name: 'Controls' }));
    const back = screen.getByRole('link', { name: 'Back' });
    expect(back).toHaveAttribute('href', '/strategies');
    await user.click(back);
    await waitFor(() => expect(screen.queryByRole('tab', { name: 'Controls' })).toBeNull());
  });
});

describe('iOS status bar', () => {
  it('uses the solid "black" status bar style, not black-translucent', () => {
    const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf-8');
    expect(html).toMatch(/apple-mobile-web-app-status-bar-style" content="black"/);
    expect(html).not.toMatch(/black-translucent" \/>/);
  });
});
