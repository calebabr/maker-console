import { useEffect, useRef } from 'react';

/**
 * Analytics section tabs: one row that scrolls sideways on a phone, every tab >= 40 px tall, the active tab
 * scrolled into view. Roving tabindex (one Tab stop); Left / Right move focus and wrap, Home / End jump;
 * Enter and Space select the focused tab.
 *   <TabScroller tabs={[{key,label}]} value={key} onChange={fn} label="Analytics sections" />
 */
export default function TabScroller({ tabs, value, onChange, label = 'Sections' }) {
  const ref = useRef(null);
  const current = Math.max(0, tabs.findIndex((t) => t.key === value));

  useEffect(() => {
    const el = ref.current && ref.current.querySelector('[aria-selected="true"]');
    if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest', inline: 'center' });
  }, [value]);

  const onKeyDown = (e) => {
    const btns = Array.from(ref.current.querySelectorAll('[role="tab"]'));
    const i = btns.indexOf(document.activeElement);
    if (i < 0) return;
    let next = null;
    if (e.key === 'ArrowRight') next = (i + 1) % btns.length;
    else if (e.key === 'ArrowLeft') next = (i - 1 + btns.length) % btns.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = btns.length - 1;
    if (next == null) return;
    e.preventDefault();
    btns[next].focus();
  };

  return (
    <div className="tab-scroller" role="tablist" aria-label={label} ref={ref} onKeyDown={onKeyDown}>
      {tabs.map((t, i) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={i === current}
          tabIndex={i === current ? 0 : -1}
          className="tab-scroller-tab"
          onClick={() => onChange(t.key)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
