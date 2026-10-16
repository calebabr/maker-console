import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

const CONFIG = {
  config: { run_budget: 25, max_order: 8, poll_minutes: 10, cells: [[0.3, 0.45, 6, 12, "no"]] },
  schema: { run_budget: { type: 'number', min: 1, max: 500 }, max_order: { type: 'number', min: 1 }, poll_minutes: { type: 'integer', min: 1 } },
  ceilings: {},
};
const DEVICES = { count: 1, subscriptions: [{ host: 'web.push.apple.com', created_utc: '2026-10-07T12:00:00+00:00' }] };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Push Devices card edge cases', () => {
  it('shows a load error, no list, and a disabled Clear All when the list call fails', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG, 'GET /api/push/subscriptions': [500, { detail: 'boom' }] });
    renderApp('/settings');
    expect(await screen.findByText(/Could not load devices/)).toBeInTheDocument();
    expect(screen.queryByText('No devices registered.')).toBeNull();
    expect(screen.getByRole('button', { name: 'Clear all push devices' })).toBeDisabled();
  });

  it('keeps the list and reports an error when Clear All fails', async () => {
    const user = userEvent.setup();
    const calls = stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': CONFIG,
      'GET /api/push/subscriptions': DEVICES,
      'DELETE /api/push/subscriptions': [503, { detail: 'store_unavailable' }],
    });
    renderApp('/settings');
    await screen.findByText('web.push.apple.com');
    await user.click(screen.getByRole('button', { name: 'Clear all push devices' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Clear All' }));
    expect(await screen.findByText('Not Cleared')).toBeInTheDocument();
    expect(screen.getByText('web.push.apple.com')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Clear all push devices' })).toBeEnabled();
    expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(1);
  });

  it('singular wording for exactly one removed device', async () => {
    const user = userEvent.setup();
    let cleared = false;
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': CONFIG,
      'GET /api/push/subscriptions': () => (cleared ? { count: 0, subscriptions: [] } : DEVICES),
      'DELETE /api/push/subscriptions': () => {
        cleared = true;
        return { removed: 1 };
      },
    });
    renderApp('/settings');
    await screen.findByText('web.push.apple.com');
    await user.click(screen.getByRole('button', { name: 'Clear all push devices' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Clear All' }));
    expect(await screen.findByText('1 device removed.')).toBeInTheDocument();
  });

  it('a list failure does not break the rest of Settings', async () => {
    stubApi({ 'GET /api/status': STATUS, 'GET /api/config': CONFIG, 'GET /api/push/subscriptions': [503, { detail: 'store_unavailable' }] });
    renderApp('/settings');
    expect(await screen.findByLabelText(/run budget/i)).toBeInTheDocument();
  });
});

describe('Settings form lock edge cases', () => {
  it('unlocks the form again when the post-save reload fails', async () => {
    const user = userEvent.setup();
    let gets = 0;
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': () => {
        gets += 1;
        return gets > 1 ? [500, { detail: 'reload failed' }] : CONFIG;
      },
      'PUT /api/config': { config: { ...CONFIG.config, run_budget: 50 }, version: 2 },
      'GET /api/push/subscriptions': DEVICES,
    });
    renderApp('/settings');
    const field = await screen.findByLabelText(/run budget/i);
    await user.clear(field);
    await user.type(field, '50');
    await user.click(screen.getByRole('button', { name: /save/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    const btns = screen.getAllByRole('button', { name: 'Save' });
    await user.click(btns[btns.length - 1]);
    await waitFor(() => expect(gets).toBeGreaterThan(1));
    // Never stuck locked: either the field is usable, or the page shows the reload error instead of a form.
    await waitFor(() => {
      const f = screen.queryByLabelText(/run budget/i);
      expect(f === null || !f.disabled).toBe(true);
    });
    expect(screen.queryByText('Refreshing settings…')).toBeNull();
  });

  it('a rejected save (400) never locks the form', async () => {
    const user = userEvent.setup();
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/config': CONFIG,
      'PUT /api/config': [400, { detail: 'run_budget above ceiling' }],
      'GET /api/push/subscriptions': DEVICES,
    });
    renderApp('/settings');
    const field = await screen.findByLabelText(/run budget/i);
    await user.clear(field);
    await user.type(field, '50');
    await user.click(screen.getByRole('button', { name: /save/i }));
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    const btns = screen.getAllByRole('button', { name: 'Save' });
    await user.click(btns[btns.length - 1]);
    expect(await screen.findByText(/run_budget above ceiling/)).toBeInTheDocument();
    expect(screen.getByLabelText(/run budget/i)).toBeEnabled();
    expect(screen.queryByText('Refreshing settings…')).toBeNull();
  });
});
