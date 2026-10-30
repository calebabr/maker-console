import { useRef } from 'react';
import { RANGE_OPTIONS, validRange } from '../utils/range.js';
import '../styles/nav.css';

/**
 * The shared period control for Home, the Strategies hub and Performance: a segmented radio group
 * (7 days | 30 days | This period). Controlled: `value` is a range id from utils/range.js, `onChange(id)` fires on
 * pick. Pair it with rangeParams(id) for the API query. Each segment is >= 40 px; arrows move and select
 * (radio-group behaviour), Home / End jump; only the selected segment is in the Tab order.
 *   <RangeSelect value={range} onChange={setRange} label="Range" />
 */
export default function RangeSelect({ value, onChange, options = RANGE_OPTIONS, label = 'Range', disabled = false, className = '' }) {
  const ref = useRef(null);
  const current = options.some((o) => o.id === value) ? value : validRange(value);
  const idx = Math.max(0, options.findIndex((o) => o.id === current));

  const move = (e) => {
    let next = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (idx + 1) % options.length;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (idx - 1 + options.length) % options.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = options.length - 1;
    if (next == null) return;
    e.preventDefault();
    onChange(options[next].id);
    const btns = ref.current ? ref.current.querySelectorAll('[role="radio"]') : [];
    if (btns[next]) btns[next].focus();
  };

  return (
    <div className={`range-select ${className}`.trim()} role="radiogroup" aria-label={label} ref={ref} onKeyDown={move}>
      {options.map((o, i) => (
        <button
          key={o.id}
          type="button"
          role="radio"
          aria-checked={i === idx}
          tabIndex={i === idx ? 0 : -1}
          className="range-opt"
          disabled={disabled}
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
