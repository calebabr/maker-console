import { useState } from 'react';
import Sparkline from '../Sparkline.jsx';
import { num } from '../../utils/format.js';
import { isNum, rate, weekLabel } from '../../utils/analytics.js';

export const SHOW_WEEKS = 8;

// "W33 to W40" over the weeks that have a value for `key`.
function firstLast(rows, key) {
  const has = rows.filter((w) => isNum(w[key]));
  return has.length ? `${weekLabel(has[0].week)} to ${weekLabel(has[has.length - 1].week)}` : '';
}

/** Weekly fill quality: front-of-line share and average contracts ahead when joining. */
export default function FillQualityTrend({ weeks }) {
  const [all, setAll] = useState(false);
  const rows = (Array.isArray(weeks) ? weeks : []).filter(Boolean);
  if (!rows.length) return <p className="muted small">No maker fills in this window.</p>;
  const fills = rows.reduce((s, w) => s + (isNum(w.fills) ? w.fills : 0), 0);
  const front = rows.reduce((s, w) => s + (isNum(w.front) ? w.front : 0), 0);
  const tt = rows.reduce((s, w) => s + (isNum(w.trade_through) ? w.trade_through : 0), 0);
  const overall = front + tt ? front / (front + tt) : null;
  // Newest week first in the table.
  const newest = rows.slice().reverse();
  const list = all ? newest : newest.slice(0, SHOW_WEEKS);
  const trend = rows.map((w) => w.front_rate).filter(isNum);
  const ahead = rows.map((w) => w.avg_queue_ahead).filter(isNum);
  const aheadDigits = ahead.length && Math.max(...ahead) - Math.min(...ahead) < 3 ? 1 : 0;
  const weekNames = rows.map((w) => weekLabel(w.week));
  return (
    <>
      <dl className="stats">
        <div>
          <dt>Front-of-Line Share</dt>
          <dd>{rate(overall, 0)}</dd>
        </div>
        <div>
          <dt>Fills Measured</dt>
          <dd>
            {num(front + tt)}
            <span className="muted small"> of {num(fills)}</span>
          </dd>
        </div>
      </dl>
      {trend.length >= 2 && (
        <Sparkline
          values={rows.map((w) => w.front_rate)}
          labels={weekNames}
          height={56}
          yFormat={(v) => rate(v, 0)}
          yTitle="Front-of-line rate (%)"
          xTitle="Week"
          testId="fq-front-chart"
          label={`Weekly front-of-line rate in percent over ${trend.length} weeks, ${firstLast(rows, 'front_rate')}; latest ${rate(trend[trend.length - 1], 0)}`}
        />
      )}
      {ahead.length >= 2 && (
        <Sparkline
          values={rows.map((w) => w.avg_queue_ahead)}
          labels={weekNames}
          height={56}
          color="var(--muted)"
          yFormat={(v) => num(v, aheadDigits)}
          yTitle="Contracts ahead when joining (avg)"
          xTitle="Week"
          testId="fq-ahead-chart"
          label={`Weekly average contracts ahead in the queue over ${ahead.length} weeks, ${firstLast(rows, 'avg_queue_ahead')}; latest ${num(ahead[ahead.length - 1], 1)} contracts`}
        />
      )}
      <div className="table-wrap">
        <table className="queue-table fq-table" aria-label="Fill quality by week">
          <thead>
            <tr>
              <th scope="col">Week</th>
              <th scope="col">Fills</th>
              <th scope="col">Front</th>
              <th scope="col">Ahead</th>
              <th scope="col">Re-Quotes</th>
            </tr>
          </thead>
          <tbody>
            {list.map((w) => (
              <tr key={w.week}>
                <th scope="row">{weekLabel(w.week)}</th>
                <td>{num(w.fills)}</td>
                <td>
                  <span className="queue-rate">
                    <span className="queue-bar" aria-hidden="true">
                      <span style={{ width: `${isNum(w.front_rate) ? Math.max(2, w.front_rate * 100) : 0}%` }} />
                    </span>
                    <span>{rate(w.front_rate, 0)}</span>
                  </span>
                </td>
                <td>{isNum(w.avg_queue_ahead) ? num(w.avg_queue_ahead) : '—'}</td>
                <td>{num(w.requotes)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {newest.length > SHOW_WEEKS && (
        <button type="button" className="btn small" style={{ marginTop: 8 }} onClick={() => setAll((v) => !v)} aria-expanded={all}>
          {all ? `Show latest ${SHOW_WEEKS} weeks` : `Show all ${newest.length} weeks`}
        </button>
      )}
      <p className="hint">
        Front = share of labelled fills that came from the front of the line rather than a trade-through. Ahead = average
        contracts queued in front of the bid when it was placed.
      </p>
    </>
  );
}
