// "Open in Kalshi" links. We only ever build a link to the public market page; nothing is fetched.
const BASE = 'https://kalshi.com';

// Kalshi tickers are upper-case letters, digits and hyphens (e.g. KXDEMOD-26OCT15-T84).
const TICKER_OK = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

/**
 * Public Kalshi page for a market ticker, or null when the ticker is missing or looks wrong.
 * Kalshi routes /markets/<ticker> to the market (or its event) page.
 */
export function kalshiMarketUrl(ticker) {
  const t = String(ticker == null ? '' : ticker).trim();
  if (!t || !TICKER_OK.test(t)) return null;
  return `${BASE}/markets/${encodeURIComponent(t.toLowerCase())}`;
}
