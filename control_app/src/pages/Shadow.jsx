import { useState } from 'react';
import { Link } from 'react-router-dom';
import Card from '../components/Card.jsx';
import ShadowBand from '../components/ShadowBand.jsx';
import { ErrorNotice, Loading, StaleNotice } from '../components/Notices.jsx';
import { useResource } from '../hooks/useResource.js';
import { api } from '../services/api.js';
import { ago, money, pct } from '../utils/format.js';
import '../styles/shadow.css';

// Shadow estimate (docs/SHADOW_CONTRACT.md): a simulation from public market data. No orders are placed,
// so every figure here is an estimate, never real P&L.
//   GET /api/shadow/summary -> {enabled, standin, status, headline, headline_zero_fee, by_series, by_cell, daily, caveats}

export const CAVEATS = [
  'Our own market impact: real orders would sit in the queue and change who gets filled. The simulation cannot see that.',
  'Hidden queue changes between checks: cancels and trades between two looks are unknown, which is why the result is a range of low, middle and high.',
  'No cash limits in v1: the simulation may take positions a real balance could not fund.',
  'Outages and rejects: a real run can miss passes or have orders refused. The simulation assumes none.',
  'Small sample: with few settled episodes the range is wide and the middle figure moves a lot.',
];

const sgn = (v) => (typeof v === 'number' && v > 0 ? 'pos' : typeof v === 'number' && v < 0 ? 'neg' : '');
const usd = (v) => money(v, { sign: true });
const rate = (v) => pct(v, { sign: false, digits: 1 });
const roi = (v) => pct(v, { sign: true });
const cellLabel = (c) => (Array.isArray(c) ? c.join(' / ') : String(c ?? ''));

function Range({ label, r, fmt }) {
  if (!r) return null;
  const ci = Array.isArray(r.ci90) ? r.ci90 : null;
  return (
    <div>
      <h3 className="subhead">{label}</h3>
      <dl className="sh-range">
        <div>
          <dt>Low</dt>
          <dd className={sgn(r.lo)}>{fmt(r.lo)}</dd>
        </div>
        <div className="mid">
          <dt>Middle</dt>
          <dd className={sgn(r.mid)}>{fmt(r.mid)}</dd>
          {ci && (
            <div className="sh-ci">
              90%: {fmt(ci[0])} to {fmt(ci[1])}
            </div>
          )}
        </div>
        <div>
          <dt>High</dt>
          <dd className={sgn(r.hi)}>{fmt(r.hi)}</dd>
        </div>
      </dl>
    </div>
  );
}

function PnlTable({ rows, keyOf, labelOf, head }) {
  if (!rows || !rows.length) return <p className="muted small">Nothing settled yet.</p>;
  return (
    <div className="sh-table-wrap">
      <table className="sh-table">
        <thead>
          <tr>
            <th scope="col">{head}</th>
            <th scope="col">Episodes</th>
            <th scope="col">Fills</th>
            <th scope="col">Low</th>
            <th scope="col">Mid</th>
            <th scope="col">High</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={keyOf(r)}>
              <th scope="row">{labelOf(r)}</th>
              <td>{r.episodes}</td>
              <td>{typeof r.fills_mid === 'number' ? r.fills_mid.toFixed(1) : '—'}</td>
              <td>{usd(r.pnl && r.pnl.lo)}</td>
              <td>{usd(r.pnl && r.pnl.mid)}</td>
              <td>{usd(r.pnl && r.pnl.hi)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Shadow() {
  const { data, error, loading, staleAt, reload } = useResource(() => api.getShadowSummary(), '/api/shadow/summary');
  const [zeroFee, setZeroFee] = useState(false);

  const st = (data && data.status) || {};
  const h = data && (zeroFee && data.headline_zero_fee ? data.headline_zero_fee : data.headline);
  const caveats = data && Array.isArray(data.caveats) && data.caveats.length ? data.caveats : CAVEATS;

  return (
    <div className="page">
      <div className="page-head">
        <h1>Shadow Estimate</h1>
        <button type="button" className="btn small" onClick={reload} disabled={loading}>
          Refresh
        </button>
      </div>
      <StaleNotice staleAt={error ? staleAt : null} />
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data && <Loading />}

      {data && (
        <>
          <div className="notice notice-info sh-banner">
            Simulated. No orders were placed. Real fills could be lower or higher; the range runs from pessimistic to
            optimistic queue assumptions.
          </div>
          {data.standin && (
            <div className="notice notice-error" role="alert">
              Strategy not installed: shadow uses a placeholder, so these numbers say nothing about the real strategy.
            </div>
          )}

          {!data.enabled ? (
            <Card title="Shadow estimate is off">
              <p>
                Turn it on in <Link to="/server">Server</Link>: tick Shadow estimate (the shadow_enabled cloud setting)
                and save with a fresh 2FA code. Nothing is simulated while it is off. No orders are ever placed either
                way.
              </p>
            </Card>
          ) : (
            <>
              <Card title="Coverage">
                <dl className="stats">
                  <div>
                    <dt>Days covered</dt>
                    <dd>{st.days_covered ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>Settled episodes</dt>
                    <dd>{st.settled_episodes ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>Estimated fills (middle)</dt>
                    <dd>{typeof st.central_fills === 'number' ? st.central_fills.toFixed(1) : '—'}</dd>
                  </div>
                  <div>
                    <dt>Last pass</dt>
                    <dd>{st.last_pass_ts ? ago(st.last_pass_ts) : '—'}</dd>
                  </div>
                </dl>
                {st.truncated && <p className="hint">Older episodes were left out of this estimate to keep it fast.</p>}
              </Card>

              {st.too_early || !h ? (
                <div className="notice notice-info" role="status">
                  Too early to estimate. The range needs more days and settled episodes before the figures mean much.
                </div>
              ) : (
                <>
                  <Card title="Estimated results (simulated)">
                    <div className="sh-toggle" role="group" aria-label="Fee treatment">
                      <button type="button" className="btn small" aria-pressed={!zeroFee} onClick={() => setZeroFee(false)}>
                        After fees
                      </button>
                      <button
                        type="button"
                        className="btn small"
                        aria-pressed={zeroFee}
                        onClick={() => setZeroFee(true)}
                        disabled={!data.headline_zero_fee}
                      >
                        Zero fees
                      </button>
                    </div>
                    <Range label={zeroFee ? 'Estimated P&L, zero fees' : 'Estimated P&L, after maker fees'} r={h.pnl} fmt={usd} />
                    <Range label="Estimated ROI" r={h.roi} fmt={roi} />
                    <Range label="Estimated fill rate" r={h.fill_rate} fmt={rate} />
                    <dl className="stats">
                      <div>
                        <dt>Peak capital</dt>
                        <dd>{money(h.peak_capital)}</dd>
                      </div>
                    </dl>
                  </Card>

                  <Card title="Cumulative estimated P&L">
                    <ShadowBand daily={data.daily} />
                  </Card>

                  <Card title="By series">
                    <PnlTable rows={data.by_series} keyOf={(r) => r.series} labelOf={(r) => r.series} head="Series" />
                  </Card>
                  <Card title="By cell">
                    <PnlTable rows={data.by_cell} keyOf={(r) => cellLabel(r.cell)} labelOf={(r) => cellLabel(r.cell)} head="Cell" />
                  </Card>
                </>
              )}
            </>
          )}

          <Card title="How this is estimated and what it cannot capture">
            <p>
              The app watches public order books and replays where a resting bid would have stood in the queue. Fills
              are bracketed by a pessimistic and an optimistic queue assumption. Every figure is an estimate, not a
              record of trades.
            </p>
            <ul className="sh-notes">
              {caveats.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </Card>
        </>
      )}
    </div>
  );
}
