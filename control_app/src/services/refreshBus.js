// Registry of "refetch" callbacks for the data sources currently mounted. The Refresh buttons call each
// hook's own reload; pull-to-refresh calls refreshAll(), which runs the same reloads for the current page.
const sources = new Set();

export function registerRefresh(fn) {
  sources.add(fn);
  return () => sources.delete(fn);
}

export async function refreshAll() {
  const fns = [...sources];
  await Promise.allSettled(fns.map((fn) => Promise.resolve().then(fn)));
  return fns.length;
}

export function refreshSourceCount() {
  return sources.size;
}
