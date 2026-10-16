import { createHmac } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect } from '@playwright/test';

export const API = `http://127.0.0.1:${process.env.E2E_API_PORT || 8765}`;
const REPO = join(fileURLToPath(new URL('.', import.meta.url)), '..', '..');
export const CREDS_FILE = join(REPO, 'local_dev', 'dev_credentials.json');
export const KILL_FILE = join(REPO, 'local_dev', 'kalshi_bot', 'MAKER_KILL_SWITCH');
export const ARB_KILL_FILE = join(REPO, 'local_dev', 'kalshi_bot', 'ARB_KILL_SWITCH');

export function creds() {
  return JSON.parse(readFileSync(CREDS_FILE, 'utf8'));
}

// ---- RFC 6238 TOTP (SHA-1, 6 digits, 30 s) without dependencies
function base32(s) {
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const ch of s.replace(/=+$/, '').toUpperCase()) bits += A.indexOf(ch).toString(2).padStart(5, '0');
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

export function totpAtStep(secret, step) {
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(step));
  const h = createHmac('sha1', base32(secret)).update(msg).digest();
  const o = h[h.length - 1] & 0xf;
  const n = ((h[o] & 0x7f) << 24) | (h[o + 1] << 16) | (h[o + 2] << 8) | h[o + 3];
  return String(n % 1_000_000).padStart(6, '0');
}

const STEP_MS = 30_000;
let lastStep = 0;

/**
 * A code the server has never seen. The server accepts steps current-1..current+1 and only strictly increasing
 * ones, so up to three codes are free; after that this waits for the next 30 s window.
 */
export async function freshCode() {
  const { totp_secret: secret } = creds();
  for (;;) {
    const cur = Math.floor(Date.now() / STEP_MS);
    const next = Math.max(lastStep + 1, cur - 1);
    if (next <= cur + 1) {
      lastStep = next;
      return totpAtStep(secret, next);
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

/** The code the server already consumed last (for the single-use check). */
export function lastUsedCode() {
  return totpAtStep(creds().totp_secret, lastStep);
}

let cachedToken = null;
/** Logs in through the real API once (password + 2FA) and returns the access token. */
export async function apiToken(request) {
  if (cachedToken) return cachedToken;
  const { username, password } = creds();
  const l = await request.post(`${API}/api/auth/login`, { data: { username, password } });
  expect(l.status()).toBe(200);
  const { mfa_token } = await l.json();
  const t = await request.post(`${API}/api/auth/2fa`, { data: { mfa_token, code: await freshCode() } });
  expect(t.status()).toBe(200);
  cachedToken = (await t.json()).access_token;
  return cachedToken;
}
export function rememberToken(t) {
  cachedToken = t;
}

export async function apiGet(request, path) {
  const r = await request.get(`${API}${path}`, { headers: { Authorization: `Bearer ${await apiToken(request)}` } });
  return { status: r.status(), body: await r.json().catch(() => null) };
}

export async function apiSend(request, method, path, { data, code } = {}) {
  const headers = { Authorization: `Bearer ${await apiToken(request)}` };
  if (code) headers['X-2FA-Code'] = code;
  const r = await request.fetch(`${API}${path}`, { method, headers, data });
  return { status: r.status(), body: await r.json().catch(() => null) };
}

/** Opens the app already signed in (token injected into sessionStorage, as the app stores it). */
export async function openSignedIn(page, request, path = '/') {
  const token = await apiToken(request);
  await page.addInitScript((t) => sessionStorage.setItem('km.token', t), token);
  await page.goto(path);
  await expect(page.locator('header.appbar').getByRole('button', { name: /stop the bot/i })).toBeVisible();
}

export { existsSync };
