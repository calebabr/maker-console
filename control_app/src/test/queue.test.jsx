// Queue-position display (queue_stats on /api/status and /api/markets). Display only.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { clearToken } from '../services/api.js';
import QueuePosition, { QUEUE_EXPLAINER } from '../components/QueuePosition.jsx';
import { analyticsSummary, mockMyTrades } from '../services/mockAnalytics.js';
import { renderApp, stubApi, STATUS } from './helpers.jsx';

beforeEach(() => clearToken());
afterEach(() => vi.unstubAllGlobals());

const QS = {
  episodes: 800,
  fills: 120,
  median_queue_ahead_at_join: 74,
  requote_reset_share: 0.23,
  front_fill_share: 0.41,
  by_queue: [
    { bucket: '>1000', episodes: 50, fills: 1, fill_rate: 0.02, trade_through_share: null },
    { bucket: '0', episodes: 150, fills: 49, fill_rate: 0.327, trade_through_share: 0.3 },
    { bucket: '1-50', episodes: 200, fills: 40, fill_rate: 0.2, trade_through_share: 0.35 },
    { bucket: '51-200', episodes: 250, fills: 24, fill_rate: 0.096, trade_through_share: 0.45 },
    { bucket: '201-1000', episodes: 150, fills: 6, fill_rate: 0.04, trade_through_share: 0.6 },
  ],
  updated_utc: '2026-10-13T12:00:00Z',
};

describe('QueuePosition component', () => {
  it('null shows "Queue data unavailable"', () => {
    render(<QueuePosition qs={null} />);
    expect(screen.getByText('Queue data unavailable.')).toBeInTheDocument();
  });

  it('null in compact mode renders nothing', () => {
    const { container } = render(<QueuePosition qs={null} variant="compact" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('episodes 0 shows "No quotes measured yet"', () => {
    render(<QueuePosition qs={{ ...QS, episodes: 0, fills: 0, by_queue: [] }} />);
    expect(screen.getByText(/no quotes measured yet/i)).toBeInTheDocument();
    expect(screen.queryByTestId('queue-position')).toBeNull();
  });

  it('full view shows the headline numbers, explainer, demo note and buckets in order', () => {
    render(<QueuePosition qs={QS} />);
    const el = screen.getByTestId('queue-position');
    expect(within(el).getByText('74')).toBeInTheDocument();
    expect(within(el).getByText('41%')).toBeInTheDocument();
    expect(within(el).getByText('23%')).toBeInTheDocument();
    expect(within(el).getByText(QUEUE_EXPLAINER)).toBeInTheDocument();
    expect(within(el).getByText(/aren't representative/)).toBeInTheDocument();
    expect(within(el).queryByText(/Too few fills/)).toBeNull();
    const rows = within(el).getAllByRole('row').slice(1).map((r) => r.querySelector('th').textContent);
    expect(rows).toEqual(['0', '1-50', '51-200', '201-1000', '>1000']);
    expect(within(el).getByText('33%')).toBeInTheDocument();
  });

  it('under 50 fills shows numbers plus "Too few fills to judge (n/50)"', () => {
    render(<QueuePosition qs={{ ...QS, fills: 12 }} />);
    expect(screen.getByText('74')).toBeInTheDocument();
    expect(screen.getByText('Too few fills to judge (12/50).')).toBeInTheDocument();
  });

  it('missing optional fields render as dashes, not crashes', () => {
    render(
      <QueuePosition
        qs={{ episodes: 10, fills: 0, median_queue_ahead_at_join: null, requote_reset_share: null, front_fill_share: null, by_queue: [] }}
      />,
    );
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(3);
    expect(screen.queryByRole('table')).toBeNull();
  });
});

describe('queue position on pages', () => {
  it('Overview no longer carries the queue sentence (it lives on Markets and Analytics)', async () => {
    stubApi({ 'GET /api/status': { ...STATUS, mode: 'demo', queue_stats: QS }, 'GET /api/history': { snapshots: [] } });
    renderApp('/');
    await screen.findByTestId('status-card');
    expect(screen.queryByTestId('queue-summary')).toBeNull();
    expect(document.querySelector('main').textContent).not.toMatch(/median 74 ahead|from the front|Demo numbers are not representative/);
  });

  it('Markets no longer shows queue blocks; Analytics Execution carries the all-series queue table', async () => {
    const markets = {
      markets: [
        { series: 'KXDEMOA', enabled: true, queue_stats: { ...QS, fills: 20 } },
        { series: 'KXDEMOB', enabled: true, queue_stats: { ...QS, episodes: 0, fills: 0, by_queue: [] } },
      ],
      queue_stats: QS,
    };
    stubApi({ 'GET /api/status': STATUS, 'GET /api/markets': markets });
    const { unmount } = renderApp('/markets');
    await screen.findByText('Demo Series A');
    expect(screen.queryByTestId('queue-position')).toBeNull();
    expect(screen.queryByText(/Queue Position/)).toBeNull();
    unmount();
    const NOW = Date.parse('2026-10-13T18:00:00Z');
    stubApi({
      'GET /api/status': STATUS,
      'GET /api/markets': markets,
      'GET /api/analytics/summary': (c) => analyticsSummary(new URL(c.path, 'http://x').searchParams, { nowMs: NOW }),
      'GET /api/analytics/my-trades': (c) => mockMyTrades(new URL(c.path, 'http://x').searchParams, { nowMs: NOW }),
    });
    renderApp('/analytics?tab=execution');
    expect(await screen.findByText('Queue Position, All Series')).toBeInTheDocument();
    const block = await screen.findByTestId('queue-position');
    expect(within(block).getByText('Typical Place in Line')).toBeInTheDocument();
    expect(within(block).getByText('Lost Our Spot When We Moved Our Price')).toBeInTheDocument();
    expect(within(block).getByRole('table')).toBeInTheDocument();
  });
});
