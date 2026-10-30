import { ArbResults } from './ArbitrageViews.jsx';
import { MarketsResults } from '../Markets.jsx';

// Results of one strategy: Arbitrage (profit and loss, executed sets, opportunities) or Maker (P&L, by series).
export default function StrategyResults({ id }) {
  return (
    <div data-testid="strategy-results" data-strategy={id}>
      {id === 'arb' ? <ArbResults /> : <MarketsResults />}
    </div>
  );
}
