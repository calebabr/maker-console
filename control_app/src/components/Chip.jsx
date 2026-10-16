const TONES = ['ok', 'warn', 'danger', 'muted', 'accent'];

/**
 * Small status chip (verdicts, modes, source labels). `tone` is a meaning, not a colour:
 * ok = green / on track, warn = amber, danger = red / loss, muted = neutral, accent = the theme accent.
 * Text is never the only carrier of meaning: pass the label in `children`.
 * (The older `.chip` CSS class is the tappable filter button; this is the read-only tag.)
 */
export default function Chip({ tone = 'muted', title, className = '', children }) {
  const t = TONES.includes(tone) ? tone : 'muted';
  return (
    <span className={`tag tag-${t} ${className}`.trim()} title={title} data-tone={t}>
      {children}
    </span>
  );
}
