import { useEffect, useState } from 'react';
import Card from './Card.jsx';
import { IconLock } from './Icons.jsx';
import { ErrorNotice } from './Notices.jsx';
import { useDialogs } from '../context/DialogContext.jsx';
import { useResource } from '../hooks/useResource.js';
import { api } from '../services/api.js';
import { DASH, dateTime, parseDate, until } from '../utils/format.js';

// A release waits here for the owner's decision. Approve needs a fresh 2FA code (like Start);
// Reject needs only the login. Hidden when nothing is pending or the server answers 404 (feature off).

function reasonText(err) {
  if (err.status === 409) return 'This release was already decided or has expired. Nothing was changed.';
  if (err.status === 404) return 'This release no longer exists. Nothing was changed.';
  if (err.isNetwork) return 'Could not reach the server. Nothing was changed. Check your connection and try again.';
  return err.message;
}

export default function PendingReleaseCard() {
  const { data, error, reload, setData } = useResource(() => api.getReleasePending(), null);
  const { twoFactor, confirm, notify } = useDialogs();
  const [busy, setBusy] = useState(null);
  const [problem, setProblem] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(id);
  }, []);

  const rel = data && data.release;
  if (error && error.status === 404) return null; // feature off: stay invisible
  if (!rel && !outcome && !error) return null;

  const exp = rel ? parseDate(rel.expires_at) : null;
  const expired = !!exp && exp.getTime() <= now;

  async function approve() {
    if (busy) return;
    setBusy('approve');
    setProblem(null);
    try {
      const r = await twoFactor({
        title: 'Approve this release?',
        message: (
          <p>
            Version <strong>{rel.version}</strong> ({String(rel.commit || '').slice(0, 7)}) will be deployed to the
            server. A running bot hands over to the new version.
          </p>
        ),
        confirmLabel: 'Approve',
        action: (code) => api.approveRelease(rel.id, code),
      });
      if (r) {
        setOutcome({ kind: 'ok', text: `Release ${rel.version} approved. The deploy continues on its own.` });
        setData({ release: null });
        notify({ kind: 'ok', title: 'Release approved' });
      }
    } catch (err) {
      setProblem(reasonText(err));
      if (err.status === 409 || err.status === 404) reload();
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    if (busy) return;
    setBusy('reject');
    setProblem(null);
    const yes = await confirm({
      title: 'Reject this release?',
      message: `Version ${rel.version} will not be deployed. The release workflow stops.`,
      confirmLabel: 'Reject',
      danger: true,
    });
    if (!yes) {
      setBusy(null);
      return;
    }
    try {
      await api.rejectRelease(rel.id);
      setOutcome({ kind: 'info', text: `Release ${rel.version} rejected. Nothing was deployed.` });
      setData({ release: null });
      notify({ kind: 'ok', title: 'Release rejected' });
    } catch (err) {
      setProblem(reasonText(err));
      if (err.status === 409 || err.status === 404) reload();
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Pending Release" subtitle="Waiting for your decision before anything is deployed.">
      {error && !rel && <ErrorNotice error={error} onRetry={reload} />}
      {outcome && !rel && (
        <div className={`notice notice-${outcome.kind}`} role="status">
          {outcome.text}
        </div>
      )}
      {rel && (
        <>
          <dl className="stats">
            <div>
              <dt>Version</dt>
              <dd className="mono">{rel.version}</dd>
            </div>
            <div>
              <dt>Commit</dt>
              <dd className="mono">{String(rel.commit || '').slice(0, 7) || DASH}</dd>
            </div>
            <div>
              <dt>Tests (CI)</dt>
              <dd>{rel.ci || DASH}</dd>
            </div>
            <div>
              <dt>Time Left</dt>
              <dd title={rel.expires_at ? dateTime(rel.expires_at) : undefined}>
                {expired ? <span className="neg">Expired</span> : exp ? until(rel.expires_at, now).replace(/^in /, '') : DASH}
              </dd>
            </div>
          </dl>
          {rel.summary && <p className="release-summary">{rel.summary}</p>}
          {expired && (
            <div className="notice notice-error" role="alert">
              This release request has expired. Approving is no longer possible; start the release again.
            </div>
          )}
          {problem && (
            <div className="notice notice-error" role="alert">
              {problem}
            </div>
          )}
          <div className="release-actions">
            <button type="button" className="btn primary" onClick={approve} disabled={!!busy || expired}>
              {busy === 'approve' ? 'Approving…' : <><IconLock /> Approve</>}
            </button>
            <button type="button" className="btn danger-outline" onClick={reject} disabled={!!busy}>
              {busy === 'reject' ? 'Rejecting…' : 'Reject'}
            </button>
          </div>
        </>
      )}
    </Card>
  );
}
