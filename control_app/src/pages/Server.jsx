import { useEffect, useMemo, useState } from 'react';
import { IconLock } from '../components/Icons.jsx';
import Card from '../components/Card.jsx';
import HelpIcon from '../components/HelpIcon.jsx';
import { ErrorNotice, Loading, StaleNotice } from '../components/Notices.jsx';
import { useDialogs } from '../context/DialogContext.jsx';
import { useAuth } from '../context/AuthContext.jsx';
import { useResource } from '../hooks/useResource.js';
import { api } from '../services/api.js';
import { ago, dateTime, humanize } from '../utils/format.js';

// Keep-awake (self-ping) + external cron-job.org pinger. GET/PUT /api/cloud.
// Interval inputs are capped at 1–14 min (Render sleeps after 15 min idle).

export const INTERVAL_MAX = 14;
const DEFAULT_SCHEMA = {
  keepalive_minutes: { type: 'integer', unit: 'minutes', min: 1, max: 14, help: 'Self-ping: the service calls its own /healthz.' },
  pinger_minutes: { type: 'integer', unit: 'minutes', min: 1, max: 14, help: 'External cron-job.org job calling POST /api/tick.' },
  pinger_enabled: { type: 'bool', help: 'Enable the external pinger.' },
  silent_alert_minutes: { type: 'integer', unit: 'minutes', min: 15, max: 240, help: 'ntfy alert if no heartbeat for this long.' },
};
const INTERVAL_KEYS = ['keepalive_minutes', 'pinger_minutes'];

function bounds(key, spec) {
  let min = spec && typeof spec.min === 'number' ? spec.min : undefined;
  let max = spec && typeof spec.max === 'number' ? spec.max : undefined;
  if (INTERVAL_KEYS.includes(key)) {
    min = Math.max(1, min ?? 1);
    max = Math.min(INTERVAL_MAX, max ?? INTERVAL_MAX);
  }
  return { min, max };
}

function isBool(key, spec, value) {
  const t = String((spec && spec.type) || '').toLowerCase();
  return t === 'bool' || t === 'boolean' || typeof value === 'boolean';
}

export default function Server() {
  const { data, error, loading, staleAt, reload, setData } = useResource(() => api.getCloud(), '/api/cloud');
  const { twoFactor, notify } = useDialogs();
  const { endSession } = useAuth();
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState(null);
  const [saveError, setSaveError] = useState(null);

  const settings = (data && data.settings) || null;
  const schema = { ...DEFAULT_SCHEMA, ...((data && data.schema) || {}) };
  const sleepMin = (data && data.sleep_timeout_minutes) || 15;

  useEffect(() => {
    if (settings) setDraft({ ...settings });
  }, [settings]);

  const keys = settings ? Object.keys({ ...DEFAULT_SCHEMA, ...settings }).filter((k) => k in settings) : [];

  const { patch, errors } = useMemo(() => {
    const p = {};
    const errs = {};
    if (!settings) return { patch: p, errors: errs };
    for (const k of keys) {
      const spec = schema[k];
      let v = draft[k];
      if (isBool(k, spec, settings[k])) {
        v = !!v;
      } else {
        if (v === '' || v == null) {
          errs[k] = 'Required';
          continue;
        }
        v = Number(v);
        const { min, max } = bounds(k, spec);
        if (!Number.isInteger(v)) errs[k] = 'Whole minutes only';
        else if (min != null && v < min) errs[k] = `Minimum ${min}`;
        else if (max != null && v > max) errs[k] = `Maximum ${max}`;
      }
      if (!errs[k] && v !== settings[k]) p[k] = v;
    }
    return { patch: p, errors: errs };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, settings]);

  const dirty = Object.keys(patch).length > 0;
  const hasErrors = Object.keys(errors).length > 0;

  // Warn if any interval currently stored on the server is >= the sleep timeout.
  const tooSlow = settings
    ? INTERVAL_KEYS.filter((k) => typeof settings[k] === 'number' && settings[k] >= sleepMin)
    : [];

  async function save(e) {
    e.preventDefault();
    if (!dirty || hasErrors || busy) return;
    setBusy('save');
    setSaveError(null);
    try {
      const r = await twoFactor({
        title: 'Save server settings?',
        message: (
          <ul className="change-list">
            {Object.entries(patch).map(([k, v]) => (
              <li key={k}>
                <strong>{humanize(k)}</strong>: {String(settings[k])} → {String(v)}
              </li>
            ))}
          </ul>
        ),
        confirmLabel: 'Save',
        action: (code) => api.putCloud(patch, code),
      });
      if (r) {
        if (r.settings) setData(r);
        else reload();
        const syncErr = r.pinger && r.pinger.error;
        notify({
          kind: syncErr ? 'info' : 'ok',
          title: 'Server settings saved',
          text: syncErr ? `Saved, but the external pinger did not sync: ${syncErr}` : undefined,
        });
      }
    } catch (err) {
      // 400 (our validation: list of messages) and 422 (FastAPI type errors: [{loc, msg}])
      // both end up as err.details; anything else shows err.message.
      setSaveError(err);
    } finally {
      setBusy(null);
    }
  }

  async function resync() {
    if (busy) return;
    setBusy('sync');
    try {
      const r = await twoFactor({
        title: 'Re-sync external pinger?',
        message: 'Creates or updates the cron-job.org job with the current interval now.',
        confirmLabel: 'Re-sync',
        action: (code) => api.syncPinger(code),
      });
      if (r) {
        if (r.settings) setData(r);
        else reload();
        // Response is the GET /api/cloud shape; pinger.synced is true | false | null (null = not configured).
        const p = r.pinger || r;
        const notConfigured = p && (p.configured === false || p.synced == null) && p.error;
        notify({
          kind: notConfigured ? 'info' : p && p.error ? 'error' : 'ok',
          title: notConfigured ? 'Pinger not configured' : p && p.error ? 'Pinger sync failed' : 'Pinger synced',
          text: p && p.error ? String(p.error) : undefined,
        });
      }
    } catch (err) {
      notify({ kind: 'error', title: 'Re-sync failed', text: err.message, details: err.details });
    } finally {
      setBusy(null);
    }
  }

  // S-15: POST /api/auth/logout-all (fresh 2FA) invalidates every token on every device,
  // including this one, so sign out locally afterwards without calling /logout.
  async function logoutAll() {
    if (busy) return;
    setBusy('logout-all');
    try {
      const r = await twoFactor({
        title: 'Log out all sessions?',
        message: (
          <>
            <p>Signs out every device that is logged in, including this phone. Use it if a phone is lost.</p>
            <p className="hint">The bot keeps running. You will sign in again with your password and a code.</p>
          </>
        ),
        confirmLabel: 'Log out all',
        danger: true,
        action: (code) => api.logoutAll(code),
      });
      if (r) {
        setBusy(null);
        endSession('logged_out_all');
        return;
      }
    } catch (err) {
      notify({ kind: 'error', title: 'Sessions NOT logged out', text: err.message, details: err.details });
    }
    setBusy(null);
  }

  const ka = (data && data.keepalive) || {};
  const pg = (data && data.pinger) || {};

  return (
    <div className="page">
      <div className="page-head">
        <h1>Server</h1>
        <button type="button" className="btn small" onClick={reload} disabled={loading}>
          Refresh
        </button>
      </div>
      <StaleNotice staleAt={error ? staleAt : null} />
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data && <Loading />}

      {data && (
        <>
          <div className="notice notice-info">
            Render free tier sleeps after {sleepMin} min with no inbound requests. Keep both intervals at 14 min or less.
            <HelpIcon term="keep-awake" />
          </div>
          {tooSlow.length > 0 && (
            <div className="notice notice-error" role="alert">
              Warning: {tooSlow.map(humanize).join(' and ')} {tooSlow.length > 1 ? 'are' : 'is'} {sleepMin} min or more.
              The server may fall asleep between pings.
            </div>
          )}

          <Card title="Status">
            <dl className="stats">
              <div>
                <dt>Last inbound request</dt>
                <dd>{ago(data.last_inbound_utc)}</dd>
              </div>
              <div>
                <dt>Last self-ping</dt>
                <dd>
                  {ago(ka.last_ping_utc)}{' '}
                  {ka.ok === true ? <span className="pos">ok</span> : ka.ok === false ? <span className="neg">failed</span> : null}
                </dd>
              </div>
            </dl>
            {ka.error && <div className="notice notice-error">Self-ping error: {String(ka.error)}</div>}

            <h3 className="subhead">External pinger (cron-job.org)</h3>
            {pg.configured === false ? (
              <p className="muted">
                Not configured (no CRONJOB_API_KEY on the server). Only the self-ping keeps the service awake.
              </p>
            ) : (
              <dl className="stats">
                <div>
                  <dt>Job id</dt>
                  <dd className="mono">{pg.job_id ?? '—'}</dd>
                </div>
                <div>
                  <dt>Synced</dt>
                  <dd>{pg.synced === true ? <span className="pos">yes</span> : pg.synced === false ? <span className="neg">no</span> : '—'}</dd>
                </div>
                <div>
                  <dt>Last sync</dt>
                  <dd>{dateTime(pg.last_sync_utc)}</dd>
                </div>
                {pg.schedule && (
                  <div>
                    <dt>Schedule</dt>
                    <dd>{String(pg.schedule)}</dd>
                  </div>
                )}
              </dl>
            )}
            {pg.error && <div className="notice notice-error">Pinger error: {String(pg.error)}</div>}
            <button type="button" className="btn block" onClick={resync} disabled={!!busy || pg.configured === false}>
              {busy === 'sync' ? 'Syncing…' : <><IconLock /> Re-sync pinger</>}
            </button>
          </Card>

          {settings && (
            <form onSubmit={save}>
              <Card title={<>Keep-awake settings<HelpIcon term="keep-awake" /></>}>
                <div className="stack">
                  {keys.map((k) => {
                    const spec = schema[k] || {};
                    if (isBool(k, spec, settings[k])) {
                      return (
                        <label className="field check" key={k}>
                          <input
                            type="checkbox"
                            checked={!!draft[k]}
                            onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.checked }))}
                          />
                          <span className="field-label">{spec.label || humanize(k)}</span>
                          {spec.help && <span className="field-help">{spec.help}</span>}
                        </label>
                      );
                    }
                    const { min, max } = bounds(k, spec);
                    return (
                      <label className="field" key={k}>
                        <span className="field-label">
                          {spec.label || humanize(k)}
                          {spec.unit && <span className="unit"> ({spec.unit})</span>}
                        </span>
                        <input
                          type="number"
                          inputMode="numeric"
                          min={min}
                          max={max}
                          step={1}
                          value={draft[k] ?? ''}
                          onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
                          aria-invalid={!!errors[k]}
                        />
                        <span className="field-range">
                          Range {min ?? '…'} – {max ?? '…'}
                        </span>
                        {spec.help && <span className="field-help">{spec.help}</span>}
                        {errors[k] && <span className="error-text">{errors[k]}</span>}
                      </label>
                    );
                  })}
                </div>
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
                <button type="button" className="btn" disabled={!dirty || !!busy} onClick={() => setDraft({ ...settings })}>
                  Undo changes
                </button>
                <button type="submit" className="btn primary" disabled={!dirty || hasErrors || !!busy}>
                  {busy === 'save' ? 'Saving…' : <><IconLock /> Save</>}
                </button>
              </div>
            </form>
          )}
        </>
      )}

      <Card title="Sessions" subtitle="For a lost or shared phone.">
        <button type="button" className="btn danger-outline block" onClick={logoutAll} disabled={!!busy}>
          {busy === 'logout-all' ? 'Logging out…' : <><IconLock /> Log out all sessions</>}
        </button>
      </Card>
    </div>
  );
}
