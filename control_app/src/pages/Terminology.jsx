import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { GLOSSARY, GROUPS, getTerm } from '../utils/glossary.js';
import { highlightSegments, scoreTerm, searchTerms } from '../utils/search.js';
import { getTrail, restoreScroll } from '../utils/navTrail.js';
import '../styles/help.css';

// Plain-English definitions for every term used in the app (single source: utils/glossary.js).
// /terminology#<id> scrolls to and briefly highlights that entry; (?) HelpIcons link here.

export const HIGHLIGHT_MS = 2200;

// While the box has text: one flat list ranked by relevance (utils/search.js).
// Empty box: the normal grouped sections.
function Marked({ text, query }) {
  if (!query) return text;
  return highlightSegments(text, query).map((seg, i) =>
    seg.hit ? <mark key={i}>{seg.text}</mark> : <span key={i}>{seg.text}</span>,
  );
}

const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function TermItem({ e, highlight, query = '', best = false, via = null }) {
  return (
    <li
      id={e.id}
      className={`term ${highlight === e.id ? 'highlight' : ''}`}
      aria-current={highlight === e.id ? 'true' : undefined}
    >
      <h3>
        <Link to={{ hash: `#${e.id}` }}>
          <Marked text={e.term} query={query} />
        </Link>
        {best && <span className="term-best">Best Match</span>}
      </h3>
      {via && <p className="term-via">Also called “{via}”</p>}
      <p className="term-short">
        <Marked text={e.short} query={query} />
      </p>
      <p className="term-body">
        <Marked text={e.body} query={query} />
      </p>
      {e.related && e.related.length > 0 && (
        <p className="term-related">
          <span>See also:</span>
          {e.related
            .map((id) => getTerm(id))
            .filter(Boolean)
            .map((r) => (
              <Link key={r.id} to={{ hash: `#${r.id}` }}>
                {r.term}
              </Link>
            ))}
        </p>
      )}
    </li>
  );
}

export default function Terminology() {
  const { hash } = useLocation();
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(null);
  const timer = useRef(null);
  const [showWeak, setShowWeak] = useState(false);
  // Where the person came from (recorded when they tapped a link here): "← Back to <Page>".
  const [trail] = useState(() => getTrail());
  const searchRef = useRef(null);
  const listRef = useRef(null);
  const lastQuery = useRef('');

  // A malformed escape (e.g. #%E0%A4%A) must not throw during render (S-29): ignore it.
  let target = '';
  if (hash) {
    try {
      target = decodeURIComponent(hash.slice(1));
    } catch {
      target = '';
    }
  }

  // On load and on every hash change: clear a filter that would hide the target,
  // scroll to it and highlight it briefly.
  useEffect(() => {
    if (!target || !getTerm(target)) return undefined;
    setQuery((q) => (q.trim() && scoreTerm(q, getTerm(target)) === null ? '' : q));
    setHighlight(target);
    const raf = (typeof window !== 'undefined' && window.requestAnimationFrame) || ((f) => setTimeout(f, 0));
    raf(() => {
      const el = document.getElementById(target);
      if (el && typeof el.scrollIntoView === 'function') {
        const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
      }
    });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setHighlight(null), HIGHLIGHT_MS);
    return () => clearTimeout(timer.current);
  }, [target]);

  const searching = query.trim() !== '';
  const found = useMemo(() => (searching ? searchTerms(query, GLOSSARY) : []), [query, searching]);
  const strong = useMemo(() => found.filter((r) => r.strong), [found]);
  const weak = useMemo(() => found.filter((r) => !r.strong), [found]);
  // With no strong match at all, the body-only matches are the answer: show them without a fold.
  const weakOpen = showWeak || strong.length === 0;
  const visible = weakOpen ? found : strong;

  // A new query starts folded again.
  useEffect(() => {
    setShowWeak(false);
  }, [query]);

  // A deep link to a term that is folded away opens the fold.
  useEffect(() => {
    if (target && weak.some((r) => r.entry.id === target)) setShowWeak(true);
  }, [target, weak]);

  // Item 24b: when the query changes, bring the first result up under the sticky search box. Never when the
  // query is unchanged (tapping a result), never moves focus, and the animation is skipped under reduced motion.
  useEffect(() => {
    const q = query.trim();
    if (q === lastQuery.current) return;
    lastQuery.current = q;
    if (!q || !listRef.current || typeof window === 'undefined' || typeof window.scrollTo !== 'function') return;
    const root = document.documentElement;
    const stickyTop = parseFloat(getComputedStyle(root).getPropertyValue('--sticky-top')) || 51;
    const boxH = searchRef.current ? searchRef.current.offsetHeight : 0;
    const top = listRef.current.getBoundingClientRect().top + window.scrollY - (stickyTop + boxH);
    if (window.scrollY > top) {
      try {
        window.scrollTo({ top: Math.max(0, top), behavior: reducedMotion() ? 'auto' : 'smooth' });
      } catch {
        /* no scrolling support: the list is still correct */
      }
    }
  }, [query, found]);
  const groups = useMemo(
    () => GROUPS.map((g) => ({ ...g, entries: GLOSSARY.filter((e) => e.group === g.id) })).filter((g) => g.entries.length),
    [],
  );
  const shown = searching ? visible.length : GLOSSARY.length;

  return (
    <div className="page terminology">
      {trail && (
        <Link
          to={trail.path}
          className="term-back"
          onClick={() => restoreScroll(trail.scrollY)}
        >
          <span aria-hidden="true">←</span> Back to {trail.label}
        </Link>
      )}
      <div className="page-head">
        <h1>Terminology</h1>
      </div>
      <p className="muted small">
        Plain-English meanings of the words used in this app, as they apply to this bot. Tap a (?) anywhere in the app to
        jump here.
      </p>

      <div className="term-search" role="search" ref={searchRef}>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter terms, e.g. edge, fill, stop"
          aria-label="Filter terms"
          autoComplete="off"
          spellCheck="false"
        />
        <div className="term-count" aria-live="polite">
          {searching ? `${shown} of ${GLOSSARY.length} terms` : `${GLOSSARY.length} terms`}
        </div>
      </div>

      {!searching && (
        <nav className="term-jump" aria-label="Sections">
          {GROUPS.map((g) => (
            <a key={g.id} href={`#group-${g.id}`} onClick={(e) => {
              e.preventDefault();
              const el = document.getElementById(`group-${g.id}`);
              if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'start' });
            }}>
              {g.title}
            </a>
          ))}
        </nav>
      )}

      {searching && shown === 0 && <p className="muted">No terms match “{query.trim()}”.</p>}

      {searching ? (
        <>
          <ul
            key={query.trim()}
            ref={listRef}
            className="term-list term-results"
            aria-label="Matching terms, best match first"
          >
            {visible.map((r, i) => (
              <TermItem
                key={r.entry.id}
                e={r.entry}
                highlight={highlight}
                query={query}
                best={i === 0 && r.strong}
                via={r.via && r.kind === 'name' ? r.via : null}
              />
            ))}
          </ul>
          {!weakOpen && weak.length > 0 && (
            <button type="button" className="term-more" aria-expanded="false" onClick={() => setShowWeak(true)}>
              Also mentioned in {weak.length} more {weak.length === 1 ? 'term' : 'terms'}
            </button>
          )}
        </>
      ) : (
        groups.map((g) => (
          <section key={g.id} className="term-group" id={`group-${g.id}`} aria-labelledby={`group-${g.id}-h`}>
            <h2 id={`group-${g.id}-h`}>{g.title}</h2>
            <ul className="term-list">
              {g.entries.map((e) => (
                <TermItem key={e.id} e={e} highlight={highlight} />
              ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}
