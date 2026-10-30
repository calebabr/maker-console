import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import fs from 'node:fs';
import path from 'node:path';
import SubNav from '../components/SubNav.jsx';

const ITEMS = [
  { to: '/strategies/maker', label: 'Overview' },
  { to: '/strategies/maker/results', label: 'Results' },
  { to: '/strategies/maker/controls', label: 'Controls' },
];

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}
function setup(start = '/strategies/maker/results') {
  render(
    <MemoryRouter initialEntries={[start]}>
      <SubNav items={ITEMS} label="Maker views" />
      <Where />
    </MemoryRouter>,
  );
  return userEvent.setup();
}

describe('SubNav', () => {
  it('is a tablist of links; the current view is selected and the only tab stop', () => {
    setup();
    expect(screen.getByRole('tablist', { name: 'Maker views' })).toBeInTheDocument();
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual(['Overview', 'Results', 'Controls']);
    expect(tabs.map((t) => t.getAttribute('aria-selected'))).toEqual(['false', 'true', 'false']);
    expect(tabs.map((t) => t.tabIndex)).toEqual([-1, 0, -1]);
    expect(tabs[1]).toHaveAttribute('href', '/strategies/maker/results');
  });

  it('the first view is current on the strategy root path, not on its sub-pages', () => {
    setup('/strategies/maker');
    expect(screen.getAllByRole('tab').map((t) => t.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
  });

  it('ArrowRight and ArrowLeft move focus and wrap; Home and End jump', async () => {
    const user = setup();
    const [a, b, c] = screen.getAllByRole('tab');
    b.focus();
    await user.keyboard('{ArrowRight}');
    expect(c).toHaveFocus();
    await user.keyboard('{ArrowRight}');
    expect(a).toHaveFocus();
    await user.keyboard('{ArrowLeft}');
    expect(c).toHaveFocus();
    await user.keyboard('{Home}');
    expect(a).toHaveFocus();
    await user.keyboard('{End}');
    expect(c).toHaveFocus();
  });

  it('moving focus does not navigate; Enter and Space activate', async () => {
    const user = setup();
    const tabs = screen.getAllByRole('tab');
    tabs[1].focus();
    await user.keyboard('{ArrowRight}');
    expect(tabs[2]).toHaveFocus();
    expect(screen.getByTestId('where')).toHaveTextContent('/strategies/maker/results');
    await user.keyboard('{Enter}');
    expect(screen.getByTestId('where')).toHaveTextContent('/strategies/maker/controls');
    tabs[0].focus();
    await user.keyboard(' ');
    expect(screen.getByTestId('where').textContent).toBe('/strategies/maker');
  });

  it('every tab is declared at least 40 px tall (CSS token)', () => {
    // jsdom has no layout: assert the stylesheet carries the rule instead.
    const css = fs.readFileSync(path.resolve(__dirname, '../styles/nav.css'), 'utf8');
    expect(css).toMatch(/\.subnav-tab\s*\{[^}]*min-height:\s*var\(--tap\)/);
    expect(fs.readFileSync(path.resolve(__dirname, '../index.css'), 'utf8')).toMatch(/--tap:\s*40px/);
  });
});
