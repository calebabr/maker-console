// One registry of what every P&L figure measures and which date rule it uses. A new strategy or figure is one entry
// here; <PnlScope id="..."/> renders it. Entries are a string or a function of a small context ({ tz }).
// Keep each to one short line: they sit under the number.

export const TODAY_EMPTY = 'No settlements yet today';

/** "America/Chicago" -> "America/Chicago"; "UTC" or nothing -> "UTC". Display only; the server validates the zone. */
export function zoneLabel(tz) {
  return typeof tz === 'string' && tz && tz !== 'UTC' ? tz.replace(/_/g, ' ') : 'UTC';
}

export const PNL_SCOPES = {
  // Overview
  maker: 'Realized, maker fills only, by settlement date (UTC)',
  makerByFill: 'Realized, maker fills only, by fill date (UTC)',
  arbRealized: 'Realized, arbitrage fills only, by settlement date (UTC)',
  makerArb: 'Realized, maker + arbitrage, by settlement date (UTC)',
  today: ({ tz } = {}) => `Realized, settled since midnight ${zoneLabel(tz)} (bot and manual)`,
  todayShort: ({ tz } = {}) => `Settled since midnight ${zoneLabel(tz)}`,
  bySource: ({ tz } = {}) => `Realized by source, settled since midnight ${zoneLabel(tz)}`,
  bySourcePeriod: 'Realized by source, by fill date (UTC) within the chosen period',
  // Analytics
  analytics: 'Realized, maker + arbitrage, by settlement date (UTC)',
  // Account
  period: 'Realized, bot fills in this period, by fill date (UTC)',
  equity: 'Equity minus deposits, open positions at cost',
  // Arbitrage
  arbGuaranteed: 'Guaranteed at execution, not yet realized',
  // Shadow
  shadow: 'Simulated estimate. No orders were placed, so this is not real P&L',
};

/** The text for a scope id, or '' for an unknown id. */
export function scopeText(id, ctx) {
  const e = PNL_SCOPES[id];
  return typeof e === 'function' ? e(ctx || {}) : e || '';
}
