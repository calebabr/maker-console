import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Terminology from '../pages/Terminology.jsx';
import { setToken } from '../services/api.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

// S-29: a render error in one page must not take down the shell (Header + Stop).
vi.mock('../pages/Logs.jsx', () => ({
  default: function BrokenLogs() {
    throw new Error('boom');
  },
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setToken(null);
});

describe('S-29 malformed hash', () => {
  it('Terminology ignores a malformed percent-escape instead of throwing', () => {
    Element.prototype.scrollIntoView = vi.fn();
    expect(() =>
      render(
        <MemoryRouter initialEntries={['/terminology#%E0%A4%A']}>
          <Routes>
            <Route path="/terminology" element={<Terminology />} />
          </Routes>
        </MemoryRouter>,
      ),
    ).not.toThrow();
    expect(screen.getByRole('heading', { level: 1, name: 'Terminology' })).toBeInTheDocument();
    expect(document.querySelector('li.term.highlight')).toBeNull();
    delete Element.prototype.scrollIntoView;
  });

  it('the full app survives the malformed hash too', async () => {
    Element.prototype.scrollIntoView = vi.fn();
    stubApi({ 'GET /api/status': STATUS });
    renderApp('/terminology#%E0%A4%A');
    expect(await screen.findByRole('heading', { level: 1, name: 'Terminology' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /stop the bot/i })).toBeInTheDocument();
    delete Element.prototype.scrollIntoView;
  });
});

describe('S-29 error boundary', () => {
  it('a throwing page shows the fallback while Stop stays present and works', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'POST /api/bot/stop': { desired: 'stopped', kill_switch: true, cancelled: 0, errors: [] },
    });
    renderApp('/logs');
    const alert = await screen.findByText('This page could not be shown.');
    expect(within(alert.closest('[role="alert"]')).getByRole('link', { name: 'Reload' })).toHaveAttribute('href', '/');

    const stop = screen.getByRole('button', { name: /stop the bot/i });
    await user.click(stop);
    await user.click(await screen.findByRole('button', { name: 'Stop now' }));
    await waitFor(() => expect(calls.some((c) => c.key === 'POST /api/bot/stop')).toBe(true));
    expect(await screen.findByText('Stopped. Kill switch set.')).toBeInTheDocument();
  });

  it('navigating to another page clears the error', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const user = userEvent.setup();
    stubApi({ 'GET /api/status': STATUS, 'GET /api/history': { snapshots: [] } });
    renderApp('/logs');
    await screen.findByText('This page could not be shown.');
    await user.click(screen.getByRole('link', { name: /^home$/i }));
    await waitFor(() => expect(screen.queryByText('This page could not be shown.')).toBeNull());
    expect(screen.getAllByRole('button', { name: /stop the bot/i }).length).toBeGreaterThan(0);
  });
});
