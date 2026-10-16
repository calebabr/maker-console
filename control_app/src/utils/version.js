// App version handshake (docs/RELEASE_CONTRACT.md section 5). The version is injected at build time from
// package.json (vite.config.js `define`); unknown builds fall back to 0.0.0.
/* global __APP_VERSION__ */
export const APP_VERSION = typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : '0.0.0';

function parts(v) {
  const m = /^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?/.exec(String(v == null ? '' : v).trim());
  return m ? [Number(m[1]), Number(m[2] || 0), Number(m[3] || 0)] : null;
}

/** True only when both versions parse and `a` is strictly older than `b`. Anything unreadable is "not older". */
export function isOlder(a, b) {
  const x = parts(a);
  const y = parts(b);
  if (!x || !y) return false;
  for (let i = 0; i < 3; i++) {
    if (x[i] !== y[i]) return x[i] < y[i];
  }
  return false;
}

/** First 8 characters of an id, for display. */
export function shortId(v) {
  const s = String(v == null ? '' : v);
  return s.length > 8 ? s.slice(0, 8) : s;
}
