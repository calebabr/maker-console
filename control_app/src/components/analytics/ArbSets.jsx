import HelpIcon from '../HelpIcon.jsx';
import { isNum, signCls } from '../../utils/analytics.js';
import { dateTime, formatMoney, num } from '../../utils/format.js';

const OUTCOME_LABELS = [
  ['filled', 'Filled'],
  ['partial', 'Partial'],
  ['error', 'Error'],
  ['skipped', 'Skipped by a risk rule'],
  ['log_only', 'Logged only'],
];

const na = <span className="na">not available</span>;

function Window({ title, w, testId }) {
  if (!w) return null;
  const o = w.by_outcome || {};
  return (
    <div data-testid={testId}>
      <h3 className="subhead">{title}</h3>
      <dl className="stats">
        <div>
          <dt>Distinct sets<HelpIcon term="arb-set" /></dt>
          <dd data-testid={`${testId}-sets`}>{num(w.sets)}</dd>
        </div>
        <div>
          <dt>Sightings</dt>
          <dd>{num(w.sightings)}</dd>
        </div>
      </dl>
      <dl className="stats" aria-label={`${title}: what happened to each set`}>
        {OUTCOME_LABELS.map(([k, label]) => (
          <div key={k}>
            <dt>{k === 'skipped' ? <>{label}<HelpIcon term="risk-rule-outcome" /></> : label}</dt>
            <dd>{num(o[k])}</dd>
          </div>
        ))}
      </dl>
      {w.skipped_by_rule && Object.keys(w.skipped_by_rule).length > 0 && (
        <p className="hint">
          Skipped by rule: {Object.entries(w.skipped_by_rule).map(([r, n]) => `${r.replace(/_/g, ' ')} ${n}`).join(', ')}.
        </p>
      )}
    </div>
  );
}

/** Distinct sets today and over the period, with the gate-outcome breakdown (arb_summary). */
export function ArbCounters({ summary }) {
  if (!summary) return null;
  return (
    <>
      <Window title="Today" w={summary.today} testId="arb-today" />
      <Window title={summary.period && summary.period.label ? `Period: ${summary.period.label}` : 'Period'} w={summary.period} testId="arb-period" />
    </>
  );
}

const STATUS_TEXT = { PARTIAL: 'Partial', FILLED: 'Filled', OPEN: 'Open', SETTLED: 'Settled' };

/** One row per set (newest first); tap a row to see its legs. */
export function ArbSetsTable({ sets, total }) {
  const rows = Array.isArray(sets) ? sets : [];
  if (!rows.length) return <p className="hint" data-testid="arb-sets-empty">No executed sets in this window.</p>;
  return (
    <div data-testid="arb-sets">
      {rows.map((s) => (
        <details className="set-row" key={s.set_id} data-testid="arb-set-row">
          <summary>
            <span>
              <b>{s.event}</b> <span className="muted">{s.kind}</span>
              <br />
              <span className="hint">{dateTime(s.executed_utc)} · {STATUS_TEXT[s.status] || s.status}</span>
            </span>
            <span style={{ textAlign: 'right' }}>
              <span className="hint">guaranteed </span>
              {formatMoney(s.guaranteed_profit)}
              <br />
              <span className="hint">realized </span>
              {isNum(s.realized_pnl) ? <span className={signCls(s.realized_pnl)}>{formatMoney(s.realized_pnl, { sign: true })}</span> : na}
            </span>
          </summary>
          <div className="legs">
            {(s.legs || []).map((l, i) => (
              <div key={`${l.ticker}-${i}`}>
                <span>{l.ticker} {String(l.side || '').toUpperCase()}</span>
                <span>
                  {num(l.filled ?? 0)}/{num(l.contracts)} filled
                  {l.settled === true ? `, settled ${l.result || ''}` : l.settled === false ? ', open' : ''}
                  {isNum(l.pnl) ? ` ${formatMoney(l.pnl, { sign: true })}` : ''}
                </span>
              </div>
            ))}
            {(s.notes || []).map((n, i) => (
              <p className="hint" key={i}>{n}</p>
            ))}
          </div>
        </details>
      ))}
      {isNum(total) && total > rows.length && <p className="hint">Showing the newest {rows.length} of {total} sets.</p>}
    </div>
  );
}
