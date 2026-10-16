// Derive the Overview badge from GET /api/status.
//   live running -> 'live'   (green)
//   demo running -> 'demo'   (yellow)
//   stopped      -> 'stopped'(grey)
//   error, or running with heartbeat older than 30 min -> 'error' (red)

export const HEARTBEAT_STALE_S = 30 * 60;

export function badgeKind(status) {
  if (!status) return { kind: 'unknown', reason: 'No status yet' };
  const running = status.desired === 'running' && !status.kill_switch;
  if (status.last_error) return { kind: 'error', reason: String(status.last_error) };
  if (running) {
    const age = status.heartbeat_age_s;
    if (typeof age === 'number' && age > HEARTBEAT_STALE_S) {
      return { kind: 'error', reason: `No heartbeat for ${Math.round(age / 60)} min` };
    }
    return { kind: status.mode === 'live' ? 'live' : 'demo', reason: null };
  }
  if (status.desired === 'running' && status.kill_switch) {
    return { kind: 'stopped', reason: 'Kill switch is set' };
  }
  return { kind: 'stopped', reason: null };
}

// $ at risk vs the hard cap: max_open_frac * (cash + exposure).
export function riskLimit(status) {
  const a = status && status.account;
  const frac = status && status.limits && status.limits.max_open_frac;
  if (!a || typeof frac !== 'number') return null;
  const total = (a.cash || 0) + (a.exposure || 0);
  return frac * total;
}
