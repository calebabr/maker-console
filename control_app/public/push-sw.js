// Web push handlers, loaded into the generated service worker (vite.config.js: workbox.importScripts).
// Payloads are short ({title, body, type, url}); nothing sensitive is ever sent. `url` is an in-app path.

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = {};
  }
  const title = typeof data.title === 'string' && data.title ? data.title : 'Keel';
  const options = {
    body: typeof data.body === 'string' ? data.body : '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    tag: typeof data.type === 'string' ? data.type : undefined,
    data: { url: data.url },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Only same-origin in-app paths are followed; anything else opens the app root.
function safePath(u) {
  if (typeof u !== 'string' || u.charAt(0) !== '/' || u.charAt(1) === '/' || u.indexOf('\\') !== -1) return '/';
  // Browsers strip tab/newline before parsing, so "/<TAB>/evil.example" would become "//evil.example". Reject any
  // control or whitespace character (S-34), and re-check that the resolved URL stays on this origin.
  if (/[\u0000-\u0020\u007f-\u009f\u2028\u2029]/.test(u)) return '/';
  try {
    if (new URL(u, self.location.origin).origin !== self.location.origin) return '/';
  } catch (e) {
    return '/';
  }
  return u;
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const path = safePath(event.notification.data && event.notification.data.url);
  const target = new URL(path, self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if (c.url.indexOf(self.location.origin) === 0 && 'focus' in c) {
          return c.focus().then((f) => (f && 'navigate' in f ? f.navigate(target) : undefined));
        }
      }
      return self.clients.openWindow ? self.clients.openWindow(target) : undefined;
    }),
  );
});
