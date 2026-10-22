import { useMemo, useState } from 'react';
import Card from '../components/Card.jsx';
import KalshiLink from '../components/KalshiLink.jsx';
import SourceBadge from '../components/SourceBadge.jsx';
import Chip from '../components/Chip.jsx';
import PeriodsCard from '../components/PeriodsCard.jsx';
import { ErrorNotice, Loading, StaleNotice } from '../components/Notices.jsx';
import { useResource } from '../hooks/useResource.js';
import { api } from '../services/api.js';
import { formatMoney } from '../utils/format.js';
import { matchesSource, sourceChips } from '../utils/activity.js';
import '../styles/settings.css';
import CollapsibleList from '../components/CollapsibleList.jsx';

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

function friendly(error) {
  if (!error) return null;
  if (error.status === 409 && error.detail === 'kalshi_not_configured') {
    return { message: 'Kalshi keys are not set on the server for this mode, so positions cannot be read.', details: [] };
  }
  if (error.status === 503 && error.detail === 'kalshi_unavailable') {
    return { message: 'Kalshi did not answer. Try again in a moment.', details: [] };
  }
  return error;
}

function PositionRow({ p, strategies }) {
  const cents = isNum(p.price) ? `${Math.round(p.price * 1000) / 10}¢` : '—';
  return (
    <li className="pos-row" data-source={p.source === 'you' ? 'you' : 'bot'}>
      <div className="pos-top">
        <span className="pos-title">{p.title || p.ticker}</span>
        <SourceBadge row={p} strategies={strategies} />
      </div>
      <div className="mono muted small" style={{ overflowWrap: 'anywhere' }}>{p.ticker}</div>
      <div className="pos-meta num">
        <span>{String(p.side || '').toUpperCase()}</span>
        <span>{isNum(p.count) ? p.count : '—'} × {cents}</span>
        <span>Value {formatMoney(p.value)}</span>
        <span>Pays {formatMoney(p.payout_if_wins)} if it wins</span>
      </div>
      <div className="row-links">
        <KalshiLink ticker={p.ticker} />
      </div>
    </li>
  );
}

export default function Account() {
  const { data, error, loading, staleAt, reload } = useResource(() => api.getAccountPositions(), '/api/account/positions');
  const { data: strategies } = useResource(() => api.getStrategies(), '/api/strategies');
  const [source, setSource] = useState('all');
  const rows = useMemo(() => (Array.isArray(data) ? data : []), [data]);
  const chips = useMemo(() => sourceChips(strategies), [strategies]);
  const shown = rows.filter((r) => matchesSource(r, source));
  const positions = shown.filter((r) => r.kind !== 'order');
  const orders = shown.filter((r) => r.kind === 'order');
  const total = (list) => list.reduce((a, r) => a + (isNum(r.value) ? r.value : 0), 0);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Account</h1>
        <button type="button" className="btn small" onClick={reload} disabled={loading}>
          Refresh
        </button>
      </div>
      <p className="hint">Everything open on your Kalshi account, tagged by who placed it. Read-only: manage trades in Kalshi.</p>
      <StaleNotice staleAt={error ? staleAt : null} />
      <ErrorNotice error={friendly(error)} onRetry={reload} />
      {loading && !data && <Loading />}

      {rows.length > 0 && (
        <div className="chips" role="group" aria-label="Filter by source">
          {chips.map((c) => (
            <button key={c.id} type="button" className={`chip ${source === c.id ? 'active' : ''}`} aria-pressed={source === c.id} onClick={() => setSource(c.id)}>
              {c.label}
            </button>
          ))}
        </div>
      )}

      {data && !rows.length && !error && <p className="muted">Nothing open on the account right now.</p>}
      {rows.length > 0 && !shown.length && <p className="muted">Nothing matches this filter.</p>}

      {positions.length > 0 && (
        <Card title="Open Positions" subtitle={<>{positions.length} <Chip tone="muted">{formatMoney(total(positions))} Value</Chip></>}>
          <CollapsibleList items={positions} pageKey="account-positions" label="positions">
            {(vis) => (
              <ul className="rows">
                {vis.map((p, i) => (
                  <PositionRow key={`${p.ticker}-${p.side}-${p.source}-${p.strategy}-${i}`} p={p} strategies={strategies} />
                ))}
              </ul>
            )}
          </CollapsibleList>
        </Card>
      )}
      {orders.length > 0 && (
        <Card title="Resting Orders" subtitle={`${orders.length} waiting to fill`}>
          <CollapsibleList items={orders} pageKey="account-orders" label="orders">
            {(vis) => (
              <ul className="rows">
                {vis.map((p, i) => (
                  <PositionRow key={`o-${p.ticker}-${p.side}-${p.source}-${p.strategy}-${i}`} p={p} strategies={strategies} />
                ))}
              </ul>
            )}
          </CollapsibleList>
        </Card>
      )}

      <PeriodsCard />
    </div>
  );
}
