// Axis helpers shared by the hand-rolled SVG charts (no chart library).
// Tick positions are fractions of the plot box: x 0 = left .. 1 = right, y 0 = top .. 1 = bottom.

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

/** Drop ticks whose label repeats the previous one or that sit too close to a kept tick. */
export function dedupeTicks(ticks, minGap = 0.12) {
  const out = [];
  for (const t of ticks) {
    if (!t || !isNum(t.pos) || !t.label) continue;
    if (out.some((k) => k.label === t.label || Math.abs(k.pos - t.pos) < minGap)) continue;
    out.push(t);
  }
  return out;
}

/**
 * Value ticks at min / mid / max (or just one when the data is flat).
 * `pos(v)` maps a value to a 0-1 position from the top of the plot.
 */
export function minMidMax(min, max, fmt, pos) {
  if (!isNum(min) || !isNum(max)) return [];
  if (max === min) return [{ v: min, label: fmt(min), pos: pos(min) }];
  const vals = [max, (min + max) / 2, min];
  return dedupeTicks(vals.map((v) => ({ v, label: fmt(v), pos: pos(v) })));
}

/** First / middle / last index ticks for a series of `n` points. */
export function indexTicks(n, labelAt, pos) {
  if (!(n >= 1)) return [];
  const idx = n === 1 ? [0] : n === 2 ? [0, 1] : [0, Math.round((n - 1) / 2), n - 1];
  return dedupeTicks(
    idx.map((i) => ({ i, label: labelAt(i) || '', pos: pos(i) })),
    0.18,
  );
}

/** Even steps from lo to hi (inclusive), e.g. 5-pt ticks on a win-rate scale. */
export function stepTicks(lo, hi, step) {
  if (!isNum(lo) || !isNum(hi) || !(step > 0) || hi < lo) return [];
  const out = [];
  const start = Math.ceil(lo / step - 1e-9) * step;
  for (let v = start; v <= hi + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

/** Whole-dollar labels when the range is wide enough to keep them distinct, else cents. */
export function moneyDigits(min, max) {
  return isNum(min) && isNum(max) && Math.abs(max - min) >= 4 ? 0 : 2;
}
