import { useId, useState } from 'react';
import { IconChevron } from './Icons.jsx';

/**
 * Folded-by-default section: a hairline row with a title, an always-visible `summary` slot and a chevron;
 * tap to open the details (`children`). The body is not rendered while folded.
 *   <FoldCard title="KXDEMOD" summary={<Chip tone="ok">On Track</Chip>}>...details...</FoldCard>
 * Uncontrolled by default (`defaultOpen`); pass `open` + `onOpenChange` to control it.
 * `actions` sit beside the header (e.g. a Pause button) and never toggle the card.
 */
export default function FoldCard({
  title,
  summary,
  actions,
  defaultOpen = false,
  open: controlled,
  onOpenChange,
  className = '',
  id,
  children,
}) {
  const auto = useId();
  const bodyId = `${id || auto}-body`;
  const [inner, setInner] = useState(!!defaultOpen);
  const isOpen = controlled !== undefined ? !!controlled : inner;

  function toggle() {
    const next = !isOpen;
    if (controlled === undefined) setInner(next);
    if (onOpenChange) onOpenChange(next);
  }

  return (
    <section className={`fold ${isOpen ? 'is-open' : ''} ${className}`.trim()} id={id}>
      <div className="fold-head">
        <button type="button" className="fold-toggle" onClick={toggle} aria-expanded={isOpen} aria-controls={bodyId}>
          <span className="fold-main">
            <span className="fold-title">{title}</span>
            {summary != null && <span className="fold-summary">{summary}</span>}
          </span>
          <span className="fold-chevron" aria-hidden="true">
            <IconChevron />
          </span>
        </button>
        {actions && <div className="fold-actions">{actions}</div>}
      </div>
      {isOpen && (
        <div className="fold-body" id={bodyId}>
          {children}
        </div>
      )}
    </section>
  );
}
