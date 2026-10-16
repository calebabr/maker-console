import { useEffect, useState } from 'react';
import { getSince } from '../services/overviewApi.js';
import { readLastVisit, sinceLabel, sinceParts, writeLastVisit } from '../utils/overview.js';
import '../styles/overview.css';

/**
 * "Since You Last Checked" (item 30): new fills, markets settled and the P&L change since this device's
 * last visit (localStorage keel.lastVisit). Read-only. The last-visit time is written when the page is left
 * or hidden, and when the card is dismissed. First visit: writes now, shows nothing. Hidden when nothing
 * changed or the request fails.
 */
export default function SinceCard() {
  const [since, setSince] = useState(null);
  const [from, setFrom] = useState(null);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    let alive = true;
    const mountedAt = Date.now();
    const last = readLastVisit();
    if (!last) writeLastVisit();
    else {
      setFrom(last);
      getSince(last)
        .then((d) => alive && setSince(d))
        .catch(() => {});
    }
    const stamp = () => writeLastVisit();
    const hide = () => document.visibilityState === 'hidden' && stamp();
    document.addEventListener('visibilitychange', hide);
    window.addEventListener('pagehide', stamp);
    return () => {
      alive = false;
      document.removeEventListener('visibilitychange', hide);
      window.removeEventListener('pagehide', stamp);
      // Not for the instant unmount of a StrictMode dev double-mount.
      if (Date.now() - mountedAt > 2000) stamp();
    };
  }, []);

  if (gone || !since || !from) return null;
  const parts = sinceParts(since, sinceLabel(from));
  if (!parts.length) return null;
  return (
    <section className="since-card" data-testid="since-card" aria-label="Since you last checked">
      <div className="since-head">
        <h2 className="since-title">Since You Last Checked</h2>
        <button
          type="button"
          className="since-dismiss"
          aria-label="Dismiss"
          onClick={() => {
            writeLastVisit();
            setGone(true);
          }}
        >
          ×
        </button>
      </div>
      <p className="since-line">{parts.join(' · ')}</p>
    </section>
  );
}
