import { useEffect, useState } from 'react';
import '../styles/nav.css';

/**
 * Loading placeholder at the final height (rows 56 px, figure 32 px), never a spinner-only screen.
 * It reserves its space at once but only becomes visible after `delay` ms (default 250), so fast loads do not
 * flash. The "Waking up the server" banner (WakingBanner) still takes over after 5 s.
 *   <Skeleton rows={3} />            three 56 px rows
 *   <Skeleton figure rows={2} />     a 32 px headline bar, then two rows
 */
export default function Skeleton({ rows = 3, figure = false, delay = 250, label = 'Loading', className = '' }) {
  const [shown, setShown] = useState(delay <= 0);
  useEffect(() => {
    if (delay <= 0) return undefined;
    const id = setTimeout(() => setShown(true), delay);
    return () => clearTimeout(id);
  }, [delay]);
  return (
    <div
      className={`skeleton ${className}`.trim()}
      data-pending={shown ? 'false' : 'true'}
      data-testid="skeleton"
      role="status"
      aria-busy="true"
    >
      <span className="sr-only">{label}</span>
      {figure && <div className="skeleton-figure" />}
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton-row" />
      ))}
    </div>
  );
}
