import Card from './Card.jsx';
import { useStatus } from '../context/StatusContext.jsx';
import { DASH, dateTime, until } from '../utils/format.js';
import { shortId } from '../utils/version.js';

// Lease & Release: which server instance is trading right now, plus the version handshake numbers.
// The role is always spelled out in words (never colour alone).
export default function LeaseCard() {
  const { status } = useStatus();
  if (!status || !status.role) return null;
  const leader = status.role === 'leader';
  const holder = status.lease_holder;
  const mine = holder && status.instance_id && holder === status.instance_id;
  return (
    <Card
      title="Lease & Release"
      subtitle="Only one server instance trades at a time. During an update the new one waits as standby."
    >
      <p className="lease-role" data-testid="lease-role">
        <span className={`tag ${leader ? 'tag-ok' : 'tag-warn'}`}>{leader ? 'Leader' : 'Standby'}</span>{' '}
        <strong>{leader ? 'This server is trading' : 'This server is standing by'}</strong>
      </p>
      {!leader && (
        <p className="muted lease-note">
          Another server instance holds the lease. This one serves the app and Stop, but places no orders.
        </p>
      )}
      <dl className="stats">
        <div>
          <dt>Lease Holder</dt>
          <dd className="mono" title={holder || undefined}>
            {holder ? shortId(holder) : 'Nobody'}
            {mine ? ' (this server)' : ''}
          </dd>
        </div>
        <div>
          <dt>Generation</dt>
          <dd className="mono">{status.lease_generation ?? DASH}</dd>
        </div>
        <div>
          <dt>Lease Ends</dt>
          <dd title={status.lease_expires_at ? dateTime(status.lease_expires_at) : undefined}>
            {status.lease_expires_at ? until(status.lease_expires_at) : DASH}
          </dd>
        </div>
        <div>
          <dt>API Version</dt>
          <dd className="mono">{status.api_version || DASH}</dd>
        </div>
        <div>
          <dt>Schema Version</dt>
          <dd className="mono">{status.schema_version ?? DASH}</dd>
        </div>
        <div>
          <dt>Oldest App Allowed</dt>
          <dd className="mono">{status.min_app_version || DASH}</dd>
        </div>
      </dl>
    </Card>
  );
}
