// The one route -> page title map for signed-in pages, plus the navigation layout. Used by the sticky page
// title bar (components/PageTitle.jsx), document.title, the bottom tabs, the wide-screen sidebar and the
// More page. Keep it in step with the <Route>s in App.jsx (src/test/page-title.test.jsx checks every entry
// renders).
export const APP_NAME = 'Keel';
export const TAGLINE_LEAD = 'Your strategies.';
export const TAGLINE_ACCENT = 'One tap away.';
export const TAGLINE = `${TAGLINE_LEAD} ${TAGLINE_ACCENT}`;

export const ROUTE_TITLES = [
  { path: '/', title: 'Overview' },
  { path: '/analytics', title: 'Analytics' },
  { path: '/activity', title: 'Activity' },
  { path: '/markets', title: 'Markets' },
  { path: '/more', title: 'More' },
  { path: '/health', title: 'Health' },
  { path: '/settings', title: 'Settings' },
  { path: '/account', title: 'Account' },
  { path: '/alerts', title: 'Alerts' },
  { path: '/mode', title: 'Mode' },
  { path: '/server', title: 'Server' },
  { path: '/logs', title: 'Logs' },
  { path: '/arb', title: 'Arbitrage' },
  { path: '/terminology', title: 'Terminology' },
];

// Bottom tabs on phones, in the owner's order. (UX contract section 4.)
export const TAB_ROUTES = [
  { path: '/', label: 'Overview' },
  { path: '/analytics', label: 'Analytics' },
  { path: '/activity', label: 'Activity' },
  { path: '/markets', label: 'Markets' },
  { path: '/more', label: 'More' },
];

// What the More page lists (and the sidebar shows under its main entries).
export const MORE_ROUTES = [
  { path: '/health', label: 'Health', blurb: 'Monitor summary and health check' },
  { path: '/settings', label: 'Settings', blurb: 'Limits, polling, display and text size' },
  { path: '/account', label: 'Account', blurb: 'Your Kalshi balance and open positions' },
  { path: '/alerts', label: 'Alerts', blurb: 'Past alerts and notification choices' },
  { path: '/mode', label: 'Mode', blurb: 'Switch between demo and live' },
  { path: '/server', label: 'Server', blurb: 'Keep-awake pings and the external pinger' },
  { path: '/logs', label: 'Logs', blurb: 'Server log and audit trail' },
  { path: '/arb', label: 'Arbitrage', blurb: 'The bracket arbitrage scanner' },
  { path: '/terminology', label: 'Terminology', blurb: 'Plain-language glossary' },
];

function normalise(pathname) {
  return String(pathname || '/').replace(/\/+$/, '') || '/';
}

/** Page title for a pathname ("/markets/" and "/markets" both match), or null if unknown. */
export function titleForPath(pathname) {
  const p = normalise(pathname);
  const hit = ROUTE_TITLES.find((r) => r.path === p);
  return hit ? hit.title : null;
}

/** document.title for a page: "Markets · Keel"; just "Keel" when unknown. */
export function documentTitle(title) {
  return title ? `${title} · ${APP_NAME}` : APP_NAME;
}

/** True when `pathname` is one of the pages listed under More (the More tab then shows as active). */
export function isMorePath(pathname) {
  const p = normalise(pathname);
  return p === '/more' || MORE_ROUTES.some((r) => p === r.path || p.startsWith(`${r.path}/`));
}
