import { forwardRef, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import Chip from './Chip.jsx';
import HelpIcon from './HelpIcon.jsx';
import HeartbeatStrip from './HeartbeatStrip.jsx';
import StopButton from './StopButton.jsx';
import PnlScope from './PnlScope.jsx';
import PnlCombined from './PnlCombined.jsx';
import { TODAY_EMPTY } from '../utils/pnlScopes.js';
import { IconLock } from './Icons.jsx';
import { formatAgo, formatMoney, formatUntil } from '../utils/format.js';
import { abnormalities, isRunning, overallState, ringProgress, runTimes } from '../utils/overview.js';
import { riskLimit } from '../utils/status.js';
import '../styles/overview.css';

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

/** Re-render every `ms` so "next in 5 min" and the ring stay fresh between status polls. */
export function useNow(ms = 30000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

export function pnlClass(v) {
  return isNum(v) ? (v < 0 ? 'neg' : v > 0 ? 'pos' : '') : 'muted';
}

/** Status ring: a dot in the middle, the arc fills as the next run approaches. */
export function StatusRing({ state, progress, size = 44 }) {
  const r = 18;
  const c = 2 * Math.PI * r;
  return (
    <svg className={`ov-ring ov-ring-${state}`} width={size} height={size} viewBox="0 0 44 44" aria-hidden="true">
      <circle className="ov-ring-track" cx="22" cy="22" r={r} fill="none" strokeWidth="3" />
      <circle
        className="ov-ring-arc"
        cx="22"
        cy="22"
        r={r}
        fill="none"
        strokeWidth="3"
        strokeLinecap="round"
        strokeDasharray={`${(c * progress).toFixed(1)} ${c.toFixed(1)}`}
        transform="rotate(-90 22 22)"
      />
      <circle className="ov-ring-dot" cx="22" cy="22" r="6" />
    </svg>
  );
}

function ActionsMenu({ onRunOnce, disabled }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false);
    const esc = (e) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('pointerdown', away);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('pointerdown', away);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);
  return (
    <div className="ov-menu" ref={ref}>
      <button
        type="button"
        className="ov-menu-btn"
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span aria-hidden="true">⋯</span>
      </button>
      {open && (
        <div className="ov-menu-list" role="menu">
          <button
            type="button"
            role="menuitem"
            disabled={disabled}
            onClick={() => {
              setOpen(false);
              onRunOnce();
            }}
          >
            Run Once
          </button>
          <Link role="menuitem" to="/mode" onClick={() => setOpen(false)}>
            Mode and Live Access
          </Link>
        </div>
      )}
    </div>
  );
}

/**
 * The status and control card (item 25): state ring + label + mode chip, a context button
 * (Stop while running, Start otherwise; Stop is ALWAYS present and identical to the header one),
 * three tiles, the heartbeat strip and one timing line. Abnormal facts show in red on their own.
 */
const StatusCard = forwardRef(function StatusCard({ status, busy, onStart, onRunOnce }, ref) {
  const now = useNow();
  const running = isRunning(status);
  const st = overallState(status, now);
  const bad = abnormalities(status, now);
  const { last, next } = runTimes(status);
  const a = status && status.account;
  const limit = riskLimit(status);
  const today = status && status.pnl_today && status.pnl_today.total;
  const d30 = status && status.pnl && status.pnl.d30;
  const overdue = bad.some((b) => b.key === 'overdue');
  const mode = status && status.mode === 'live' ? 'live' : 'demo';

  return (
    <section className={`card hero status-card state-${st.key}`} ref={ref} data-testid="status-card" data-state={st.key}>
      <div className="ov-top">
        <StatusRing state={st.key} progress={ringProgress(status, now)} />
        <div className="ov-state">
          <div className="ov-state-label" data-testid="state-label">{st.label}</div>
          <div className="ov-chips">
            {status && <Chip tone={mode === 'live' ? 'danger' : 'warn'}>{mode === 'live' ? 'Live' : 'Demo'}</Chip>}
            <HelpIcon term="stop" />
          </div>
        </div>
        <ActionsMenu onRunOnce={onRunOnce} disabled={!!busy || !status} />
      </div>

      {bad.length > 0 && (
        <ul className="ov-bad" data-testid="abnormal">
          {bad.map((b) => (
            <li key={b.key} className="error-text">{b.text}</li>
          ))}
        </ul>
      )}

      <dl className="ov-tiles" data-testid="status-tiles">
        <div>
          <dt>Today's P&amp;L</dt>
          {status && !isNum(today) ? (
            <dd className="ov-empty" data-testid="today-empty">{TODAY_EMPTY}</dd>
          ) : (
            <dd className={pnlClass(today)}>{formatMoney(today, { sign: true })}</dd>
          )}
          <PnlScope id="todayShort" ctx={{ tz: status && status.pnl_today_tz }} as="dd" />
        </div>
        <div>
          <dt>Maker P&amp;L (30 days)</dt>
          <dd className={pnlClass(d30)}>{formatMoney(d30, { sign: true })}</dd>
          <PnlScope id="maker" as="dd" />
        </div>
        <div>
          <dt>At Risk</dt>
          <dd>{formatMoney(a && a.exposure)}{isNum(limit) && isNum(a && a.exposure) && a.exposure > limit ? <span className="error-text"> !</span> : null}</dd>
        </div>
      </dl>
      <PnlCombined status={status} />

      <HeartbeatStrip passes={status && status.recent_passes} />

      <p className="ov-timing" data-testid="timing-line">
        <span>Last Run {last ? formatAgo(last) : '—'}</span>
        {running && next && (
          <>
            {' · '}
            <span className={overdue ? 'error-text' : undefined}>Next {formatUntil(next)}</span>
          </>
        )}
      </p>

      <div className="ov-actions">
        {running || st.key === 'error' ? (
          <>
            <StopButton />
            {!running && (
              <button type="button" className="btn" onClick={onStart} disabled={!!busy || !status}>
                {busy === 'start' ? 'Starting…' : <><IconLock /> Start</>}
              </button>
            )}
          </>
        ) : (
          <>
            <button type="button" className="btn primary ov-start" onClick={onStart} disabled={!!busy || !status}>
              {busy === 'start' ? 'Starting…' : <><IconLock /> Start</>}
            </button>
            <StopButton />
          </>
        )}
      </div>
      {busy === 'run' && <p className="hint">Running one pass…</p>}
    </section>
  );
});

export default StatusCard;
