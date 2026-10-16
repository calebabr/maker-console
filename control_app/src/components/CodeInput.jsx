import { useRef, useState } from 'react';

/**
 * Six-digit code entry drawn as separate boxes. Behind the boxes is ONE real <input>
 * (autocomplete="one-time-code", inputMode numeric), so iOS / Android code suggestions, paste, the
 * on-screen keyboard and screen readers all work like a normal field; the boxes just display its value.
 * Non-digits are dropped; pasting "123 456" or "123456" fills all six.
 */
export default function CodeInput({ value, onChange, disabled = false, autoFocus = false, length = 6, label = '6-digit code' }) {
  const ref = useRef(null);
  const [focused, setFocused] = useState(false);
  const digits = String(value || '');

  const clean = (s) => String(s || '').replace(/\D/g, '').slice(0, length);
  const toEnd = () => {
    const el = ref.current;
    if (el) {
      try {
        el.setSelectionRange(el.value.length, el.value.length);
      } catch {
        /* some input types refuse selection */
      }
    }
  };

  return (
    <div className={`otp ${focused ? 'is-focused' : ''} ${disabled ? 'is-disabled' : ''}`}>
      <div className="otp-boxes" aria-hidden="true">
        {Array.from({ length }, (_, i) => (
          <span key={i} className={`otp-box ${digits[i] ? 'is-filled' : ''} ${focused && i === Math.min(digits.length, length - 1) ? 'is-active' : ''}`}>
            {digits[i] || ''}
          </span>
        ))}
      </div>
      <input
        ref={ref}
        className="otp-input"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="\d{6}"
        maxLength={length}
        value={digits}
        disabled={disabled}
        autoFocus={autoFocus}
        aria-label={label}
        onChange={(e) => onChange(clean(e.target.value))}
        onPaste={(e) => {
          const text = (e.clipboardData && e.clipboardData.getData('text')) || '';
          e.preventDefault();
          onChange(clean(text));
        }}
        onFocus={() => {
          setFocused(true);
          toEnd();
        }}
        onBlur={() => setFocused(false)}
        onClick={toEnd}
        onSelect={toEnd}
      />
    </div>
  );
}
