import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext.jsx';
import { IconChevron, IconSignOut } from '../components/Icons.jsx';
import { iconForPath } from '../components/navIcons.jsx';
import { MORE_ROUTES } from '../utils/routes.js';

// Menu page behind the "More" tab: every page that is not a bottom tab.
export default function More() {
  const { logout, username } = useAuth();
  return (
    <div className="page">
      <div className="page-head">
        <h1>More</h1>
      </div>
      <nav className="menu" aria-label="More pages">
        {MORE_ROUTES.map((r) => {
          const I = iconForPath(r.path);
          return (
            <Link
              key={r.path}
              to={r.path}
              className="menu-item"
              aria-labelledby={`menu${r.path.replace(/\//g, '-')}-label`}
              aria-describedby={`menu${r.path.replace(/\//g, '-')}-blurb`}
            >
              <span className="menu-icon" aria-hidden="true">
                {I && <I />}
              </span>
              <span className="menu-text">
                <span className="menu-label" id={`menu${r.path.replace(/\//g, '-')}-label`}>
                  {r.label}
                </span>
                <span className="menu-blurb" id={`menu${r.path.replace(/\//g, '-')}-blurb`}>
                  {r.blurb}
                </span>
              </span>
              <span className="menu-chevron" aria-hidden="true">
                <IconChevron />
              </span>
            </Link>
          );
        })}
      </nav>
      <button type="button" className="menu-item menu-logout" onClick={logout}>
        <span className="menu-icon" aria-hidden="true">
          <IconSignOut />
        </span>
        <span className="menu-text">
          <span className="menu-label">Sign Out{username ? ` (${username})` : ''}</span>
        </span>
      </button>
    </div>
  );
}
