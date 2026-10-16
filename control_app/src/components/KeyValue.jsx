import { humanize } from '../utils/format.js';

// Generic renderer for JSON objects whose keys we may not know in advance
// (monitor summary, audit details, extra market stats).
function isPlain(v) {
  return v === null || ['string', 'number', 'boolean'].includes(typeof v);
}

export function formatValue(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(+v.toFixed(4));
  return String(v);
}

export default function KeyValue({ data, depth = 0, skip = [] }) {
  if (isPlain(data)) return <span>{formatValue(data)}</span>;
  if (Array.isArray(data)) {
    if (data.length === 0) return <span className="muted">none</span>;
    if (data.every(isPlain)) return <span>{data.map(formatValue).join(', ')}</span>;
    return (
      <ol className="kv-list">
        {data.map((item, i) => (
          <li key={i}>
            <KeyValue data={item} depth={depth + 1} />
          </li>
        ))}
      </ol>
    );
  }
  if (typeof data !== 'object') return <span>{String(data)}</span>;
  const entries = Object.entries(data).filter(([k]) => !skip.includes(k));
  if (!entries.length) return <span className="muted">empty</span>;
  return (
    <dl className={`kv kv-depth-${Math.min(depth, 3)}`}>
      {entries.map(([k, v]) => (
        <div className={`kv-row ${isPlain(v) || (Array.isArray(v) && v.every(isPlain)) ? '' : 'kv-nested'}`} key={k}>
          <dt>{humanize(k)}</dt>
          <dd>
            <KeyValue data={v} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
