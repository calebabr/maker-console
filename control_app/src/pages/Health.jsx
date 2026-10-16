import { useState } from 'react';
import Card from '../components/Card.jsx';
import KeyValue from '../components/KeyValue.jsx';
import HelpIcon from '../components/HelpIcon.jsx';
import FoldCard from '../components/FoldCard.jsx';
import Chip from '../components/Chip.jsx';
import { ErrorNotice, Loading, StaleNotice } from '../components/Notices.jsx';
import { useDialogs } from '../context/DialogContext.jsx';
import { useResource } from '../hooks/useResource.js';
import { api } from '../services/api.js';
import { formatAgo, formatMoney, formatPct, humanize, num, titleCase } from '../utils/format.js';
import { seriesName } from '../utils/analytics.js';
import '../styles/settings.css';

// Summary keys rendered specially; everything else is shown generically under "More Details".
const SPECIAL = ['generated_utc', 'window_days', 'expected_roi', 'overall', 'per_series', 'actions', 'last_account', 'quoted_dollars', 'filled_dollars', 'fill_rate'];

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const signCls = (v) => (isNum(v) && v < 0 ? 'neg' : '');

function likelyRange(s) {
  return isNum(s && s.ci_low) && isNum(s && s.ci_high) ? `${formatPct(s.ci_low)} to ${formatPct(s.ci_high)}` : null;
}

// One plain sentence for the whole window.
export function overallSentence(summary) {
  const o = summary && summary.overall;
  if (!o) return null;
  const parts = [`${num(o.fills)} fills`, `${formatMoney(o.pnl, { sign: true })} P&L`, `return ${formatPct(o.roi)}`];
  const range = likelyRange(o);
  if (range) parts.push(`likely range ${range}`);
  if (isNum(summary.expected_roi)) parts.push(`expected ${formatPct(summary.expected_roi)}`);
  return parts.join(' · ');
}

function OverallStats({ s }) {
  if (!s || typeof s !== 'object') return null;
  const range = likelyRange(s);
  return (
    <dl className="stats">
      <div>
        <dt>Fills<HelpIcon term="fill" /></dt>
        <dd className="num">{num(s.fills)}</dd>
      </div>
      <div>
        <dt>P&amp;L<HelpIcon term="realised-pnl" /></dt>
        <dd className={`num ${signCls(s.pnl)}`}>{formatMoney(s.pnl, { sign: true })}</dd>
      </div>
      <div>
        <dt>Return<HelpIcon term="roi" /></dt>
        <dd className={`num ${signCls(s.roi)}`}>{formatPct(s.roi)}</dd>
      </div>
      <div>
        <dt>Likely Range<HelpIcon term="confidence-interval" /></dt>
        <dd className="num">{range || '—'}</dd>
      </div>
    </dl>
  );
}

function MarketRow({ id, v, paused, needMore }) {
  const range = likelyRange(v || {});
  return (
    <li className="market-row">
      <div className="market-row-top">
        <span className="market-name">{titleCase(seriesName(id))}</span>
        {paused ? <Chip tone="danger">Pause</Chip> : needMore > 0 ? <Chip tone="muted">{`${needMore} More Fills Needed`}</Chip> : <Chip tone="ok">Keep</Chip>}
      </div>
      <div className="market-row-meta num">
        <span>{num(v && v.fills)} fills</span>
        <span className={signCls(v && v.pnl)}>{formatMoney(v && v.pnl, { sign: true })}</span>
        <span className={signCls(v && v.roi)}>return {formatPct(v && v.roi)}</span>
        {range && <span>range {range}</span>}
      </div>
    </li>
  );
}

export default function Health() {
  const { data, error, loading, staleAt, reload, setData } = useResource(() => api.getHealth(), '/api/health');
  const { confirm, notify } = useDialogs();
  const [running, setRunning] = useState(false);
  const summary = data && data.summary;
  const report = data && data.report_text;

  async function runCheck() {
    const ok = await confirm({
      title: 'Run Health Check Now?',
      message: 'Runs the weekly monitor on the last 30 days of fills. It may take a minute.',
      confirmLabel: 'Run Check',
    });
    if (!ok) return;
    setRunning(true);
    try {
      const r = await api.runMonitor();
      setData({ ...(data || {}), summary: r ? r.summary : null });
      notify({ kind: 'ok', title: 'Health Check Finished' });
      reload();
    } catch (err) {
      notify({ kind: 'error', title: 'Health Check Failed', text: err.message, details: err.details });
    } finally {
      setRunning(false);
    }
  }

  const hasSummary = summary && typeof summary === 'object';
  const rest = hasSummary ? Object.fromEntries(Object.entries(summary).filter(([k]) => !SPECIAL.includes(k))) : null;
  const actions = (hasSummary && summary.actions) || {};
  const paused = Array.isArray(actions.pause) ? actions.pause : [];
  const needMore = actions.need_more && typeof actions.need_more === 'object' ? actions.need_more : {};
  const acct = hasSummary && summary.last_account && typeof summary.last_account === 'object' ? summary.last_account : null;
  const sentence = overallSentence(summary);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Health</h1>
        <button type="button" className="btn small" onClick={reload} disabled={loading}>
          Refresh
        </button>
      </div>
      <button type="button" className="btn primary block" onClick={runCheck} disabled={running}>
        {running ? 'Running Health Check…' : 'Run Health Check'}
      </button>
      <StaleNotice staleAt={error ? staleAt : null} />
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data && <Loading />}
      {data && !summary && !report && <p className="muted">No health report yet. Run a check.</p>}

      {hasSummary && (
        <>
          <Card
            title="Overall"
            subtitle={[
              summary.generated_utc ? `Checked ${formatAgo(summary.generated_utc)}` : null,
              summary.window_days ? `last ${summary.window_days} days` : null,
            ]
              .filter(Boolean)
              .join(' · ')}
          >
            {sentence && <p className="health-line">{sentence}</p>}
            <OverallStats s={summary.overall} />
            {(isNum(summary.quoted_dollars) || isNum(summary.filled_dollars)) && (
              <p className="hint num">
                Quoted {formatMoney(summary.quoted_dollars)} · Filled {formatMoney(summary.filled_dollars)}
                {isNum(summary.fill_rate) ? ` · Fill Rate ${formatPct(summary.fill_rate, { sign: false, digits: 1 })}` : ''}
              </p>
            )}
          </Card>

          {summary.per_series && typeof summary.per_series === 'object' && (
            <Card title="By Market">
              <ul className="market-rows">
                {Object.entries(summary.per_series).map(([s, v]) => (
                  <MarketRow key={s} id={s} v={v} paused={paused.includes(s)} needMore={Number(needMore[s]) || 0} />
                ))}
              </ul>
            </Card>
          )}

          <Card title="Actions">
            <dl className="kv">
              <div className="kv-row">
                <dt>Paused Markets</dt>
                <dd>{paused.length ? paused.map((s) => titleCase(seriesName(s))).join(', ') : 'None'}</dd>
              </div>
              {actions.collateral_return != null && (
                <div className="kv-row">
                  <dt>Collateral Return</dt>
                  <dd>{String(actions.collateral_return)}</dd>
                </div>
              )}
              {acct && (
                <>
                  {isNum(acct.cash) && (
                    <div className="kv-row">
                      <dt>Cash</dt>
                      <dd className="num">{formatMoney(acct.cash)}</dd>
                    </div>
                  )}
                  {isNum(acct.exposure) && (
                    <div className="kv-row">
                      <dt>At Risk</dt>
                      <dd className="num">{formatMoney(acct.exposure)}</dd>
                    </div>
                  )}
                  {Object.entries(acct)
                    .filter(([k]) => !['cash', 'exposure'].includes(k))
                    .map(([k, v]) => (
                      <div className="kv-row" key={k}>
                        <dt>{titleCase(humanize(k))}</dt>
                        <dd className="num">{isNum(v) ? formatMoney(v) : String(v)}</dd>
                      </div>
                    ))}
                </>
              )}
            </dl>
          </Card>

          {rest && Object.keys(rest).length > 0 && (
            <FoldCard title="More Details">
              <KeyValue data={rest} />
            </FoldCard>
          )}
        </>
      )}

      {report && (
        <FoldCard title="Show Raw Report">
          <pre className="report-wrap">{report}</pre>
        </FoldCard>
      )}
    </div>
  );
}
