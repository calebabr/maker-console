import { useEffect, useState } from 'react';

export const SCROLL_IDLE_MS = 2500;
// Within this many px of the top counts as "at the top" (the title bar never hides there).
export const TOP_THRESHOLD_PX = 8;

function readScrollTop(target) {
  if (!target) return 0;
  if (target === window) return window.scrollY || document.documentElement.scrollTop || 0;
  return target.scrollTop || 0;
}

/**
 * Tracks the page scroll for the sticky page title.
 * Returns { atTop, idle }: idle turns true `delay` ms after the last scroll event and false on the
 * next one. The app scrolls the window (the shell is min-height: 100vh; <main> does not overflow),
 * so the default target is window. `resetKey` (e.g. the pathname) re-reads the position and clears idle.
 */
export default function useScrollIdle({ delay = SCROLL_IDLE_MS, target, resetKey } = {}) {
  const el = target === undefined ? (typeof window === 'undefined' ? null : window) : target;
  const [atTop, setAtTop] = useState(() => readScrollTop(el) <= TOP_THRESHOLD_PX);
  const [idle, setIdle] = useState(false);

  useEffect(() => {
    if (!el) return undefined;
    let timer = null;
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(() => setIdle(true), delay);
    };
    const onScroll = () => {
      setAtTop(readScrollTop(el) <= TOP_THRESHOLD_PX);
      setIdle(false);
      arm();
    };
    // New page (or new target): start shown, then the normal idle countdown.
    setAtTop(readScrollTop(el) <= TOP_THRESHOLD_PX);
    setIdle(false);
    arm();
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      clearTimeout(timer);
      el.removeEventListener('scroll', onScroll);
    };
  }, [el, delay, resetKey]);

  return { atTop, idle };
}
