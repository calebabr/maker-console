import { useCallback, useEffect, useRef, useState } from 'react';
import { analyticsApi } from '../../services/analyticsApi.js';

/**
 * GET /api/analytics/my-trades for the selected window (Y5). Waits while `enabled` is false so a page load
 * never runs two heavy builds at once (the server shares one cache with /summary). A newer request or
 * unmount aborts the one in flight. Manual trades never touch the bot statistics.
 */
export function useMyTrades({ days, env, enabled = true }) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const ctrl = useRef(null);

  const load = useCallback(async () => {
    if (ctrl.current) ctrl.current.abort();
    const mine = typeof AbortController !== 'undefined' ? new AbortController() : null;
    ctrl.current = mine;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await analyticsApi.myTrades({ days, env }, { signal: mine ? mine.signal : undefined });
      if (ctrl.current !== mine) return;
      setState({ data, error: null, loading: false });
    } catch (error) {
      if ((error && error.aborted) || ctrl.current !== mine) return;
      setState((s) => ({ ...s, error, loading: false }));
    }
  }, [days, env]);

  useEffect(() => {
    if (!enabled) return undefined;
    load();
    return () => {
      if (ctrl.current) ctrl.current.abort();
    };
  }, [load, enabled]);

  const patchNote = useCallback((fillId, note) => {
    setState((s) =>
      s.data
        ? { ...s, data: { ...s.data, trades: s.data.trades.map((t) => (t.fill_id === fillId ? { ...t, note: note || null } : t)) } }
        : s,
    );
  }, []);

  return { ...state, reload: load, patchNote };
}

export default useMyTrades;
