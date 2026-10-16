import { dateTime } from '../utils/format.js';

// Error box for a failed load / action. Shows the server's `detail` list if any.
export function ErrorNotice({ error, onRetry }) {
  if (!error) return null;
  const details = error.details && error.details.length > 1 ? error.details : null;
  return (
    <div className="notice notice-error" role="alert">
      <div>{error.message || String(error)}</div>
      {details && (
        <ul className="detail-list">
          {details.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      )}
      {onRetry && (
        <button type="button" className="btn small" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

// "Showing saved data from …" when we fell back to the sessionStorage cache.
export function StaleNotice({ staleAt }) {
  if (!staleAt) return null;
  return <div className="notice notice-stale">Offline copy from {dateTime(staleAt)}. It may be out of date.</div>;
}

export function Loading({ text = 'Loading…' }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden="true" /> {text}
    </div>
  );
}
