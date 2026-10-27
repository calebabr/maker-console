import { formatMoney } from '../utils/format.js';
import PnlScope from './PnlScope.jsx';

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const pnlClass = (v) => (isNum(v) ? (v < 0 ? 'neg' : v > 0 ? 'pos' : '') : 'muted');

/**
 * Combined 30-day realized P&L (maker + arbitrage) with a compact Maker / Arbitrage split, from
 * GET /api/status `pnl_total`, `pnl` (maker) and `pnl_arb`. Renders nothing until some settled figure exists, and
 * never turns a missing figure into $0.00.
 */
export default function PnlCombined({ status }) {
  const total = status && status.pnl_total && status.pnl_total.d30;
  if (!isNum(total)) return null;
  const maker = status.pnl && status.pnl.d30;
  const arb = status.pnl_arb && status.pnl_arb.d30;
  return (
    <div className="ov-combined" data-testid="pnl-combined">
      <p className="ov-combined-line">
        <span className="ov-combined-label">Maker + Arbitrage (30 days)</span>
        <b className={pnlClass(total)} data-testid="pnl-combined-total">{formatMoney(total, { sign: true })}</b>
      </p>
      <p className="ov-combined-split" data-testid="pnl-split">
        Maker <span className={pnlClass(maker)}>{formatMoney(maker, { sign: true })}</span>
        {' / '}
        Arbitrage <span className={pnlClass(arb)}>{formatMoney(arb, { sign: true })}</span>
      </p>
      <PnlScope id="makerArb" />
    </div>
  );
}
