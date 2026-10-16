import { useCallback, useEffect, useRef, useState } from 'react';
import { readCache } from '../services/cache.js';

/**
 * Load data from the API with an offline fallback.
 *   const { data, error, loading, staleAt, reload, setData } = useResource(() => api.getHealth(), '/api/health');
 * cacheKey is the request path (api.js caches GET responses under it). If the
 * request fails with a network error and cached data exists, `data` is the cached
 * value and `staleAt` is its timestamp.
 */
export function useResource(fetcher, cacheKey, { auto = true } = {}) {
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const [state, setState] = useState(() => {
    const c = cacheKey ? readCache(cacheKey) : null;
    return { data: c ? c.data : null, error: null, loading: auto, staleAt: c ? c.ts : null };
  });
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    setState((s) => ({ ...s, loading: true }));
    try {
      const data = await fetcherRef.current();
      if (alive.current) setState({ data, error: null, loading: false, staleAt: null });
      return data;
    } catch (error) {
      const c = cacheKey ? readCache(cacheKey) : null;
      if (alive.current) {
        setState((s) => ({
          data: c ? c.data : s.data,
          error,
          loading: false,
          staleAt: c ? c.ts : s.staleAt,
        }));
      }
      return null;
    }
  }, [cacheKey]);

  useEffect(() => {
    if (auto) reload();
  }, [auto, reload]);

  const setData = useCallback((data) => setState((s) => ({ ...s, data, staleAt: null })), []);

  return { ...state, reload, setData };
}
