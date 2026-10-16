import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import ConfirmDialog from '../components/ConfirmDialog.jsx';
import TwoFactorPrompt from '../components/TwoFactorPrompt.jsx';
import Toasts from '../components/Toasts.jsx';

/**
 * App-wide modals and toasts.
 *
 *   const { confirm, twoFactor, notify } = useDialogs();
 *
 *   await confirm({ title, message, confirmLabel, danger, requireText })  -> boolean
 *   await twoFactor({ title, message, confirmLabel, danger, action: (code) => api.x(code) })
 *        -> action result, or null if cancelled. A rejected/used code keeps the
 *           modal open for another try; any other error closes it and is re-thrown.
 *   notify({ kind: 'ok' | 'error' | 'info', title, text, details })
 */

const DialogContext = createContext(null);

export function DialogProvider({ children }) {
  const [confirmState, setConfirmState] = useState(null);
  const [tfaState, setTfaState] = useState(null);
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(1);

  const confirm = useCallback(
    (opts) =>
      new Promise((resolve) => {
        setConfirmState({
          ...opts,
          resolve: (v) => {
            setConfirmState(null);
            resolve(v);
          },
        });
      }),
    [],
  );

  const twoFactor = useCallback(
    (opts) =>
      new Promise((resolve, reject) => {
        setTfaState({
          ...opts,
          done: (result) => {
            setTfaState(null);
            resolve(result);
          },
          fail: (err) => {
            setTfaState(null);
            reject(err);
          },
        });
      }),
    [],
  );

  const dismiss = useCallback((id) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  const notify = useCallback(
    (toast) => {
      const id = nextId.current++;
      setToasts((t) => [...t.slice(-3), { id, kind: 'info', ...toast }]);
      const ms = toast.kind === 'error' ? 12000 : 6000;
      setTimeout(() => dismiss(id), ms);
      return id;
    },
    [dismiss],
  );

  const value = useMemo(() => ({ confirm, twoFactor, notify }), [confirm, twoFactor, notify]);

  return (
    <DialogContext.Provider value={value}>
      {children}
      {confirmState && <ConfirmDialog {...confirmState} />}
      {tfaState && <TwoFactorPrompt {...tfaState} />}
      <Toasts toasts={toasts} onDismiss={dismiss} />
    </DialogContext.Provider>
  );
}

export function useDialogs() {
  const ctx = useContext(DialogContext);
  if (!ctx) throw new Error('useDialogs must be used inside <DialogProvider>');
  return ctx;
}
