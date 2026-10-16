// Display helpers for the Analytics page (docs/ANALYTICS_CONTRACT.md shapes).

export const DAY_OPTIONS = [7, 30, 90, 180];
// Below this many settled fills a group's win-rate CI is too wide to call an edge.
export const MIN_N = 30;
// Price bands come from the installed strategy's cells (the summary's by_band keys), never a fixed list.

export const SERIES_NAMES = {
  KXDEMOA: 'Demo series A',
  KXDEMOB: 'Demo series B',
  KXDEMOC: 'Demo series C',
  KXDEMOD: 'Demo series D',
  KXDEMOE: 'Demo series E',
  KXDEMOG: 'Demo series G',
  KXDEMOH: 'Demo series H',
  KXDEMOF: 'Demo series F',
};

export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

export function seriesName(key) {
  return SERIES_NAMES[key] || key || '—';
}

// 0.434 -> "43.4¢"; 0.43 -> "43¢"
export function cents(v) {
  if (!isNum(v)) return '—';
  const c = Math.round(v * 1000) / 10;
  return `${Number.isInteger(c) ? c.toFixed(0) : c.toFixed(1)}¢`;
}

// Rate 0-1 -> "95.0%"
export function rate(v, digits = 1) {
  if (!isNum(v)) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

// Edge (rate difference) -> "+1.6 pts"
export function points(v, digits = 1) {
  if (!isNum(v)) return '—';
  const s = (v * 100).toFixed(digits);
  return `${v > 0 ? '+' : ''}${s} pts`;
}

// "0.35-0.50|3-9h|yes" -> {price: "35-50¢", ttc: "3-9 h", side: "YES"}
export function parseCell(id) {
  const [price, ttc, side] = String(id || '').split('|');
  const [lo, hi] = String(price || '').split('-').map(Number);
  return {
    price: isNum(lo) && isNum(hi) ? `${Math.round(lo * 100)}-${Math.round(hi * 100)}¢` : price || '?',
    ttc: ttc ? ttc.replace(/h$/, ' h') : '?',
    side: side ? side.toUpperCase() : '?',
  };
}

export function bandLabel(b) {
  if (b === 'other') return 'Other prices';
  const [lo, hi] = String(b || '').split('-').map(Number);
  return isNum(lo) && isNum(hi) ? `${Math.round(lo * 100)}-${Math.round(hi * 100)}¢` : b || '—';
}

export function ttcLabel(b) {
  return b ? String(b).replace(/h$/, ' h') : '—';
}

// "2026-10-01" -> "Oct 1"
export function shortDate(d) {
  if (!d) return '—';
  const t = new Date(`${String(d).slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(t.getTime())) return String(d);
  return t.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
}

// "2026-W40" -> "W40"
export function weekLabel(w) {
  const m = /W(\d+)$/.exec(String(w || ''));
  return m ? `W${m[1]}` : String(w || '—');
}

export function signCls(v) {
  if (!isNum(v)) return 'muted';
  return v < 0 ? 'neg' : v > 0 ? 'pos' : '';
}

// ---- Summary-first UX (item 13, 17, 18, 23) ----

// Edge (a rate difference) in the one main-view unit: "+1.6%". Never "pts".
export function edgeText(v, digits = 1) {
  if (!isNum(v)) return '—';
  const n = v * 100;
  const body = Math.abs(n).toFixed(digits);
  if (Number(body) === 0) return `${body}%`;
  return `${n > 0 ? '+' : '−'}${body}%`;
}

// Plain-words meaning of each verdict, shown as the chip tooltip and in the one legend per page (item 23).
export const VERDICT_WORDS = {
  too_early: 'Not enough markets yet to judge.',
  on_track: 'Results match what the research expected.',
  below: 'Clearly worse than expected. Worth a look.',
  above: 'Clearly better than expected.',
};

// "Won 79%; expected 95%" (item 13). Falls back to the server's reason when the numbers are missing.
export function shortReason(g) {
  if (!g) return '';
  if (isNum(g.win_rate) && isNum(g.expected_win_rate)) {
    return `Won ${rate(g.win_rate, 0)}; expected ${rate(g.expected_win_rate, 0)}`;
  }
  if (isNum(g.roi) && isNum(g.expected_roi)) {
    return `Returned ${edgeText(g.roi)}; expected ${edgeText(g.expected_roi)}`;
  }
  return g.verdict_reason || '';
}
