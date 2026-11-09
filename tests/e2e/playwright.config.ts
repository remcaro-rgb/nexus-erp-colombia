/**
 * P22 Playwright configuration (P22a harness; journeys P22b/P22d, visual + a11y P22a/P22d).
 *
 * DEV-only: the target is the local build on loopback (D-002). The browser is the system Chrome
 * ($CHROME_PATH); Playwright's browser CDN is not on the cloud allowlist, so run with
 * PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1. es-CO, America/Bogota, reduced motion, traces on failure.
 *
 *   PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 pnpm --dir frontend exec playwright test -c ../tests/e2e/playwright.config.ts
 */
import { defineConfig } from '@playwright/test';

const baseURL = process.env.ERP_BASE_URL ?? `http://127.0.0.1:${process.env.ERP_DEV_PORT ?? '8080'}`;
const host = new URL(baseURL).hostname;
if (!['127.0.0.1', 'localhost', '::1'].includes(host)) {
  throw new Error(`refusing non-loopback target ${baseURL} (DEV-only, D-002)`);
}
const chrome = process.env.CHROME_PATH;
if (!chrome) {
  throw new Error('CHROME_PATH is not set: the e2e suites use the system Chrome');
}

export default defineConfig({
  testDir: '.',
  testMatch: ['**/*.spec.ts'],
  outputDir: '../../reports/playwright/artifacts',
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 2,
  timeout: 60_000,
  expect: { timeout: 10_000, toHaveScreenshot: { animations: 'disabled', caret: 'hide', scale: 'css' } },
  reporter: [
    ['list'],
    ['json', { outputFile: '../../reports/playwright/results.json' }],
    ['junit', { outputFile: '../../reports/junit/playwright.xml' }],
  ],
  use: {
    baseURL,
    locale: 'es-CO',
    timezoneId: 'America/Bogota',
    colorScheme: 'light',
    reducedMotion: 'reduce',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    launchOptions: { executablePath: chrome, args: ['--headless=new', '--no-sandbox'] },
  },
  projects: [
    { name: 'journeys', testMatch: ['journeys/**/*.spec.ts', 'dev-smoke.spec.ts'] },
    { name: 'visual', testMatch: ['visual/**/*.spec.ts'] },
    { name: 'a11y', testMatch: ['a11y/**/*.spec.ts'] },
  ],
});
