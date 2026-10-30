import { useCallback, useState } from 'react';
import { DEFAULT_RANGE, validRange } from '../utils/range.js';

const KEY = 'prior.range';

function read() {
  try {
    return validRange(window.sessionStorage.getItem(KEY) || DEFAULT_RANGE);
  } catch {
    return DEFAULT_RANGE;
  }
}

/**
 * The page-wide range shared by Home, the Strategies hub and Performance. Kept in sessionStorage (a display
 * choice, not a bot setting), so switching pages keeps the same window. Returns [rangeId, setRange].
 */
export default function useRange() {
  const [range, setInner] = useState(read);
  const setRange = useCallback((id) => {
    const v = validRange(id);
    setInner(v);
    try {
      window.sessionStorage.setItem(KEY, v);
    } catch {
      /* storage blocked: the choice lasts for this page only */
    }
  }, []);
  return [range, setRange];
}
