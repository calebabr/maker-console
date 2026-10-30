import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import Card from './Card.jsx';
import Chip from './Chip.jsx';
import { useDialogs } from '../context/DialogContext.jsx';
import { api } from '../services/api.js';
import { isInstalledApp, pushSupported, urlBase64ToUint8Array } from '../utils/pwa.js';

async function currentSubscription() {
  try {
    const reg = await navigator.serviceWorker.ready;
    return (await reg.pushManager.getSubscription()) || null;
  } catch {
    return null;
  }
}

/**
 * Web push notifications (item 45). The Enable button only exists when Prior runs as an installed Home Screen
 * app: iPhone only delivers web push to those. In a normal browser tab it shows how to install instead.
 * Subscription endpoint and keys go straight from the browser to the API; they are never shown or logged here.
 */
export default function PushCard() {
  const { notify } = useDialogs();
  const installed = isInstalledApp();
  const supported = pushSupported();
  const [subscribed, setSubscribed] = useState(false);
  const [busy, setBusy] = useState(null); // 'enable' | 'disable' | 'test'
  const [problem, setProblem] = useState(null);

  useEffect(() => {
    let alive = true;
    if (installed && supported) {
      currentSubscription().then((s) => alive && setSubscribed(!!s));
    }
    return () => {
      alive = false;
    };
  }, [installed, supported]);

  const enable = useCallback(async () => {
    setBusy('enable');
    setProblem(null);
    try {
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') {
        setProblem('Notifications are blocked for Prior. Turn them on in your phone settings, then try again.');
        return;
      }
      const key = await api.pushPublicKey();
      if (!key || !key.enabled || !key.public_key) {
        setProblem('Notifications are not set up on the server yet.');
        return;
      }
      const reg = await navigator.serviceWorker.ready;
      const sub =
        (await reg.pushManager.getSubscription()) ||
        (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key.public_key) }));
      await api.pushSubscribe(sub.toJSON ? sub.toJSON() : sub);
      setSubscribed(true);
      notify({ kind: 'ok', title: 'Notifications On', text: 'Send a test to confirm they arrive.' });
    } catch (err) {
      setProblem(err && err.message ? err.message : 'Could not turn notifications on.');
    } finally {
      setBusy(null);
    }
  }, [notify]);

  const disable = useCallback(async () => {
    setBusy('disable');
    setProblem(null);
    try {
      const sub = await currentSubscription();
      if (sub) {
        const endpoint = sub.endpoint;
        await sub.unsubscribe();
        await api.pushUnsubscribe(endpoint);
      }
      setSubscribed(false);
      notify({ kind: 'ok', title: 'Notifications Off' });
    } catch (err) {
      setProblem(err && err.message ? err.message : 'Could not turn notifications off.');
    } finally {
      setBusy(null);
    }
  }, [notify]);

  const test = useCallback(async () => {
    setBusy('test');
    setProblem(null);
    try {
      const r = await api.pushTest();
      if (r && r.sent > 0) notify({ kind: 'ok', title: 'Test Sent', text: 'It should arrive in a few seconds.' });
      else notify({ kind: 'error', title: 'Test Not Delivered', text: 'No device received it. Turn notifications off and on again.' });
    } catch (err) {
      setProblem(err && err.message ? err.message : 'Could not send the test.');
    } finally {
      setBusy(null);
    }
  }, [notify]);

  return (
    <Card title="Notifications" subtitle="Everyday alerts arrive under the Prior icon. Critical alerts always use ntfy as well.">
      {!installed && (
        <div data-testid="push-install-steps">
          <p className="hint" style={{ marginTop: 0 }}>
            Phones only allow notifications for an installed Home Screen app. In Chrome on iPhone:
          </p>
          <ol className="steps">
            <li>Tap the Share button in Chrome.</li>
            <li>Choose Add to Home Screen, then Add.</li>
            <li>Open Prior from its new Home Screen icon.</li>
            <li>Come back to Settings and tap Enable Notifications.</li>
          </ol>
        </div>
      )}
      {installed && !supported && <p className="muted">This device cannot receive push notifications.</p>}
      {installed && supported && (
        <div className="stack">
          <div>
            <Chip tone={subscribed ? 'ok' : 'muted'}>{subscribed ? 'On' : 'Off'}</Chip>
          </div>
          {!subscribed ? (
            <button type="button" className="btn primary block" onClick={enable} disabled={busy != null}>
              {busy === 'enable' ? 'Turning On…' : 'Enable Notifications'}
            </button>
          ) : (
            <div className="btn-row">
              <button type="button" className="btn" onClick={test} disabled={busy != null}>
                {busy === 'test' ? 'Sending…' : 'Send Test'}
              </button>
              <button type="button" className="btn" onClick={disable} disabled={busy != null}>
                {busy === 'disable' ? 'Turning Off…' : 'Turn Off'}
              </button>
            </div>
          )}
        </div>
      )}
      {problem && (
        <div className="notice notice-error" role="alert">
          {problem}
        </div>
      )}
      <p className="hint">
        Choose what gets sent on the <Link to="/alerts">Alerts</Link> page.
      </p>
    </Card>
  );
}
