import { ArbOverview } from './ArbitrageViews.jsx';
import { MakerOverview } from '../Markets.jsx';

// Overview of one strategy: Arbitrage (state, Stop line, scanner, top opportunities) or Maker (state, passes, exposure).
export default function StrategyOverview({ id }) {
  return (
    <div data-testid="strategy-overview" data-strategy={id}>
      {id === 'arb' ? <ArbOverview /> : <MakerOverview />}
    </div>
  );
}
