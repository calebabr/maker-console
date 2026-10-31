import { useLayoutEffect, useSyncExternalStore } from 'react';
import { useLocation, useNavigationType } from 'react-router-dom';

// In-app history trail (paths visited in this session of the app, oldest first). The installed iPhone app has no
// browser back button, so the back control uses real history (navigate(-1)) when the trail has a previous page,
// and the route's parent only on a deep link, reload or first page. PUSH adds, REPLACE swaps the last entry,
// POP steps back (or resets when the target is not the previous entry, e.g. a forward jump).
let trail = [];
let snapshot = 0;
const subs = new Set();
const emit = () => {
  snapshot += 1;
  subs.forEach((f) => f());
};
const norm = (p) => String(p || '/').replace(/\/+$/, '') || '/';

export function resetNavTrail() {
  trail = [];
  emit();
}

function record(path, type, key) {
  const p = norm(path);
  if (trail.length === 0) trail = [p];
  else if (type === 'PUSH') trail = [...trail, p];
  else if (type === 'REPLACE') trail = [...trail.slice(0, -1), p];
  else if (trail.length > 1 && trail[trail.length - 2] === p) trail = trail.slice(0, -1);
  else trail = [p];
  emit();
}

/** Mount once (PageTitle does) to record navigation. */
export function useNavTrailRecorder() {
  const { pathname, key } = useLocation();
  const type = useNavigationType();
  useLayoutEffect(() => {
    record(pathname, type, key);
    // key identifies each history entry; a repeat render of the same entry must not record twice.
  }, [key, pathname]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** The previous in-app path, or null when there is none (deep link, reload, first page). */
export function usePreviousPath() {
  useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => snapshot,
  );
  return trail.length > 1 ? trail[trail.length - 2] : null;
}
