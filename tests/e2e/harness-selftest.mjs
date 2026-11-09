// P22a e2e harness self-test (no app, no network): launches the system Chrome through the
// pre-installed `playwright` library with the same context options as playwright.config.ts and
// verifies locale, time zone and reduced motion. Prints one JSON line; exit 1 on mismatch.
//   node tests/e2e/harness-selftest.mjs
import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';

const require = createRequire(import.meta.url);
let playwright;
try {
  playwright = require('playwright');
} catch {
  const root = execSync('npm root -g').toString().trim();
  playwright = require(`${root}/playwright`);
}
const executablePath = process.env.CHROME_PATH;
if (!executablePath) {
  console.log(JSON.stringify({ ok: false, error: 'CHROME_PATH not set' }));
  process.exit(1);
}
const browser = await playwright.chromium.launch({ executablePath, args: ['--headless=new', '--no-sandbox'] });
let ok = false;
try {
  const context = await browser.newContext({ locale: 'es-CO', timezoneId: 'America/Bogota', reducedMotion: 'reduce' });
  const page = await context.newPage();
  await page.setContent('<!doctype html><html lang="es-CO"><body><p id="x">ok</p></body></html>');
  const got = await page.evaluate(() => ({
    language: navigator.language,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    money: new Intl.NumberFormat('es-CO', { minimumFractionDigits: 2 }).format(1234567.89),
    reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
    bogotaHour: new Date('2026-11-09T15:00:00Z').getHours(),
  }));
  ok =
    got.language === 'es-CO' && got.timeZone === 'America/Bogota' && got.money === '1.234.567,89' &&
    got.reducedMotion === true && got.bogotaHour === 10;
  console.log(JSON.stringify({ ok, browser: browser.version(), ...got }));
} finally {
  await browser.close();
}
process.exit(ok ? 0 : 1);
