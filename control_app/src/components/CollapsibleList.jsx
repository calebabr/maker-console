import { useId, useState } from 'react';

const PREFIX = 'keel.list.';

function readOpen(pageKey) {
  if (!pageKey) return false;
  try {
    return window.localStorage.getItem(PREFIX + pageKey) === '1';
  } catch {
    return false;
  }
}

function writeOpen(pageKey, open) {
  if (!pageKey) return;
  try {
    if (open) window.localStorage.setItem(PREFIX + pageKey, '1');
    else window.localStorage.removeItem(PREFIX + pageKey);
  } catch {
    /* storage unavailable: the choice just is not remembered */
  }
}

/**
 * Long list that shows the first `limit` rows (default 5) with a "Show all N" / "Hide" toggle.
 *   <CollapsibleList items={rows} pageKey="activity" pinned={(r) => r.ok === false}>
 *     {(visible) => <ul>{visible.map(...)}</ul>}
 *   </CollapsibleList>
 * `children` is a render function that receives the visible items (so it works for lists, tables, anything).
 * Items for which `pinned(item)` is true (errors, alerts needing attention) are always visible, even when collapsed.
 * The open/closed choice is remembered per `pageKey` in localStorage (every access is guarded).
 */
export default function CollapsibleList({ items, limit = 5, pageKey, pinned, label = 'rows', children }) {
  const id = useId();
  const [open, setOpen] = useState(() => readOpen(pageKey));
  const list = Array.isArray(items) ? items : [];
  const isPinned = typeof pinned === 'function' ? pinned : () => false;
  const visible = open ? list : list.filter((it, i) => i < limit || isPinned(it));
  const hidden = list.length - visible.length;
  const canToggle = open ? list.length > limit : hidden > 0;

  function toggle() {
    const next = !open;
    setOpen(next);
    writeOpen(pageKey, next);
  }

  return (
    <>
      <div id={`${id}-list`}>{children(visible)}</div>
      {canToggle && (
        <button type="button" className="collapsible-toggle" onClick={toggle} aria-expanded={open} aria-controls={`${id}-list`}>
          {open ? 'Hide' : `Show all ${list.length}`}
          <span className="sr-only"> {label}</span>
        </button>
      )}
    </>
  );
}
