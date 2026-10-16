import { kalshiMarketUrl } from '../utils/kalshi.js';

/** "Open in Kalshi" link to the public market page; renders nothing for a missing or odd ticker. */
export default function KalshiLink({ ticker, className = '' }) {
  const href = kalshiMarketUrl(ticker);
  if (!href) return null;
  return (
    <a className={`kalshi-link ${className}`.trim()} href={href} target="_blank" rel="noopener noreferrer">
      Open in Kalshi<span className="sr-only"> ({ticker})</span>
    </a>
  );
}
