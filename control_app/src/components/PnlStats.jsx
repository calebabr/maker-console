import { money } from '../utils/format.js';
import HelpIcon from './HelpIcon.jsx';

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

function cls(v) {
  if (!isNum(v)) return 'muted';
  return v < 0 ? 'neg' : v > 0 ? 'pos' : '';
}

/**
 * 7- and 30-day P&L from `pnl: {d7, d30} | null` (GET /api/status, GET /api/arb).
 * Null (or a missing / non-numeric value) renders as a dash with a short reason,
 * never as $0.00.
 */
export default function PnlStats({ pnl, big = true, emptyText = 'No settled fills yet.' }) {
  const d7 = pnl && pnl.d7;
  const d30 = pnl && pnl.d30;
  const any = isNum(d7) || isNum(d30);
  return (
    <>
      <dl className="stats" data-testid="pnl">
        <div>
          <dt>7 days<HelpIcon term="pnl-window" /></dt>
          <dd className={`${big ? 'big ' : ''}${cls(d7)}`}>{money(d7, { sign: true })}</dd>
        </div>
        <div>
          <dt>30 days</dt>
          <dd className={`${big ? 'big ' : ''}${cls(d30)}`}>{money(d30, { sign: true })}</dd>
        </div>
      </dl>
      {!any && <p className="hint">{emptyText}</p>}
    </>
  );
}
