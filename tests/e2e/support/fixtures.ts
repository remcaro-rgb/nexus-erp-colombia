/**
 * Shared Playwright fixtures for the P22 suites.
 *
 * - frozen clock (2026-11-09 10:00 America/Bogota) for deterministic screens (UI-CONTRACT §9);
 * - `expectDevBanner(page)`: the non-dismissible DEV banner (D-P08) is visible;
 * - `checkA11y(page)`: axe-core WCAG 2.2 AA scan; serious/critical violations fail (shared/10 §E).
 *   axe-core is resolved from the frontend's node_modules (P08 owns package.json; CCR P22a-5).
 *
 * Test titles carry the registry test id and user requirements: `[T-PORTAL-SUP-06] ... @UR-20`.
 */
import { test as base, expect, type Page } from '@playwright/test';
import { createRequire } from 'node:module';

export const FROZEN_NOW = new Date('2026-11-09T15:00:00Z'); // 10:00 in America/Bogota
export const DEV_BANNER_TEXT =
  'AMBIENTE DE DESARROLLO — DATOS SINTÉTICOS — NO VÁLIDO PARA EFECTOS LEGALES O TRIBUTARIOS';

export async function expectDevBanner(page: Page): Promise<void> {
  await expect(page.getByText(DEV_BANNER_TEXT, { exact: true })).toBeVisible();
}

export type AxeViolation = { id: string; impact: string | null; nodes: unknown[] };

export async function checkA11y(page: Page, label: string): Promise<AxeViolation[]> {
  const require = createRequire(import.meta.url);
  let source: string;
  try {
    source = require('node:fs').readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
  } catch {
    throw new Error('axe-core is not installed in the frontend workspace (CCR P22a-5)');
  }
  await page.addScriptTag({ content: source });
  const result = (await page.evaluate(async () => {
    // @ts-expect-error axe is injected above
    return await window.axe.run(document, { runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] });
  })) as { violations: AxeViolation[] };
  const blocking = result.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  expect(blocking, `${label}: serious/critical axe violations`).toEqual([]);
  return result.violations;
}

export const test = base.extend<{ frozenClock: void }>({
  frozenClock: [
    async ({ page }, use) => {
      await page.clock.install({ time: FROZEN_NOW });
      await use();
    },
    { auto: true },
  ],
});

export { expect };
