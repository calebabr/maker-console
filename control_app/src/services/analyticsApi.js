// Analytics-page API calls that are not in services/api.js (owned by the shell agent).
// Same client: auth header, 401 handling and the slow-server notice all come from `request`.
import { request, query } from './api.js';

const enc = encodeURIComponent;

export const NOTE_MAX = 200;

export const analyticsApi = {
  // GET /api/analytics/my-trades: the owner's manual trades beside the bot (Y5). Read-only, no 2FA.
  //   {env, days, generated_utc, totals, bot, pnl_curve, trades:[{fill_id, ts, ticker, title?, side, price, count,
  //    cost, status, result, pnl, note}], total_trades}
  myTrades: ({ days = 30, env, limit = 100 } = {}, { signal } = {}) =>
    request('GET', `/api/analytics/my-trades${query({ days, env, limit })}`, { signal }),
  // Journal note on one fill (plain text, up to 200 chars, "" clears). A signed-in session is enough: no 2FA.
  putNote: (fillId, note) =>
    request('PUT', `/api/trades/${enc(fillId)}/note`, { body: { note }, cache: false }),
};

export default analyticsApi;
