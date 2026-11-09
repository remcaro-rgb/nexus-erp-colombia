# P22 end-to-end harness (Playwright)

Owner: P22 (harness P22a; journeys P22b/P22d; visual + a11y P22a/P22d per D-P22 and UI-CONTRACT §9).

* `playwright.config.ts` — system Chrome (`$CHROME_PATH`), loopback target only (`ERP_BASE_URL`, default
  `http://127.0.0.1:${ERP_DEV_PORT:-8080}`), `es-CO`, `America/Bogota`, reduced motion, light scheme, 1440×900,
  traces/screenshots on failure, JSON → `reports/playwright/results.json`, JUnit → `reports/junit/playwright.xml`.
  Projects: `journeys` (`journeys/**`, `dev-smoke.spec.ts`), `visual` (`visual/**`), `a11y` (`a11y/**`).
* `support/fixtures.ts` — frozen clock (2026-11-09 10:00 Bogotá, auto), `expectDevBanner`, `checkA11y` (axe-core,
  serious/critical fail). `support/locale.ts` — COP/es-CO formatting for assertions.
* `harness-selftest.mjs` — no app needed: `node tests/e2e/harness-selftest.mjs` proves the system Chrome runs with the
  same context options (locale, time zone, number format, reduced motion).

Run (once `frontend/package.json` with `@playwright/test` exists — P08):

```bash
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 pnpm --dir frontend exec playwright test -c ../tests/e2e/playwright.config.ts
```

Naming: test titles carry the registry test id and the user requirements, e.g.
`test('[T-PORTAL-SUP-06] proveedor ve solo sus órdenes @UR-21', …)`; `docs/traceability.csv` picks up `@UR-xx`.
Visual baselines are never auto-updated (D-022): `--update-snapshots` is an integrator decision with a recorded reason.
