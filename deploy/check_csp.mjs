// Checks control_app/vercel.json's Content-Security-Policy `connect-src` (security finding S-14).
//
//   node deploy/check_csp.mjs                       # CI: no wildcards, https only, at most one API origin
//   node deploy/check_csp.mjs https://x.onrender.com  # owner: also require connect-src to be exactly that origin
//
// Exit 1 on a violation. The placeholder origin (*.invalid) only produces a warning so CI stays green
// before the owner has a Render URL; the Vercel app cannot reach the API until it is replaced.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const cfg = JSON.parse(readFileSync(join(root, 'control_app', 'vercel.json'), 'utf8'));
const csp = (cfg.headers || [])
  .flatMap((h) => h.headers || [])
  .find((h) => h.key.toLowerCase() === 'content-security-policy');
const fail = (m) => { console.error(`CSP check FAILED: ${m}`); process.exit(1); };
if (!csp) fail('no Content-Security-Policy header in control_app/vercel.json');

const directive = csp.value.split(';').map((d) => d.trim()).find((d) => d.startsWith('connect-src '));
if (!directive) fail('no connect-src directive');
const sources = directive.split(/\s+/).slice(1);
const origins = sources.filter((s) => s !== "'self'");

for (const s of origins) {
  if (s.includes('*')) fail(`wildcard source "${s}" (anyone can host a matching server)`);
  let u;
  try { u = new URL(s); } catch { fail(`"${s}" is not an origin`); }
  if (u.protocol !== 'https:') fail(`"${s}" must be https`);
  if (u.origin !== s.replace(/\/+$/, '')) fail(`"${s}" must be a bare origin (no path)`);
}
if (origins.length > 1) fail(`expected one API origin, got ${origins.join(' ')}`);

const expected = (process.argv[2] || '').replace(/\/+$/, '');
if (expected) {
  if (origins[0] !== expected) fail(`connect-src has ${origins[0] || "only 'self'"}, expected ${expected}`);
} else if (!origins.length || origins[0].endsWith('.invalid')) {
  console.warn('CSP check: connect-src still has the placeholder API origin; see deploy/vercel.md');
}
console.log(`CSP check ok: connect-src ${sources.join(' ')}`);
