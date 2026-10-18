import { money } from '../utils/format.js';
import { shortDate } from '../utils/analytics.js';
import { dedupeTicks, indexTicks, moneyDigits } from '../utils/chart.js';
import ChartFrame from './ChartFrame.jsx';

const W = 340;
const H = 150;
const PAD = 4;
const ok = (v) => typeof v === 'number' && Number.isFinite(v);

/** Cumulative estimated P&L by day: a lo..hi band with the middle line. Hand-rolled SVG like PnlCurve. */
export default function ShadowBand({ daily }) {
  const data = (Array.isArray(daily) ? daily : []).filter((d) => ok(d.cum_lo) && ok(d.cum_mid) && ok(d.cum_hi));
  if (data.length < 2) return <p className="muted small">Not enough days for a chart yet.</p>;
  const hi = Math.max(0, ...data.map((d) => d.cum_hi));
  const lo = Math.min(0, ...data.map((d) => d.cum_lo));
  const span = hi - lo || 1;
  const step = (W - PAD * 2) / (data.length - 1);
  const x = (i) => PAD + i * step;
  const y = (v) => PAD + (H - PAD * 2) * (1 - (v - lo) / span);
  const f = (n) => n.toFixed(1);
  const mid = data.map((d, i) => `${i ? 'L' : 'M'}${f(x(i))},${f(y(d.cum_mid))}`).join(' ');
  const top = data.map((d, i) => `${i ? 'L' : 'M'}${f(x(i))},${f(y(d.cum_hi))}`).join(' ');
  const bottom = data
    .map((d, i) => `L${f(x(i))},${f(y(d.cum_lo))}`)
    .reverse()
    .join(' ');
  const band = `${top} ${bottom} Z`;
  const digits = moneyDigits(lo, hi);
  const fmt = (v) => (Math.abs(v) < 0.005 ? '$0' : money(v, { sign: true, digits }));
  const yTicks = dedupeTicks([hi, lo, (lo + hi) / 2].map((v) => ({ label: fmt(v), pos: y(v) / H })), 0.18);
  const xTicks = indexTicks(data.length, (i) => shortDate(data[i].day), (i) => x(i) / W);
  const last = data[data.length - 1];
  const label =
    `Estimated cumulative P&L by day, simulated, from ${shortDate(data[0].day)} to ${shortDate(last.day)}. ` +
    `Latest: low ${money(last.cum_lo, { sign: true })}, middle ${money(last.cum_mid, { sign: true })}, high ${money(last.cum_hi, { sign: true })}.`;
  return (
    <div className="sh-chart" data-testid="shadow-band">
      <ChartFrame yTitle="Estimated cumulative P&L ($)" xTitle="Date" yTicks={yTicks} xTicks={xTicks} grid={false}>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
          {yTicks.map((t) => (
            <line key={t.label} x1="0" x2={W} y1={t.pos * H} y2={t.pos * H} stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          ))}
          <path d={band} fill="var(--accent)" opacity="0.15" />
          <line x1="0" x2={W} y1={y(0)} y2={y(0)} stroke="var(--muted)" strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
          <path d={mid} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        </svg>
      </ChartFrame>
      <p className="hint">Shaded band: pessimistic to optimistic queue assumptions. Line: middle estimate.</p>
    </div>
  );
}
