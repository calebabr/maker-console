import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
import PullToRefresh from '../components/PullToRefresh.jsx';
import { registerRefresh } from '../services/refreshBus.js';

function Shell() {
  return (
    <div>
      <main className="content">
        <p data-testid="body">hi</p>
      </main>
      <PullToRefresh />
    </div>
  );
}
function touch(type, target, x, y) {
  const t = { clientX: x, clientY: y, identifier: 1, target };
  const ev = new Event(type, { bubbles: true, cancelable: true });
  ev.touches = type === 'touchend' ? [] : [t];
  fireEvent(target, ev);
}
async function pullTo(target, dy) {
  await act(async () => {
    touch('touchstart', target, 100, 100);
    touch('touchmove', target, 100, 100 + dy);
    touch('touchend', target, 100, 100 + dy);
  });
}

afterEach(() => {
  vi.useRealTimers();
  document.body.style.position = '';
  Object.defineProperty(window, 'scrollY', { value: 0, configurable: true });
});

describe('PullToRefresh', () => {
  it('refetches once past the threshold, flashes Updated, then resets', async () => {
    vi.useFakeTimers();
    const fn = vi.fn(() => Promise.resolve());
    const off = registerRefresh(fn);
    render(<Shell />);
    await pullTo(screen.getByTestId('body'), 200);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('status')).toHaveTextContent('Updated');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200);
    });
    expect(screen.queryByRole('status')).toBeNull();
    off();
  });

  it('does not refetch for a short pull and resets the indicator', async () => {
    const fn = vi.fn(() => Promise.resolve());
    const off = registerRefresh(fn);
    render(<Shell />);
    await pullTo(screen.getByTestId('body'), 60);
    expect(fn).not.toHaveBeenCalled();
    expect(screen.queryByRole('status')).toBeNull();
    off();
  });

  it('does nothing when scrolled away from the top', async () => {
    Object.defineProperty(window, 'scrollY', { value: 40, configurable: true });
    const fn = vi.fn(() => Promise.resolve());
    const off = registerRefresh(fn);
    render(<Shell />);
    await pullTo(screen.getByTestId('body'), 200);
    expect(fn).not.toHaveBeenCalled();
    off();
  });

  it('does nothing while a modal is open', async () => {
    const fn = vi.fn(() => Promise.resolve());
    const off = registerRefresh(fn);
    render(
      <>
        <Shell />
        <div role="dialog" />
      </>,
    );
    await pullTo(screen.getByTestId('body'), 200);
    expect(fn).not.toHaveBeenCalled();
    off();
  });

  it('never gets stuck: a hung refetch is released after 10 s', async () => {
    vi.useFakeTimers();
    const off = registerRefresh(() => new Promise(() => {}));
    render(<Shell />);
    await pullTo(screen.getByTestId('body'), 200);
    expect(screen.getByRole('status')).toHaveTextContent('Refreshing');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(11500);
    });
    expect(screen.queryByRole('status')).toBeNull();
    off();
  });
});
