import { useState } from 'react';
import HelpIcon from '../HelpIcon.jsx';
import { num } from '../../utils/format.js';
import { isNum, parseCell, rate, seriesName } from '../../utils/analytics.js';

// 42.3 -> "42 min"; 185 -> "3.1 h"
export function minutes(v) {
  if (!isNum(v)) return '—';
  if (v < 90) return `${Math.round(v)} min`;
  return `${(v / 60).toFixed(1)} h`;
}

function cellText(id) {
  const c = parseCell(id);
  return `${c.price} ${c.ttc} ${c.side}`;
}

/** Fill rate from quote episodes: how many resting bids got filled, and how long they waited. */
export default function FillRate({ fr }) {
  const [by, setBy] = useState('cell');
  const t = fr && fr.totals;
  if (!t || !t.quotes) return <p className="muted small">No closed quotes in this window.</p>;
  const rows = ((by === 'cell' ? fr.by_cell : fr.by_series) || []).filter(Boolean);
  return (
    <div data-testid="fill-rate">
      <dl className="stats">
        <div>
          <dt>Fill Rate</dt>
          <dd>{rate(t.fill_rate, 0)}</dd>
        </div>
        <div>
          <dt>Quotes Closed</dt>
          <dd>{num(t.quotes)}</dd>
        </div>
        <div>
          <dt>
            Filled
            <span className="muted small"> (partly {num(t.partial)})</span>
          </dt>
          <dd>{num(t.filled)}</dd>
        </div>
        <div>
          <dt>
            Missed
            <HelpIcon term="missed-fill" />
          </dt>
          <dd>{num(t.unfilled)}</dd>
        </div>
        <div>
          <dt>
            Median Rest
            <HelpIcon term="rest-time" />
          </dt>
          <dd>{minutes(t.median_rest_minutes)}</dd>
        </div>
        <div>
          <dt>Median Time to Fill</dt>
          <dd>{minutes(t.median_minutes_to_fill)}</dd>
        </div>
      </dl>

      <div className="segmented compact" role="group" aria-label="Fill rate by" style={{ marginTop: 12 }}>
        <button type="button" className={by === 'cell' ? 'active' : ''} aria-pressed={by === 'cell'} onClick={() => setBy('cell')}>
          By cell
        </button>
        <button type="button" className={by === 'series' ? 'active' : ''} aria-pressed={by === 'series'} onClick={() => setBy('series')}>
          By series
        </button>
      </div>
      {rows.length ? (
        <div className="table-wrap">
          <table className="queue-table fr-table" aria-label={`Fill rate by ${by}`}>
            <thead>
              <tr>
                <th scope="col">{by === 'cell' ? 'Cell' : 'Series'}</th>
                <th scope="col">Quotes</th>
                <th scope="col">Filled</th>
                <th scope="col">Rest</th>
                <th scope="col">To Fill</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <th scope="row">{by === 'cell' ? cellText(r.key) : seriesName(r.key)}</th>
                  <td>{num(r.quotes)}</td>
                  <td>{rate(r.fill_rate, 0)}</td>
                  <td>{minutes(r.median_rest_minutes)}</td>
                  <td>{minutes(r.median_minutes_to_fill)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted small">No quotes to break down.</p>
      )}
      <p className="hint">
        A quote is one resting bid from placement until it filled, was re-priced, expired or was cancelled. Rest is how long
        bids sat on the book; to fill is the wait for the ones that filled.
      </p>
    </div>
  );
}
