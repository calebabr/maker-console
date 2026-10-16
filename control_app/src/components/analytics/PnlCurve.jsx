import { useMemo, useRef, useState } from 'react';
import { money } from '../../utils/format.js';
import { isNum, shortDate, signCls } from '../../utils/analytics.js';
import { dedupeTicks, indexTicks, moneyDigits } from '../../utils/chart.js';
import ChartFrame, { longestLabel } from '../ChartFrame.jsx';

const W = 340;
const H = 140;
const UH = 40;
const PAD = 4;

// pnl_curve -> [{date, cum, dd}] for all fills, maker only or taker only.
export function curveFor(points, kind = 'all') {
  const pts = Array.isArray(points) ? points : [];
  if (kind === 'all') {
    return pts.map((p) => ({
      date: p.date,
      cum: isNum(p.cum_pnl) ? p.cum_pnl : 0,
      dd: isNum(p.drawdown) ? p.drawdown : 0,
    }));
  }
  const key = kind === 'maker' ? 'maker_pnl' : 'taker_pnl';
  let cum = 0;
  let peak = 0;
  return pts.map((p) => {
    cum += isNum(p[key]) ? p[key] : 0;
    peak = Math.max(peak, cum);
    return { date: p.date, cum: Math.round(cum * 100) / 100, dd: Math.round((cum - peak) * 100) / 100 };
  });
}

/**
 * Cumulative P&L (top) with the drawdown "underwater" strip beneath it.
 * Hand-rolled SVG; drag or hover to read a day, otherwise the readout shows the latest day.
 */
export default function PnlCurve({ points, kind = 'all' }) {
  const data = useMemo(() => curveFor(points, kind), [points, kind]);
  const [idx, setIdx] = useState(null);
  const svgRef = useRef(null);

  if (data.length < 2) return <p className="muted small">Not enough settled days for a chart yet.</p>;

  const cums = data.map((d) => d.cum);
  const hi = Math.max(0, ...cums);
  const lo = Math.min(0, ...cums);
  const span = hi - lo || 1;
  const minDd = Math.min(0, ...data.map((d) => d.dd));
  const step = (W - PAD * 2) / (data.length - 1);
  const x = (i) => PAD + i * step;
  const y = (v) => PAD + (H - PAD * 2) * (1 - (v - lo) / span);
  const uy = (v) => (minDd < 0 ? (v / minDd) * (UH - 2) : 0);
  const line = data.map((d, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(d.cum).toFixed(1)}`).join(' ');
  const area = `${line} L${x(data.length - 1).toFixed(1)},${y(0).toFixed(1)} L${x(0).toFixed(1)},${y(0).toFixed(1)} Z`;
  const under =
    `M${x(0).toFixed(1)},0 ` +
    data.map((d, i) => `L${x(i).toFixed(1)},${uy(d.dd).toFixed(1)}`).join(' ') +
    ` L${x(data.length - 1).toFixed(1)},0 Z`;
  const maxDdIdx = data.findIndex((d) => d.dd === minDd);

  const shown = idx == null ? data.length - 1 : idx;
  const cur = data[shown];
  const last = data[data.length - 1];

  function pick(e) {
    const el = svgRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (!r.width) return;
    const rel = ((e.clientX - r.left) / r.width) * W;
    setIdx(Math.max(0, Math.min(data.length - 1, Math.round((rel - PAD) / step))));
  }

  const digits = moneyDigits(Math.min(lo, minDd), hi);
  const fmt = (v) => (Math.abs(v) < 0.005 ? '$0' : money(v, { sign: true, digits }));
  // Top, bottom, then the zero line when it is inside the range, then the middle if there is room.
  const yVals = [hi, lo, ...(lo < 0 && hi > 0 ? [0] : []), (lo + hi) / 2];
  const yTicks = dedupeTicks(yVals.map((v) => ({ v, label: fmt(v), pos: y(v) / H })), 0.14);
  const ddTicks = minDd < 0 ? dedupeTicks([{ label: '$0', pos: 0 }, { label: fmt(minDd), pos: uy(minDd) / UH }], 0.3) : [{ label: '$0', pos: 0 }];
  const xTicks = indexTicks(data.length, (i) => shortDate(data[i].date), (i) => x(i) / W);
  const sizer = longestLabel([...yTicks, ...ddTicks]);

  const label =
    `Cumulative P&L in dollars by date, from ${shortDate(data[0].date)} to ${shortDate(last.date)}: ${money(last.cum, { sign: true })}. ` +
    `Largest drawdown ${money(minDd)}. Scale ${money(lo, { sign: true })} to ${money(hi, { sign: true })}.`;

  return (
    <div className="pnl-chart" data-testid="pnl-curve">
      <div className="pnl-readout" aria-live="off">
        <strong className={signCls(cur.cum)}>{money(cur.cum, { sign: true })}</strong>
        <span>{idx == null ? `to ${shortDate(cur.date)}` : `on ${shortDate(cur.date)}`}</span>
        <span>
          Drawdown <span className={cur.dd < 0 ? 'neg' : ''}>{cur.dd < 0 ? money(cur.dd) : '$0.00'}</span>
        </span>
      </div>
      <ChartFrame yTitle="Cumulative P&L ($)" yTicks={yTicks} sizer={sizer} testId="pnl-main" grid={false}>
        <svg
          ref={svgRef}
          className="pnl-svg"
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={label}
          onPointerDown={pick}
          onPointerMove={pick}
          onPointerLeave={() => setIdx(null)}
        >
          {yTicks.map((t) => (
            <line key={t.label} x1="0" x2={W} y1={t.pos * H} y2={t.pos * H} stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          ))}
          <path d={area} fill="var(--accent)" opacity="0.1" />
          <line x1="0" x2={W} y1={y(0)} y2={y(0)} stroke="var(--muted)" strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
          <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          <line x1={x(shown)} x2={x(shown)} y1="0" y2={H} stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <circle cx={x(shown)} cy={y(cur.cum)} r="3.5" fill="var(--accent)" vectorEffect="non-scaling-stroke" />
        </svg>
      </ChartFrame>
      <ChartFrame
        className="pnl-under-frame"
        yTitle="Drawdown From Peak ($)"
        xTitle="Date"
        yTicks={ddTicks}
        xTicks={xTicks}
        sizer={sizer}
        testId="pnl-drawdown"
        grid={false}
      >
        <svg className="pnl-under" viewBox={`0 0 ${W} ${UH}`} preserveAspectRatio="none" aria-hidden="true">
          <line x1="0" x2={W} y1="0.5" y2="0.5" stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <path d={under} fill="var(--danger)" opacity="0.35" />
          {minDd < 0 && (
            <line x1={x(maxDdIdx)} x2={x(maxDdIdx)} y1="0" y2={UH} stroke="var(--danger)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          )}
          <line x1={x(shown)} x2={x(shown)} y1="0" y2={UH} stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        </svg>
      </ChartFrame>
      <p className="hint pnl-dd-note">
        Max Drawdown <span className={minDd < 0 ? 'neg' : ''}>{minDd < 0 ? money(minDd) : 'none'}</span>
        {minDd < 0 && ` on ${shortDate(data[maxDdIdx].date)}`}
      </p>
    </div>
  );
}
