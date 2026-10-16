import { useState } from 'react';
import { IconLock } from '../components/Icons.jsx';
import Card from '../components/Card.jsx';
import Chip from '../components/Chip.jsx';
import FoldCard from '../components/FoldCard.jsx';
import KeyValue from '../components/KeyValue.jsx';
import CiBar, { CiSummary } from '../components/CiBar.jsx';
import HelpIcon from '../components/HelpIcon.jsx';
import FillQuality from '../components/FillQuality.jsx';
import { ErrorNotice, Loading, StaleNotice } from '../components/Notices.jsx';
import { useDialogs } from '../context/DialogContext.jsx';
import { useResource } from '../hooks/useResource.js';
import { api } from '../services/api.js';
import { ago, formatMoney, formatPct, num, titleCase } from '../utils/format.js';
import '../styles/analytics.css';

// queue_stats is not shown here any more (item 8): the queue-position table lives in Analytics > Execution.
const KNOWN = ['series', 'enabled', 'fills', 'cost', 'pnl', 'events', 'roi', 'ci_low', 'ci_high', 'verdict', 'need_more', 'fill_quality', 'queue_stats'];

const NAMES = {
  KXDEMOA: 'Demo series A',
  KXDEMOB: 'Demo series B',
  KXDEMOC: 'Demo series C',
  KXDEMOD: 'Demo series D',
  KXDEMOE: 'Demo series E',
  KXDEMOF: 'Demo series F',
};

export function marketName(series) {
  return NAMES[series] ? titleCase(NAMES[series]) : series;
}

// Plain-words meaning of each chip (item 23), shown as the chip tooltip.
const VERDICT_TEXT = {
  pause: 'The research says this series is clearly losing. Pausing is suggested.',
  keep: 'Results are in line with what the research expected. Keep it running.',
  collecting: 'Not enough fills yet to judge this series.',
};

// /api/markets rows: {series, enabled, ...maker_monitor per_series stats (fills, cost, pnl, events, roi,
// ci_low, ci_high), verdict: "pause"|"keep"|null, need_more: int|null} (from summary.actions).
export function verdictInfo(m) {
  const more = typeof m.need_more === 'number' && m.need_more > 0 ? m.need_more : 0;
  const v = String(m.verdict || m.action || '').toLowerCase();
  if (v.includes('pause') || v.includes('losing')) return { label: 'Pause Suggested', tone: 'danger', title: VERDICT_TEXT.pause };
  if (v === 'keep' && more) return { label: `Keep (${more} More Fills to Judge)`, tone: 'muted', title: VERDICT_TEXT.collecting };
  if (v === 'keep' || v === 'ok') return { label: 'Keep', tone: 'ok', title: VERDICT_TEXT.keep };
  if (v || more) return { label: more ? `Collecting Data (${more} More Fills)` : titleCase(v), tone: 'muted', title: VERDICT_TEXT.collecting };
  return null;
}

export default function Markets() {
  const { data, error, loading, staleAt, reload } = useResource(() => api.getMarkets(), '/api/markets');
  const { twoFactor, notify } = useDialogs();
  const [busy, setBusy] = useState(null);
  const markets = (data && data.markets) || [];

  async function toggle(m) {
    const enable = !m.enabled;
    setBusy(m.series);
    try {
      const r = await twoFactor({
        title: enable ? `Resume ${m.series}?` : `Pause ${m.series}?`,
        message: enable
          ? 'The bot will quote this series again from the next pass.'
          : 'The bot stops placing new bids on this series. Existing fills are held to settlement.',
        confirmLabel: enable ? 'Resume' : 'Pause',
        danger: !enable,
        action: (code) => api.setMarket(m.series, enable, code),
      });
      if (r) {
        notify({
          kind: 'ok',
          title: `${m.series} ${enable ? 'resumed' : 'paused'}`,
          text: r.version != null ? `Settings saved as version ${r.version}.` : undefined,
        });
        reload();
      }
    } catch (err) {
      notify({ kind: 'error', title: 'Change not saved', text: err.message, details: err.details });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Markets</h1>
        <button type="button" className="btn small" onClick={reload} disabled={loading}>
          Refresh
        </button>
      </div>
      {data && data.report_utc && <p className="hint">Stats from the health report {ago(data.report_utc)}.</p>}
      <StaleNotice staleAt={error ? staleAt : null} />
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data && <Loading />}
      {data && !markets.length && <p className="muted">No markets reported.</p>}
      {data && 'fill_quality' in data && (
        <Card title={<>Fill Quality, All Series<HelpIcon term="fill-quality" /></>} subtitle="Our fills vs the market tape.">
          <FillQuality fq={data.fill_quality} />
        </Card>
      )}

      {markets.length > 0 && (
        <p className="hint mk-legend" data-testid="ci-explainer">
          Tap a market for its chart. The dot is the return so far and the bar is the likely range
          <HelpIcon term="ci-bars" />. If the bar crosses zero, it is too soon to say.
        </p>
      )}

      <div className="mk-list">
        {markets.map((m) => {
          const v = verdictInfo(m);
          const extra = Object.fromEntries(Object.entries(m).filter(([k]) => !KNOWN.includes(k)));
          const hasCi = typeof m.ci_low === 'number' && typeof m.ci_high === 'number';
          return (
            <FoldCard
              key={m.series}
              className="mk-card"
              title={
                <>
                  {marketName(m.series)} <span className="mono muted small">{m.series}</span>
                </>
              }
              summary={
                <>
                  <span>
                    <b className="num">{num(m.fills)}</b> Fills
                  </span>
                  <span className={m.pnl < 0 ? 'neg' : m.pnl > 0 ? 'pos' : ''}>{formatMoney(m.pnl, { sign: true })}</span>
                  {!m.enabled && <Chip tone="muted">Not Trading</Chip>}
                  {v && (
                    <Chip tone={v.tone} title={v.title}>
                      {v.label}
                    </Chip>
                  )}
                </>
              }
              actions={
                <button
                  type="button"
                  className={`btn small ${m.enabled ? '' : 'primary'}`}
                  onClick={() => toggle(m)}
                  disabled={!!busy}
                  aria-label={`${m.enabled ? 'Pause' : 'Resume'} ${marketName(m.series)}`}
                >
                  {busy === m.series ? 'Saving…' : m.enabled ? <><IconLock /> Pause</> : <><IconLock /> Resume</>}
                </button>
              }
            >
              <dl className="stats">
                <div>
                  <dt>Fills<HelpIcon term="fill" /></dt>
                  <dd>{num(m.fills)}</dd>
                </div>
                <div>
                  <dt>P&amp;L<HelpIcon term="pnl" /></dt>
                  <dd className={m.pnl < 0 ? 'neg' : ''}>{formatMoney(m.pnl, { sign: true })}</dd>
                </div>
                <div>
                  <dt>Profit per $1 Spent<HelpIcon term="roi" /></dt>
                  <dd className={m.roi < 0 ? 'neg' : ''}>{formatPct(m.roi, { digits: 1 })}</dd>
                </div>
                <div>
                  <dt>Spent</dt>
                  <dd>{formatMoney(m.cost)}</dd>
                </div>
              </dl>
              {hasCi ? (
                <>
                  <CiSummary roi={m.roi} low={m.ci_low} high={m.ci_high} />
                  <CiBar roi={m.roi} low={m.ci_low} high={m.ci_high} />
                </>
              ) : (
                <p className="hint">Not enough fills yet for a likely range.</p>
              )}
              {m.fill_quality && <FillQuality fq={m.fill_quality} compact />}
              {Object.keys(extra).length > 0 && (
                <details className="more-details">
                  <summary>More Stats</summary>
                  <KeyValue data={extra} />
                </details>
              )}
            </FoldCard>
          );
        })}
      </div>
    </div>
  );
}
