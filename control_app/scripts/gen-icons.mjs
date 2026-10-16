// Generates the Keel PWA icons (PNG + SVG) into public/ with no dependencies.
// The artwork is the "Keel G" level gauge from src/components/KeelLogo.jsx, drawn in the default Midnight Slate colours:
//   ring circle(24,24,r15) stroke 3 (faint) · horizon M9 24 H39 stroke 3.5 round (accent)
//   keel M18 24 L24 38 L30 24 Z (success) · dot circle(24,15,r3) (text)
// Run: npm run icons
import { deflateSync } from 'node:zlib';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const out = join(dirname(fileURLToPath(import.meta.url)), '..', 'public');
mkdirSync(out, { recursive: true });

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const BG = '#0f141b';
const TEXT = '#e6edf3';
const ACCENT = '#4ea1ff';
const OK = '#3fb97a';
// The ring is the text colour at 28% over the background (SVG uses stroke-opacity; PNGs have no alpha).
const RING = '#' + hex(TEXT).map((c, i) => Math.round(c * 0.28 + hex(BG)[i] * 0.72).toString(16).padStart(2, '0')).join('');

// ---- geometry in the 48-unit logo box -------------------------------------------------------------

const dist = (px, py, ax, ay, bx, by) => {
  const dx = bx - ax;
  const dy = by - ay;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
};
const inTriangle = (px, py) => {
  const A = [18, 24];
  const B = [24, 38];
  const C = [30, 24];
  const s = (p, q, r) => (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1]);
  const P = [px, py];
  const d1 = s(P, A, B);
  const d2 = s(P, B, C);
  const d3 = s(P, C, A);
  const neg = d1 < 0 || d2 < 0 || d3 < 0;
  const pos = d1 > 0 || d2 > 0 || d3 > 0;
  return !(neg && pos);
};

// Colour of the logo at unit coordinates (x, y), or null for background.
function logoAt(x, y) {
  if (Math.hypot(x - 24, y - 15) <= 3) return TEXT;
  if (inTriangle(x, y)) return OK;
  if (dist(x, y, 9, 24, 39, 24) <= 1.75) return ACCENT;
  if (Math.abs(Math.hypot(x - 24, y - 24) - 15) <= 1.5) return RING;
  return null;
}

// ---- PNG -------------------------------------------------------------------------------------------

const crcTable = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}

// `fill` = share of the icon width the 48-unit logo box occupies (smaller for the maskable safe zone).
function png(size, fill) {
  const SS = 4; // 4x4 samples per pixel
  const bg = hex(BG);
  const k = (size * fill) / 48;
  const off = (size - 48 * k) / 2;
  const raw = Buffer.alloc((size * 3 + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (size * 3 + 1)] = 0;
    for (let x = 0; x < size; x++) {
      let r = 0;
      let g = 0;
      let b = 0;
      for (let sy = 0; sy < SS; sy++) {
        for (let sx = 0; sx < SS; sx++) {
          const ux = (x + (sx + 0.5) / SS - off) / k;
          const uy = (y + (sy + 0.5) / SS - off) / k;
          const c = logoAt(ux, uy);
          const rgb = c ? hex(c) : bg;
          r += rgb[0];
          g += rgb[1];
          b += rgb[2];
        }
      }
      const n = SS * SS;
      const o = y * (size * 3 + 1) + 1 + x * 3;
      raw[o] = Math.round(r / n);
      raw[o + 1] = Math.round(g / n);
      raw[o + 2] = Math.round(b / n);
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

// ---- SVG -------------------------------------------------------------------------------------------

function svg(fill, radius) {
  const k = (512 * fill) / 48;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">` +
    `<rect width="512" height="512" rx="${radius}" fill="${BG}"/>` +
    `<g transform="translate(256 256) scale(${k.toFixed(4)}) translate(-24 -24)" fill="none">` +
    `<circle cx="24" cy="24" r="15" stroke="${TEXT}" stroke-opacity="0.28" stroke-width="3"/>` +
    `<path d="M9 24 H39" stroke="${ACCENT}" stroke-width="3.5" stroke-linecap="round"/>` +
    `<path d="M18 24 L24 38 L30 24 Z" fill="${OK}"/>` +
    `<circle cx="24" cy="15" r="3" fill="${TEXT}"/>` +
    `</g></svg>\n`
  );
}

writeFileSync(join(out, 'icon-192.png'), png(192, 0.9));
writeFileSync(join(out, 'icon-512.png'), png(512, 0.9));
writeFileSync(join(out, 'maskable-512.png'), png(512, 0.74)); // ring stays inside the 80% maskable safe circle
writeFileSync(join(out, 'apple-touch-icon.png'), png(180, 0.9));
writeFileSync(join(out, 'icon.svg'), svg(0.9, 96));
writeFileSync(join(out, 'favicon.svg'), svg(1.1, 96));
console.log('icons written to', out);
