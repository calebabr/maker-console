import { useEffect, useState } from 'react';
import { subscribeSlow, subscribeStoreDown, SLOW_MS } from '../services/api.js';
import { readCache } from '../services/cache.js';
import { formatMoney, formatTime } from '../utils/format.js';

// Typical Render free-tier cold start.
export const WAKE_SECONDS = 60;

// Last status numbers the app saw (sessionStorage), or null: shown while the server is still asleep.
function staleNumbers() {
  const c = readCache('/api/status');
  if (!c || !c.data) return null;
  const s = c.data;
  const parts = [];
  if (s.account && typeof s.account.cash === 'number') parts.push(['Cash', formatMoney(s.account.cash)]);
  if (s.account && typeof s.account.exposure === 'number') parts.push(['At Risk', formatMoney(s.account.exposure)]);
  if (s.pnl && typeof s.pnl.d30 === 'number') parts.push(['Maker P&L (30 days)', formatMoney(s.pnl.d30, { sign: true })]);
  return parts.length ? { parts, at: c.ts } : null;
}

// Seconds since the request started (it was flagged slow after SLOW_MS), ticking once a second.
function useWakeElapsed(active) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!active) {
      setElapsed(0);
      return undefined;
    }
    const t0 = Date.now() - SLOW_MS;
    const tick = () => setElapsed(Math.max(0, (Date.now() - t0) / 1000));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [active]);
  return elapsed;
}

// Shown while any API request has been pending for more than 5 s
// (Render free tier: a sleeping service takes ~30-60 s to wake). Stop is in the header and keeps working.
export default function WakingBanner() {
  const [slow, setSlow] = useState(false);
  const [dbDown, setDbDown] = useState(false);
  const [online, setOnline] = useState(() => typeof navigator === 'undefined' || navigator.onLine !== false);
  const elapsed = useWakeElapsed(slow);

  useEffect(() => subscribeSlow(setSlow), []);
  useEffect(() => subscribeStoreDown(setDbDown), []);
  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => {
      window.removeEventListener('online', on);
      window.removeEventListener('offline', off);
    };
  }, []);

  // S-15: Atlas unreachable -> every endpoint except Stop returns 503. Offline data stays on screen.
  const db = dbDown ? (
    <div className="banner banner-db" role="alert">
      Server can&apos;t reach the database. Stop still works. Showing the last data received.
    </div>
  ) : null;

  if (!online) {
    return (
      <div className="banner banner-offline" role="status">
        You are offline. Showing the last data received.
      </div>
    );
  }
  if (!slow) return db;

  const frac = Math.min(0.95, elapsed / WAKE_SECONDS);
  const stale = staleNumbers();
  const long = elapsed > WAKE_SECONDS;
  return (
    <>
      {db}
      <div className="banner banner-waking" role="status">
        <div className="waking-row">
          <span className="spinner" aria-hidden="true" />
          <span className="waking-title">
            {long ? 'Still waking the server… this is taking longer than usual' : 'Waking the server… usually 30–60 s'}
          </span>
        </div>
        <div
          className="waking-bar"
          role="progressbar"
          aria-label="Server wake-up progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(frac * 100)}
        >
          <span className="waking-fill" style={{ width: `${(frac * 100).toFixed(0)}%` }} />
        </div>
        {stale && (
          <div className="waking-stale">
            <span className="tag tag-warn">Stale</span>{' '}
            {stale.parts.map(([k, v], i) => (
              <span key={k}>
                {i > 0 && ' · '}
                {k} {v}
              </span>
            ))}{' '}
            <span className="muted">as of {formatTime(stale.at)}</span>
          </div>
        )}
        <div className="waking-note">Stop still works.</div>
      </div>
    </>
  );
}
