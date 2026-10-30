import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import useScrollIdle from '../hooks/useScrollIdle.js';
import { documentTitle, titleForPath } from '../utils/routes.js';
import { iconForPath } from './navIcons.jsx';

const REDUCE_QUERY = '(prefers-reduced-motion: reduce)';

function usePrefersReducedMotion() {
  const get = () => typeof window !== 'undefined' && !!window.matchMedia && !!window.matchMedia(REDUCE_QUERY).matches;
  const [reduce, setReduce] = useState(get);
  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const mq = window.matchMedia(REDUCE_QUERY);
    const on = () => setReduce(!!mq.matches);
    if (mq.addEventListener) mq.addEventListener('change', on);
    else if (mq.addListener) mq.addListener(on);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', on);
      else if (mq.removeListener) mq.removeListener(on);
    };
  }, []);
  return reduce;
}

/**
 * Title bar directly under the app header on every signed-in page.
 * - Sticky (top: --appbar-h), in normal flow at the top of <main>: at the top of the page it fills its own
 *   reserved band; once scrolled it pins under the header and overlays content (no layout shift either way).
 * - Always shown at the top. Scrolled down: shown while scrolling, fades out after ~2.5 s idle, back on any scroll.
 *   Hidden = opacity 0 + visibility hidden + no pointer events, so it never blocks taps on the content beneath.
 * - aria-hidden: each page carries its own <h1> (visually hidden by index.css, see "page title" there), so
 *   screen readers get the heading once and never on reappearance.
 * - Publishes --appbar-h and --sticky-top (header + this bar while shown) on :root for other sticky elements.
 * Themed: accent bar + the page's nav icon + bold title; once stuck it is a slightly raised, accent-tinted band
 * with a soft bottom edge so content visibly scrolls under it.
 * Never covers the header (z-index below it) or the bottom tab bar (it sits at the top).
 */
export default function PageTitle() {
  const { pathname } = useLocation();
  const title = titleForPath(pathname);
  const Icon = iconForPath(pathname);
  const { atTop, idle } = useScrollIdle({ resetKey: pathname });
  const reduce = usePrefersReducedMotion();
  const shown = atTop || !idle;
  const ref = useRef(null);

  useEffect(() => {
    document.title = documentTitle(title);
  }, [title]);
  useEffect(() => () => {
    document.title = documentTitle(null);
  }, []);

  // Sticky-stack height for the bar's own `top` and for other sticky elements.
  useLayoutEffect(() => {
    const root = document.documentElement;
    const appbar = document.querySelector('header.appbar');
    const measure = () => {
      const headerH = appbar ? appbar.offsetHeight : 0;
      const barH = ref.current ? ref.current.offsetHeight : 0;
      if (headerH) root.style.setProperty('--appbar-h', `${headerH}px`);
      root.style.setProperty('--sticky-top', `${headerH + (shown ? barH : 0)}px`);
    };
    measure();
    let ro = null;
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(measure);
      if (appbar) ro.observe(appbar);
      if (ref.current) ro.observe(ref.current);
    }
    window.addEventListener('resize', measure);
    return () => {
      if (ro) ro.disconnect();
      window.removeEventListener('resize', measure);
    };
    // title: the bar mounts/unmounts with it (ref.current changes), so re-measure and re-observe.
  }, [shown, title]);
  useEffect(() => () => {
    document.documentElement.style.removeProperty('--sticky-top');
    document.documentElement.style.removeProperty('--appbar-h');
  }, []);

  if (!title) return null;
  const cls = ['page-title', shown ? 'is-shown' : 'is-hidden', atTop ? 'at-top' : 'is-stuck', reduce ? 'no-motion' : '']
    .filter(Boolean)
    .join(' ');
  return (
    <div
      ref={ref}
      className={cls}
      aria-hidden="true"
      data-testid="page-title"
      data-state={shown ? 'shown' : 'hidden'}
    >
      <span className="page-title-accent" />
      {Icon && (
        <span className="page-title-icon">
          <Icon />
        </span>
      )}
      <span className="page-title-text">{title}</span>
    </div>
  );
}
