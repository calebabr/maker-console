import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { useState } from 'react';
import TabScroller from '../components/analytics/TabScroller.jsx';
import RangeSelect from '../components/RangeSelect.jsx';
import Empty from '../components/Empty.jsx';
import { TABS } from '../pages/Analytics.jsx';
import { DAY_OPTIONS } from '../utils/analytics.js';

function Harness() {
  const [v, setV] = useState('summary');
  return <TabScroller tabs={TABS} value={v} onChange={setV} label="Analytics sections" />;
}

describe('TabScroller', () => {
  it('has one Tab stop and moves focus with arrows, Home and End (wrapping)', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1, -1, -1, -1, -1]);
    await user.tab();
    expect(tabs[0]).toHaveFocus();
    await user.keyboard('{ArrowLeft}');
    expect(tabs[5]).toHaveFocus();
    await user.keyboard('{ArrowRight}');
    expect(tabs[0]).toHaveFocus();
    await user.keyboard('{End}');
    expect(tabs[5]).toHaveFocus();
    await user.keyboard('{Home}');
    expect(tabs[0]).toHaveFocus();
  });

  it('selects with Enter and Space, not by moving focus', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.tab();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Summary' })).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{Enter}');
    expect(screen.getByRole('tab', { name: 'Edge' })).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{ArrowRight}{ }');
    expect(screen.getByRole('tab', { name: 'Execution' })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('Analytics range control', () => {
  it('offers the day windows as a radio group and reports the pick', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const options = DAY_OPTIONS.map((d) => ({ id: String(d), label: `${d} days` }));
    render(<RangeSelect label="Window" value="30" options={options} onChange={onChange} />);
    expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['7 days', '30 days', '90 days', '180 days']);
    expect(screen.getByRole('radio', { name: '30 days' })).toHaveAttribute('aria-checked', 'true');
    await user.click(screen.getByRole('radio', { name: '90 days' }));
    expect(onChange).toHaveBeenCalledWith('90');
  });
});

describe('Empty (filters)', () => {
  it('offers a reset action', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<MemoryRouter><Empty what="Nothing matches these filters." next={{ label: 'Show all', onClick }} /></MemoryRouter>);
    await user.click(screen.getByRole('button', { name: 'Show all' }));
    expect(onClick).toHaveBeenCalled();
  });
});
