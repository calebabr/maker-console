import HelpIcon from '../HelpIcon.jsx';
import { VERDICTS, VerdictPill } from './EdgeBreakdown.jsx';
import { parseCell, shortReason } from '../../utils/analytics.js';

const ORDER = ['below', 'on_track', 'above', 'too_early'];

function cellName(id) {
  const c = parseCell(id);
  return `${c.price}, ${c.ttc}, ${c.side}`;
}

/** Answer at the top of the page: per-cell verdicts against the research expectation. */
export function edgeHeadline(cells) {
  const count = (v) => cells.filter((c) => c.verdict === v).length;
  const judged = cells.length - count('too_early');
  const below = count('below');
  if (!judged) return { tone: 'muted', text: 'Too early to tell. No market type has enough fills yet.' };
  if (below) {
    return {
      tone: 'neg',
      text: `Not everywhere. ${below} of ${judged} judged market type${judged === 1 ? '' : 's'} ${below === 1 ? 'is' : 'are'} below.`,
    };
  }
  return { tone: 'pos', text: `Yes, so far. ${judged === 1 ? 'The 1 judged market type is' : `All ${judged} judged market types are`} on track or better.` };
}

export default function EdgeHolding({ summary }) {
  const cells = ((summary && summary.by_cell) || []).filter((c) => c && VERDICTS[c.verdict]);
  if (!cells.length) return null;
  const exp = (summary && summary.expectations) || null;
  const head = edgeHeadline(cells);
  const notable = cells.filter((c) => c.verdict === 'below' || c.verdict === 'above');
  return (
    <section className="card edge-holding" data-testid="edge-holding" aria-labelledby="edge-holding-title">
      <header className="card-head">
        <div>
          <h2 className="card-title" id="edge-holding-title">
            Is the Edge Holding?
            <HelpIcon term="edge-verdict" />
          </h2>
          <div className="card-subtitle">Are the bot's real results matching what the research predicted? Bot trades only.</div>
        </div>
      </header>
      <p className={`edge-headline ${head.tone}`}>{head.text}</p>
      <p className="muted small">Market types by result</p>
      <ul className="verdict-counts" aria-label="Cells by verdict">
        {ORDER.map((v) => {
          const n = cells.filter((c) => c.verdict === v).length;
          return (
            <li key={v} className={n ? '' : 'muted'}>
              <b>{n}</b> <VerdictPill verdict={v} />
            </li>
          );
        })}
      </ul>
      {notable.length > 0 && (
        <ul className="verdict-notes">
          {notable.map((c) => (
            <li key={c.key}>
              <span className="verdict-cell">{cellName(c.cell || c.key)}</span> <VerdictPill verdict={c.verdict} />
              {shortReason(c) && <div className="muted small">{shortReason(c)}</div>}
            </li>
          ))}
        </ul>
      )}
      {exp && !exp.loaded && (
        <p className="hint">No research benchmark is loaded, so every market type uses the default expectation.</p>
      )}
      {exp && exp.loaded && exp.source && <p className="hint">Benchmark: {exp.source}.</p>}
    </section>
  );
}
