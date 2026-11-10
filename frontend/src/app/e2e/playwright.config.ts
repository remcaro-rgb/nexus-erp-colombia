// Playwright config for the P08 shell smoke e2e (owner P08). System Chrome only ($CHROME_PATH; no browser download),
// loopback dev server, JSON report at reports/playwright/results.json. The full visual/a11y suites are P22's
// (tests/e2e/**); this config only runs files named *.e2e.ts under frontend/src.
import { defineConfig } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

const port = Number(process.env.ERP_E2E_PORT ?? 5179);
const repoRoot = resolve(__dirname, "../../../..");

export default defineConfig({
  testDir: resolve(__dirname, "../.."),
  testMatch: /.*\.e2e\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [
    ["list"],
    ["json", { outputFile: process.env.PLAYWRIGHT_JSON_OUTPUT_NAME ?? resolve(repoRoot, "reports/playwright/results.json") }],
  ],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "off",
    locale: "es-CO",
    timezoneId: "America/Bogota",
    colorScheme: "light",
    reducedMotion: "reduce",
    viewport: { width: 1440, height: 900 },
    launchOptions: { executablePath: process.env.CHROME_PATH || undefined, args: ["--no-sandbox"] },
  },
  webServer: {
    command: `pnpm exec vite --host 127.0.0.1 --port ${port} --strictPort`,
    cwd: resolve(__dirname, "../../.."),
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
