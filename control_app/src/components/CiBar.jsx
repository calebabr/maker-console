import { formatPct, pct } from '../utils/format.js';
import ChartFrame from './ChartFrame.jsx';
import BarLegend, { BarEnds } from './analytics/BarLegend.jsx';

// Return with its 95% CI on a shared axis, with zero marked, and the expected return when a benchmark supplies one
// (`expected` null/undefined = no benchmark: no marker, no legend entry).
export default function CiBar({ roi, low, high, expected = null, domain = 0.08, legend = true }) {
  const hasExp = typeof expected === 'number' && Number.isFinite(expected);
  if (typeof roi !== 'number' || typeof low !== 'number' || typeof high !== 'number') return null;
  const W = 300;
  const x = (v) => ((Math.max(-domain, Math.min(domain, v)) + domain) / (2 * domain)) * W;
  // Ticks at -domain, -domain/2, 0, +domain/2, +domain (e.g. -8% .. +8% in 4-pt steps).
  const ticks = [-1, -0.5, 0, 0.5, 1].map((k) => k * domain);
  const tickLabel = (v) => (Math.abs(v) < 1e-12 ? '0%' : pct(v, { digits: Math.abs(v * 100) % 1 ? 1 : 0 }));
  const xTicks = ticks.map((v) => ({ label: tickLabel(v), pos: x(v) / W }));
  const stepPts = Math.round(domain * 50 * 10) / 10;
  return (
    <>
      <svg className="ci" viewBox={`0 0 ${W} 26`} preserveAspectRatio="none" role="img"
        aria-label={`Return per filled dollar ${pct(roi)}, likely range ${pct(low)} to ${pct(high)}, on a scale from ${tickLabel(-domain)} to ${tickLabel(domain)} in ${stepPts}% steps${hasExp ? `; expected ${pct(expected, { digits: 1 })}` : ''}`}>
        {ticks.map((v) => (
          <line key={v} data-mark="grid" x1={x(v)} x2={x(v)} y1="0" y2="26" stroke="var(--border)" strokeWidth="1" opacity="0.6" vectorEffect="non-scaling-stroke" />
        ))}
        <line x1="0" x2={W} y1="13" y2="13" stroke="var(--border)" strokeWidth="1" />
        <line x1={x(0)} x2={x(0)} y1="3" y2="23" stroke="var(--accent)" strokeWidth="2" />
        {hasExp && <line x1={x(expected)} x2={x(expected)} y1="5" y2="21" stroke="var(--text)" strokeWidth="1" strokeDasharray="2 2" />}
        <rect x={x(low)} y="9" width={Math.max(2, x(high) - x(low))} height="8" rx="2"
          fill={low > 0 ? 'var(--ok)' : high < 0 ? 'var(--danger)' : 'var(--off)'} opacity="0.45" />
        <circle cx={x(roi)} cy="13" r="4" fill={roi < 0 ? 'var(--danger)' : 'var(--text)'} />
      </svg>
      <ChartFrame className="chart-scale ci-scale-frame" xTicks={xTicks} grid={false} testId="ci-bar-scale" />
      <BarEnds lo={x(low) / W} hi={x(high) / W} loText={formatPct(low, { digits: 1 })} hiText={formatPct(high, { digits: 1 })} testId="ci-ends" />
      {legend && (
        <BarLegend
          bandColor={low > 0 ? 'var(--ok)' : high < 0 ? 'var(--danger)' : 'var(--off)'}
          items={[
            { key: 'dot', label: `Actual Return ${formatPct(roi, { digits: 1 })}` },
            { key: 'band', label: 'Likely Range (95%)' },
            { key: 'tick', label: 'Break-Even 0%' },
            ...(hasExp ? [{ key: 'expected', label: `Expected ${formatPct(expected, { digits: 1 })}` }] : []),
          ]}
        />
      )}
    </>
  );
}

/** "Return +2.1% · Likely Range −1.4% to +5.6%" (item 22): the numbers next to the plotted bar. */
export function CiSummary({ roi, low, high }) {
  if (typeof roi !== 'number' || typeof low !== 'number' || typeof high !== 'number') return null;
  return (
    <p className="ci-summary" data-testid="ci-summary">
      <span>
        Return <b className={roi < 0 ? 'neg' : ''}>{formatPct(roi, { digits: 1 })}</b>
      </span>
      <span className="sep" aria-hidden="true">·</span>
      <span>
        Likely Range <b>{formatPct(low, { digits: 1 })}</b> to <b>{formatPct(high, { digits: 1 })}</b>
      </span>
    </p>
  );
}
