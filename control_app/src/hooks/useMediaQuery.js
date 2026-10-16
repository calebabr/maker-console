import { useEffect, useState } from 'react';

function match(query) {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && !!window.matchMedia(query).matches;
}

/** Live `window.matchMedia(query).matches` (false where matchMedia is unavailable). */
export function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => match(query));
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const mq = window.matchMedia(query);
    const on = () => setMatches(!!mq.matches);
    on();
    if (mq.addEventListener) mq.addEventListener('change', on);
    else if (mq.addListener) mq.addListener(on);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', on);
      else if (mq.removeListener) mq.removeListener(on);
    };
  }, [query]);
  return matches;
}

export const WIDE_QUERY = '(min-width: 1024px)';
/** >= 1024 px: left sidebar instead of bottom tabs. */
export const useIsWide = () => useMediaQuery(WIDE_QUERY);
