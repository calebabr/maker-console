// Install / push detection for item 45. iOS only delivers web push to a Home Screen web app (iOS 16.4+).

/** True when running as an installed app (Home Screen / standalone window), not in a browser tab. */
export function isInstalledApp() {
  try {
    if (typeof navigator !== 'undefined' && navigator.standalone === true) return true; // iOS
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      return !!window.matchMedia('(display-mode: standalone)').matches;
    }
  } catch {
    /* ignore */
  }
  return false;
}

/** The browser can do web push at all (service worker + PushManager + Notification). */
export function pushSupported() {
  try {
    return typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  } catch {
    return false;
  }
}

// VAPID public key (base64url) -> Uint8Array for pushManager.subscribe.
export function urlBase64ToUint8Array(b64) {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}
