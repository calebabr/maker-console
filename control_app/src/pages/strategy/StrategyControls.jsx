import { ArbControls } from './ArbitrageViews.jsx';
import { MarketsControls } from '../Markets.jsx';

// Controls of one strategy. Every write keeps its confirm and fresh-2FA path (Pause, Resume, Turn on or off,
// Go live with typed LIVE, Save settings).
export default function StrategyControls({ id }) {
  return (
    <div data-testid="strategy-controls" data-strategy={id}>
      {id === 'arb' ? <ArbControls /> : <MarketsControls />}
    </div>
  );
}
