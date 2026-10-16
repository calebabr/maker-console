import { cents, isNum, rate } from '../../utils/analytics.js';
import { stepTicks } from '../../utils/chart.js';
import ChartFrame from '../ChartFrame.jsx';
import BarLegend, { BarEnds } from './BarLegend.jsx';

const W = 300;
const PADX = 4;
const xOf = ([d0, d1], v) => PADX + ((Math.max(d0, Math.min(d1, v)) - d0) / (d1 - d0 || 1)) * (W - 2 * PADX);

/** Tick values for a win-rate scale: every 5 pts, or 10 / 20 pts on a wide domain (at most 6 ticks). */
export function ciTicks([d0, d1] = [0, 1]) {
  for (const step of [0.05, 0.1, 0.2, 0.25]) {
    const t = stepTicks(d0, d1, step);
    if (t.length <= 6) return { step, values: t };
  }
  return { step: d1 - d0, values: [d0, d1] };
}

/** Shared x-axis for a list of groups: from a little below the lowest CI / price / expectation to 100% (an empty list gives the full 0-100% scale). */
export function ciDomain(groups) {
  const vals = [];
  for (const g of groups || []) {
    for (const v of [g.win_ci_low, g.cluster_ci_low, g.avg_price, g.win_rate, g.expected_win_rate]) if (isNum(v)) vals.push(v);
  }
  if (!vals.length) return [0, 1];
  const lo = Math.floor((Math.min(...vals) - 0.01) * 20) / 20; // round down to 5 pts
  return [Math.max(0, Math.min(0.95, lo)), 1];
}

/**
 * Realized win rate (dot) with its 95% range (bar) against the average price paid
 * (accent tick = break-even win rate).
 *  - Main bar: the clustered CI when given (fills in one market move together), else Wilson.
 *  - Thin line under it: the per-fill Wilson CI, for comparison (only when the clustered one is shown).
 *  - Dashed tick with a small marker on top: the research expectation, when known.
 * Bar wholly right of the price tick = edge confirmed, wholly left = losing, straddling = undecided.
 */
export default function WinRateCiBar({ winRate, low, high, avgPrice, clusterLow, clusterHigh, expected, domain = [0, 1], legend = true }) {
  const hasCluster = isNum(clusterLow) && isNum(clusterHigh);
  if (!isNum(winRate) || !isNum(avgPrice) || (!hasCluster && (!isNum(low) || !isNum(high)))) return null;
  const mainLo = hasCluster ? clusterLow : low;
  const mainHi = hasCluster ? clusterHigh : high;
  const x = (v) => xOf(domain, v);
  const grid = ciTicks(domain).values;
  const verdict = mainLo > avgPrice ? 'above' : mainHi < avgPrice ? 'below' : 'straddles';
  const fill = verdict === 'above' ? 'var(--ok)' : verdict === 'below' ? 'var(--danger)' : 'var(--off)';
  const words = {
    above: 'whole range above the price paid',
    below: 'whole range below the price paid',
    straddles: 'range includes the price paid',
  }[verdict];
  const showWilson = hasCluster && isNum(low) && isNum(high);
  const hasExp = isNum(expected);
  const label =
    `Win rate ${rate(winRate)}, ${hasCluster ? 'clustered ' : ''}likely range ${rate(mainLo)} to ${rate(mainHi)}` +
    (showWilson ? ` (per-fill ${rate(low)} to ${rate(high)})` : '') +
    `, price paid ${cents(avgPrice)}` +
    (hasExp ? `, expected ${rate(expected)}` : '') +
    `: ${words}`;
  return (
    <div className="ci-block">
    <svg
        className="wr-ci"
        viewBox={`0 0 ${W} 26`}
        preserveAspectRatio="none"
        role="img"
        data-verdict={verdict}
        data-clustered={hasCluster ? 'true' : 'false'}
        aria-label={label}
      >
        {grid.map((v) => (
          <line key={v} data-mark="grid" x1={x(v)} x2={x(v)} y1="0" y2="26" stroke="var(--border)" strokeWidth="1" opacity="0.6" vectorEffect="non-scaling-stroke" />
        ))}
        <line x1="0" x2={W} y1="13" y2="13" stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        <rect x={x(mainLo)} y="9" width={Math.max(2, x(mainHi) - x(mainLo))} height="8" rx="2" fill={fill} opacity="0.5" />
        {showWilson && (
          <line data-mark="wilson" x1={x(low)} x2={Math.max(x(low) + 1, x(high))} y1="21.5" y2="21.5"
            stroke="var(--muted)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        )}
        {hasExp && (
          <>
            <line data-mark="expected" x1={x(expected)} x2={x(expected)} y1="4" y2="22" stroke="var(--text)"
              strokeWidth="1" strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
            <path d={`M${x(expected) - 3},0 L${x(expected) + 3},0 L${x(expected)},4 Z`} fill="var(--text)" />
          </>
        )}
        <line x1={x(avgPrice)} x2={x(avgPrice)} y1="3" y2="23" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        <circle cx={x(winRate)} cy="13" r="3.5" fill="var(--text)" />
      </svg>
      <BarEnds lo={x(mainLo) / W} hi={x(mainHi) / W} loText={rate(mainLo)} hiText={rate(mainHi)} />
      {legend && (
        <BarLegend
          bandColor={fill}
          items={[
            { key: 'dot', label: `Actual Win Rate ${rate(winRate)}` },
            { key: 'band', label: 'Likely Range (95%)' },
            { key: 'tick', label: `Break-Even (Price Paid ${cents(avgPrice)})` },
            ...(hasExp ? [{ key: 'expected', label: `Research Expected ${rate(expected)}` }] : []),
            ...(showWilson ? [{ key: 'thin', label: 'Per-Fill Range' }] : []),
          ]}
        />
      )}
    </div>
  );
}

/** Shared win-rate scale for a list of CI bars: % ticks aligned with the bars. */
export function CiScale({ domain }) {
  const { step, values } = ciTicks(domain);
  const xTicks = values.map((v) => ({ label: rate(v, 0), pos: xOf(domain, v) / W }));
  return (
    <ChartFrame
      className="chart-scale wr-scale-frame"
      yTitle={`Win Rate (%), ticks every ${Math.round(step * 100)}%`}
      xTicks={xTicks}
      grid={false}
      testId="ci-scale"
    />
  );
}
