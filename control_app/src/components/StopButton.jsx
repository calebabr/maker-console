import { useState } from 'react';
import { api } from '../services/api.js';
import { useDialogs } from '../context/DialogContext.jsx';
import { useStatus } from '../context/StatusContext.jsx';
import { IconStop } from './Icons.jsx';
import { stopToastTitle } from '../utils/overview.js';

// Fired on window after every Stop request (ok or not) so open pages can reload.
export const STOPPED_EVENT = 'km:stopped';

/**
 * STOP: one tap + one confirm, no 2FA. Stops BOTH strategies (maker + arbitrage).
 * Calls POST /api/bot/stop and reports
 * exactly what the server said (kill switch, cancelled orders, errors).
 * Never assumes success: a failed request is shown as an error with a retry hint.
 */
function arbText(r) {
  if (!r) return '';
  const a = r.arb && typeof r.arb === 'object' ? r.arb : r;
  const kill = a.arb_kill_switch ?? a.kill_switch_arb ?? (r.arb ? a.kill_switch : undefined);
  if (kill === true) return ' Arbitrage stopped.';
  if (kill === false) return ' Arbitrage kill switch NOT confirmed.';
  return '';
}

export default function StopButton({ compact = false }) {
  const { confirm, notify } = useDialogs();
  const { refresh } = useStatus();
  const [busy, setBusy] = useState(false);

  async function onStop() {
    if (busy) return;
    const ok = await confirm({
      title: 'Stop all trading?',
      message: (
        <>
          <p>Stops the maker and the arbitrage scanner: sets both kill switches, cancels resting bot orders and marks the bot stopped.</p>
          <p className="hint">Filled positions stay open until settlement.</p>
        </>
      ),
      confirmLabel: 'Stop now',
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const r = await api.stop();
      const errors = (r && r.errors) || [];
      notify({
        kind: errors.length ? 'info' : 'ok',
        title: stopToastTitle(r) || (r && r.kill_switch ? 'Stopped. Kill switch set.' : 'Stop sent'),
        text: `Bot is ${r && r.desired ? r.desired : 'stopped'}; cancelled ${r && r.cancelled != null ? r.cancelled : '?'} resting order(s).${arbText(r)}`,
        details: errors.length ? errors.map((e) => `Not done: ${typeof e === 'string' ? e : JSON.stringify(e)}`) : undefined,
      });
    } catch (err) {
      notify({
        kind: 'error',
        title: 'Stop did NOT reach the server',
        text: `${err.message} Tap Stop again.`,
      });
    } finally {
      setBusy(false);
      refresh();
      try {
        window.dispatchEvent(new Event(STOPPED_EVENT));
      } catch {
        /* non-browser test env */
      }
    }
  }

  return (
    <button
      type="button"
      className={`btn danger stop-btn ${compact ? 'stop-compact' : 'stop-big'}`}
      onClick={onStop}
      disabled={busy}
      aria-label="Stop the bot (maker and arbitrage)"
    >
      <IconStop />
      <span>{busy ? 'Stopping…' : compact ? 'Stop' : 'Stop bot'}</span>
    </button>
  );
}
