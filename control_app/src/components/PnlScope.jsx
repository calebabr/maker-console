import { scopeText } from '../utils/pnlScopes.js';

/**
 * One muted line under a P&L figure saying what it measures and its date rule (registry: utils/pnlScopes.js).
 * `as` picks the element (use "dd" inside a <dl> tile).
 */
export default function PnlScope({ id, ctx, as: Tag = 'p', className = '' }) {
  const text = scopeText(id, ctx);
  if (!text) return null;
  return (
    <Tag className={`hint pnl-scope ${className}`.trim()} data-testid={`scope-${id}`}>
      {text}
    </Tag>
  );
}
