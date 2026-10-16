import { useEffect, useRef } from 'react';

// Bottom-sheet style modal (mobile-first). Escape / backdrop tap call onClose
// (pass onClose={undefined} to make it non-dismissable, e.g. while busy).
export default function Modal({ title, onClose, children, labelledBy = 'modal-title' }) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && closeRef.current) closeRef.current();
    };
    document.addEventListener('keydown', onKey);
    const first = ref.current && ref.current.querySelector('input, .btn.primary, .btn.danger');
    if (first) first.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div
      className="modal-backdrop"
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
