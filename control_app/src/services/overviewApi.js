// Overview-only read calls built on the shared request(): kept out of api.js (FE-Shell's file).
import { request } from './api.js';

const enc = encodeURIComponent;

/** GET /api/since?ts=<ISO> -> {fills, settled, pnl_change, since} (UX contract item 30). Never cached. */
export const getSince = (ts) => request('GET', `/api/since?ts=${enc(ts)}`, { cache: false });

/** GET /api/account/positions -> [{ticker, value, source, strategy, ...}] (read-only, from Kalshi). */
export const getPositions = () =>
  request('GET', '/api/account/positions').then((d) => (Array.isArray(d) ? d : (d && d.positions) || []));
