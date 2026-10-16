import { useCallback, useEffect, useRef, useState } from 'react';
import HelpIcon from '../HelpIcon.jsx';
import Chip from '../Chip.jsx';
import { ErrorNotice, Loading } from '../Notices.jsx';
import { saveBlob } from '../../utils/download.js';
import { api } from '../../services/api.js';
import { dateTime, money, num } from '../../utils/format.js';
import { bandLabel, cents, parseCell, seriesName, signCls, ttcLabel } from '../../utils/analytics.js';

export const PAGE = 50;
export const DEBOUNCE_MS = 300;
export const CSV_MAX = 5000;
export const EMPTY_FILTERS = { series: '', side: '', kind: '', status: '', band: '', cell: '', result: '' };

const FIXED = {
  side: [['yes', 'YES'], ['no', 'NO']],
  kind: [['maker', 'Maker'], ['taker', 'Taker']],
  status: [['settled', 'Settled'], ['open', 'Open']],
  result: [['win', 'Won'], ['loss', 'Lost']],
};

/** Band filter options: the summary's band keys (they follow the installed cells) plus "other". */
export function bandChoices(bandOptions = []) {
  return [...new Set([...bandOptions.filter(Boolean), 'other'])].map((b) => [b, bandLabel(b)]);
}

function Select({ name, label, value, options, onChange, wide }) {
  return (
    <label className={wide ? 'wide' : ''}>
      {label}
      <select name={name} value={value} onChange={(e) => onChange(name, e.target.value)}>
        <option value="">All</option>
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

function cellText(id) {
  const c = parseCell(id);
  return `${c.price}, ${c.ttc}, ${c.side}`;
}

function TradeRow({ t }) {
  const settled = t.status === 'settled';
  const pill = !settled
    ? { cls: 'pill-muted', text: 'Open' }
    : t.result === 'win'
      ? { cls: 'pill-ok', text: 'Won' }
      : { cls: 'pill-danger', text: 'Lost' };
  return (
    <li className="trade" data-testid="trade-row">
      <div className="trade-top">
        <span className="left">
          <span className={`pill ${pill.cls}`}>{pill.text}</span>
          {t.source === 'you' && <Chip tone="accent">You</Chip>}
          <span className="muted small">{dateTime(t.ts)}</span>
        </span>
        <span className={`trade-pnl ${settled ? signCls(t.pnl) : 'muted'}`}>{settled ? money(t.pnl, { sign: true }) : '—'}</span>
      </div>
      <div className="mono row-ticker">{t.ticker}</div>
      <div className="trade-meta">
        <span>
          <b>{String(t.side || '').toUpperCase()}</b> @ {cents(t.price)} × {num(t.count)}
        </span>
        <span>{money(t.cost)}</span>
        <span>{t.kind === 'taker' ? 'Taker' : 'Maker'}</span>
        {t.ttc_band && <span>{ttcLabel(t.ttc_band)} to Close</span>}
        {t.fee > 0 && <span>Fee {money(t.fee)}</span>}
        {t.fill_quality === 'front' && <span>Front of Line</span>}
        {t.fill_quality === 'trade_through' && <span>Traded Through</span>}
        {t.cell && <span>Cell {cellText(t.cell)}</span>}
      </div>
    </li>
  );
}

/**
 * Paged trade list (GET /api/analytics/trades) with server-side filters.
 * `enabled` false = wait (the page holds it until the summary for the window is done).
 */
export default function TradeHistory({ days, env, mode, source, enabled = true, seriesOptions = [], cellOptions = [], bandOptions = [] }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  // Filters apply to the request DEBOUNCE_MS after the last change (S-28).
  const [applied, setApplied] = useState(EMPTY_FILTERS);
  const [state, setState] = useState({ trades: [], total: 0, loading: true, error: null, more: false });
  const ctrl = useRef(null);

  useEffect(() => {
    if (filters === applied) return undefined;
    const t = setTimeout(() => setApplied(filters), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [filters, applied]);

  const load = useCallback(
    async (offset) => {
      // A new page, filter or window supersedes (and aborts) the request in flight.
      if (ctrl.current) ctrl.current.abort();
      const mine = typeof AbortController !== 'undefined' ? new AbortController() : null;
      ctrl.current = mine;
      setState((s) => ({ ...s, trades: offset ? s.trades : [], loading: !offset, more: !!offset, error: null }));
      try {
        const r = await api.analyticsTrades(
          { days, env, source, ...applied, limit: PAGE, offset },
          { signal: mine ? mine.signal : undefined },
        );
        if (ctrl.current !== mine) return;
        const page = (r && Array.isArray(r.trades) ? r.trades : []).filter(Boolean);
        setState((s) => ({
          trades: offset ? [...s.trades, ...page] : page,
          total: r && typeof r.total === 'number' ? r.total : page.length,
          loading: false,
          more: false,
          error: null,
        }));
      } catch (error) {
        if ((error && error.aborted) || ctrl.current !== mine) return;
        setState((s) => ({ ...s, loading: false, more: false, error }));
      }
    },
    [days, env, source, applied],
  );

  useEffect(() => {
    if (!enabled) {
      // Waiting for the summary of this window: cancel anything older and show loading.
      if (ctrl.current) ctrl.current.abort();
      ctrl.current = null;
      setState((s) => ({ ...s, trades: [], loading: true, more: false, error: null }));
      return;
    }
    load(0);
  }, [load, enabled]);

  useEffect(
    () => () => {
      if (ctrl.current) ctrl.current.abort();
      ctrl.current = null;
    },
    [],
  );

  // CSV export of every matching trade (current filters), fetched with auth, saved as a file.
  const [csv, setCsv] = useState({ busy: false, error: null, note: '' });
  const csvCtrl = useRef(null);
  useEffect(() => () => csvCtrl.current && csvCtrl.current.abort(), []);
  async function downloadCsv() {
    if (csv.busy) return;
    const mine = typeof AbortController !== 'undefined' ? new AbortController() : null;
    csvCtrl.current = mine;
    setCsv({ busy: true, error: null, note: '' });
    try {
      const r = await api.analyticsTradesCsv({ days, env, source, ...filters }, { signal: mine ? mine.signal : undefined, mode });
      saveBlob(r.blob, r.filename);
      setCsv({
        busy: false,
        error: null,
        note: r.truncated
          ? `Saved ${r.filename}. It holds the first ${num(CSV_MAX)} trades only; narrow the window or filters for the rest.`
          : `Saved ${r.filename}.`,
      });
    } catch (error) {
      if (error && error.aborted) return;
      setCsv({ busy: false, error, note: '' });
    }
  }

  const setOne = (k, v) => setFilters((f) => ({ ...f, [k]: v }));
  const active = Object.values(filters).filter(Boolean).length;
  const { trades, total, loading, more, error } = state;

  return (
    <>
      <details className="filters">
        <summary>Filters{active ? ` (${active} on)` : ''}</summary>
        <div className="filter-grid">
          <Select name="series" label="Series" value={filters.series} onChange={setOne}
            options={seriesOptions.map((s) => [s, seriesName(s)])} />
          <Select name="side" label="Side" value={filters.side} options={FIXED.side} onChange={setOne} />
          <Select name="kind" label="Maker or Taker" value={filters.kind} options={FIXED.kind} onChange={setOne} />
          <Select name="status" label="Status" value={filters.status} options={FIXED.status} onChange={setOne} />
          <Select name="band" label="Price Band" value={filters.band} options={bandChoices(bandOptions)} onChange={setOne} />
          <Select name="result" label="Result" value={filters.result} options={FIXED.result} onChange={setOne} />
          <Select name="cell" label="Cell" value={filters.cell} wide onChange={setOne}
            options={cellOptions.map((c) => [c, cellText(c)])} />
          {active > 0 && (
            <button type="button" className="btn small wide" onClick={() => setFilters(EMPTY_FILTERS)}>
              Clear filters
            </button>
          )}
        </div>
      </details>

      <div className="csv-row">
        <button type="button" className="btn small" onClick={downloadCsv} disabled={csv.busy}>
          {csv.busy ? 'Preparing CSV…' : 'Download CSV'}
        </button>
        <HelpIcon term="csv-export" />
        <span className="muted small">All trades matching the filters, up to {num(CSV_MAX)}.</span>
      </div>
      {csv.note && (
        <p className="hint" role="status">
          {csv.note}
        </p>
      )}
      <ErrorNotice error={csv.error} />

      <ErrorNotice error={error} onRetry={() => load(trades.length)} />
      {loading && <Loading text="Loading trades…" />}
      {!loading && !error && !trades.length && (
        <p className="muted">{active ? 'No trades match these filters.' : 'No trades in this window yet.'}</p>
      )}
      <ul className="rows" aria-label="Trades">
        {trades.map((t, i) => (
          <TradeRow key={t.fill_id || `${t.ts}-${i}`} t={t} />
        ))}
      </ul>
      {trades.length > 0 && (
        <div className="trades-foot">
          <span className="muted small">
            Showing {num(trades.length)} of {num(total)}
          </span>
          {trades.length < total && (
            <button type="button" className="btn small" onClick={() => load(trades.length)} disabled={more}>
              {more ? 'Loading…' : 'Load more'}
            </button>
          )}
        </div>
      )}
    </>
  );
}
