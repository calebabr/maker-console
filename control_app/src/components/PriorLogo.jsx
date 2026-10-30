/**
 * Prior logo ("full ring"): a ring, a baseline, a wide muted PRIOR curve and a narrow, taller green
 * POSTERIOR curve (belief before the evidence, then after). One SVG painted only from the theme variables,
 * so it follows all three themes. Decorative by default (the name sits next to it); pass `title` to make it
 * an accessible image.
 * Geometry (48x48 viewBox, no fills, round caps): ring (24,24) r19 stroke 2.5; baseline (11,33)-(37,33) stroke 2;
 * prior M10 33 C16 33 18 21 24 21 C30 21 32 33 38 33 stroke 2.2;
 * posterior M15 33 C20 33 21 12 24 12 C27 12 28 33 33 33 stroke 2.8.
 */
export default function PriorLogo({ size = 24, title, className = '' }) {
  return (
    <svg
      className={`prior-logo ${className}`.trim()}
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      strokeLinecap="round"
      role={title ? 'img' : undefined}
      aria-label={title || undefined}
      aria-hidden={title ? undefined : 'true'}
      data-testid="prior-logo"
    >
      <circle cx="24" cy="24" r="19" stroke="var(--muted)" strokeOpacity="0.45" strokeWidth="2.5" />
      <path d="M11 33 H37" stroke="var(--muted)" strokeOpacity="0.7" strokeWidth="2" />
      <path d="M10 33 C16 33 18 21 24 21 C30 21 32 33 38 33" stroke="var(--muted)" strokeWidth="2.2" />
      <path d="M15 33 C20 33 21 12 24 12 C27 12 28 33 33 33" stroke="var(--ok)" strokeWidth="2.8" />
    </svg>
  );
}
