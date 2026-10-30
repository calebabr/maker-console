import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import Empty from '../components/Empty.jsx';
import RangeSelect from '../components/RangeSelect.jsx';
import Skeleton from '../components/Skeleton.jsx';
import StrategyCard from '../components/StrategyCard.jsx';
import { DEFAULT_RANGE, RANGE_OPTIONS, rangeParams, validRange } from '../utils/range.js';

afterEach(() => vi.useRealTimers());

describe('range options', () => {
  it('maps ids to API params and has no "All" until the backend can give an all-time window', () => {
    expect(RANGE_OPTIONS.map((r) => r.label)).toEqual(['7 days', '30 days', 'This period']);
    expect(rangeParams('7d')).toEqual({ days: 7 });
    expect(rangeParams('current')).toEqual({ period: 'current' });
    expect(rangeParams('all')).toEqual({ days: 30 }); // unknown ids fall back to the default
    expect(validRange('nope')).toBe(DEFAULT_RANGE);
  });
});

describe('RangeSelect', () => {
  it('is a radio group; picking and arrow keys call onChange; selected is the only tab stop', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<RangeSelect value="30d" onChange={onChange} />);
    const radios = screen.getAllByRole('radio');
    expect(radios.map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'true', 'false']);
    expect(radios.map((r) => r.tabIndex)).toEqual([-1, 0, -1]);
    await user.click(radios[0]);
    expect(onChange).toHaveBeenLastCalledWith('7d');
    radios[1].focus();
    await user.keyboard('{ArrowRight}');
    expect(onChange).toHaveBeenLastCalledWith('current');
    await user.keyboard('{Home}');
    expect(onChange).toHaveBeenLastCalledWith('7d');
  });
});

describe('Empty', () => {
  it('says what and why, with an optional next step (link or button)', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <MemoryRouter>
        <Empty what="No settled fills in this period" why="Fills settle after their market closes." next={{ label: 'Show 30 days', onClick }} />
        <Empty what="Nothing yet" next={{ label: 'Open Activity', to: '/activity' }} />
      </MemoryRouter>,
    );
    expect(screen.getByText('No settled fills in this period')).toBeInTheDocument();
    expect(screen.getByText('Fills settle after their market closes.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show 30 days' }));
    expect(onClick).toHaveBeenCalled();
    expect(screen.getByRole('link', { name: 'Open Activity' })).toHaveAttribute('href', '/activity');
  });
});

describe('Skeleton', () => {
  it('reserves its height at once and shows after the delay', () => {
    vi.useFakeTimers();
    render(<Skeleton rows={2} figure />);
    const sk = screen.getByTestId('skeleton');
    expect(sk).toHaveAttribute('data-pending', 'true');
    expect(sk.querySelectorAll('.skeleton-row')).toHaveLength(2);
    expect(sk.querySelector('.skeleton-figure')).not.toBeNull();
    act(() => vi.advanceTimersByTime(250));
    expect(sk).toHaveAttribute('data-pending', 'false');
  });
});

describe('StrategyCard', () => {
  const S = {
    id: 'maker', shortName: 'Maker', color: 'var(--accent)', to: '/strategies/maker', state: 'running', mode: 'demo',
    stateText: 'Demo running', pnl: -1.4, roi: 0.046, openCost: 8.2, fills: 12, lastAction: null,
  };
  const wrap = (s) => render(<MemoryRouter><StrategyCard strategy={s} rangeText="30 days" /></MemoryRouter>);

  it('is one link with signed minus P&L, one-decimal return and plain open cost', () => {
    wrap(S);
    const card = screen.getByRole('link');
    expect(card).toHaveAttribute('href', '/strategies/maker');
    expect(within(card).getByText('Maker')).toBeInTheDocument();
    expect(within(card).getByText('Demo running')).toBeInTheDocument();
    expect(within(card).getByText('−$1.40')).toHaveClass('neg');
    expect(within(card).getByText('+4.6%')).toHaveClass('pos');
    expect(within(card).getByText('$8.20').className).not.toMatch(/pos|neg/);
    expect(within(card).getByText('No activity yet')).toBeInTheDocument();
  });

  it('a missing figure is a dash with the tooltip, never 0', () => {
    wrap({ ...S, pnl: null, roi: null, openCost: null, fills: null, state: 'unknown', stateText: 'Unknown' });
    const dashes = screen.getAllByText('—');
    expect(dashes).toHaveLength(4);
    for (const d of dashes) expect(d).toHaveAttribute('title', 'Not available yet');
  });
});
