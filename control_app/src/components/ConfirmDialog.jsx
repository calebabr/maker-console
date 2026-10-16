import { useState } from 'react';
import Modal from './Modal.jsx';

/**
 * Yes/no confirmation. With `requireText` (e.g. "LIVE") the confirm button only
 * enables once the user has typed that exact text.
 */
export default function ConfirmDialog({
  title = 'Are you sure?',
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
  requireText,
  resolve,
}) {
  const [typed, setTyped] = useState('');
  const ok = !requireText || typed === requireText;
  return (
    <Modal title={title} onClose={() => resolve(false)} labelledBy="confirm-title">
      {message && <div className="modal-body">{message}</div>}
      {requireText && (
        <label className="field">
          <span className="field-label">
            Type <strong>{requireText}</strong> to confirm
          </span>
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoCapitalize="characters"
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            aria-label={`Type ${requireText} to confirm`}
          />
        </label>
      )}
      <div className="modal-actions">
        <button type="button" className="btn" onClick={() => resolve(false)}>
          {cancelLabel}
        </button>
        <button
          type="button"
          className={`btn ${danger ? 'danger' : 'primary'}`}
          disabled={!ok}
          onClick={() => resolve(true)}
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
