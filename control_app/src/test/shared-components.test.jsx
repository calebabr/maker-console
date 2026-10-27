import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useRef, useState } from 'react';
import Chip from '../components/Chip.jsx';
import FoldCard from '../components/FoldCard.jsx';
import Popover from '../components/Popover.jsx';
import Time from '../components/Time.jsx';
import WhatsNew, { SEEN_KEY, compareVersions, unseenEntries } from '../components/WhatsNew.jsx';
import WakingBanner from '../components/WakingBanner.jsx';
import { request, setToken, SLOW_MS } from '../services/api.js';
import { writeCache } from '../services/cache.js';
import { kalshiMarketUrl } from '../utils/kalshi.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => {
  setToken(null);
  localStorage.clear();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('FoldCard', () => {
  it('is folded by default: summary visible, body not rendered; tap opens and closes', async () => {
    const user = userEvent.setup();
    render(
      <FoldCard title="KXDEMOD" summary={<Chip tone="ok">On Track</Chip>}>
        <p>The details</p>
      </FoldCard>,
    );
    const btn = screen.getByRole('button', { name: /KXDEMOD/ });
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('On Track')).toBeInTheDocument();
    expect(screen.queryByText('The details')).toBeNull();
    await user.click(btn);
    expect(btn).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('The details')).toBeInTheDocument();
    expect(btn).toHaveAttribute('aria-controls', document.querySelector('.fold-body').id);
    await user.click(btn);
    expect(screen.queryByText('The details')).toBeNull();
  });

  it('defaultOpen opens it; controlled mode follows `open` and reports changes', async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <FoldCard title="A" defaultOpen>
        body-a
      </FoldCard>,
    );
    expect(screen.getByText('body-a')).toBeInTheDocument();
    const onOpenChange = vi.fn();
    rerender(
      <FoldCard title="A" open={false} onOpenChange={onOpenChange}>
        body-a
      </FoldCard>,
    );
    expect(screen.queryByText('body-a')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'A' }));
    expect(onOpenChange).toHaveBeenCalledWith(true);
    expect(screen.queryByText('body-a')).toBeNull(); // stays shut until the parent opens it
  });

  it('actions beside the header never toggle the card', async () => {
    const user = userEvent.setup();
    const pause = vi.fn();
    render(
      <FoldCard title="Series" actions={<button onClick={pause}>Pause</button>}>
        x
      </FoldCard>,
    );
    await user.click(screen.getByRole('button', { name: 'Pause' }));
    expect(pause).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Series' })).toHaveAttribute('aria-expanded', 'false');
  });
});

describe('Chip', () => {
  it.each(['ok', 'warn', 'danger', 'muted', 'accent'])('renders the %s tone', (tone) => {
    render(<Chip tone={tone}>Label</Chip>);
    expect(screen.getByText('Label')).toHaveClass(`tag-${tone}`);
    expect(screen.getByText('Label')).toHaveAttribute('data-tone', tone);
  });
  it('unknown tone falls back to muted and a title shows as a tooltip', () => {
    render(
      <Chip tone="purple" title="why">
        X
      </Chip>,
    );
    expect(screen.getByText('X')).toHaveClass('tag-muted');
    expect(screen.getByText('X')).toHaveAttribute('title', 'why');
  });
});

describe('Time', () => {
  it('renders local time with the exact UTC in the tooltip and a machine-readable dateTime', () => {
    render(<Time value="2026-10-15T02:41:07Z" />);
    const el = document.querySelector('time');
    expect(el.textContent).toMatch(/^\d{1,2}:\d{2} (AM|PM)$/);
    expect(el).toHaveAttribute('title', '2026-10-15 02:41:07 UTC');
    expect(el).toHaveAttribute('datetime', '2026-10-15T02:41:07.000Z');
  });
  it('shows a dash for nothing', () => {
    render(<Time value={null} />);
    expect(document.querySelector('time')).toBeNull();
    expect(document.body.textContent).toBe('—');
  });
});

describe('kalshiMarketUrl', () => {
  it('builds the public market link', () => {
    expect(kalshiMarketUrl('KXDEMOD-26OCT15-T84')).toBe('https://kalshi.com/markets/kxdemod-26oct15-t84');
  });
  it.each([null, undefined, '', '   ', 'a b', 'x/../y', '<script>', 'javascript:alert(1)'])('rejects %j', (t) => {
    expect(kalshiMarketUrl(t)).toBeNull();
  });
});

describe('Popover', () => {
  function Harness({ onClose = () => {} }) {
    const ref = useRef(null);
    const [open, setOpen] = useState(false);
    return (
      <div>
        <button ref={ref} onClick={() => setOpen((o) => !o)}>
          anchor
        </button>
        <button>outside</button>
        <Popover
          open={open}
          onClose={() => {
            onClose();
            setOpen(false);
          }}
          anchorRef={ref}
          label="Definition"
        >
          <p>Short definition</p>
          <a href="/terminology#x">More</a>
          <button>Close</button>
        </Popover>
      </div>
    );
  }

  it('opens in a portal as a labelled dialog and moves focus inside', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.queryByRole('dialog')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'anchor' }));
    const dlg = screen.getByRole('dialog', { name: 'Definition' });
    expect(dlg.parentElement).toBe(document.body);
    expect(dlg).toContainElement(document.activeElement);
    expect(screen.getByText('Short definition')).toBeInTheDocument();
  });

  it('Esc closes it and focus returns to the anchor', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: 'anchor' }));
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('button', { name: 'anchor' })).toHaveFocus();
  });

  it('a tap outside closes it; a tap inside does not', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: 'anchor' }));
    await user.click(screen.getByText('Short definition'));
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'outside' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('the anchor click toggles it (not treated as an outside tap)', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    const a = screen.getByRole('button', { name: 'anchor' });
    await user.click(a);
    await user.click(a);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(onClose).not.toHaveBeenCalled(); // closed by the toggle, not by outside-tap handling
  });

  it('Tab and Shift+Tab stay inside the popover', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: 'anchor' }));
    const dlg = screen.getByRole('dialog');
    const link = screen.getByRole('link', { name: 'More' });
    const close = screen.getByRole('button', { name: 'Close' });
    expect(link).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.tab(); // wraps to the first
    expect(link).toHaveFocus();
    await user.tab({ shift: true }); // wraps to the last
    expect(close).toHaveFocus();
    expect(dlg).toContainElement(document.activeElement);
  });

  it('stays inside the viewport on a 375 px screen (clamped left/width)', async () => {
    window.innerWidth = 375;
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: 'anchor' }));
    const dlg = screen.getByRole('dialog');
    const left = parseFloat(dlg.style.left);
    const width = parseFloat(dlg.style.width);
    expect(left).toBeGreaterThanOrEqual(8);
    expect(left + width).toBeLessThanOrEqual(375 - 8 + 0.5);
  });
});

describe("What's New (item 44)", () => {
  it('compares dotted versions numerically', () => {
    expect(compareVersions('1.10.0', '1.9.2')).toBe(1);
    expect(compareVersions('0.4.0', '0.4.0')).toBe(0);
    expect(compareVersions('0.3.9', '0.4.0')).toBe(-1);
    expect(compareVersions('2', '1.9')).toBe(1);
  });

  const feed = [
    { version: '0.3.0', date: '2026-10-13', items: ['old'] },
    { version: '0.4.0', date: '2026-10-15', items: ['a', 'b'] },
    { version: '0.3.5', date: '2026-10-14', items: ['mid'] },
  ];

  it('lists only entries newer than the last seen version, newest first; a new device gets the newest only', () => {
    expect(unseenEntries(feed, '0.3.0').map((e) => e.version)).toEqual(['0.4.0', '0.3.5']);
    expect(unseenEntries(feed, '0.4.0')).toEqual([]);
    expect(unseenEntries(feed, null).map((e) => e.version)).toEqual(['0.4.0']);
    expect(unseenEntries([], null)).toEqual([]);
    expect(unseenEntries('garbage', null)).toEqual([]);
    expect(unseenEntries([{ version: '1.0.0' }, null], null)).toEqual([]);
  });

  it('shows the card, and "Got It" saves the newest version and hides it', async () => {
    const user = userEvent.setup();
    stubApi({ 'GET /api/changelog': [200, feed] });
    render(<WhatsNew />);
    expect(await screen.findByText(/What.s New/)).toBeInTheDocument();
    expect(screen.getByText('v0.4.0')).toBeInTheDocument();
    expect(screen.getByText('a')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Got It' }));
    expect(screen.queryByText(/What.s New/)).toBeNull();
    expect(localStorage.getItem(SEEN_KEY)).toBe('0.4.0');
  });

  it('the X button dismisses too, and the card stays gone on the next visit', async () => {
    const user = userEvent.setup();
    stubApi({ 'GET /api/changelog': [200, feed] });
    const { unmount } = render(<WhatsNew />);
    await user.click(await screen.findByRole('button', { name: /dismiss/i }));
    expect(localStorage.getItem(SEEN_KEY)).toBe('0.4.0');
    unmount();
    render(<WhatsNew />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(screen.queryByText(/What.s New/)).toBeNull();
  });

  it('a long list is trimmed with Show All / Show Less', async () => {
    const user = userEvent.setup();
    stubApi({ 'GET /api/changelog': [200, [{ version: '1.0.0', date: '2026-10-15', items: ['1', '2', '3', '4', '5'] }]] });
    render(<WhatsNew />);
    await screen.findByText('1');
    expect(screen.queryByText('5')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Show All 5' }));
    expect(screen.getByText('5')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show Less' }));
    expect(screen.queryByText('5')).toBeNull();
  });

  it('shows the release day as written, not shifted by the time zone', async () => {
    stubApi({ 'GET /api/changelog': [200, [{ version: '1.0.0', date: '2026-10-15', items: ['x'] }]] });
    render(<WhatsNew />);
    expect(await screen.findByText('Oct 15, 2026')).toBeInTheDocument();
  });

  it('is silent when the feed fails or is empty, and blocked storage does not break it', async () => {
    stubApi({ 'GET /api/changelog': [500, { detail: 'boom' }] });
    const { container } = render(<WhatsNew />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('works when localStorage throws (card shows; dismissing just does not persist)', async () => {
    const user = userEvent.setup();
    stubApi({ 'GET /api/changelog': [200, feed] });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    render(<WhatsNew />);
    await user.click(await screen.findByRole('button', { name: 'Got It' }));
    expect(screen.queryByText(/What.s New/)).toBeNull();
    vi.restoreAllMocks();
  });
});

describe('waking screen (item 35)', () => {
  const releases = [];
  function pendingFetch() {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise((resolve) => {
            releases.push(() => resolve({ ok: true, status: 200, text: () => Promise.resolve('{}') }));
          }),
      ),
    );
  }

  it('after 5 s shows the progress bar, stale cached numbers with their time, and that Stop works', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setToken('tok');
    writeCache('/api/status', { account: { cash: 212.4, exposure: 37.6 }, pnl: { d7: 4.83, d30: 17.12 } });
    pendingFetch();
    render(<WakingBanner />);
    expect(screen.queryByRole('progressbar')).toBeNull();
    request('GET', '/api/status').catch(() => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SLOW_MS + 500);
    });
    expect(screen.getByText(/Waking the server… usually 30–60 s/)).toBeInTheDocument();
    const bar = screen.getByRole('progressbar', { name: /wake-up progress/i });
    const early = Number(bar.getAttribute('aria-valuenow'));
    expect(early).toBeGreaterThan(0);
    expect(early).toBeLessThan(25);
    const stale = document.querySelector('.waking-stale');
    expect(stale).toHaveTextContent('Stale');
    expect(stale).toHaveTextContent('Cash $212.40');
    expect(stale).toHaveTextContent('At Risk $37.60');
    expect(stale).toHaveTextContent('Maker P&L (30 days) +$17.12');
    expect(stale).toHaveTextContent(/as of \d{1,2}:\d{2} (AM|PM)/);
    expect(screen.getByText('Stop still works.')).toBeInTheDocument();
    // The bar advances with time and never reaches 100% while the server is still asleep.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(40_000);
    });
    const later = Number(screen.getByRole('progressbar').getAttribute('aria-valuenow'));
    expect(later).toBeGreaterThan(early);
    expect(later).toBeLessThan(100);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(screen.getByText(/Still waking the server/)).toBeInTheDocument();
    await act(async () => {
      releases.forEach((r) => r());
      await vi.advanceTimersByTimeAsync(10);
    });
    await waitFor(() => expect(screen.queryByRole('progressbar')).toBeNull());
  });

  it('without cached numbers it shows no stale line', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setToken('tok');
    pendingFetch();
    render(<WakingBanner />);
    request('GET', '/api/other').catch(() => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SLOW_MS + 500);
    });
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(document.querySelector('.waking-stale')).toBeNull();
    await act(async () => {
      releases.forEach((r) => r());
      await vi.advanceTimersByTimeAsync(10);
    });
  });

  it('in the app, the header Stop stays visible and opens its confirm while the banner shows', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    pendingFetch();
    renderApp('/');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SLOW_MS + 500);
    });
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    const header = document.querySelector('header.appbar');
    const stop = header.querySelector('button[aria-label^="Stop the bot"]');
    expect(stop).not.toBeNull();
    expect(stop).toBeEnabled();
    await act(async () => {
      stop.click();
    });
    expect(await screen.findByRole('button', { name: 'Stop now' })).toBeInTheDocument();
    await act(async () => {
      releases.forEach((r) => r());
      await vi.advanceTimersByTimeAsync(10);
    });
  });
});

describe("What's New in the app shell", () => {
  it('appears above the page for a device that has not seen the latest version', async () => {
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/history': { snapshots: [] },
      'GET /api/changelog': [200, [{ version: '0.4.0', date: '2026-10-15', items: ['Renamed.'] }]],
    });
    renderApp('/');
    expect(await screen.findByText('Renamed.')).toBeInTheDocument();
    expect(document.querySelector('.shell-notices').contains(screen.getByText('Renamed.'))).toBe(true);
  });
});
