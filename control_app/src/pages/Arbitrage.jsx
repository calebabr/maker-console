import { useCallback, useEffect, useMemo, useState } from 'react';
import { IconLock } from '../components/Icons.jsx';
import Card from '../components/Card.jsx';
import HelpIcon from '../components/HelpIcon.jsx';
import PnlStats from '../components/PnlStats.jsx';
import PnlScope from '../components/PnlScope.jsx';
import { useStatus } from '../context/StatusContext.jsx';
import StopButton, { STOPPED_EVENT } from '../components/StopButton.jsx';
import { ErrorNotice, Loading, StaleNotice } from '../components/Notices.jsx';
import { useDialogs } from '../context/DialogContext.jsx';
import { useResource } from '../hooks/useResource.js';
import { usePageVisible } from '../hooks/usePageVisible.js';
import { api } from '../services/api.js';
import { ago, dateTime, humanize, money, num } from '../utils/format.js';
import CollapsibleList from '../components/CollapsibleList.jsx';
import { ArbCounters, ArbSetsTable } from '../components/analytics/ArbSets.jsx';

// Bracket arbitrage (DASHBOARD_PLAN.md "Strategies run by the app").
//   GET  /api/arb               -> {settings, schema?, ceilings?, status, pnl, expectation?}
//   PUT  /api/arb  (2FA)        -> same shape; body: any settings field, plus confirm:"LIVE", ack_legging
//   GET  /api/arb/opportunities -> {items:[{ts, event, kind, contracts, profit, cost, sum_best, legs, executed, ...}]}
// Log-only by default. Live needs its own switch (typed LIVE + 2FA) plus server env gates.

export const POLL_MS = 30000;
const OPP_LIMIT = 50;
// Series options come from the API's arb schema (the installed scanner); no ticker list is built into the app.
export const ARB_SERIES = [];
const FIELDS = [
  'arb_max_contracts', 'arb_min_profit', 'arb_poll_seconds', 'arb_series',
  'arb_scan_cap', 'arb_day_cap', 'arb_series_cap_frac', 'arb_demo_execute',
];
const DEFAULT_SCHEMA = {
  arb_max_contracts: {
    type: 'number', unit: 'contracts per leg', min: 1, max: 100,
    help: 'Largest set the scanner will size an opportunity to. Each leg is a taker order.',
  },
  arb_min_profit: {
    type: 'number', unit: 'USD per set, after fees', min: 0.01, max: 10, step: 0.01,
    help: 'Ignore opportunities that clear less than this after taker fees.',
  },
  arb_poll_seconds: {
    type: 'integer', unit: 'seconds', min: 5, max: 60,
    help: 'Scan interval. Faster catches more short windows but uses more of the read rate limit and the 0.1 CPU.',
  },
  arb_series: {
    type: 'list', unit: 'series tickers',
    help: 'Bracket events to scan. Each event has mutually exclusive brackets.',
  },
  arb_scan_cap: {
    type: 'number', unit: 'USD per scan', min: 1, max: 100,
    help: 'Most money one scan may spend on sets in demo-execute. Sets over this are skipped.',
  },
  arb_day_cap: {
    type: 'number', unit: 'USD per day', min: 1, max: 500,
    help: 'Most money arbitrage may spend per UTC day in demo-execute.',
  },
  arb_series_cap_frac: {
    type: 'number', unit: 'fraction of the open-exposure ceiling', min: 0.05, max: 0.9, step: 0.01,
    help: 'Most one series may hold in open exposure, as a share of the account ceiling.',
  },
  arb_demo_execute: {
    type: 'bool', label: 'Demo Execute',
    help: 'Place the sets it finds as real orders on the Kalshi demo account (play money). Never on live.',
  },
};

const STATES = [
  { key: 'stopped', label: 'Off' },
  { key: 'log-only', label: 'Log only' },
  { key: 'live', label: 'Live' },
];

function arbState(settings, status) {
  if (status && status.state) return String(status.state);
  if (!settings || !settings.enabled) return 'stopped';
  return settings.live ? 'live' : 'log-only';
}

export function ArbStateText({ state }) {
  const s = STATES.find((x) => x.key === state);
  return <span className={`arb-state arb-state-${state || 'unknown'}`}>{s ? s.label : state || 'Unknown'}</span>;
}

// Three-position readout, like a selector switch on an instrument panel.
function StateTrack({ state }) {
  return (
    <div className="arb-track" role="img" aria-label={`Arbitrage is ${(STATES.find((s) => s.key === state) || {}).label || state}`}>
      {STATES.map((s) => (
        <div key={s.key} className={`arb-pos arb-pos-${s.key} ${state === s.key ? 'on' : ''}`}>
          <span className="arb-pos-mark" aria-hidden="true" />
          {s.label}
        </div>
      ))}
    </div>
  );
}

function kindOf(spec) {
  const t = String((spec && spec.type) || '').toLowerCase();
  if (['int', 'integer'].includes(t)) return 'int';
  if (['list', 'array', 'multiselect'].includes(t)) return 'list';
  if (['bool', 'boolean'].includes(t)) return 'bool';
  return 'number';
}

function toDraft(settings) {
  const d = {};
  for (const k of FIELDS) {
    if (!(k in (settings || {}))) continue;
    const v = settings[k];
    d[k] = Array.isArray(v) ? [...v] : typeof v === 'boolean' ? v : v == null ? '' : String(v);
  }
  return d;
}

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const showVal = (v) => (Array.isArray(v) ? v.join(', ') : typeof v === 'boolean' ? (v ? 'On' : 'Off') : String(v));

/** "skipped: gate total exposure over the limit" -> the plain reason, or null when this is not a gate skip. */
export function gateReason(executed) {
  const m = /^skipped:\s*gate\s*(.*)$/i.exec(String(executed || ''));
  return m ? m[1].trim() || 'risk limit' : null;
}

function executedPill(executed) {
  const e = String(executed || 'log-only');
  if (e.startsWith('FILLED')) return { cls: 'pill-ok', text: 'Filled' };
  if (e.startsWith('PARTIAL')) return { cls: 'pill-danger', text: 'Partial fill' };
  if (e === 'log-only') return { cls: 'pill-muted', text: 'Logged only' };
  if (gateReason(e) !== null) return { cls: 'pill-warn', text: 'Skipped by risk limit' };
  if (e.startsWith('ERROR')) return { cls: 'pill-danger', text: 'Error' };
  return { cls: 'pill-muted', text: e };
}

export default function Arbitrage() {
  const visible = usePageVisible();
  const arb = useResource(() => api.getArb(), '/api/arb');
  const opps = useResource(() => api.getArbOpportunities(OPP_LIMIT), `/api/arb/opportunities?limit=${OPP_LIMIT}`);
  // Grouped sets come from the analytics summary (one row per set). A failure here only hides that card.
  const sets = useResource(() => api.analyticsSummary({ days: 30, env: 'all' }), '/api/analytics/summary?arb-sets');
  const { confirm, twoFactor, notify } = useDialogs();
  const { status: appStatus } = useStatus();
  const [busy, setBusy] = useState(null);
  const [draft, setDraft] = useState({});
  const [saveError, setSaveError] = useState(null);

  const { data, setData } = arb;
  const reloadArb = arb.reload;
  const reloadOpps = opps.reload;
  const reloadAll = useCallback(() => {
    reloadArb();
    reloadOpps();
  }, [reloadArb, reloadOpps]);

  // Poll while visible; reload right after a Stop from anywhere in the app.
  useEffect(() => {
    if (!visible) return undefined;
    const id = setInterval(reloadAll, POLL_MS);
    return () => clearInterval(id);
  }, [visible, reloadAll]);
  useEffect(() => {
    window.addEventListener(STOPPED_EVENT, reloadAll);
    return () => window.removeEventListener(STOPPED_EVENT, reloadAll);
  }, [reloadAll]);

  const settings = (data && data.settings) || null;
  const st = (data && data.status) || {};
  const schema = { ...DEFAULT_SCHEMA, ...((data && data.schema) || {}) };
  const ceilings = (data && data.ceilings) || {};
  const arbSummary = (data && data.arb_summary) || null;
  const sinceRestart = (arbSummary && arbSummary.since_restart) || {};
  const restartUtc = sinceRestart.since_utc || st.since_restart_utc || null;
  const state = arbState(settings, data && data.status);
  const enabled = (settings && 'enabled' in settings ? !!settings.enabled : state !== 'stopped') && !st.kill_switch;
  const live = state === 'live';
  const liveAllowed = !!st.live_allowed;
  const legging = st.kill_reason === 'legging';

  useEffect(() => {
    if (settings) setDraft(toDraft(settings));
  }, [settings]);

  const fields = settings ? FIELDS.filter((k) => k in settings) : [];

  const { patch, errors } = useMemo(() => {
    const p = {};
    const errs = {};
    if (!settings) return { patch: p, errors: errs };
    for (const k of fields) {
      const spec = schema[k] || {};
      const kind = kindOf(spec);
      let v = draft[k];
      if (kind === 'bool') {
        v = !!v;
      } else if (kind === 'list') {
        if (!Array.isArray(v) || !v.length) errs[k] = 'Pick at least one';
      } else {
        if (v === '' || v == null) {
          errs[k] = 'Required';
          continue;
        }
        v = Number(v);
        const max = [spec.max, ceilings[k]].filter((x) => typeof x === 'number');
        const hi = max.length ? Math.min(...max) : undefined;
        if (!Number.isFinite(v)) errs[k] = 'Not a number';
        else if (kind === 'int' && !Number.isInteger(v)) errs[k] = 'Whole number only';
        else if (typeof spec.min === 'number' && v < spec.min) errs[k] = `Minimum ${spec.min}`;
        else if (typeof hi === 'number' && v > hi) errs[k] = `Maximum ${hi}`;
      }
      if (!errs[k] && JSON.stringify(v) !== JSON.stringify(settings[k])) p[k] = v;
    }
    return { patch: p, errors: errs };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, settings, data]);

  const dirty = Object.keys(patch).length > 0;
  const hasErrors = Object.keys(errors).length > 0;

  function applyResult(r) {
    if (r && (r.settings || r.status)) setData({ ...(data || {}), ...r });
    else reloadArb();
  }

  function errorToast(err, fallbackTitle) {
    if (err.status === 409 && String(err.detail).startsWith('live_not_allowed')) {
      notify({
        kind: 'error',
        title: 'Live arbitrage is not allowed by the server',
        text: 'The Render environment needs the prod key and KALSHI_LIVE_CONFIRM. The app cannot enable real money on its own.',
      });
    } else if (err.status === 409 && String(err.detail).startsWith('arb_legging_kill')) {
      notify({
        kind: 'error',
        title: 'Legging kill switch is set',
        text: 'A partial fill tripped the arbitrage kill switch. Check Kalshi for the unmatched leg, then turn arbitrage on again and acknowledge it.',
      });
    } else {
      notify({ kind: 'error', title: fallbackTitle, text: err.message, details: err.details });
    }
  }

  async function setEnabled(on) {
    if (busy) return;
    const body = { enabled: on };
    if (on && legging) {
      const ok = await confirm({
        title: 'Clear the legging kill switch?',
        message: (
          <>
            <p>A partial fill left one or more legs unmatched and stopped arbitrage.</p>
            <p className="hint">Check your Kalshi positions before turning it back on.</p>
          </>
        ),
        confirmLabel: 'I have checked',
        danger: true,
      });
      if (!ok) return;
      body.ack_legging = true;
    }
    setBusy('enabled');
    try {
      const r = await twoFactor({
        title: on ? 'Turn arbitrage on?' : 'Turn arbitrage off?',
        message: on
          ? live
            ? 'The scanner starts and trades LIVE when it finds a set.'
            : 'The scanner starts in log-only mode: it records opportunities and places no orders.'
          : 'The scanner stops. Stop (no code needed) also turns it off.',
        confirmLabel: on ? 'Turn on' : 'Turn off',
        danger: on && live,
        action: (code) => api.putArb(body, code),
      });
      if (r) {
        applyResult(r);
        notify({ kind: 'ok', title: on ? 'Arbitrage is on' : 'Arbitrage is off' });
      }
    } catch (err) {
      errorToast(err, 'Arbitrage not changed');
    } finally {
      setBusy(null);
    }
  }

  async function setLive(on) {
    if (busy) return;
    if (on) {
      const ok = await confirm({
        title: 'Arbitrage LIVE (real money)?',
        message: (
          <>
            <p>The scanner will send taker orders for every leg of a set it finds, with real money.</p>
            <p>A partial fill leaves an unhedged leg; it trips the arbitrage kill switch and sends an alert.</p>
            <p className="hint">Independent of the maker&apos;s mode. You will need a code next.</p>
          </>
        ),
        confirmLabel: 'Continue',
        danger: true,
        requireText: 'LIVE',
      });
      if (!ok) return;
    }
    setBusy('live');
    try {
      const r = await twoFactor({
        title: on ? 'Confirm arbitrage LIVE' : 'Back to log-only?',
        message: on ? 'Final Step: Enter your code to trade arbitrage live.' : 'Opportunities will be recorded only. No orders.',
        confirmLabel: on ? 'Go LIVE' : 'Log only',
        danger: on,
        action: (code) => api.putArb(on ? { live: true, confirm: 'LIVE' } : { live: false }, code),
      });
      if (r) {
        applyResult(r);
        notify({ kind: 'ok', title: on ? 'Arbitrage is LIVE' : 'Arbitrage is log-only' });
      }
    } catch (err) {
      errorToast(err, 'Arbitrage mode not changed');
    } finally {
      setBusy(null);
    }
  }

  async function save(e) {
    e.preventDefault();
    if (!dirty || hasErrors || busy) return;
    setBusy('save');
    setSaveError(null);
    try {
      const r = await twoFactor({
        title: 'Save arbitrage settings?',
        message: (
          <ul className="change-list">
            {Object.entries(patch).map(([k, v]) => (
              <li key={k}>
                <strong>{humanize(k.replace(/^arb_/, ''))}</strong>:{' '}
                {showVal(settings[k])} → {showVal(v)}
              </li>
            ))}
          </ul>
        ),
        confirmLabel: 'Save',
        action: (code) => api.putArb(patch, code),
      });
      if (r) {
        applyResult(r);
        notify({ kind: 'ok', title: 'Arbitrage settings saved' });
      }
    } catch (err) {
      setSaveError(err);
    } finally {
      setBusy(null);
    }
  }

  const setField = (k, v) => setDraft((d) => ({ ...d, [k]: v }));

  function renderField(k) {
    const spec = schema[k] || {};
    const kind = kindOf(spec);
    const label = (
      <span className="field-label">
        {spec.label || humanize(k.replace(/^arb_/, ''))}
        {spec.unit && <span className="unit"> ({spec.unit})</span>}
      </span>
    );
    const help = spec.help && <span className="field-help">{spec.help}</span>;
    if (kind === 'list') {
      const options = spec.options && spec.options.length ? spec.options : Array.from(new Set([...ARB_SERIES, ...(settings[k] || [])]));
      const cur = draft[k] || [];
      return (
        <fieldset className="field" key={k}>
          <legend>{label}</legend>
          <div className="checks">
            {options.map((o) => (
              <label key={o} className="check">
                <input
                  type="checkbox"
                  checked={cur.includes(o)}
                  onChange={(ev) =>
                    setField(k, ev.target.checked ? options.filter((x) => x === o || cur.includes(x)) : cur.filter((x) => x !== o))
                  }
                />
                <span className="mono">{o}</span>
              </label>
            ))}
          </div>
          {help}
          {errors[k] && <span className="error-text">{errors[k]}</span>}
        </fieldset>
      );
    }
    if (kind === 'bool') {
      return (
        <div className="field" key={k}>
          <label className="check">
            <input type="checkbox" checked={!!draft[k]} onChange={(ev) => setField(k, ev.target.checked)} />
            {label}
          </label>
          {help}
        </div>
      );
    }
    const hiList = [spec.max, ceilings[k]].filter((x) => typeof x === 'number');
    const max = hiList.length ? Math.min(...hiList) : undefined;
    return (
      <label className="field" key={k}>
        {label}
        <input
          type="number"
          inputMode={kind === 'int' ? 'numeric' : 'decimal'}
          min={spec.min}
          max={max}
          step={kind === 'int' ? 1 : spec.step || 'any'}
          value={draft[k] ?? ''}
          onChange={(ev) => setField(k, ev.target.value)}
          aria-invalid={!!errors[k]}
          aria-label={spec.label || humanize(k.replace(/^arb_/, ''))}
        />
        {(spec.min != null || max != null) && (
          <span className="field-range">
            Range {spec.min ?? '…'} – {max ?? '…'}
          </span>
        )}
        {help}
        {errors[k] && <span className="error-text">{errors[k]}</span>}
      </label>
    );
  }

  const items = (opps.data && opps.data.items) || [];
  const loggedProfit = items.reduce((a, o) => a + (Number(o.profit) || 0), 0);
  const expectation =
    (data && data.expectation) ||
    'Bracket arbitrage is rare and small at a small balance. Opportunities last seconds, so a scan catches only some of them. The maker strategy is the main earner.';

  return (
    <div className="page">
      <div className="page-head">
        <h1>Arbitrage</h1>
        <div className="page-head-actions">
          <HelpIcon term="arbitrage" />
          <button type="button" className="btn small" onClick={reloadAll} disabled={arb.loading}>
            Refresh
          </button>
        </div>
      </div>
      <StaleNotice staleAt={arb.error ? arb.staleAt : null} />
      <ErrorNotice error={arb.error} onRetry={reloadAll} />
      {arb.loading && !data && <Loading />}

      {data && (
        <>
          <Card className="hero arb-hero">
            <StateTrack state={state} />
            <p className="muted arb-explain">
              {state === 'live'
                ? 'Scanning and trading real money: Every leg of a set is sent as a taker order.'
                : state === 'log-only'
                  ? 'Scanning and recording opportunities. No orders are placed.'
                  : 'Not scanning.'}
            </p>
            {st.kill_switch && (
              <div className="notice notice-error" role="alert">
                <strong>Kill switch set</strong>
                <HelpIcon term={legging ? 'legging' : 'kill-switch'} />
                {legging ? ': a partial fill left an unmatched leg. Check Kalshi positions.' : st.kill_reason === 'stop' ? ' by Stop.' : '.'}
              </div>
            )}
            <StopButton />
            <p className="hint">
              Stop turns arbitrage off too, no code needed.<HelpIcon term="stop" />
            </p>
            <div className="btn-row">
              <button
                type="button"
                className={`btn ${enabled ? '' : 'primary'}`}
                onClick={() => setEnabled(!enabled)}
                disabled={!!busy}
              >
                {busy === 'enabled' ? 'Working…' : <><IconLock /> {enabled ? 'Turn off' : 'Turn on'}</>}
              </button>
              {live ? (
                <button type="button" className="btn" onClick={() => setLive(false)} disabled={!!busy}>
                  {busy === 'live' ? 'Working…' : <><IconLock /> Log only</>}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn danger-outline"
                  onClick={() => setLive(true)}
                  disabled={!!busy || !liveAllowed}
                >
                  {busy === 'live' ? 'Working…' : <><IconLock /> Go live</>}
                </button>
              )}
            </div>
            {!live && !liveAllowed && (
              <p className="hint">Live Is Disabled: The server environment does not allow real money.</p>
            )}
            {live && <p className="hint">A server restart never restores LIVE; you re-arm it here (2FA + typing LIVE).</p>}
          </Card>

          <Card title="Scanner">
            <dl className="stats">
              <div>
                <dt>Last scan</dt>
                <dd>{ago(st.last_scan_utc)}</dd>
              </div>
              <div>
                <dt>Interval</dt>
                <dd>{settings && settings.arb_poll_seconds != null ? `${settings.arb_poll_seconds}s` : '—'}</dd>
              </div>
              <div>
                <dt>Scans since restart<HelpIcon term="scans-since-restart" /></dt>
                <dd data-testid="scans-since-restart">{num(sinceRestart.scans != null ? sinceRestart.scans : st.scans)}</dd>
                {restartUtc && <dd className="hint" data-testid="restart-time">since {dateTime(restartUtc)}</dd>}
              </div>
              <div>
                <dt>Sightings since restart<HelpIcon term="arb-set" /></dt>
                <dd>{num(sinceRestart.sightings != null ? sinceRestart.sightings : st.found)}</dd>
              </div>
              <div>
                <dt>Live allowed<HelpIcon term="demo-vs-live" /></dt>
                <dd>{liveAllowed ? 'yes' : 'no'}</dd>
              </div>
              <div>
                <dt>Demo orders</dt>
                <dd data-testid="demo-exec-state">{st.demo_execute ? 'on' : 'off'}</dd>
              </div>
              <div>
                <dt>Kill switch<HelpIcon term="kill-switch" /></dt>
                <dd className={st.kill_switch ? 'neg' : ''}>{st.kill_switch ? 'SET' : 'off'}</dd>
              </div>
            </dl>
            {st.last_error && (
              <div className="notice notice-error">
                <strong>Last error:</strong> {String(st.last_error)}
              </div>
            )}
          </Card>

          <Card title={<>Profit &amp; loss<HelpIcon term="realised-pnl" /></>} subtitle="Executed arbitrage sets only.">
            <h3 className="subhead">Guaranteed Profit</h3>
            <PnlStats pnl={data.pnl} big={false} emptyText="No executed sets yet." scope="arbGuaranteed" />
            <h3 className="subhead">Realized Profit</h3>
            <div data-testid="arb-realized">
              <PnlStats pnl={appStatus && appStatus.pnl_arb} big={false} emptyText="No arbitrage fills have settled yet." scope="arbRealized" />
              <p className="hint">
                Guaranteed is what the scanner promised for sets that filled, counted when they were executed. Realized is
                what settled fills actually paid, counted when the market settled, so the two cover different sets and
                dates until every executed set has settled.
                {appStatus && appStatus.mode !== 'live' ? ' The app is in demo mode, so this is play money.' : ''}
              </p>
            </div>
            {data.pnl_demo_execute && (
              <div data-testid="demo-pnl">
                <h3 className="subhead">Demo Orders (Play Money)</h3>
                <PnlStats pnl={data.pnl_demo_execute} big={false} emptyText="No demo sets yet." />
                {isNum(data.pnl_demo_execute.sets) && <p className="hint">{data.pnl_demo_execute.sets} demo sets. Never part of the live figure above.</p>}
              </div>
            )}
            <p className="hint">{expectation}</p>
          </Card>
        </>
      )}

      {arbSummary && (
        <Card title={<>Sets found<HelpIcon term="arb-set" /></>} subtitle="Counted once each from the stored log, not per scan.">
          <ArbCounters summary={arbSummary} />
        </Card>
      )}
      {sets.data && Array.isArray(sets.data.arb_sets) && (
        <Card title="Executed sets" subtitle="One row per set. Tap a row for its legs.">
          <ArbSetsTable sets={sets.data.arb_sets} total={sets.data.arb_sets_total} />
        </Card>
      )}

      <Card
        title={<>Recent opportunities<HelpIcon term="fee" /></>}
        subtitle={items.length ? `${items.length} shown, ${money(loggedProfit, { sign: true })} total after fees` : undefined}
      >
        <StaleNotice staleAt={opps.error ? opps.staleAt : null} />
        <ErrorNotice error={opps.error} onRetry={reloadOpps} />
        {opps.loading && !opps.data && <Loading />}
        {opps.data && !items.length && <p className="muted">None found yet.</p>}
        <CollapsibleList items={items} pageKey="arbitrage-opps" label="opportunities">
          {(vis) => (
            <ul className="rows">
              {vis.map((o, i) => {
                const ex = executedPill(o.executed);
                const when = o.ts || o.utc;
                const demoExec = o.mode === 'demo-execute';
                const why = gateReason(o.executed);
                return (
                  <li key={`${when}-${o.event}-${i}`} className={`row-item ${demoExec ? 'row-demo' : ''}`} data-mode={o.mode || undefined}>
                    <div className="row-top">
                      <span className="mono">{o.event || '?'}</span>
                      <span className="muted small">{dateTime(when)}</span>
                    </div>
                    <div className="row-meta">
                      <span>{o.kind === 'YES' ? 'Buy every YES' : o.kind === 'NO' ? 'Buy every NO' : String(o.kind || '')}</span>
                      <span>× {num(Number(o.contracts), 0)}</span>
                      <span className={Number(o.profit) > 0 ? 'pos' : ''}>{money(Number(o.profit), { sign: true })}</span>
                      <span className="muted">cost {money(Number(o.cost))}</span>
                      {o.seen_count > 1 && <span className="muted">seen {o.seen_count}×</span>}
                      {demoExec && <span className="pill pill-demo" data-testid="demo-exec-badge">Demo order</span>}
                      <span className={`pill ${ex.cls}`}>{ex.text}</span>
                    </div>
                    {why && <div className="muted small" data-testid="gate-reason">Not placed: {why}</div>}
                  </li>
                );
              })}
            </ul>
          )}
        </CollapsibleList>
      </Card>

      {settings && fields.length > 0 && (
        <form onSubmit={save}>
          <Card title="Arbitrage settings" subtitle="Applies from the next scan.">
            <div className="stack">{fields.map(renderField)}</div>
          </Card>
          {saveError && (
            <div className="notice notice-error" role="alert">
              <strong>Not saved.</strong>
              {saveError.details && saveError.details.length ? (
                <ul className="detail-list">
                  {saveError.details.map((d, i) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
              ) : (
                <div>{saveError.message}</div>
              )}
            </div>
          )}
          <div className="sticky-actions">
            <button type="button" className="btn" disabled={!dirty || !!busy} onClick={() => setDraft(toDraft(settings))}>
              Undo changes
            </button>
            <button type="submit" className="btn primary" disabled={!dirty || hasErrors || !!busy}>
              {busy === 'save' ? 'Saving…' : <><IconLock /> Save</>}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
