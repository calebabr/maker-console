import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext.jsx';
import { IconChevron, IconSignOut } from '../components/Icons.jsx';
import { iconForPath } from '../components/navIcons.jsx';
import { MORE_GROUPS } from '../utils/routes.js';
import '../styles/nav.css';

const slug = (path) => path.replace(/\//g, '-');

// Menu page behind the "More" tab: every page that is not a bottom tab, in groups (System, You, Help).
export default function More() {
  const { logout, username } = useAuth();
  return (
    <div className="page">
      <div className="page-head">
        <h1>More</h1>
      </div>
      <nav aria-label="More pages">
        {MORE_GROUPS.map((g) => (
          <section key={g.id} className="menu-group" aria-labelledby={`menu-group-${g.id}`}>
            <h2 className="menu-group-title" id={`menu-group-${g.id}`}>
              {g.label}
            </h2>
            <div className="menu">
              {g.items.map((r) => {
                const I = iconForPath(r.path);
                return (
                  <Link
                    key={r.path}
                    to={r.path}
                    className="menu-item"
                    aria-labelledby={`menu${slug(r.path)}-label`}
                    aria-describedby={`menu${slug(r.path)}-blurb`}
                  >
                    <span className="menu-icon" aria-hidden="true">
                      {I && <I />}
                    </span>
                    <span className="menu-text">
                      <span className="menu-label" id={`menu${slug(r.path)}-label`}>
                        {r.label}
                      </span>
                      <span className="menu-blurb" id={`menu${slug(r.path)}-blurb`}>
                        {r.blurb}
                      </span>
                    </span>
                    <span className="menu-chevron" aria-hidden="true">
                      <IconChevron />
                    </span>
                  </Link>
                );
              })}
            </div>
          </section>
        ))}
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
