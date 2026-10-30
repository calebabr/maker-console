import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setToken } from '../services/api.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => {
  setToken(null);
  window.localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

const PERIODS = {
  periods: [
    { id: 'p1', name: 'Demo Period 1', current: false, started_at: '2026-01-01T00:00:00Z' },
    { id: 'p2', name: 'Demo Period 2', current: true, started_at: '2026-03-01T00:00:00Z' },
  ],
  cash_flows: [],
};

function setup() {
  return stubApi({
    'GET /api/status': STATUS,
    'GET /api/periods': PERIODS,
    'GET /api/periods/p2/summary': {},
    'POST /api/cash-flows': PERIODS,
  });
}

async function fill(user, amount) {
  await user.type(await screen.findByLabelText('Amount ($)'), amount);
}
async function submit(user) {
  await user.click(screen.getByRole('button', { name: /Add Entry/ }));
  await user.type(await screen.findByLabelText('6-digit code'), '123456');
  await user.click(screen.getByRole('button', { name: 'Add Cash Flow', exact: true }));
}

describe('cash flow date', () => {
  it('always sends ts (the date is required), defaulting to now', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderApp('/account');
    await fill(user, '50');
    expect(screen.getByText('Set the real date for past deposits')).toBeInTheDocument();
    await submit(user);
    await screen.findByText('Cash Flow Recorded');
    const post = calls.find((c) => c.key === 'POST /api/cash-flows');
    expect(post.body).toMatchObject({ kind: 'deposit', amount: 50 });
    expect(Number.isNaN(Date.parse(post.body.ts))).toBe(false);
  });

  it('sends an ISO ts for a past date and shows it in the confirm text', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderApp('/account');
    await fill(user, '50');
    fireEvent.change(screen.getByLabelText('When it happened'), { target: { value: '2026-02-10T09:30' } });
    expect(screen.queryByTestId('flow-when-current')).toBeNull();
    await user.click(screen.getByRole('button', { name: /Add Entry/ }));
    expect(await screen.findByText(/dated/)).toBeInTheDocument();
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Add Cash Flow', exact: true }));
    await screen.findByText('Cash Flow Recorded');
    const post = calls.find((c) => c.key === 'POST /api/cash-flows');
    expect(post.body.ts).toBe(new Date('2026-02-10T09:30').toISOString());
  });

  it('blocks a future time with an inline message', async () => {
    const user = userEvent.setup();
    setup();
    renderApp('/account');
    await fill(user, '50');
    fireEvent.change(screen.getByLabelText('When it happened'), { target: { value: '2999-01-01T00:00' } });
    expect(screen.getByTestId('flow-when-future')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add Entry/ })).toBeDisabled();
  });

  it('warns when the time falls inside the current period', async () => {
    const user = userEvent.setup();
    setup();
    renderApp('/account');
    await fill(user, '50');
    await screen.findByText('Demo Period 2 (current)');
    fireEvent.change(screen.getByLabelText('When it happened'), { target: { value: '2026-03-05T12:00' } });
    expect(screen.getByTestId('flow-when-current')).toHaveTextContent('This changes the capital base of the current period');
  });
});
