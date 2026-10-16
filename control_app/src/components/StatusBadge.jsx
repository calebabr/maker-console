import { badgeStyles } from '../utils/theme.js';
import { badgeKind } from '../utils/status.js';

/**
 * 🟢 live running / 🟡 demo running / ⚪ stopped / 🔴 error or heartbeat > 30 min.
 * Pass `status` (GET /api/status) or an explicit `kind`.
 */
export default function StatusBadge({ status, kind: forcedKind, compact = false }) {
  const { kind, reason } = forcedKind ? { kind: forcedKind, reason: null } : badgeKind(status);
  const style = badgeStyles[kind] || badgeStyles.unknown;
  return (
    <span
      className={`badge badge-${kind} ${compact ? 'badge-compact' : ''}`}
      title={reason || style.label}
      data-kind={kind}
    >
      <span className="badge-dot" aria-hidden="true" />
      <span className="badge-label">{compact && kind === 'error' ? 'Error' : style.label}</span>
    </span>
  );
}
