import { num } from '../../utils/format.js';
import { cents, isNum, rate } from '../../utils/analytics.js';
import { stepTicks } from '../../utils/chart.js';
import { useWidth } from '../../hooks/useWidth.js';
import ChartFrame from '../ChartFrame.jsx';

// Plot height in px; the width is measured so 1 SVG unit = 1 px (round dots, true-size text).
const H = 220;
const P = 9;

/** x-axis (price) domain from the buckets themselves: 5-cent steps around the lowest and highest bucket, so no
 * price range is hard-coded (the buckets follow the installed strategy's cells). */
export function calibrationXDomain(buckets) {
  const los = buckets.map((b) => b.lo).filter(isNum);
  const his = buckets.map((b) => b.hi).filter(isNum);
  if (!los.length || !his.length) return [0, 1];
  const x0 = Math.max(0, Math.floor((Math.min(...los) - 0.01) * 20 + 1e-9) / 20);
  const x1 = Math.min(1, Math.ceil(Math.max(...his) * 20 - 1e-9) / 20);
  return x1 > x0 ? [x0, x1] : [x0, Math.min(1, x0 + 0.05)];
}

/** y-axis lower bound: a 5-pt step below the lowest point / whisker (never above 0.75, so the scale keeps some height); 0 with no data. */
export function calibrationFloor(buckets) {
  const vals = [];
  for (const b of buckets) for (const v of [b.ci_low, b.win_rate]) if (isNum(v)) vals.push(v);
  if (!vals.length) return 0;
  return Math.max(0, Math.min(0.75, Math.floor((Math.min(...vals) - 0.01) * 20) / 20));
}

function tone(b, xv) {
  if (isNum(b.ci_low) && b.ci_low > xv) return 'above';
  if (isNum(b.ci_high) && b.ci_high < xv) return 'below';
  return 'level';
}

/**
 * Calibration: realized win rate vs price paid in 2-cent buckets, with the diagonal
 * "fair price" line (win rate = price). Above the line = the side bought wins more often
 * than its price implies (the edge). Whiskers are clustered 95% ranges; dot size grows with n.
 */
export default function Calibration({ buckets }) {
  const [plotRef, W] = useWidth(300);
  const all = (Array.isArray(buckets) ? buckets : []).filter(Boolean);
  const pts = all.filter((b) => b.n > 0 && isNum(b.win_rate));
  if (!pts.length) return <p className="muted small">No settled maker fills to compare with prices yet.</p>;
  const y0 = calibrationFloor(pts);
  const [X0, X1] = calibrationXDomain(all);
  const px = (v) => P + ((Math.max(X0, Math.min(X1, v)) - X0) / (X1 - X0)) * (W - 2 * P);
  const py = (v) => P + (1 - (Math.max(y0, Math.min(1, v)) - y0) / (1 - y0)) * (H - 2 * P);
  const maxN = Math.max(...pts.map((b) => b.n));
  const rad = (n) => 2.5 + 5.5 * Math.sqrt(n / maxN);
  const xVals = stepTicks(X0, X1, X1 - X0 > 0.3 ? 0.1 : 0.05);
  const yVals = stepTicks(y0, 1, (1 - y0) > 0.3 ? 0.1 : 0.05);
  const xTicks = xVals.map((v) => ({ label: `${Math.round(v * 100)}¢`, pos: px(v) / W }));
  const yTicks = yVals.map((v) => ({ label: `${Math.round(v * 100)}%`, pos: py(v) / H }));
  const xOf = (b) => (isNum(b.avg_price) ? b.avg_price : (b.lo + b.hi) / 2);
  const above = pts.filter((b) => tone(b, xOf(b)) === 'above').length;
  const below = pts.filter((b) => tone(b, xOf(b)) === 'below').length;
  const fx = Math.max(X0, y0);
  const noteAt = X0 + (X1 - X0) * 0.35;

  return (
    <div data-testid="calibration">
      <ChartFrame yTitle="Win Rate (%)" xTitle="Price Paid (¢)" yTicks={yTicks} xTicks={xTicks} grid={false} testId="calibration-chart">
        <div ref={plotRef}>
          <svg
            className="calib calib-plot"
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label={
              `Calibration: Win rate in percent against price paid in cents, ${Math.round(X0 * 100)}¢ to ${Math.round(X1 * 100)}¢, ` +
              `win rate ${Math.round(y0 * 100)}% to 100%. ${pts.length} price buckets with fills; ` +
              `${above} clearly above the dashed fair-price line (win rate equals price), ${below} clearly below.`
            }
          >
            {xVals.map((v) => (
              <line key={`x${v}`} x1={px(v)} x2={px(v)} y1={0} y2={H} stroke="var(--border)" strokeWidth="1" />
            ))}
            {yVals.map((v) => (
              <line key={`y${v}`} x1={0} x2={W} y1={py(v)} y2={py(v)} stroke="var(--border)" strokeWidth="1" />
            ))}
            {/* Fair price: win rate = price paid. */}
            <line
              data-mark="fair"
              x1={px(fx)}
              y1={py(fx)}
              x2={px(X1)}
              y2={py(X1)}
              stroke="var(--accent)"
              strokeWidth="1.5"
              strokeDasharray="4 3"
            />
            <text x={px(noteAt)} y={py(noteAt) + 16} className="calib-note" aria-hidden="true">
              fair price
            </text>
            {pts.map((b) => {
              const xv = xOf(b);
              const t = tone(b, xv);
              const color = t === 'above' ? 'var(--ok)' : t === 'below' ? 'var(--danger)' : 'var(--text)';
              return (
                <g key={b.bucket} data-bucket={b.bucket} data-tone={t}>
                  {isNum(b.ci_low) && isNum(b.ci_high) && (
                    <line x1={px(xv)} x2={px(xv)} y1={py(b.ci_low)} y2={py(b.ci_high)} stroke={color} strokeWidth="1.5" opacity="0.7" />
                  )}
                  <circle cx={px(xv)} cy={py(b.win_rate)} r={rad(b.n)} fill={color} fillOpacity="0.35" stroke={color} strokeWidth="1.2" />
                </g>
              );
            })}
          </svg>
        </div>
      </ChartFrame>
      <p className="hint">
        Each dot is a 2¢ price bucket. Above the dashed line, the side bought won more often than its price implied. Bigger
        dots have more fills; whiskers are the likely range.
      </p>
      <details className="more-details">
        <summary>Show Numbers</summary>
        <div className="table-wrap">
          <table className="table small" aria-label="Calibration buckets">
            <thead>
              <tr>
                <th scope="col">Price</th>
                <th scope="col">Fills</th>
                <th scope="col">Markets</th>
                <th scope="col">Won</th>
                <th scope="col">Likely Range</th>
              </tr>
            </thead>
            <tbody>
              {all.map((b) => (
                <tr key={b.bucket}>
                  <td>{b.bucket.split('-').map((v) => cents(Number(v))).join('-')}</td>
                  <td>{num(b.n)}</td>
                  <td>{num(b.markets)}</td>
                  <td>{rate(b.win_rate)}</td>
                  <td>{isNum(b.ci_low) && isNum(b.ci_high) ? `${rate(b.ci_low, 0)}-${rate(b.ci_high, 0)}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
