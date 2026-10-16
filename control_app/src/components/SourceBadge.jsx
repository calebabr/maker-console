import { sourceInfo } from '../utils/activity.js';

/** Who placed this: "Bot · Maker", "Bot · Arb", "You". Name and colour come from /api/strategies. */
export default function SourceBadge({ row, strategies }) {
  const info = sourceInfo(row, strategies);
  const color = info.color || 'var(--muted)';
  return (
    <span
      className="tag source-badge"
      data-source={info.id}
      style={{ color, borderColor: `color-mix(in srgb, ${color} 45%, transparent)`, background: `color-mix(in srgb, ${color} 12%, transparent)` }}
    >
      {info.name}
    </span>
  );
}
