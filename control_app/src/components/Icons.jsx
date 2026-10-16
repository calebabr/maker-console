// Minimal stroke icons (inline SVG, currentColor). No emoji in the chrome.
const base = {
  width: 20,
  height: 20,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
};

export const IconOverview = (p) => (
  <svg {...base} {...p}>
    <path d="M3 12h4l3-7 4 14 3-7h4" />
  </svg>
);
export const IconMarkets = (p) => (
  <svg {...base} {...p}>
    <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
  </svg>
);
export const IconActivity = (p) => (
  <svg {...base} {...p}>
    <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
  </svg>
);
export const IconHealth = (p) => (
  <svg {...base} {...p}>
    <path d="M12 21s-8-5.2-8-11a4.5 4.5 0 0 1 8-2.8A4.5 4.5 0 0 1 20 10c0 5.8-8 11-8 11z" />
  </svg>
);
export const IconMore = (p) => (
  <svg {...base} {...p}>
    <circle cx="5" cy="12" r="1" />
    <circle cx="12" cy="12" r="1" />
    <circle cx="19" cy="12" r="1" />
  </svg>
);
export const IconSettings = (p) => (
  <svg {...base} {...p}>
    <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
    <circle cx="16" cy="7" r="2" />
    <circle cx="10" cy="17" r="2" />
  </svg>
);
export const IconMode = (p) => (
  <svg {...base} {...p}>
    <path d="M7 4 3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7" />
  </svg>
);
export const IconServer = (p) => (
  <svg {...base} {...p}>
    <rect x="3" y="4" width="18" height="7" rx="1.5" />
    <rect x="3" y="13" width="18" height="7" rx="1.5" />
    <path d="M7 7.5h.01M7 16.5h.01" />
  </svg>
);
export const IconLogs = (p) => (
  <svg {...base} {...p}>
    <path d="M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h5" />
  </svg>
);
export const IconSignOut = (p) => (
  <svg {...base} {...p}>
    <path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11" />
  </svg>
);
export const IconLock = (p) => (
  <svg {...base} width={14} height={14} strokeWidth={2} {...p}>
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </svg>
);
export const IconStop = (p) => (
  <svg {...base} width={16} height={16} {...p}>
    <rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none" />
  </svg>
);
// Bracket arbitrage: a set of brackets that sum to one.
export const IconArb = (p) => (
  <svg {...base} {...p}>
    <path d="M7 4H4v16h3M17 4h3v16h-3M9 9h6M9 15h6" />
  </svg>
);

export const IconAnalytics = (p) => (
  <svg {...base} {...p}>
    <path d="M4 4v16h16M7 15l4-5 3 3 5-6" />
  </svg>
);

export const IconAccount = (p) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="8" r="3.5" />
    <path d="M5 20c.8-3.6 3.5-5.5 7-5.5s6.2 1.9 7 5.5" />
  </svg>
);
export const IconBell = (p) => (
  <svg {...base} {...p}>
    <path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 21h4" />
  </svg>
);
export const IconChevron = (p) => (
  <svg {...base} width={16} height={16} strokeWidth={2} {...p}>
    <path d="m9 6 6 6-6 6" />
  </svg>
);
export const IconEye = (p) => (
  <svg {...base} {...p}>
    <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);
export const IconEyeOff = (p) => (
  <svg {...base} {...p}>
    <path d="M3 3l18 18M10.6 5.7A10 10 0 0 1 12 5.5c6.4 0 10 6.5 10 6.5a17 17 0 0 1-3.2 3.9M6.6 6.7A17 17 0 0 0 2 12s3.6 6.5 10 6.5a9.6 9.6 0 0 0 4.1-.9M9.9 9.9a3 3 0 0 0 4.2 4.2" />
  </svg>
);
export const IconClose = (p) => (
  <svg {...base} width={16} height={16} strokeWidth={2} {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </svg>
);
export const IconExternal = (p) => (
  <svg {...base} width={14} height={14} strokeWidth={2} {...p}>
    <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
  </svg>
);
export const IconBook = (p) => (
  <svg {...base} {...p}>
    <path d="M4 4.5A1.5 1.5 0 0 1 5.5 3H20v15H5.5A1.5 1.5 0 0 0 4 19.5zM4 19.5A1.5 1.5 0 0 0 5.5 21H20v-3M9 8h7M9 11.5h5" />
  </svg>
);
