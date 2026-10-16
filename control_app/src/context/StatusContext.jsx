import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../services/api.js';
import { readCache } from '../services/cache.js';
import { usePageVisible } from '../hooks/usePageVisible.js';

// App-wide GET /api/status, polled every 30 s while the page is visible.
// Shared by the header badge, the Overview page and the Mode page.

export const POLL_MS = 30000;
const StatusContext = createContext(null);

export function StatusProvider({ enabled = true, children }) {
  const visible = usePageVisible();
  const [state, setState] = useState(() => {
    const c = readCache('/api/status');
    return { status: c ? c.data : null, error: null, loading: true, updatedAt: c ? c.ts : null, stale: !!c };
  });
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const status = await api.status();
      setState({ status, error: null, loading: false, updatedAt: Date.now(), stale: false });
    } catch (error) {
      setState((s) => ({ ...s, error, loading: false, stale: !!s.status }));
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    if (!enabled || !visible) return undefined;
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [enabled, visible, refresh]);

  const value = useMemo(() => ({ ...state, refresh }), [state, refresh]);
  return <StatusContext.Provider value={value}>{children}</StatusContext.Provider>;
}

export function useStatus() {
  const ctx = useContext(StatusContext);
  if (!ctx) throw new Error('useStatus must be used inside <StatusProvider>');
  return ctx;
}
