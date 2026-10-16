import {
  IconAccount, IconActivity, IconAnalytics, IconArb, IconBell, IconHealth, IconLogs, IconMarkets, IconMode,
  IconBook, IconMore, IconOverview, IconServer, IconSettings,
} from './Icons.jsx';

// Route -> the icon shown in the tab bar, sidebar, More page and the page-title bar.
const ICONS = {
  '/': IconOverview,
  '/analytics': IconAnalytics,
  '/activity': IconActivity,
  '/markets': IconMarkets,
  '/more': IconMore,
  '/health': IconHealth,
  '/settings': IconSettings,
  '/account': IconAccount,
  '/alerts': IconBell,
  '/mode': IconMode,
  '/server': IconServer,
  '/logs': IconLogs,
  '/arb': IconArb,
  '/terminology': IconBook,
};

export function iconForPath(path) {
  return ICONS[path] || null;
}
