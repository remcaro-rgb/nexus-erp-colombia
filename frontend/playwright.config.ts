// Playwright config of `make test-ids` / `make e2e` (FIX-CI-W02, CCR P08-3). Playwright picks this file up
// because both targets run `pnpm --dir frontend exec playwright test` (cwd = frontend/).
//
// Collects only the feature e2e specs that tasks keep in their own paths
// (src/features/<module>/.../e2e/.../<name>.spec.ts, e.g. P05's T-ACC-DIM-08) and never the vitest
// `*.test.ts` files. The P08 shell smoke (`*.e2e.ts`) keeps its own config (`pnpm --dir frontend run e2e`)
// and P22's suites theirs (`tests/e2e/playwright.config.ts`).
//
// DEV-only: system Chrome ($CHROME_PATH; browsers are never downloaded), loopback target only (D-002).
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..');

const baseURL =
  process.env.ERP_BASE_URL ?? process.env.ERP_PUBLIC_ORIGIN ?? `http://127.0.0.1:${process.env.ERP_DEV_PORT ?? '8080'}`;
const host = new URL(baseURL).hostname;
if (!['127.0.0.1', 'localhost', '::1', '[::1]'].includes(host)) {
  throw new Error(`refusing non-loopback target ${baseURL} (DEV-only, D-002)`);
}
const chrome = process.env.CHROME_PATH;
if (!chrome) {
  throw new Error('CHROME_PATH is not set: the e2e specs use the system Chrome (browsers are never downloaded)');
}

export default defineConfig({
  testDir: resolve(here, 'src/features'),
  testMatch: ['**/e2e/**/*.spec.ts'],
  testIgnore: ['**/*.test.ts', '**/*.test.tsx'],
  outputDir: resolve(repoRoot, 'reports/playwright/artifacts'),
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 90_000,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.PLAYWRIGHT_JSON_OUTPUT_NAME ?? resolve(repoRoot, 'reports/playwright/results.json') }],
  ],
  use: {
    baseURL,
    locale: 'es-CO',
    timezoneId: 'America/Bogota',
    colorScheme: 'light',
    reducedMotion: 'reduce',
    trace: 'off',
    launchOptions: { executablePath: chrome, args: ['--headless=new', '--no-sandbox'] },
  },
});
