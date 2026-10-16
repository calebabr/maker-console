import '../styles/charts.css';

export const longestLabel = (ticks) =>
  (ticks || []).reduce((a, t) => (String(t.label).length > a.length ? String(t.label) : a), '');

const pct = (f) => `${(Math.max(0, Math.min(1, f)) * 100).toFixed(2)}%`;

function xStyle(pos) {
  // Keep the end labels inside the plot: left-align the first, right-align the last.
  if (pos <= 0.05) return { left: pct(pos) };
  if (pos >= 0.95) return { right: pct(1 - pos) };
  return { left: pct(pos), transform: 'translateX(-50%)' };
}

/**
 * Axis frame around a stretched SVG plot. Tick labels and axis titles are HTML (real px,
 * theme colours, tabular figures) positioned by fraction of the plot box, so they stay
 * readable at 375 px while the SVG itself scales with preserveAspectRatio="none".
 * Ticks and titles are aria-hidden: the chart's own aria-label carries the units and range.
 *
 * yTicks: [{label, pos}] pos 0 = top .. 1 = bottom.  xTicks: [{label, pos}] pos 0 = left .. 1 = right.
 */
export default function ChartFrame({ yTitle, xTitle, yTicks = [], xTicks = [], grid = true, className = '', testId, sizer, children, footer }) {
  // `sizer` lets stacked charts share one gutter width so their plots line up.
  const longest = sizer ?? longestLabel(yTicks);
  return (
    <div className={`chart ${className}`.trim()} data-testid={testId}>
      {yTitle && (
        <div className="chart-ytitle" aria-hidden="true" data-axis="y">
          {yTitle}
        </div>
      )}
      <div className="chart-grid">
        <div className="chart-y" aria-hidden="true">
          <span className="chart-sizer" data-sizer={longest} />
          {yTicks.map((t) => (
            <span key={`${t.label}@${t.pos}`} className="chart-tick chart-ytick" style={{ top: pct(t.pos) }}>
              {t.label}
            </span>
          ))}
        </div>
        <div className="chart-plot">
          {grid &&
            yTicks.map((t) => (
              <span key={`g${t.label}@${t.pos}`} className="chart-gridline" style={{ top: pct(t.pos) }} aria-hidden="true" />
            ))}
          {children}
        </div>
        {xTicks.length > 0 && (
          <>
            <span />
            <div className="chart-x" aria-hidden="true">
              {xTicks.map((t) => (
                <span key={`${t.label}@${t.pos}`} className="chart-tick chart-xtick" style={xStyle(t.pos)}>
                  {t.label}
                </span>
              ))}
            </div>
          </>
        )}
      </div>
      {xTitle && (
        <div className="chart-xtitle" aria-hidden="true" data-axis="x">
          {xTitle}
        </div>
      )}
      {footer}
    </div>
  );
}
