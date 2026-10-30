import HelpIcon from '../HelpIcon.jsx';
import { isNum, rate } from '../../utils/analytics.js';
import { num } from '../../utils/format.js';

function interval(w) {
  return isNum(w.ci_low) && isNum(w.ci_high) ? `95% range ${rate(w.ci_low)} to ${rate(w.ci_high)}` : null;
}

function Tile({ label, unitWord, w, testId }) {
  const empty = !w || !w.n;
  return (
    <div data-testid={testId}>
      <dt>
        {label}
        <HelpIcon term="win-rate-by-strategy" />
      </dt>
      <dd>{empty ? 'No settled yet' : rate(w.win_rate)}</dd>
      <dd className="hint">
        {empty ? `Counted per ${unitWord}.` : `${num(w.wins)} of ${num(w.n)} ${unitWord}${w.n === 1 ? '' : 's'} won. ${interval(w) || ''}`}
      </dd>
    </div>
  );
}

/** Win rate per strategy: maker per fill, arbitrage per set. Never one pooled number. */
export default function WinRateByStrategy({ wr }) {
  if (!wr) return null;
  return (
    <dl className="stats an-totals" data-testid="win-rate-by-strategy">
      <Tile label="Maker Win Rate (Per Fill)" unitWord="fill" w={wr.maker} testId="wr-maker" />
      <Tile label="Arbitrage Win Rate (Per Set)" unitWord="set" w={wr.arb} testId="wr-arb" />
    </dl>
  );
}
