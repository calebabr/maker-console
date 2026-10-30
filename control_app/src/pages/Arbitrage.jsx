// The Arbitrage page now lives at /strategies/arb (Overview | Results | Controls): see pages/strategy/.
// This module keeps the names other files import (the legacy /arb route redirects to the strategy page).
export { POLL_MS, ARB_SERIES, ArbStateText, gateReason } from './strategy/arbShared.jsx';
export { ArbOverview, ArbResults, ArbControls } from './strategy/ArbitrageViews.jsx';
