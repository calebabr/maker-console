import { expect } from '@playwright/test';

let seq = 100000 + (process.pid % 1000) * 100;
/** A fresh, never-used 6-digit code (the mock rejects a reused code, like the real API). */
export const nextCode = () => String(++seq).padStart(6, '0');

export async function login(page) {
  await page.goto('/');
  await expect(page.getByLabel('Username')).toBeVisible();
  await page.getByLabel('Username').fill('owner');
  await page.getByLabel('Password', { exact: true }).fill('correct-horse');
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByLabel('6-digit code').fill(nextCode());
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.locator('header.appbar').getByRole('button', { name: /stop the bot/i })).toBeVisible();
}

const MAIN = ['Overview', 'Analytics', 'Activity', 'Markets'];

/** Click through the real nav (a full page.goto would reset the in-memory mock and the session). */
export async function nav(page, label) {
  if (MAIN.includes(label)) {
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: label }).click();
  } else {
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'More' }).click();
    await page.getByRole('navigation', { name: 'More pages' }).getByRole('link', { name: label }).click();
  }
}

export async function stopFromHeader(page) {
  await page.locator('header.appbar').getByRole('button', { name: /stop the bot/i }).click();
  await page.getByRole('button', { name: 'Stop now' }).click();
}

export async function noHorizontalScroll(page) {
  const { sw, cw } = await page.evaluate(() => ({
    sw: document.documentElement.scrollWidth,
    cw: document.documentElement.clientWidth,
  }));
  expect(sw, `page is ${sw}px wide in a ${cw}px viewport`).toBeLessThanOrEqual(cw);
}
