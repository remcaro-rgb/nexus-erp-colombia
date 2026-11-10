// Playwright config for T-ACC-DIM-08 only (owner P05). DEV-only: system Chrome ($CHROME_PATH), no browser download,
// loopback Vite started by the spec itself. Used by backend/ledger/tests/test_dimension_e2e.py so the result does not
// depend on what a config-less `playwright test` run of the whole frontend tree happens to collect (CCR P05-5).
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  testDir: here,
  testMatch: /T-ACC-DIM-08\..*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  reporter: [["list"]],
  use: {
    trace: "off",
    locale: "es-CO",
    timezoneId: "America/Bogota",
    launchOptions: { executablePath: process.env.CHROME_PATH || undefined, args: ["--no-sandbox"] },
  },
});
