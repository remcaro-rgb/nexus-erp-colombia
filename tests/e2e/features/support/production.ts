// P22c group C helpers (Inventario › Producción): row actions with the reason prompt, status message, stock check.
import { expect, type Page } from "@playwright/test";
import type { App } from "./stack";

/** The action message of the page (the last role=status that is not the live quantity balance). */
export function message(page: Page, startsWith: string) {
  return page.getByRole("main").getByRole("status").filter({ hasText: new RegExp(`^${startsWith}`) });
}

/** Click a row action button (`<Label> <number>`); answers the reason prompt when `reason` is given. */
export async function rowAction(page: Page, label: string, reason?: string): Promise<void> {
  const button = page.getByRole("button", { name: label, exact: true });
  if (reason !== undefined) page.once("dialog", (d) => void d.accept(reason));
  await button.click();
}

/** Existencias › Por presentación for one item: packages of `presentation` (optional), loose and canonical stock. */
export async function expectStock(
  app: App,
  itemLabel: string,
  expected: { canonical: string; loose?: string; presentation?: { code: string; packages: string } },
): Promise<void> {
  const { page } = app;
  await app.open("/m/inventario/existencias/por-presentacion");
  await page.getByRole("combobox", { name: "Producto", exact: true }).selectOption({ label: itemLabel });
  const table = page.getByRole("table", { name: "Stock empacado por presentación y suelto" });
  await expect(table.getByRole("row").filter({ hasText: "Existencia canónica" }).getByRole("cell").last()).toHaveText(expected.canonical);
  if (expected.loose !== undefined) {
    await expect(table.getByRole("row").filter({ hasText: "Suelto" }).getByRole("cell").nth(4)).toHaveText(expected.loose);
  }
  if (expected.presentation) {
    const row = table.getByRole("row").filter({ hasText: expected.presentation.code });
    await expect(row.getByRole("cell").nth(2)).toHaveText(expected.presentation.packages);
  }
}
