import ChartFrame from './ChartFrame.jsx';
import { indexTicks, minMidMax } from '../utils/chart.js';

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

/**
 * Small inline SVG line chart. No chart library.
 *   values: number[]; labels: string[] parallel to values (x-axis, e.g. dates or weeks).
 *   yFormat: value -> tick label (e.g. dollars). yTitle / xTitle: axis titles with units.
 * With any axis prop the chart gets a y scale (min / mid / max) and x ticks (first / middle / last).
 */
export default function Sparkline({
  values,
  labels,
  width = 320,
  height = 64,
  label,
  color = 'var(--accent)',
  yFormat,
  yTitle,
  xTitle,
  testId,
}) {
  const src = Array.isArray(values) ? values : [];
  const keep = [];
  src.forEach((v, i) => {
    if (isNum(v)) keep.push(i);
  });
  const pts = keep.map((i) => src[i]);
  const xl = keep.map((i) => (Array.isArray(labels) ? labels[i] : undefined));
  if (pts.length < 2) return <div className="muted small">Not enough data for a chart yet.</div>;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const flat = max === min;
  const span = max - min || 1;
  const pad = 4;
  const step = (width - pad * 2) / (pts.length - 1);
  const y = (v) => (flat ? height / 2 : pad + (height - pad * 2) * (1 - (v - min) / span));
  const d = pts.map((v, i) => `${i ? 'L' : 'M'}${(pad + i * step).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const area = `${d} L${(pad + (pts.length - 1) * step).toFixed(1)},${height - pad} L${pad},${height - pad} Z`;
  const svg = (
    <svg
      className="sparkline"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label || 'trend chart'}
      style={{ height }}
    >
      <path d={area} fill={color} opacity="0.12" />
      <path d={d} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <circle cx={pad + (pts.length - 1) * step} cy={y(pts[pts.length - 1])} r="3" fill={color} />
    </svg>
  );
  const axes = yFormat || yTitle || xTitle || Array.isArray(labels);
  if (!axes) return svg;
  const fmt = yFormat || ((v) => String(Math.round(v * 100) / 100));
  const yTicks = minMidMax(min, max, fmt, (v) => y(v) / height);
  const xTicks = Array.isArray(labels) ? indexTicks(pts.length, (i) => xl[i], (i) => (pad + i * step) / width) : [];
  return (
    <ChartFrame yTitle={yTitle} xTitle={xTitle} yTicks={yTicks} xTicks={xTicks} testId={testId}>
      {svg}
    </ChartFrame>
  );
}
