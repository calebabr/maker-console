// Self-explanatory labels for the range bars (win rate on Edge rows, return on Markets cards):
// the two bound values under the ends of the bar, and a compact legend listing only the marks drawn.

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/**
 * Bound values under the bar ends. `lo` / `hi` are 0-1 positions along the bar; labels are clamped so the text
 * never overflows the card at 375 px, and merge into one centred "a to b" label when the bounds sit close together.
 */
export function BarEnds({ lo, hi, loText, hiText, testId = 'bar-ends' }) {
  const a = clamp(lo, 0.1, 0.9);
  const b = clamp(hi, 0.1, 0.9);
  const merged = b - a < 0.26;
  return (
    <div className="bar-ends" data-testid={testId} aria-hidden="true">
      {merged ? (
        <span style={{ left: `${((a + b) / 2) * 100}%` }}>
          {loText} to {hiText}
        </span>
      ) : (
        <>
          <span style={{ left: `${a * 100}%` }}>{loText}</span>
          <span style={{ left: `${b * 100}%` }}>{hiText}</span>
        </>
      )}
    </div>
  );
}

const SWATCH = {
  dot: <i className="lg-dot" />,
  tick: <i className="lg-tick" />,
  expected: <i className="lg-exp" />,
  thin: <i className="lg-thin" />,
};

/** items: [{key: 'dot'|'band'|'tick'|'expected'|'thin', label}]; `bandColor` tints the band swatch like the bar. */
export default function BarLegend({ items, bandColor = 'var(--off)' }) {
  return (
    <ul className="bar-legend" data-testid="bar-legend" aria-label="Chart key">
      {items.map((it) => (
        <li key={it.key} data-mark={it.key}>
          {it.key === 'band' ? <i className="lg-bar" style={{ background: bandColor }} /> : SWATCH[it.key]}
          {it.label}
        </li>
      ))}
    </ul>
  );
}
