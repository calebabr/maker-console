// Strict checker mirroring docs/ANALYTICS_CONTRACT.md. Returns a list of problems (empty = conforms).
// The same key/type rules are checked against the real backend in control_api/tests/test_analytics_frontend_schema.py.
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const numOrNull = (v) => v === null || isNum(v);
const isIso = (v) => typeof v === 'string' && !Number.isNaN(Date.parse(v));
const isDate = (v) => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v);
const dp = (v, n) => Math.abs(v * 10 ** n - Math.round(v * 10 ** n)) < 1e-6;

// Price bands come from the installed strategy cells (backend: analytics.price_bands), so only the label format
// is checked here: "lo-last" in dollars, or "other".
export const isBand = (b) => b === 'other' || (typeof b === 'string' && /^0\.\d{2}-0\.\d{2}$/.test(b));
// Time-to-close buckets come from the installed cells' hour edges, so only the label format is checked.
export const isTtc = (b) => typeof b === 'string' && /^(\d+(\.\d+)?-\d+(\.\d+)?h|>\d+(\.\d+)?h)$/.test(b);
const SUMMARY_KEYS = ['env', 'days', 'generated_utc', 'truncated', 'totals', 'pnl_curve', 'by_cell', 'by_series', 'by_band', 'by_ttc', 'fill_quality', 'arb',
  // Sprint extension (items 1-4)
  'expectations', 'calibration', 'fill_rate'];
const CLUSTER_KEYS = ['cluster_ci_low', 'cluster_ci_high', 'edge_cluster_ci_low', 'edge_cluster_ci_high', 'roi_ci_low', 'roi_ci_high'];
const VERDICTS = ['too_early', 'on_track', 'below', 'above'];
const FR_KEYS = ['quotes', 'filled', 'partial', 'unfilled', 'fill_rate', 'median_rest_minutes', 'median_minutes_to_fill'];
const CALIB_KEYS = ['bucket', 'lo', 'hi', 'n', 'markets', 'wins', 'win_rate', 'avg_price', 'ci_low', 'ci_high'];
const TOTAL_KEYS = ['settled_fills', 'contracts', 'cost', 'pnl', 'roi', 'win_rate', 'open_fills', 'max_drawdown'];
const GROUP_NUM_OR_NULL = ['roi', 'avg_price', 'win_rate', 'win_ci_low', 'win_ci_high', 'edge', 'edge_ci_low', 'edge_ci_high'];
const TRADE_KEYS = ['fill_id', 'ts', 'ticker', 'series', 'side', 'kind', 'price', 'count', 'fee', 'cost', 'band', 'ttc_hours',
  'ttc_band', 'cell', 'status', 'result', 'settled_utc', 'pnl', 'fill_quality', 'source', 'strategy'];

function keys(obj, want, where, out, { exact = false } = {}) {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
    out.push(`${where}: not an object`);
    return false;
  }
  for (const k of want) if (!(k in obj)) out.push(`${where}: missing ${k}`);
  if (exact) for (const k of Object.keys(obj)) if (!want.includes(k)) out.push(`${where}: unexpected ${k}`);
  return true;
}

export function checkGroup(g, where, out, { cell = false } = {}) {
  const want = ['key', 'n', 'wins', 'contracts', 'cost', 'pnl', ...GROUP_NUM_OR_NULL, ...CLUSTER_KEYS,
    ...(cell ? ['cell', 'in_config', 'markets', 'expected_win_rate', 'expected_roi', 'verdict', 'verdict_reason'] : [])];
  if (!keys(g, want, where, out)) return;
  if (typeof g.key !== 'string') out.push(`${where}.key not string`);
  for (const k of ['n', 'wins', 'contracts']) if (!Number.isInteger(g[k]) || g[k] < 0) out.push(`${where}.${k} not a non-negative int`);
  for (const k of ['cost', 'pnl']) if (!isNum(g[k])) out.push(`${where}.${k} not number`);
  for (const k of GROUP_NUM_OR_NULL) if (!numOrNull(g[k])) out.push(`${where}.${k} not number|null`);
  if (g.wins > g.n) out.push(`${where}: wins > n`);
  if (g.n > 0 && g.win_rate != null) {
    if (g.win_rate < 0 || g.win_rate > 1) out.push(`${where}.win_rate out of 0..1`);
    if (!(g.win_ci_low <= g.win_rate + 1e-9 && g.win_rate <= g.win_ci_high + 1e-9)) out.push(`${where}: CI does not bracket win_rate`);
    if (g.edge_ci_low != null && !(g.edge_ci_low <= g.edge + 1e-9 && g.edge <= g.edge_ci_high + 1e-9)) {
      out.push(`${where}: edge CI does not bracket edge`);
    }
  }
  if (g.n === 0 && g.win_rate != null) out.push(`${where}: n=0 but win_rate set`);
  for (const k of CLUSTER_KEYS) if (!numOrNull(g[k])) out.push(`${where}.${k} not number|null`);
  for (const k of ['cluster_ci_low', 'cluster_ci_high']) if (g[k] != null && (g[k] < 0 || g[k] > 1)) out.push(`${where}.${k} out of 0..1`);
  if ((g.cluster_ci_low == null) !== (g.cluster_ci_high == null)) out.push(`${where}: cluster CI half null`);
  if (g.cluster_ci_low != null && g.win_rate != null && !(g.cluster_ci_low <= g.win_rate + 1e-9 && g.win_rate <= g.cluster_ci_high + 1e-9)) {
    out.push(`${where}: cluster CI does not bracket win_rate`);
  }
  if (cell) {
    if (!Number.isInteger(g.markets) || g.markets < 0 || g.markets > g.n) out.push(`${where}.markets not an int in 0..n`);
    if (!numOrNull(g.expected_win_rate) || !numOrNull(g.expected_roi)) out.push(`${where} expected_* not number|null`);
    if (!VERDICTS.includes(g.verdict)) out.push(`${where}.verdict ${g.verdict}`);
    if (typeof g.verdict_reason !== 'string' || !g.verdict_reason) out.push(`${where}.verdict_reason not a sentence`);
    if (g.markets < 20 && g.verdict !== 'too_early') out.push(`${where}: < 20 markets must be too_early`);
    if (typeof g.in_config !== 'boolean') out.push(`${where}.in_config not boolean`);
    if (g.cell !== null && typeof g.cell !== 'string') out.push(`${where}.cell not string|null`);
  }
}

export function checkSummary(s, { days, env } = {}) {
  const out = [];
  if (!keys(s, SUMMARY_KEYS, 'summary', out, { exact: true })) return out;
  if (!['demo', 'live', 'all'].includes(s.env)) out.push('env invalid');
  if (env && s.env !== env) out.push(`env ${s.env} != ${env}`);
  if (!Number.isInteger(s.days) || (days && s.days !== days)) out.push('days invalid');
  if (!isIso(s.generated_utc)) out.push('generated_utc not ISO');
  if (typeof s.truncated !== 'boolean') out.push('truncated not boolean');

  const t = s.totals;
  if (keys(t, TOTAL_KEYS, 'totals', out, { exact: true })) {
    for (const k of ['settled_fills', 'contracts', 'open_fills']) if (!Number.isInteger(t[k]) || t[k] < 0) out.push(`totals.${k} not int`);
    for (const k of ['cost', 'pnl', 'max_drawdown']) {
      if (!isNum(t[k])) out.push(`totals.${k} not number`);
      else if (!dp(t[k], 2)) out.push(`totals.${k} more than 2 dp`);
    }
    for (const k of ['roi', 'win_rate']) if (!numOrNull(t[k])) out.push(`totals.${k} not number|null`);
    if (t.max_drawdown > 0) out.push('max_drawdown > 0');
    if (t.settled_fills === 0 && (t.roi !== null || t.win_rate !== null)) out.push('empty window must have null roi/win_rate');
    if (t.win_rate != null && (t.win_rate < 0 || t.win_rate > 1)) out.push('win_rate out of 0..1');
  }

  if (!Array.isArray(s.pnl_curve)) out.push('pnl_curve not array');
  else {
    let prev = '';
    s.pnl_curve.forEach((p, i) => {
      const w = `pnl_curve[${i}]`;
      if (!keys(p, ['date', 'pnl', 'cum_pnl', 'drawdown', 'maker_pnl', 'taker_pnl'], w, out)) return;
      if (!isDate(p.date)) out.push(`${w}.date not YYYY-MM-DD`);
      if (p.date <= prev) out.push(`${w}.date not strictly ascending`);
      prev = p.date;
      for (const k of ['pnl', 'cum_pnl', 'drawdown', 'maker_pnl', 'taker_pnl']) if (!isNum(p[k])) out.push(`${w}.${k} not number`);
      if (p.drawdown > 0) out.push(`${w}.drawdown > 0`);
    });
    const last = s.pnl_curve[s.pnl_curve.length - 1];
    if (last && isNum(t.pnl) && Math.abs(last.cum_pnl - t.pnl) > 0.05) out.push(`curve end ${last.cum_pnl} != totals.pnl ${t.pnl}`);
    if (s.pnl_curve.length && isNum(t.max_drawdown) && Math.abs(Math.min(...s.pnl_curve.map((p) => p.drawdown)) - t.max_drawdown) > 0.05) {
      out.push('max_drawdown != min(curve drawdown)');
    }
  }

  for (const [name, cell] of [['by_cell', true], ['by_series', false], ['by_band', false], ['by_ttc', false]]) {
    if (!Array.isArray(s[name])) {
      out.push(`${name} not array`);
      continue;
    }
    s[name].forEach((g, i) => checkGroup(g, `${name}[${i}]`, out, { cell }));
  }
  for (const g of Array.isArray(s.by_band) ? s.by_band : []) if (!isBand(g.key)) out.push(`by_band key ${g.key}`);
  for (const g of Array.isArray(s.by_ttc) ? s.by_ttc : []) if (!isTtc(g.key) && g.key !== 'unknown') out.push(`by_ttc key ${g.key}`);

  if (!Array.isArray(s.fill_quality)) out.push('fill_quality not array');
  else {
    s.fill_quality.forEach((w, i) => {
      const p = `fill_quality[${i}]`;
      if (!keys(w, ['week', 'fills', 'front', 'trade_through', 'front_rate', 'avg_queue_ahead', 'requotes'], p, out, { exact: true })) return;
      if (!/^\d{4}-W\d{2}$/.test(w.week)) out.push(`${p}.week format`);
      for (const k of ['fills', 'front', 'trade_through', 'requotes']) if (!Number.isInteger(w[k]) || w[k] < 0) out.push(`${p}.${k} not int`);
      if (!numOrNull(w.front_rate) || !numOrNull(w.avg_queue_ahead)) out.push(`${p} rate/queue not number|null`);
      if (w.front_rate != null && (w.front_rate < 0 || w.front_rate > 1)) out.push(`${p}.front_rate out of 0..1`);
    });
  }

  if (keys(s.expectations, ['source', 'loaded'], 'expectations', out, { exact: true })) {
    if (typeof s.expectations.source !== 'string' || typeof s.expectations.loaded !== 'boolean') out.push('expectations types');
  }

  if (!Array.isArray(s.calibration)) out.push('calibration not array');
  else {
    // buckets over the configured cells' price span: contiguous and ascending
    s.calibration.forEach((b, i) => {
      if (i && isNum(b?.lo) && isNum(s.calibration[i - 1]?.hi) && Math.abs(b.lo - s.calibration[i - 1].hi) > 1e-9) out.push(`calibration[${i}] not contiguous`);
    });
    s.calibration.forEach((b, i) => {
      const w = `calibration[${i}]`;
      if (!keys(b, CALIB_KEYS, w, out, { exact: true })) return;
      if (typeof b.bucket !== 'string' || !/^0\.\d{2}-0\.\d{2}$/.test(b.bucket)) out.push(`${w}.bucket format`);
      if (!isNum(b.lo) || !isNum(b.hi) || b.lo >= b.hi) out.push(`${w} lo/hi`);
      for (const k of ['n', 'markets', 'wins']) if (!Number.isInteger(b[k]) || b[k] < 0) out.push(`${w}.${k} not int`);
      for (const k of ['win_rate', 'avg_price', 'ci_low', 'ci_high']) if (!numOrNull(b[k])) out.push(`${w}.${k} not number|null`);
      if (b.n === 0 && b.win_rate !== null) out.push(`${w}: empty bucket with win_rate`);
      if (b.wins > b.n || b.markets > b.n) out.push(`${w}: wins/markets > n`);
    });
  }

  const checkFr = (fr, w) => {
    if (!keys(fr, FR_KEYS, w, out)) return;
    for (const k of ['quotes', 'filled', 'partial', 'unfilled']) if (!Number.isInteger(fr[k]) || fr[k] < 0) out.push(`${w}.${k} not int`);
    for (const k of ['fill_rate', 'median_rest_minutes', 'median_minutes_to_fill']) if (!numOrNull(fr[k])) out.push(`${w}.${k} not number|null`);
    if (fr.filled + fr.unfilled !== fr.quotes) out.push(`${w}: filled + unfilled != quotes`);
    if (fr.fill_rate != null && (fr.fill_rate < 0 || fr.fill_rate > 1)) out.push(`${w}.fill_rate out of 0..1`);
  };
  if (keys(s.fill_rate, ['totals', 'by_cell', 'by_series'], 'fill_rate', out, { exact: true })) {
    checkFr(s.fill_rate.totals, 'fill_rate.totals');
    for (const name of ['by_cell', 'by_series']) {
      if (!Array.isArray(s.fill_rate[name])) out.push(`fill_rate.${name} not array`);
      else s.fill_rate[name].forEach((r, i) => {
        checkFr(r, `fill_rate.${name}[${i}]`);
        if (typeof r.key !== 'string') out.push(`fill_rate.${name}[${i}].key not string`);
      });
    }
  }

  if (keys(s.arb, ['opportunities', 'executed', 'pnl', 'daily'], 'arb', out, { exact: true })) {
    if (!Number.isInteger(s.arb.opportunities) || !Number.isInteger(s.arb.executed)) out.push('arb counts not int');
    if (!numOrNull(s.arb.pnl)) out.push('arb.pnl not number|null');
    if (!Array.isArray(s.arb.daily)) out.push('arb.daily not array');
    else {
      s.arb.daily.forEach((d, i) => {
        if (!keys(d, ['date', 'opportunities', 'executed'], `arb.daily[${i}]`, out, { exact: true })) return;
        if (!isDate(d.date) || !Number.isInteger(d.opportunities) || !Number.isInteger(d.executed)) out.push(`arb.daily[${i}] types`);
        if (d.executed > d.opportunities) out.push(`arb.daily[${i}] executed > opportunities`);
      });
    }
  }
  return out;
}

export function checkTrades(b, { limit, offset } = {}) {
  const out = [];
  if (!keys(b, ['total', 'offset', 'limit', 'trades'], 'trades body', out, { exact: true })) return out;
  if (!Number.isInteger(b.total) || b.total < 0) out.push('total');
  if (offset != null && b.offset !== offset) out.push('offset echo');
  if (limit != null && b.limit !== limit) out.push('limit echo');
  if (!Array.isArray(b.trades)) {
    out.push('trades not array');
    return out;
  }
  if (b.trades.length > b.limit) out.push('more rows than limit');
  let prev = Infinity;
  b.trades.forEach((t, i) => {
    const w = `trade[${i}]`;
    if (!keys(t, TRADE_KEYS, w, out, { exact: true })) return;
    if (typeof t.fill_id !== 'string' || typeof t.ticker !== 'string' || typeof t.series !== 'string') out.push(`${w} strings`);
    if (!isIso(t.ts)) out.push(`${w}.ts`);
    if (Date.parse(t.ts) > prev) out.push(`${w} not newest first`);
    prev = Date.parse(t.ts);
    if (!['yes', 'no'].includes(t.side)) out.push(`${w}.side`);
    if (!['maker', 'taker'].includes(t.kind)) out.push(`${w}.kind`);
    if (!isNum(t.price) || t.price < 0.01 || t.price > 0.99) out.push(`${w}.price`);
    if (!Number.isInteger(t.count) || t.count < 1) out.push(`${w}.count`);
    if (!isNum(t.fee) || !isNum(t.cost)) out.push(`${w}.fee/cost`);
    if (!isBand(t.band)) out.push(`${w}.band`);
    if (!numOrNull(t.ttc_hours)) out.push(`${w}.ttc_hours`);
    if (t.ttc_band !== null && !isTtc(t.ttc_band)) out.push(`${w}.ttc_band`);
    if (t.cell !== null && !/^\d\.\d{2}-\d\.\d{2}\|\d+-\d+h\|(yes|no)$/.test(t.cell)) out.push(`${w}.cell ${t.cell}`);
    if (!['settled', 'open'].includes(t.status)) out.push(`${w}.status`);
    if (![null, 'win', 'loss'].includes(t.result)) out.push(`${w}.result`);
    if (![null, 'front', 'trade_through'].includes(t.fill_quality)) out.push(`${w}.fill_quality`);
    if (t.status === 'open') {
      if (t.result !== null || t.pnl !== null || t.settled_utc !== null) out.push(`${w} open but has result/pnl/settled`);
    } else if (!isNum(t.pnl) || !isIso(t.settled_utc) || !['win', 'loss'].includes(t.result)) {
      out.push(`${w} settled but missing pnl/result/settled_utc`);
    }
  });
  return out;
}
