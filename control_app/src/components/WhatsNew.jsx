import { useEffect, useState } from 'react';
import { api } from '../services/api.js';
import { IconClose } from './Icons.jsx';

export const SEEN_KEY = 'keel.lastSeenVersion';
const FEW = 3;

// "2026-10-15" is a calendar day, not an instant: show it as that day, never shifted by the time zone.
function dayText(v) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(v || ''));
  if (!m) return '';
  return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function readSeen() {
  try {
    return localStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
}
function writeSeen(v) {
  try {
    localStorage.setItem(SEEN_KEY, v);
  } catch {
    /* blocked storage: the card just comes back next visit */
  }
}

/** -1 / 0 / 1 comparing dotted versions numerically ("1.10.0" > "1.9.2"); non-numeric parts count as 0. */
export function compareVersions(a, b) {
  const pa = String(a).split(/[.-]/).map((n) => parseInt(n, 10) || 0);
  const pb = String(b).split(/[.-]/).map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] || 0) - (pb[i] || 0);
    if (d) return d < 0 ? -1 : 1;
  }
  return 0;
}

/**
 * Entries this device has not seen, newest first. A device with no saved version (first visit, or the first
 * time after this feature shipped) gets just the newest entry, so the update note still shows once.
 */
export function unseenEntries(changelog, lastSeen) {
  const list = (Array.isArray(changelog) ? changelog : [])
    .filter((e) => e && e.version && Array.isArray(e.items))
    .sort((x, y) => compareVersions(y.version, x.version));
  if (!list.length) return [];
  if (!lastSeen) return list.slice(0, 1);
  return list.filter((e) => compareVersions(e.version, lastSeen) > 0);
}

/**
 * Dismissible "What's New" card from GET /api/changelog (built from the CHANGELOG at release time).
 * Shows what changed since the last version this device saw; "Got It" saves the newest version in
 * localStorage (keel.lastSeenVersion). Silent when the feed is empty or unavailable.
 */
export default function WhatsNew() {
  const [entries, setEntries] = useState([]);
  const [all, setAll] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .getChangelog()
      .then((data) => {
        if (live) setEntries(unseenEntries(data, readSeen()));
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  if (!entries.length) return null;
  const newest = entries[0];
  const items = entries.flatMap((e) => e.items);
  const shown = all ? items : items.slice(0, FEW);
  const date = dayText(newest.date);

  return (
    <section className="whatsnew" aria-label="What's New">
      <div className="whatsnew-head">
        <div>
          <div className="whatsnew-title">
            What&apos;s New <span className="whatsnew-version">v{newest.version}</span>
          </div>
          {date && <div className="whatsnew-date muted small">{date}</div>}
        </div>
        <button
          type="button"
          className="icon-btn"
          aria-label="Dismiss What's New"
          onClick={() => {
            writeSeen(newest.version);
            setEntries([]);
          }}
        >
          <IconClose />
        </button>
      </div>
      <ul className="whatsnew-list">
        {shown.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
      <div className="whatsnew-actions">
        {items.length > FEW && (
          <button type="button" className="btn small" onClick={() => setAll((v) => !v)} aria-expanded={all}>
            {all ? 'Show Less' : `Show All ${items.length}`}
          </button>
        )}
        <button
          type="button"
          className="btn small primary"
          onClick={() => {
            writeSeen(newest.version);
            setEntries([]);
          }}
        >
          Got It
        </button>
      </div>
    </section>
  );
}
