import { useState } from 'react';
import KeyValue from '../components/KeyValue.jsx';
import HelpIcon from '../components/HelpIcon.jsx';
import { ErrorNotice, Loading, StaleNotice } from '../components/Notices.jsx';
import { useResource } from '../hooks/useResource.js';
import { api } from '../services/api.js';
import { dateTime, humanize } from '../utils/format.js';
import CollapsibleList from '../components/CollapsibleList.jsx';

const LINES = 200;
const AUDIT_LIMIT = 100;

function LogLines() {
  const { data, error, loading, staleAt, reload } = useResource(() => api.getLogs(LINES), `/api/logs?lines=${LINES}`);
  const lines = (data && data.lines) || [];
  const [newestFirst, setNewestFirst] = useState(true);
  const shown = newestFirst ? [...lines].reverse() : lines;
  return (
    <>
      <div className="toolbar">
        <button type="button" className="btn small" onClick={reload} disabled={loading}>
          Refresh
        </button>
        <button type="button" className="btn small" onClick={() => setNewestFirst((v) => !v)}>
          {newestFirst ? 'Newest first' : 'Oldest first'}
        </button>
      </div>
      <StaleNotice staleAt={error ? staleAt : null} />
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data && <Loading />}
      {data && !lines.length && <p className="muted">No log lines.</p>}
      {lines.length > 0 && <pre className="log">{shown.map((l) => (typeof l === 'string' ? l : JSON.stringify(l))).join('\n')}</pre>}
    </>
  );
}

function AuditItems() {
  const { data, error, loading, staleAt, reload } = useResource(
    () => api.getAudit(AUDIT_LIMIT),
    `/api/audit?limit=${AUDIT_LIMIT}`,
  );
  const items = (data && data.items) || [];
  return (
    <>
      <div className="toolbar">
        <button type="button" className="btn small" onClick={reload} disabled={loading}>
          Refresh
        </button>
      </div>
      <StaleNotice staleAt={error ? staleAt : null} />
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data && <Loading />}
      {data && !items.length && <p className="muted">No audit entries.</p>}
      <CollapsibleList items={items} pageKey="logs-audit" label="audit entries" pinned={(it) => it.ok === false}>
        {(vis) => (
          <ul className="rows">
            {vis.map((it, i) => (
              <li key={`${it.ts}-${i}`} className="row-item">
                <div className="row-top">
                  <span className={`pill ${it.ok === false ? 'pill-danger' : 'pill-ok'}`}>{humanize(it.action || '?')}</span>
                  <span className="muted small">{dateTime(it.ts)}</span>
                </div>
                <div className="row-meta muted small">
                  {it.user && <span>{it.user}</span>}
                  {it.ip && <span className="mono">{it.ip}</span>}
                  {it.ok === false && <span className="neg">failed</span>}
                </div>
                {it.details != null && (
                  <details className="more-details">
                    <summary>Details</summary>
                    <KeyValue data={it.details} />
                    {it.ua && <div className="muted small">UA: {it.ua}</div>}
                  </details>
                )}
              </li>
            ))}
          </ul>
        )}
      </CollapsibleList>
    </>
  );
}

export default function Logs() {
  const [tab, setTab] = useState('logs');
  return (
    <div className="page">
      <h1>Logs</h1>
      <div className="segmented" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'logs'} className={tab === 'logs' ? 'active' : ''} onClick={() => setTab('logs')}>
          Server log
        </button>
        <button type="button" role="tab" aria-selected={tab === 'audit'} className={tab === 'audit' ? 'active' : ''} onClick={() => setTab('audit')}>
          Audit
        </button>
      </div>
      {tab === 'audit' && (
        <p className="hint">
          Every sign-in and control action, with its result.<HelpIcon term="audit-log" />
        </p>
      )}
      {tab === 'logs' ? <LogLines /> : <AuditItems />}
    </div>
  );
}
