import { money, num } from '../../utils/format.js';
import { isNum, shortDate, signCls } from '../../utils/analytics.js';
import { dedupeTicks, indexTicks } from '../../utils/chart.js';
import ChartFrame from '../ChartFrame.jsx';

const W = 340;
const H = 64;

/** Arbitrage opportunities vs executed sets per day, plus taker P&L. */
export default function ArbHistory({ arb }) {
  const a = arb || {};
  const daily = (Array.isArray(a.daily) ? a.daily : []).filter(Boolean);
  const opps = isNum(a.opportunities) ? a.opportunities : 0;
  return (
    <>
      <dl className="stats">
        <div>
          <dt>Opportunities</dt>
          <dd>{num(a.opportunities)}</dd>
        </div>
        <div>
          <dt>Executed</dt>
          <dd>{num(a.executed)}</dd>
        </div>
        <div>
          <dt>Arbitrage P&amp;L</dt>
          <dd className={signCls(a.pnl)}>{money(a.pnl, { sign: true })}</dd>
        </div>
        <div>
          <dt>Days With One</dt>
          <dd>{num(daily.filter((d) => d.opportunities > 0).length)}</dd>
        </div>
      </dl>
      {opps > 0 && daily.length > 1 ? <Bars daily={daily} /> : <p className="hint">No arbitrage opportunities in this window.</p>}
    </>
  );
}

function Bars({ daily }) {
  const counts = daily.map((d) => (isNum(d.opportunities) ? d.opportunities : 0));
  const max = Math.max(1, ...counts);
  const slot = W / daily.length;
  const bw = Math.max(1, slot * 0.7);
  const h = (v) => ((isNum(v) ? v : 0) / max) * (H - 2);
  const yPos = (v) => (H - h(v)) / H;
  // Whole-number count ticks: 0, the max, and a whole middle value when there is room.
  const mid = Math.round(max / 2);
  const yTicks = dedupeTicks(
    [max, 0, ...(mid > 0 && mid < max ? [mid] : [])].map((v) => ({ v, label: num(v), pos: yPos(v) })),
    0.25,
  );
  const xTicks = indexTicks(daily.length, (i) => shortDate(daily[i].date), (i) => (i * slot + bw / 2) / W);
  const executed = daily.reduce((s, d) => s + (isNum(d.executed) ? d.executed : 0), 0);
  return (
    <ChartFrame yTitle="Opportunities per day (count)" xTitle="Date" yTicks={yTicks} xTicks={xTicks} testId="arb-chart"
      footer={
        <div className="chart-legend arb-legend" aria-hidden="true">
          <span><i className="lg-bar" />seen</span>
          <span><i className="lg-bar" style={{ background: 'var(--accent)', opacity: 1 }} />executed</span>
        </div>
      }
    >
      <svg
        className="arb-bars"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={
          `Arbitrage opportunities per day (count), ${shortDate(daily[0].date)} to ${shortDate(daily[daily.length - 1].date)}: ` +
          `up to ${num(max)} a day, ${num(counts.reduce((s, v) => s + v, 0))} in total; filled bars are executed sets (${num(executed)})`
        }
      >
        <line x1="0" x2={W} y1={H - 0.5} y2={H - 0.5} stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {daily.map((d, i) => (
          <g key={d.date || i}>
            <rect x={i * slot} y={H - h(d.opportunities)} width={bw} height={h(d.opportunities)} fill="var(--off)" opacity="0.45" />
            {d.executed > 0 && <rect x={i * slot} y={H - h(d.executed)} width={bw} height={h(d.executed)} fill="var(--accent)" />}
          </g>
        ))}
      </svg>
    </ChartFrame>
  );
}
