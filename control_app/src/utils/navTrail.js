// "Back to <Page>" support for the Terminology page (UX item 32).
//
// A capture-phase click listener (installed once, when this module is imported by HelpIcon / Terminology)
// notices any tap on an in-app link to /terminology and remembers where the person was and how far they had
// scrolled. That covers the (?) popover's "More" link and the More menu without either file knowing about it.
// The trail lives in memory and sessionStorage (never localStorage; it holds only a path and a number).

import { titleForPath } from './routes.js';

const KEY = 'keel.helpTrail';
let trail = null;

function read() {
  if (trail) return trail;
  try {
    const raw = typeof sessionStorage !== 'undefined' && sessionStorage.getItem(KEY);
    if (raw) {
      const t = JSON.parse(raw);
      if (t && typeof t.path === 'string' && t.path.startsWith('/')) trail = { path: t.path, scrollY: Number(t.scrollY) || 0 };
    }
  } catch {
    /* storage unavailable: memory only */
  }
  return trail;
}

export function recordTrail(path, scrollY) {
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('/terminology')) return;
  trail = { path, scrollY: Math.max(0, Math.round(Number(scrollY) || 0)) };
  try {
    sessionStorage.setItem(KEY, JSON.stringify(trail));
  } catch {
    /* ignore */
  }
}

export function clearTrail() {
  trail = null;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

/** {path, scrollY, label} for the page the person came from, or null (direct load, or came from Terminology). */
export function getTrail() {
  const t = read();
  if (!t) return null;
  const pathname = t.path.split(/[?#]/)[0];
  return { ...t, label: titleForPath(pathname) || 'Previous Page' };
}

function onClick(e) {
  if (e.defaultPrevented || e.button > 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
  const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
  if (!a) return;
  let url;
  try {
    url = new URL(a.getAttribute('href'), window.location.href);
  } catch {
    return;
  }
  if (url.origin !== window.location.origin || !/^\/terminology\/?$/.test(url.pathname)) return;
  const here = window.location.pathname + window.location.search;
  if (here.startsWith('/terminology')) return; // moving around inside the glossary keeps the original trail
  recordTrail(here, window.scrollY);
}

if (typeof document !== 'undefined' && !globalThis.__keelTrailInstalled) {
  globalThis.__keelTrailInstalled = true;
  document.addEventListener('click', onClick, true);
}

/** Navigate back and restore scroll once the page is tall enough (pages fetch data, so retry briefly). */
export function restoreScroll(y, tries = 20) {
  if (!y || typeof window === 'undefined' || typeof window.scrollTo !== 'function') return;
  const step = (n) => {
    const max = document.documentElement.scrollHeight - window.innerHeight;
    if (max >= y || n <= 0) {
      try {
        window.scrollTo(0, Math.min(y, Math.max(0, max)));
      } catch {
        /* jsdom: not implemented */
      }
      return;
    }
    setTimeout(() => step(n - 1), 50);
  };
  step(tries);
}
