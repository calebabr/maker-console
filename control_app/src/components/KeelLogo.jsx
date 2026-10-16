/**
 * Keel "level gauge" logo: a faint ring, an accent horizon line, a solid keel (success colour) below the
 * line and a small dot above it. One SVG painted from the theme variables, so it follows all three themes.
 * Decorative by default (the name sits next to it); pass `title` to make it an accessible image.
 * Geometry (48x48 viewBox): ring (24,24) r15 stroke 3, horizon M9 24 H39 stroke 3.5 round,
 * keel M18 24 L24 38 L30 24 Z, dot (24,15) r3.
 */
export default function KeelLogo({ size = 24, title, className = '' }) {
  return (
    <svg
      className={`keel-logo ${className}`.trim()}
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      role={title ? 'img' : undefined}
      aria-label={title || undefined}
      aria-hidden={title ? undefined : 'true'}
      data-testid="keel-logo"
    >
      <circle cx="24" cy="24" r="15" stroke="var(--text)" strokeOpacity="0.28" strokeWidth="3" />
      <path d="M9 24 H39" stroke="var(--accent)" strokeWidth="3.5" strokeLinecap="round" />
      <path d="M18 24 L24 38 L30 24 Z" fill="var(--ok)" />
      <circle cx="24" cy="15" r="3" fill="var(--text)" />
    </svg>
  );
}
