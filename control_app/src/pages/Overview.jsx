import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import Card from '../components/Card.jsx';
import FoldCard from '../components/FoldCard.jsx';
import StatusCard from '../components/StatusCard.jsx';
import StatusWidget from '../components/StatusWidget.jsx';
import SinceCard from '../components/SinceCard.jsx';
import SourcePnl from '../components/SourcePnl.jsx';
import Sparkline from '../components/Sparkline.jsx';
import PnlStats from '../components/PnlStats.jsx';
import FillQuality from '../components/FillQuality.jsx';
import HelpIcon from '../components/HelpIcon.jsx';
import { ArbStateText } from './Arbitrage.jsx';
import { ErrorNotice, Loading } from '../components/Notices.jsx';
import { useStatus } from '../context/StatusContext.jsx';
import { useDialogs } from '../context/DialogContext.jsx';
import { useResource } from '../hooks/useResource.js';
import { api } from '../services/api.js';
import { getPositions } from '../services/overviewApi.js';
import { riskLimit } from '../utils/status.js';
import { edgeSummary, headlineParts } from '../utils/overview.js';
import { formatAgo, formatMoney, dateTime, money } from '../utils/format.js';
import { shortDate } from '../utils/analytics.js';
import { moneyDigits } from '../utils/chart.js';

// POST /api/bot/start 409 details (Start/Stop race guard, runner off).
const START_409 = {
  stop_in_progress: 'A Stop is still being saved. Try Start again in a few seconds.',
  stopped_during_start: 'A Stop arrived while starting, so Stop won. The bot is stopped.',
  runner_disabled: 'The runner is disabled on this server (RUNNER_ENABLED=false).',
};

// One bar: "$37.60 at risk of $125" (item 5).
function RiskBar({ exposure, limit }) {
  if (typeof exposure !== 'number' || typeof limit !== 'number' || limit <= 0) return null;
  const frac = Math.min(1, exposure / limit);
  const level = frac > 0.9 ? 'danger' : frac > 0.7 ? 'warn' : 'ok';
  return (
    <>
      <div className="meter" role="img" aria-label={`At risk ${Math.round(frac * 100)}% of limit`}>
        <div className={`meter-fill meter-${level}`} style={{ width: `${(frac * 100).toFixed(1)}%` }} />
      </div>
      <p className={`ov-risk-line ${level === 'danger' ? 'error-text' : ''}`} data-testid="risk-line">
        {formatMoney(exposure)} at risk of {formatMoney(limit, { digits: Number.isInteger(limit) ? 0 : 2 })}
        <HelpIcon term="exposure" />
      </p>
    </>
  );
}

// Item 1: "Demo Running · +$17.12 Over 30 Days · Edge On Track". Problems are red, good news green.
function Headline({ parts }) {
  return (
    <p className="ov-headline" data-testid="headline">
      {parts.map((p, i) => (
        <span key={p.text} className={`ov-hl ov-hl-${p.tone}`}>
          {i > 0 && <span className="ov-hl-sep" aria-hidden="true"> · </span>}
          {p.text}
        </span>
      ))}
    </p>
  );
}

export default function Overview() {
  const { status, error, loading, updatedAt, stale, refresh } = useStatus();
  const { confirm, twoFactor, notify } = useDialogs();
  const history = useResource(() => api.getHistory(30), '/api/history?days=30');
  const strategies = useResource(() => api.getStrategies(), '/api/strategies');
  const positions = useResource(() => getPositions(), '/api/account/positions');
  const edge = useResource(() => api.analyticsSummary({ days: 30 }), '/api/analytics/summary?days=30');
  const [busy, setBusy] = useState(null);
  const cardRef = useRef(null);

  const a = status && status.account;
  const limit = riskLimit(status);
  const snaps = (history.data && history.data.snapshots) || [];
  const equity = snaps.map((s) => (s.cash || 0) + (s.exposure || 0));
  const equityDates = snaps.map((s) => shortDate(s.ts));
  const demo = !!status && status.mode !== 'live';

  async function onStart() {
    const mode = (status && status.mode) || 'demo';
    setBusy('start');
    try {
      const r = await twoFactor({
        title: 'Start trading?',
        message: (
          <>
            <p>
              Removes the kill switch and starts the bot in <strong>{mode.toUpperCase()}</strong> mode.
            </p>
            {mode === 'live' && <p className="error-text">LIVE mode trades real money.</p>}
          </>
        ),
        confirmLabel: 'Start',
        action: (code) => api.start(code),
      });
      if (r) notify({ kind: 'ok', title: 'Started', text: `Bot is ${r.desired || 'running'}.` });
    } catch (err) {
      const why = START_409[err.detail];
      notify({ kind: 'error', title: 'Start failed', text: why || err.message, details: why ? undefined : err.details });
    } finally {
      setBusy(null);
      refresh();
    }
  }

  async function onRunOnce() {
    const ok = await confirm({
      title: 'Run one pass now?',
      message: 'Re-quotes all enabled markets once, using the current mode and settings.',
      confirmLabel: 'Run once',
    });
    if (!ok) return;
    setBusy('run');
    try {
      const r = await api.runOnce();
      notify({
        kind: r && r.ran ? 'ok' : 'info',
        title: r && r.ran ? 'Pass ran' : 'Pass did not run',
        text: r && r.reason ? `Reason: ${r.reason}` : undefined,
      });
    } catch (err) {
      notify({ kind: 'error', title: 'Run once failed', text: err.message, details: err.details });
    } finally {
      setBusy(null);
      refresh();
    }
  }

  return (
    <div className="page overview-page">
      {/* Visual title is the sticky PageTitle bar (aria-hidden); this is the page's one accessible heading. */}
      <h1>Overview</h1>
      <SinceCard />
      {status && <Headline parts={headlineParts(status, edgeSummary(edge.data))} />}
      {demo && (
        <p className="notice notice-info ov-demo" data-testid="demo-banner">
          Demo Mode: These numbers are not representative of live trading.
        </p>
      )}
      <StatusCard ref={cardRef} status={status} busy={busy} onStart={onStart} onRunOnce={onRunOnce} />
      <StatusWidget status={status} cardRef={cardRef} />
      {loading && !status && <Loading text="Loading status…" />}
      <ErrorNotice error={error} onRetry={refresh} />
      {stale && updatedAt && <p className="hint">Last update {dateTime(updatedAt)} (could not refresh).</p>}

      {status && (
        <>
          {status.arb && (
            <Card title="Arbitrage" actions={<Link to="/arb">Open</Link>}>
              <p className="arb-line">
                <ArbStateText state={status.arb.state} />
                {status.arb.last_scan_utc ? <span className="muted"> last scan {formatAgo(status.arb.last_scan_utc)}</span> : null}
              </p>
            </Card>
          )}

          <Card title="Money">
            {a ? (
              <>
                <dl className="stats">
                  <div>
                    <dt>Cash<HelpIcon term="balance" /></dt>
                    <dd className="big">{formatMoney(a.cash)}</dd>
                  </div>
                  <div>
                    <dt>At Risk</dt>
                    <dd className="big">{formatMoney(a.exposure)}</dd>
                  </div>
                </dl>
                <RiskBar exposure={a.exposure} limit={limit} />
              </>
            ) : (
              <p className="muted">No account snapshot yet.</p>
            )}
          </Card>

          <Card title={<>Profit &amp; Loss<HelpIcon term="realised-pnl" /></>}>
            <PnlStats pnl={status.pnl} emptyText="No settled fills yet. P&L appears once fills settle." />
            {status.fills_sync && status.fills_sync.error && (
              <p className="hint error-text">Fill sync failed {formatAgo(status.fills_sync.last_utc)}: {String(status.fills_sync.error)}</p>
            )}
            <h3 className="subhead">By Source</h3>
            <SourcePnl pnlToday={status.pnl_today} strategies={strategies.data} positions={positions.data} />
            {equity.length > 1 && (
              <>
                <Sparkline
                  values={equity}
                  labels={equityDates}
                  yFormat={(v) => money(v, { digits: moneyDigits(Math.min(...equity), Math.max(...equity)) })}
                  yTitle="Balance + positions ($)"
                  xTitle="Date"
                  testId="equity-chart"
                  label={`Balance + positions in dollars, last 30 days, ${equityDates[0]} to ${equityDates[equityDates.length - 1]}: ${money(equity[0])} to ${money(equity[equity.length - 1])}; low ${money(Math.min(...equity))}, high ${money(Math.max(...equity))}`}
                />
                <p className="hint">Balance + positions, 30 days: {money(equity[0])} → {money(equity[equity.length - 1])}</p>
              </>
            )}
          </Card>

          {'fill_quality' in status && (
            <FoldCard title={<>Fill Quality<HelpIcon term="fill-quality" /></>} id="fill-quality-fold">
              <FillQuality fq={status.fill_quality} />
            </FoldCard>
          )}
        </>
      )}
    </div>
  );
}
