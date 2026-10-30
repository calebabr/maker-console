import { useCallback, useEffect, useState } from 'react';
import Card from './Card.jsx';
import Time from './Time.jsx';
import { useDialogs } from '../context/DialogContext.jsx';
import { api } from '../services/api.js';
import CollapsibleList from './CollapsibleList.jsx';

/**
 * Registered phones / browsers (S-33). The API only exposes the push-service host (for example
 * web.push.apple.com) and when each was added, never the endpoint or keys. Clear All needs a login, not 2FA:
 * it can only stop notifications, never trade.
 */
export default function PushDevicesCard() {
  const { confirm, notify } = useDialogs();
  const [devices, setDevices] = useState(null);
  const [error, setError] = useState(null);
  const [clearing, setClearing] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.pushSubscriptions();
      setDevices(Array.isArray(r && r.subscriptions) ? r.subscriptions : []);
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function clearAll() {
    if (clearing) return;
    const ok = await confirm({
      title: 'Clear All Devices?',
      message: 'Every phone and browser stops receiving Prior notifications until you turn them on again. Critical alerts still use ntfy.',
      confirmLabel: 'Clear All',
      danger: true,
    });
    if (!ok) return;
    setClearing(true);
    try {
      const r = await api.pushClearAll();
      const n = r && typeof r.removed === 'number' ? r.removed : 0;
      notify({ kind: 'ok', title: 'Devices Cleared', text: n === 1 ? '1 device removed.' : `${n} devices removed.` });
      await load();
    } catch (err) {
      notify({ kind: 'error', title: 'Not Cleared', text: err.message });
    } finally {
      setClearing(false);
    }
  }

  const list = devices || [];
  return (
    <Card title="Push Devices" subtitle="Phones and browsers that can receive Prior notifications.">
      {error && !devices && (
        <div className="notice notice-error" role="alert">
          Could not load devices. {error.message}
        </div>
      )}
      {devices && list.length === 0 && (
        <p className="muted" style={{ margin: 0 }}>
          No devices registered.
        </p>
      )}
      {list.length > 0 && (
        <CollapsibleList items={list} pageKey="push-devices" label="devices">
          {(vis) => (
            <ul aria-label="Registered push devices" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {vis.map((d, i) => (
                <li
                  key={`${d.host}-${d.created_utc}-${i}`}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 12,
                    padding: '10px 0',
                    borderTop: i ? '1px solid var(--border)' : 'none',
                    minWidth: 0,
                  }}
                >
                  <span className="mono" style={{ overflowWrap: 'anywhere' }}>
                    {d.host || 'Unknown'}
                  </span>
                  <span className="muted small" style={{ whiteSpace: 'nowrap' }}>
                    Added <Time value={d.created_utc} withDate />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CollapsibleList>
      )}
      <button
        type="button"
        className="btn block"
        style={{ marginTop: 10 }}
        onClick={clearAll}
        disabled={clearing || !devices || list.length === 0}
        aria-label="Clear all push devices"
      >
        {clearing ? 'Clearing…' : 'Clear All'}
      </button>
    </Card>
  );
}
