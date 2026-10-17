import { useEffect, useRef, useState } from 'react';
import { refreshAll } from '../services/refreshBus.js';

// Pull-to-refresh for the installed iOS PWA (no native one). Mounted once in the signed-in shell.
export const THRESHOLD = 70; // resisted pull distance (px) that arms the refresh
export const RESISTANCE = 0.5; // finger travel -> indicator travel
export const MAX_PULL = 110;
export const TIMEOUT_MS = 10000;
const FLASH_MS = 900;

function modalOpen() {
  return !!document.querySelector('[role="dialog"], [aria-modal="true"]') || document.body.style.position === 'fixed';
}

// True when the touch started inside something that scrolls itself (wide table, chart strip, scroll box).
function inScroller(el, boundary) {
  for (let n = el; n && n !== boundary && n !== document.body; n = n.parentElement) {
    const cs = window.getComputedStyle(n);
    const ox = cs.overflowX;
    const oy = cs.overflowY;
    if ((ox === 'auto' || ox === 'scroll') && n.scrollWidth > n.clientWidth) return true;
    if ((oy === 'auto' || oy === 'scroll') && n.scrollHeight > n.clientHeight) return true;
  }
  return false;
}

export default function PullToRefresh() {
  const [pull, setPull] = useState(0);
  const [phase, setPhase] = useState('idle'); // idle | pulling | refreshing | done
  const st = useRef({ active: false, startX: 0, startY: 0, pull: 0, busy: false });
  const timers = useRef([]);

  useEffect(() => {
    const s = st.current;
    const reset = () => {
      s.active = false;
      s.pull = 0;
      setPull(0);
      setPhase('idle');
    };

    function onStart(e) {
      s.active = false;
      if (s.busy || e.touches.length !== 1) return;
      const content = document.querySelector('.content');
      if (!content || !content.contains(e.target)) return;
      if ((window.scrollY || document.documentElement.scrollTop || 0) > 0) return;
      if (modalOpen() || inScroller(e.target, content)) return;
      s.active = true;
      s.startX = e.touches[0].clientX;
      s.startY = e.touches[0].clientY;
      s.pull = 0;
    }

    function onMove(e) {
      if (!s.active) return;
      const t = e.touches[0];
      const dy = t.clientY - s.startY;
      const dx = t.clientX - s.startX;
      if (s.pull === 0 && Math.abs(dx) > Math.abs(dy)) {
        s.active = false; // horizontal gesture
        return;
      }
      if (dy <= 0 || (window.scrollY || 0) > 0 || modalOpen()) {
        if (s.pull > 0) reset();
        else s.active = false;
        return;
      }
      if (e.cancelable) e.preventDefault(); // keep iOS rubber-banding off the indicator
      s.pull = Math.min(MAX_PULL, dy * RESISTANCE);
      setPull(s.pull);
      setPhase('pulling');
    }

    async function onEnd() {
      if (!s.active) return;
      const armed = s.pull >= THRESHOLD;
      s.active = false;
      if (!armed) {
        reset();
        return;
      }
      s.busy = true;
      setPull(THRESHOLD * 0.6);
      setPhase('refreshing');
      let timer;
      await Promise.race([refreshAll(), new Promise((r) => (timer = setTimeout(r, TIMEOUT_MS)))]).catch(() => {});
      clearTimeout(timer);
      s.busy = false;
      setPhase('done');
      timers.current.push(setTimeout(reset, FLASH_MS));
    }

    document.addEventListener('touchstart', onStart, { passive: true });
    document.addEventListener('touchmove', onMove, { passive: false });
    document.addEventListener('touchend', onEnd);
    document.addEventListener('touchcancel', reset);
    return () => {
      document.removeEventListener('touchstart', onStart);
      document.removeEventListener('touchmove', onMove);
      document.removeEventListener('touchend', onEnd);
      document.removeEventListener('touchcancel', reset);
      timers.current.forEach(clearTimeout);
    };
  }, []);

  if (phase === 'idle') return null;
  const label =
    phase === 'pulling' ? (pull >= THRESHOLD ? 'Release to refresh' : 'Pull to refresh') : phase === 'refreshing' ? 'Refreshing' : 'Updated';
  return (
    <div className="ptr" data-phase={phase} style={{ transform: `translateY(${Math.round(pull)}px)` }} role="status" aria-live="polite">
      <span
        className={`ptr-spinner${phase === 'refreshing' ? ' spin' : ''}`}
        aria-hidden="true"
        style={phase === 'pulling' ? { transform: `rotate(${pull * 4}deg)` } : undefined}
      />
      <span className="ptr-label">{label}</span>
    </div>
  );
}
