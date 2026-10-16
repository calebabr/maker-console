import { useCallback, useEffect, useRef, useState } from 'react';
import { readCache } from '../services/cache.js';

/**
 * Like useResource, but each load gets an AbortSignal and a new load (key change,
 * reload) or unmount aborts the one in flight (S-28: superseded analytics requests
 * must not keep the server busy). Aborted loads are ignored, never shown as errors.
 *
 *   const r = useCancellableResource((signal) => api.analyticsSummary(args, { signal }), key);
 *
 * `settledKey` is the key of the last load that finished (with data or an error),
 * so dependents can wait for it (e.g. load the trade list after the summary).
 */
export function useCancellableResource(fetcher, key) {
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const ctrl = useRef(null);
  const [state, setState] = useState(() => {
    const c = key ? readCache(key) : null;
    return { data: c ? c.data : null, error: null, loading: true, staleAt: c ? c.ts : null, settledKey: null };
  });

  const reload = useCallback(async () => {
    if (ctrl.current) ctrl.current.abort();
    const mine = typeof AbortController !== 'undefined' ? new AbortController() : null;
    ctrl.current = mine;
    setState((s) => ({ ...s, loading: true }));
    try {
      const data = await fetcherRef.current(mine ? mine.signal : undefined);
      if (ctrl.current !== mine) return null;
      setState({ data, error: null, loading: false, staleAt: null, settledKey: key });
      return data;
    } catch (error) {
      if ((error && error.aborted) || ctrl.current !== mine) return null;
      const c = key ? readCache(key) : null;
      setState((s) => ({
        data: c ? c.data : s.data,
        error,
        loading: false,
        staleAt: c ? c.ts : s.staleAt,
        settledKey: key,
      }));
      return null;
    }
  }, [key]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Abort whatever is in flight when the page goes away.
  useEffect(
    () => () => {
      if (ctrl.current) ctrl.current.abort();
      ctrl.current = null;
    },
    [],
  );

  return { ...state, reload };
}
