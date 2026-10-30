import { NavLink, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext.jsx';
import { useIsWide } from '../hooks/useMediaQuery.js';
import { MORE_GROUPS, SIDEBAR_MAIN, TAB_ROUTES, tabForPath } from '../utils/routes.js';
import { IconSignOut } from './Icons.jsx';
import { iconForPath } from './navIcons.jsx';

function Icon({ path }) {
  const I = iconForPath(path);
  return I ? <I /> : null;
}

// Phones: bottom tab bar, in the owner's order: Home, Strategies, Performance, Activity, More.
// The lit tab comes from tabForPath(), so /analytics and /shadow light Performance and /arb and /markets
// (which redirect) light Strategies without flashing More.
function TabBar() {
  const { pathname } = useLocation();
  const current = tabForPath(pathname);
  return (
    <nav className="tabbar" aria-label="Main">
      {TAB_ROUTES.map((t) => (
        <NavLink
          key={t.path}
          to={t.path}
          className={`tab ${current === t.key ? 'active' : ''}`}
          aria-current={current === t.key ? 'page' : undefined}
        >
          <span className="tab-icon" aria-hidden="true">
            <Icon path={t.path} />
          </span>
          <span className="tab-label">{t.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

// >= 1024 px: the same destinations in a left sidebar. Strategies and Performance show their children;
// the More groups (System, You, Help) follow. Sign Out sits at the bottom.
function Sidebar() {
  const { logout, username } = useAuth();
  const { pathname } = useLocation();
  const current = tabForPath(pathname);
  // `selfActive: false` (Performance): its first child "All Strategies" is the same page, so only the child lights.
  const link = (r, child = false) => (
    <NavLink
      key={r.path}
      to={r.path}
      end={r.end || r.path === '/'}
      className={({ isActive }) =>
        [
          'side-link',
          child ? 'side-child' : '',
          r.children ? 'side-parent' : '',
          r.tab && r.tab === current ? 'is-current' : '',
          isActive && r.selfActive !== false ? 'active' : '',
        ]
          .filter(Boolean)
          .join(' ')
      }
    >
      {!child && (
        <span className="side-icon" aria-hidden="true">
          <Icon path={r.path} />
        </span>
      )}
      {r.label}
    </NavLink>
  );
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="side-group">
        {SIDEBAR_MAIN.map((r) => (
          <div key={r.path} className="side-branch">
            {link(r)}
            {r.children && <div className="side-children">{r.children.map((c) => link(c, true))}</div>}
          </div>
        ))}
      </div>
      {MORE_GROUPS.map((g) => (
        <div className="side-group" key={g.id} role="group" aria-label={g.label}>
          <div className="side-heading">{g.label}</div>
          {g.items.map((r) => link(r))}
        </div>
      ))}
      <button type="button" className="side-link side-logout" onClick={logout}>
        <span className="side-icon" aria-hidden="true">
          <IconSignOut />
        </span>
        Sign Out{username ? ` (${username})` : ''}
      </button>
    </nav>
  );
}

export default function NavBar() {
  return useIsWide() ? <Sidebar /> : <TabBar />;
}
