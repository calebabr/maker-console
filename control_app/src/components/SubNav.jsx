import { useEffect, useRef } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import '../styles/nav.css';

const norm = (p) => String(p || '/').replace(/\/+$/, '') || '/';

/**
 * Route-based tab strip under the page title (strategy pages: Overview | Results | Controls).
 *   <SubNav label="Maker views" items={[{ to: '/strategies/maker', label: 'Overview' }, ...]} />
 * Each item is a link that REPLACES the history entry (Back leaves the page instead of stepping through tabs), so Back works and the view survives a reload. Semantics: a tablist whose tabs are
 * links (aria-selected on the current one). An item matches its exact path only (items are siblings).
 * Keyboard: Left / Right move focus (wrapping), Home / End jump to the first / last; Enter and Space
 * activate (Space is added to the link's native Enter). Roving tabindex: only the current tab is in the
 * Tab order, so the strip is one Tab stop.
 * Phone: the strip scrolls sideways, the current tab is scrolled into view, every tab is >= 40 px tall.
 * Sticky below the page title (top: --sticky-top), which makes it the second and last sticky row.
 */
export default function SubNav({ items, label = 'Views', className = '' }) {
  const { pathname } = useLocation();
  const listRef = useRef(null);
  const here = norm(pathname);
  const currentIdx = Math.max(0, items.findIndex((it) => here === norm(it.to)));

  useEffect(() => {
    const el = listRef.current && listRef.current.querySelector('[aria-selected="true"]');
    if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest', inline: 'center' });
  }, [pathname]);

  const onKeyDown = (e) => {
    const tabs = Array.from(listRef.current.querySelectorAll('[role="tab"]'));
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    let next = null;
    if (e.key === 'ArrowRight') next = (i + 1) % tabs.length;
    else if (e.key === 'ArrowLeft') next = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = tabs.length - 1;
    else if (e.key === ' ') {
      e.preventDefault();
      tabs[i].click();
      return;
    }
    if (next == null) return;
    e.preventDefault();
    tabs[next].focus();
  };

  return (
    <nav className={`subnav ${className}`.trim()} aria-label={label} data-testid="subnav">
      <div className="subnav-list" role="tablist" aria-label={label} ref={listRef} onKeyDown={onKeyDown}>
        {items.map((it, i) => (
          <NavLink
            key={it.to}
            to={it.to}
            end
            replace
            role="tab"
            aria-selected={i === currentIdx}
            tabIndex={i === currentIdx ? 0 : -1}
            className="subnav-tab"
          >
            {it.label}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
