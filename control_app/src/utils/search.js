// Relevance search for the Terminology filter box (pure, zero dependencies, no React).
//
// Scoring (higher is better; ties sorted alphabetically by term). Field weights: title > alias > id >
// short line > body.
//   1000  whole title (or the part before / inside "(...)") or the id equals the query
//    950  same after stemming ("filled" -> Fill, "queues" -> Queue)
//    900  an alias equals the query            850  same after stemming
//    700  title / alias / id starts with the query
//    600  query sits at a word boundary in a title / alias / id
//    500  query is inside a title / alias / id (only for queries of 3+ characters; 1-2 letters are noise)
//  300-400  every query word matches a title / alias word by stem, prefix or typo ("qeue" -> Queue)
//  ~100-150 short line matches   ~20-40 body matches (more hits and earlier position score a little higher)
// Matching ignores case, punctuation and spacing: "pnl", "p&l" and "P & L" all reach P&L.
// "Strong" results are those whose title / alias / id / short line matched; body-only matches are "weak"
// and the page folds them under "Also mentioned in N more terms".

// "P&L (profit)" -> "pl profit": lowercase, every run of non-alphanumerics becomes one space.
export function spaced(s) {
  return String(s ?? '')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

// "P & L" -> "pl": spaced() without the spaces.
export function compact(s) {
  return spaced(s).replace(/ /g, '');
}

const STOP = new Set(['a', 'an', 'the', 'and', 'or', 'of', 'to', 'in', 'on', 'for', 'is', 'are', 'what']);

// Tiny suffix stemmer: plurals, -ed, -ing, a trailing silent "e", and a doubled final consonant.
// Only for alphabetic words of 4+ letters; anything else is returned unchanged.
export function stem(word) {
  let w = String(word || '').toLowerCase();
  if (w.length < 4 || !/^[a-z]+$/.test(w)) return w;
  if (w.endsWith('ies') && w.length > 4) w = `${w.slice(0, -3)}y`;
  else if (w.endsWith('sses')) w = w.slice(0, -2);
  else if (w.endsWith('s') && !w.endsWith('ss') && !w.endsWith('us') && !w.endsWith('is')) w = w.slice(0, -1);
  if (w.length > 4 && w.endsWith('ing')) w = w.slice(0, -3);
  else if (w.length > 3 && w.endsWith('ed')) w = w.slice(0, -2);
  if (w.length > 3 && w.endsWith('e')) w = w.slice(0, -1);
  if (w.length > 3 && /([^aeiouyls])\1$/.test(w)) w = w.slice(0, -1);
  return w;
}

// Optimal-string-alignment edit distance (insert, delete, substitute, swap neighbours), capped.
export function editDistance(a, b, cap = 3) {
  if (a === b) return 0;
  if (Math.abs(a.length - b.length) > cap) return cap + 1;
  const prev2 = new Array(b.length + 1);
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, prev2[j - 2] + 1);
      cur[j] = v;
    }
    for (let j = 0; j <= b.length; j++) prev2[j] = prev[j];
    prev = cur;
  }
  return prev[b.length];
}

// How well one query word matches one text word: 1 exact (after stemming), 0.85 prefix, 0.6 typo, else 0.
export function wordStrength(q, w) {
  if (!q || !w) return 0;
  if (q === w) return 1;
  const qs = stem(q);
  const ws = stem(w);
  if (qs === ws) return 1;
  if (q.length < 2) return 0;
  if (w.startsWith(q) || ws.startsWith(qs)) return 0.85;
  if (q.length >= 4) {
    const cap = qs.length >= 7 ? 2 : 1;
    if (editDistance(qs, ws, cap) <= cap) return 0.6;
    if (q.length >= 5 && w.length > q.length && editDistance(q, w.slice(0, q.length), 1) <= 1) return 0.55;
  }
  return 0;
}

function queryWords(query) {
  const words = spaced(query).split(' ').filter(Boolean);
  const kept = words.filter((w) => !STOP.has(w));
  return kept.length ? kept : words;
}

// Every query word must match some word of the text. Returns null, or
// {strength (mean), position (index of first matching word), hits (matching words counted)}.
function matchWords(qWords, textWords) {
  if (!qWords.length || !textWords.length) return null;
  let sum = 0;
  let pos = Infinity;
  for (const q of qWords) {
    let best = 0;
    let at = -1;
    for (let i = 0; i < textWords.length; i++) {
      const s = wordStrength(q, textWords[i]);
      if (s > best) {
        best = s;
        at = i;
        if (s === 1) break;
      }
    }
    if (!best) return null;
    sum += best;
    pos = Math.min(pos, at);
  }
  let hits = 0;
  for (const w of textWords) if (qWords.some((q) => wordStrength(q, w) >= 0.85)) hits++;
  return { strength: sum / qWords.length, position: pos, hits };
}

function titleParts(term) {
  const t = String(term || '');
  const parts = [t];
  const m = t.match(/^(.*?)\s*\(([^)]*)\)\s*$/);
  if (m) parts.push(m[1], m[2]);
  return parts.filter((p) => p.trim());
}

const stemmedJoin = (s) => spaced(s).split(' ').map(stem).join(' ');

// {score, kind: 'name'|'short'|'body', via: alias that carried the match or null}, or null for no match.
export function scoreDetail(query, entry) {
  const qC = compact(query);
  if (!qC) return null;
  const qSp = spaced(query);
  const qStem = stemmedJoin(query);
  const qWords = queryWords(query);

  const titles = titleParts(entry.term);
  const aliases = Array.isArray(entry.aliases) ? entry.aliases : [];

  let best = 0;
  let via = null;
  const take = (score, alias) => {
    if (score > best || (score === best && via && !alias)) {
      best = score;
      via = alias || null;
    }
  };

  // Exact (also stemmed) titles, ids and aliases.
  if (titles.some((t) => compact(t) === qC) || compact(entry.id) === qC) take(1000, null);
  else if (titles.some((t) => stemmedJoin(t) === qStem)) take(950, null);
  for (const a of aliases) {
    if (compact(a) === qC) take(900, a);
    else if (stemmedJoin(a) === qStem) take(850, a);
  }
  // Prefix / word-boundary / substring on the raw text.
  const names = [...titles.map((t) => [t, null]), ...aliases.map((a) => [a, a]), [entry.id, null]];
  for (const [n, alias] of names) {
    const nC = compact(n);
    if (qSp.includes(' ') ? spaced(n).startsWith(qSp) : nC.startsWith(qC)) take(700, alias);
    else if (` ${spaced(n)}`.includes(` ${qSp}`)) take(600, alias);
    else if (qC.length >= 3 && nC.includes(qC)) take(500, alias);
  }
  // Word-by-word: stems, prefixes and typos.
  if (best < 400) {
    for (const [n, alias] of names) {
      const m = matchWords(qWords, spaced(n).split(' '));
      if (m) take(300 + 100 * m.strength - Math.min(m.position, 5) * 2 - (alias ? 5 : 0), alias);
    }
  }

  const shortM = matchWords(qWords, spaced(entry.short).split(' '));
  const bodyM = matchWords(qWords, spaced(entry.body).split(' '));
  if (best > 0) {
    // Small tie-breakers so a term whose prose also matches edges ahead of one whose prose doesn't.
    const bonus = (shortM ? 3 : 0) + (bodyM ? Math.min(bodyM.hits, 5) * 0.4 : 0);
    return { score: best + bonus, kind: 'name', via };
  }
  if (shortM) {
    return { score: 100 + 40 * shortM.strength + Math.min(shortM.hits, 3) * 5 - Math.min(shortM.position, 20) * 0.5, kind: 'short', via: null };
  }
  if (bodyM) {
    return { score: 20 + 20 * bodyM.strength + Math.min(bodyM.hits, 5) * 2 - Math.min(bodyM.position, 100) * 0.05, kind: 'body', via: null };
  }
  return null;
}

// Score for one entry (higher is better), or null if it doesn't match at all.
export function scoreTerm(query, entry) {
  const d = scoreDetail(query, entry);
  return d ? d.score : null;
}

// Matching entries with detail, best first. Empty query -> [].
//   [{entry, score, kind, via, strong}]
export function searchTerms(query, entries) {
  if (!compact(query)) return [];
  return entries
    .map((entry) => {
      const d = scoreDetail(query, entry);
      return d ? { entry, ...d, strong: d.kind !== 'body' } : null;
    })
    .filter(Boolean)
    .sort((a, b) => b.score - a.score || a.entry.term.localeCompare(b.entry.term, 'en', { sensitivity: 'base' }));
}

// Matching entries, best first (no detail). Empty query -> [] (the page shows grouped sections instead).
export function rankTerms(query, entries) {
  return searchTerms(query, entries).map((r) => r.entry);
}

// Split text into [{text, hit}] segments so the page can wrap matched words in <mark>.
export function highlightSegments(text, query) {
  const s = String(text ?? '');
  const qWords = queryWords(query);
  if (!qWords.length) return [{ text: s, hit: false }];
  const out = [];
  for (const tok of s.match(/[A-Za-z0-9]+|[^A-Za-z0-9]+/g) || []) {
    const isWord = /^[A-Za-z0-9]/.test(tok);
    const hit = isWord && qWords.some((q) => wordStrength(q, tok.toLowerCase()) >= 0.6);
    const last = out[out.length - 1];
    if (last && last.hit === hit) last.text += tok;
    else out.push({ text: tok, hit });
  }
  return out;
}
