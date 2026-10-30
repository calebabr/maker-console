import { useEffect, useState } from 'react';
import Card from './Card.jsx';
import HelpIcon from './HelpIcon.jsx';
import { IconLock } from './Icons.jsx';
import { useDialogs } from '../context/DialogContext.jsx';
import { api } from '../services/api.js';
import { formatMoney } from '../utils/format.js';

/**
 * Shadow capital: the size of the simulated account. It is a cloud setting, so it is written with
 * PUT /api/cloud and a fresh 2FA code (the same path as the Server page), not through the bot config form.
 * `info` is GET /api/config -> shadow_capital {value, default, min, max, unit, help, editable, needs_fresh_2fa}.
 */
export default function ShadowCapitalCard({ info, onSaved }) {
  const { twoFactor, notify } = useDialogs();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (info && info.value != null) setText(String(info.value));
  }, [info]);
  if (!info) return null;

  const n = Number(text);
  const min = typeof info.min === 'number' ? info.min : undefined;
  const max = typeof info.max === 'number' ? info.max : undefined;
  const bad =
    text.trim() === '' || !Number.isFinite(n) || (min != null && n < min) || (max != null && n > max)
      ? `Enter an amount${min != null && max != null ? ` from ${formatMoney(min)} to ${formatMoney(max)}` : ''}.`
      : null;
  const dirty = Number.isFinite(n) && n !== info.value;
  const editable = info.editable !== false;

  async function save(e) {
    e.preventDefault();
    if (bad || !dirty || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await twoFactor({
        title: 'Change shadow capital?',
        message: (
          <p>
            Shadow capital {formatMoney(info.value)} to <strong>{formatMoney(n)}</strong>. This only changes the simulation. No orders are placed.
          </p>
        ),
        confirmLabel: 'Save',
        action: (code) => api.putCloud({ shadow_capital: n }, code),
      });
      if (r) {
        notify({ kind: 'ok', title: 'Shadow capital saved' });
        if (onSaved) onSaved(r);
      }
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={<>Shadow Capital<HelpIcon term="shadow-capital" /></>} subtitle="The simulated account size for the shadow estimate.">
      <form onSubmit={save}>
        <label className="field">
          Shadow capital ({info.unit || 'USD'})
          <input
            type="number"
            inputMode="decimal"
            min={min}
            max={max}
            step="any"
            value={text}
            disabled={!editable || busy}
            onChange={(ev) => setText(ev.target.value)}
            aria-invalid={!!(dirty && bad)}
          />
          <span className="field-range">
            Range {min ?? '…'} – {max ?? '…'}
            {typeof info.default === 'number' ? `, default ${info.default}` : ''}
          </span>
          {info.help && <span className="field-help">{info.help}</span>}
          {dirty && bad && <span className="error-text">{bad}</span>}
        </label>
        {error && (
          <div className="notice notice-error" role="alert">
            <strong>Not saved.</strong>{' '}
            {error.details && error.details.length ? error.details.join(' ') : error.message}
          </div>
        )}
        <button type="submit" className="btn block" disabled={!editable || !dirty || !!bad || busy}>
          {busy ? 'Saving…' : <><IconLock /> Save Shadow Capital</>}
        </button>
      </form>
    </Card>
  );
}
