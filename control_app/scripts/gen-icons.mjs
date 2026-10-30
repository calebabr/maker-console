// Generates the Prior PWA icons (PNG + SVG) into public/ with no dependencies.
// The artwork is the "full ring" logo from src/components/PriorLogo.jsx, drawn in the default Midnight Slate colours:
//   ring circle(24,24,r19) w2.5 · baseline (11,33)-(37,33) w2 · prior curve (muted) w2.2 · posterior curve (success) w2.8
// Run: npm run icons
import { deflateSync } from 'node:zlib';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const out = join(dirname(fileURLToPath(import.meta.url)), '..', 'public');
mkdirSync(out, { recursive: true });

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const BG = '#0f141b';
const MUTED = '#8b98a8';
const OK = '#3fb97a';
// SVG uses stroke-opacity; PNGs have no alpha, so blend the faint strokes over the background.
const mix = (c, a) => '#' + hex(c).map((v, i) => Math.round(v * a + hex(BG)[i] * (1 - a)).toString(16).padStart(2, '0')).join('');
const RING = mix(MUTED, 0.45);
const BASE = mix(MUTED, 0.7);

// ---- geometry in the 48-unit logo box -------------------------------------------------------------

const dist = (px, py, ax, ay, bx, by) => {
  const dx = bx - ax;
  const dy = by - ay;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
};
const bez = (p0, p1, p2, p3, n = 48) =>
  Array.from({ length: n + 1 }, (_, i) => {
    const t = i / n;
    const u = 1 - t;
    return [0, 1].map((k) => u * u * u * p0[k] + 3 * u * u * t * p1[k] + 3 * u * t * t * p2[k] + t * t * t * p3[k]);
  });
const PRIOR = [...bez([10, 33], [16, 33], [18, 21], [24, 21]), ...bez([24, 21], [30, 21], [32, 33], [38, 33])];
const POSTERIOR = [...bez([15, 33], [20, 33], [21, 12], [24, 12]), ...bez([24, 12], [27, 12], [28, 33], [33, 33])];
const near = (pts, x, y, r) => {
  for (let i = 0; i < pts.length - 1; i++) if (dist(x, y, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) <= r) return true;
  return false;
};

// Colour of the logo at unit coordinates (x, y), or null for background. Posterior is drawn on top.
function logoAt(x, y) {
  if (near(POSTERIOR, x, y, 1.4)) return OK;
  if (near(PRIOR, x, y, 1.1)) return MUTED;
  if (dist(x, y, 11, 33, 37, 33) <= 1) return BASE;
  if (Math.abs(Math.hypot(x - 24, y - 24) - 19) <= 1.25) return RING;
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

function svg(fill, radius, priorW = 2.2) {
  const k = (512 * fill) / 48;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">` +
    `<rect width="512" height="512" rx="${radius}" fill="${BG}"/>` +
    `<g transform="translate(256 256) scale(${k.toFixed(4)}) translate(-24 -24)" fill="none" stroke-linecap="round">` +
    `<circle cx="24" cy="24" r="19" stroke="${MUTED}" stroke-opacity="0.45" stroke-width="2.5"/>` +
    `<path d="M11 33 H37" stroke="${MUTED}" stroke-opacity="0.7" stroke-width="2"/>` +
    `<path d="M10 33 C16 33 18 21 24 21 C30 21 32 33 38 33" stroke="${MUTED}" stroke-width="${priorW}"/>` +
    `<path d="M15 33 C20 33 21 12 24 12 C27 12 28 33 33 33" stroke="${OK}" stroke-width="2.8"/>` +
    `</g></svg>
`
  );
}

writeFileSync(join(out, 'icon-192.png'), png(192, 0.9));
writeFileSync(join(out, 'icon-512.png'), png(512, 0.9));
writeFileSync(join(out, 'maskable-512.png'), png(512, 0.74)); // ring stays inside the 80% maskable safe circle
writeFileSync(join(out, 'apple-touch-icon.png'), png(180, 0.9));
writeFileSync(join(out, 'icon.svg'), svg(0.9, 96));
writeFileSync(join(out, 'favicon.svg'), svg(1.1, 96, 1.8)); // thinner prior curve so the two curves stay apart at 16 px
console.log('icons written to', out);
