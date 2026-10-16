import { useId, useRef, useState } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import Popover from './Popover.jsx';
import '../utils/navTrail.js';
import { getTerm } from '../utils/glossary.js';
import '../styles/help.css';

// Menu icon for the Terminology page (same stroke style as components/Icons.jsx;
// kept here so concurrent edits to Icons.jsx don't collide).
export const IconGlossary = (p) => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...p}>
    <path d="M4 4.5A1.5 1.5 0 0 1 5.5 3H20v15H5.5A1.5 1.5 0 0 0 4 19.5zM4 19.5A1.5 1.5 0 0 0 5.5 21H20v-3M9 8h7M9 11.5h5" />
  </svg>
);

const Glyph = () => (
  <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
    <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="1.2" />
    <path
      d="M6.1 6.2a1.95 1.95 0 0 1 3.8.5c0 1.3-1.9 1.6-1.9 2.8"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinecap="round"
    />
    <circle cx="8" cy="11.6" r="0.85" fill="currentColor" />
  </svg>
);

// <HelpIcon term="fill-rate" /> -> small (?) button. Tapping it opens a popover with the term's short
// definition and a "More" link to /terminology#fill-rate (Esc, an outside tap or the button itself closes it;
// focus is trapped inside while open and returns to the (?)). The glyph is 16px and sits inline (no layout
// shift); an invisible ::before pad widens the tap target to 34px. Never place one inside another <button>
// (or the Stop button). An id that is not in the glossary renders a plain link to the page instead.
export default function HelpIcon({ term, className = '' }) {
  const entry = getTerm(term);
  const name = entry ? entry.term : term;
  const inRouter = useInRouterContext();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const popId = useId();
  const cls = `help-icon ${className}`.trim();

  if (!entry) {
    const props = { className: cls, 'aria-label': `What is ${name}?`, 'data-term': term };
    return inRouter ? (
      <Link to={{ pathname: '/terminology', hash: `#${term}` }} {...props}>
        <Glyph />
      </Link>
    ) : (
      <a href={`/terminology#${term}`} {...props}>
        <Glyph />
      </a>
    );
  }

  const more = inRouter ? (
    <Link to={{ pathname: '/terminology', hash: `#${term}` }} className="help-more" onClick={() => setOpen(false)}>
      More <span aria-hidden="true">→</span>
    </Link>
  ) : (
    <a href={`/terminology#${term}`} className="help-more">
      More <span aria-hidden="true">→</span>
    </a>
  );

  return (
    <>
      <button
        type="button"
        ref={ref}
        className={cls}
        aria-label={`What is ${name}?`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? popId : undefined}
        data-term={term}
        onClick={(e) => {
          // Portal events bubble through React parents (a card header, a label): keep the tap to ourselves.
          e.preventDefault();
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        <Glyph />
      </button>
      <Popover open={open} onClose={() => setOpen(false)} anchorRef={ref} label={`Definition: ${name}`} className="help-pop" width={300}>
        <div id={popId} onClick={(e) => e.stopPropagation()}>
          <p className="help-pop-term">{name}</p>
          <p className="help-pop-short">{entry.short}</p>
          {more}
        </div>
      </Popover>
    </>
  );
}
