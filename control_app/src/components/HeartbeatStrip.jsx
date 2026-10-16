import { formatTimeParts } from '../utils/format.js';

/**
 * Last passes as small bars, oldest left: green = ok, amber = late or had errors.
 * `passes` is status.recent_passes: [{ts, ok, late, errors}] (newest last).
 */
export default function HeartbeatStrip({ passes }) {
  const list = Array.isArray(passes) ? passes.slice(-12) : [];
  if (!list.length) return <p className="hint hb-empty">No runs yet.</p>;
  const bad = list.filter((p) => !p.ok || p.late || p.errors > 0).length;
  return (
    <div
      className="hb-strip"
      role="img"
      aria-label={`Last ${list.length} runs: ${list.length - bad} on time, ${bad} late or with errors`}
      data-testid="heartbeat-strip"
    >
      {list.map((p, i) => {
        const flag = !p.ok || p.late || p.errors > 0;
        const what = !p.ok || p.errors > 0 ? `${p.errors || 1} error(s)` : p.late ? 'late' : 'ok';
        return (
          <span
            key={`${p.ts}-${i}`}
            className={`hb-bar ${flag ? 'hb-warn' : 'hb-ok'}`}
            title={`${formatTimeParts(p.ts).text} · ${what}`}
          />
        );
      })}
    </div>
  );
}
