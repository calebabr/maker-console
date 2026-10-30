import { dateTime, formatTime } from '../utils/format.js';

// Error box for a failed load / action: plain words, the server's `detail` list if any, and (with `staleAt`) what is
// still shown ("Showing data from 5:19 PM"). Retry is a full 44 px button.
export function ErrorNotice({ error, onRetry, staleAt }) {
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
      {staleAt && <div className="hint">Showing data from {formatTime(staleAt)}.</div>}
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
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
