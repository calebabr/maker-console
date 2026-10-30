import { Link } from 'react-router-dom';
import '../styles/nav.css';

/**
 * Empty state: one sentence for what ("No settled fills in this period"), one for why ("Fills settle after
 * their market closes"), and an optional next step. Never a bare "—" for a whole card. Sentence case.
 *   <Empty what="No settled fills in this period" why="Fills settle after their market closes."
 *          next={{ label: 'Show 30 days', onClick: ... }} />      // or next={{ label, to: '/activity' }}
 */
export default function Empty({ what, why, next, compact = false, className = '' }) {
  return (
    <div className={`empty ${compact ? 'is-compact' : ''} ${className}`.trim()} role="status" data-testid="empty">
      <p className="empty-what">{what}</p>
      {why && <p className="empty-why">{why}</p>}
      {next && next.to && (
        <Link to={next.to} className="empty-next">
          {next.label}
        </Link>
      )}
      {next && !next.to && next.onClick && (
        <button type="button" className="btn empty-next" onClick={next.onClick}>
          {next.label}
        </button>
      )}
    </div>
  );
}
