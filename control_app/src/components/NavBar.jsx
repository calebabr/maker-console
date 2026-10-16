import { NavLink, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext.jsx';
import { useIsWide } from '../hooks/useMediaQuery.js';
import { isMorePath, MORE_ROUTES, TAB_ROUTES } from '../utils/routes.js';
import { IconSignOut } from './Icons.jsx';
import { iconForPath } from './navIcons.jsx';

function Icon({ path }) {
  const I = iconForPath(path);
  return I ? <I /> : null;
}

// Phones: bottom tab bar, in the owner's order: Overview, Analytics, Activity, Markets, More.
// "More" is a normal page (/more) that lists everything else.
function TabBar() {
  const { pathname } = useLocation();
  return (
    <nav className="tabbar" aria-label="Main">
      {TAB_ROUTES.map((t) => (
        <NavLink
          key={t.path}
          to={t.path}
          end={t.path === '/'}
          className={({ isActive }) => `tab ${isActive || (t.path === '/more' && isMorePath(pathname)) ? 'active' : ''}`}
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

// >= 1024 px: the same entries in a left sidebar (main pages, then everything that lives under More).
function Sidebar() {
  const { logout, username } = useAuth();
  const main = TAB_ROUTES.filter((t) => t.path !== '/more');
  const link = (r) => (
    <NavLink key={r.path} to={r.path} end={r.path === '/'} className="side-link">
      <span className="side-icon" aria-hidden="true">
        <Icon path={r.path} />
      </span>
      {r.label}
    </NavLink>
  );
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="side-group">{main.map(link)}</div>
      <div className="side-group">
        <div className="side-heading">More</div>
        {MORE_ROUTES.map(link)}
      </div>
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
