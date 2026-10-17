import { useEffect, useRef } from 'react';

// Centered dialog (mobile-first); tracks the visual viewport so the iOS keyboard never covers it. Escape / backdrop tap call onClose
// (pass onClose={undefined} to make it non-dismissable, e.g. while busy).
export default function Modal({ title, onClose, children, labelledBy = 'modal-title' }) {
  const ref = useRef(null);
  const backdropRef = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && closeRef.current) closeRef.current();
    };
    const trap = (e) => {
      if (e.key !== 'Tab' || !ref.current) return;
      const items = ref.current.querySelectorAll('input:not(:disabled), button:not(:disabled), [href], select, textarea');
      if (!items.length) return;
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('keydown', trap);
    const first = ref.current && ref.current.querySelector('input, .btn.primary, .btn.danger');
    if (first) first.focus({ preventScroll: true });
    // Size the backdrop to the visible area (above the on-screen keyboard).
    const vv = window.visualViewport;
    const root = backdropRef.current;
    const sync = () => {
      if (!vv || !root) return;
      root.style.setProperty('--vv-top', `${vv.offsetTop}px`);
      root.style.setProperty('--vv-h', `${vv.height}px`);
    };
    sync();
    if (vv) {
      vv.addEventListener('resize', sync);
      vv.addEventListener('scroll', sync);
    }
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('keydown', trap);
      if (vv) {
        vv.removeEventListener('resize', sync);
        vv.removeEventListener('scroll', sync);
      }
    };
  }, []);

  return (
    <div
      className="modal-backdrop"
      ref={backdropRef}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && closeRef.current) closeRef.current();
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={labelledBy} ref={ref}>
        {title && (
          <h2 id={labelledBy} className="modal-title">
            {title}
          </h2>
        )}
        {children}
      </div>
    </div>
  );
}
