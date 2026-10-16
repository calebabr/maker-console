// Last-known API data, kept in sessionStorage (cleared when the tab/app closes)
// so pages can still show something when the server is unreachable.
// Never stores the token (that lives under its own key in api.js).

const PREFIX = 'km.cache:';

function store() {
  try {
    return typeof sessionStorage !== 'undefined' ? sessionStorage : null;
  } catch {
    return null;
  }
}

export function writeCache(key, data) {
  const s = store();
  if (!s) return;
  try {
    s.setItem(PREFIX + key, JSON.stringify({ ts: Date.now(), data }));
  } catch {
    /* quota or private mode: ignore */
  }
}

// -> { ts, data } | null
export function readCache(key) {
  const s = store();
  if (!s) return null;
  try {
    const raw = s.getItem(PREFIX + key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function clearCache() {
  const s = store();
  if (!s) return;
  try {
    const keys = [];
    for (let i = 0; i < s.length; i++) {
      const k = s.key(i);
      if (k && k.startsWith(PREFIX)) keys.push(k);
    }
    keys.forEach((k) => s.removeItem(k));
  } catch {
    /* ignore */
  }
}
