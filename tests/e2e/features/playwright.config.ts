// Playwright config of the P22c feature e2e specs (T-*-0x UI acceptance tests of FEATURES-ADDENDUM).
// Run through the pytest runner (tests/cross_module/test_xm_e2e_features.py), which builds the world and the live API:
//   pnpm --dir frontend exec playwright test --config ../tests/e2e/features/playwright.config.ts --grep T-TRE-RC-08
// DEV-only: system Chrome ($CHROME_PATH, never downloaded), loopback only (D-002), es-CO / America/Bogota.
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const chrome = process.env.CHROME_PATH;
if (!chrome) throw new Error("CHROME_PATH is not set: the e2e specs use the system Chrome");

export default defineConfig({
  testDir: here,
  testMatch: ["**/*.spec.ts"],
  outputDir: resolve(here, "../../../reports/playwright/p22c-artifacts"),
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["json", { outputFile: process.env.PLAYWRIGHT_JSON_OUTPUT_NAME ?? resolve(here, "../../../reports/playwright/p22c.json") }]],
  use: {
    locale: "es-CO",
    timezoneId: "America/Bogota",
    colorScheme: "light",
    reducedMotion: "reduce",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    launchOptions: { executablePath: chrome, args: ["--headless=new", "--no-sandbox"] },
  },
});
