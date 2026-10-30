// The shared period control's options (components/RangeSelect.jsx) and how they map to API query params.
// Home, the Strategies hub and Performance all use these so one choice means the same window everywhere.
//
// 'All' is deliberately absent: the ledger's `period=all` means "no period filter" (it falls back to the
// rolling `days` window, at most 180), not "all time", so offering it would mislabel the figures. It joins
// this list when the backend gives an all-time window (small backend item, see the stage 2 foundation report).
export const RANGE_OPTIONS = [
  { id: '7d', label: '7 days', params: { days: 7 } },
  { id: '30d', label: '30 days', params: { days: 30 } },
  { id: 'current', label: 'This period', params: { period: 'current' } },
];

export const DEFAULT_RANGE = '30d';

export const RANGE_IDS = RANGE_OPTIONS.map((r) => r.id);

/** A valid range id (unknown -> the default). */
export function validRange(id) {
  return RANGE_IDS.includes(id) ? id : DEFAULT_RANGE;
}

/** Query params for a range id: '7d' -> {days: 7}, 'current' -> {period: 'current'}. */
export function rangeParams(id) {
  return { ...RANGE_OPTIONS.find((r) => r.id === validRange(id)).params };
}

/** Label for a range id ("30 days"). */
export function rangeLabel(id) {
  return RANGE_OPTIONS.find((r) => r.id === validRange(id)).label;
}
