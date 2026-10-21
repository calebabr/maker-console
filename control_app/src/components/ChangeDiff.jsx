import { formatMoney, titleCase, humanize } from '../utils/format.js';

const MONEY_KEYS = ['run_budget', 'max_order'];

/** Any value as readable text: objects never print as "[object Object]" (JSON instead). */
export function plainText(v) {
  if (v === null || v === undefined) return '—';
  if (Array.isArray(v)) return v.map(plainText).join(', ');
  if (typeof v === 'object') {
    try {
      return JSON.stringify(v);
    } catch {
      return '—';
    }
  }
  return String(v);
}

/** One setting value as the owner would read it ("$40.00", "50%", "10 min", "KXDEMOA, KXDEMOB"). */
export function formatSettingValue(key, v) {
  if (v === null || v === undefined || v === '') return '—';
  if (Array.isArray(v)) return plainText(v);
  if (typeof v === 'object') return plainText(v);
  if (typeof v === 'boolean') return v ? 'On' : 'Off';
  const n = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
  if (typeof n === 'number' && Number.isFinite(n)) {
    if (MONEY_KEYS.includes(key)) return formatMoney(n);
    if (key === 'max_open_frac') return `${+(n * 100).toFixed(1)}%`;
    if (key === 'poll_minutes') return `${n} min`;
  }
  return String(v);
}

export function settingLabel(key) {
  const k = String(key);
  const tail = k.includes('.') ? k.split('.').slice(-1)[0] : k;
  return titleCase(humanize(tail));
}

/**
 * Before -> after list: "Run Budget  $40.00 -> $50.00". `changes` is [{key, from, to}].
 * `format(key, v)` overrides the value formatter (alert settings use their own).
 */
export default function ChangeDiff({ changes, format = formatSettingValue, label = settingLabel, className = '' }) {
  return (
    <ul className={`diff-list ${className}`.trim()} aria-label="Changes">
      {changes.map((c) => (
        <li key={c.key} data-key={c.key}>
          <span className="diff-key">{label(c.key)}</span>
          <span className="diff-from num">{format(c.key, c.from)}</span>
          <span className="diff-arrow" aria-label="to">→</span>
          <span className="diff-to num">{format(c.key, c.to)}</span>
        </li>
      ))}
    </ul>
  );
}
