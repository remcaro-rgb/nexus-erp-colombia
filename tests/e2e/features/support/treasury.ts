// P22c treasury e2e helpers (F-TRE-RC / F-TRE-CE): the VouchersPage entry form, rows and actions. DEV/test only.
import type { Page } from "@playwright/test";
import { expect, tabTo, type App } from "./stack";

export interface DraftLine {
  /** visible option of "Tipo línea N" */
  type: "Aplicación a factura" | "Concepto de tesorería" | "Retención" | "Anticipo";
  ref: string;
  amount: string;
}

/** Fill the "Nuevo …" entry form and save it with the keyboard (Tab to "Guardar borrador" + Enter). */
export async function createDraft(
  page: Page,
  formName: string,
  v: { party?: string; fundLabel: RegExp; method: string; lines: DraftLine[] },
): Promise<void> {
  const form = page.getByRole("form", { name: formName });
  await expect(form).toBeVisible();
  if (v.party) await form.getByLabel("Tercero (id)", { exact: true }).fill(v.party);
  const fund = form.getByLabel("Fondo (banco o caja)");
  await expect(fund.locator("option", { hasText: v.fundLabel })).toHaveCount(1);
  const fundValue = await fund.locator("option", { hasText: v.fundLabel }).getAttribute("value");
  await fund.selectOption(fundValue!);
  await form.getByLabel("Medio de pago", { exact: true }).selectOption({ label: v.method });
  for (let i = 0; i < v.lines.length; i++) {
    if (i > 0) await form.getByRole("button", { name: "Agregar línea" }).click();
    const l = v.lines[i];
    const n = i + 1;
    await form.getByLabel(`Tipo línea ${n}`, { exact: true }).selectOption({ label: l.type });
    const refLabel = l.type === "Retención" ? "Código de retención" : "Partida / concepto (id)";
    const valLabel = l.type === "Retención" ? "Base" : "Valor";
    const lineBox = form.getByRole("group", { name: "Líneas" }).locator(":scope > div").nth(i);
    if (l.ref) await lineBox.getByLabel(refLabel, { exact: true }).fill(l.ref);
    await lineBox.getByLabel(valLabel, { exact: true }).fill(l.amount);
  }
  await tabTo(page, form.getByRole("button", { name: "Guardar borrador" }));
  await page.keyboard.press("Enter");
}

/** Row of the voucher list table `table` containing `text`. */
export function row(page: Page, table: string, text: string | RegExp) {
  return page.getByRole("table", { name: table, exact: true }).getByRole("row").filter({ hasText: text });
}

/** Status cell (3rd column) of a voucher row. */
export function statusOf(r: ReturnType<typeof row>) {
  return r.getByRole("cell").nth(2);
}

/** Click an action of a row with the keyboard (focus + Enter). */
export async function keyAction(page: Page, r: ReturnType<typeof row>, name: string): Promise<void> {
  const b = r.getByRole("button", { name, exact: true });
  await b.focus();
  await expect(b).toBeFocused();
  await page.keyboard.press("Enter");
}

/** Print/PDF action of a voucher row, expected to yield an application/pdf document (download, popup or response). */
export async function printFromUi(page: Page, r: ReturnType<typeof row>): Promise<boolean> {
  const print = r.getByRole("button", { name: /Imprimir|PDF/i }).or(r.getByRole("link", { name: /Imprimir|PDF/i }));
  return (await print.count()) > 0;
}

/** The public-API PDF of a voucher (contract GET …/{id}/pdf): true when it serves application/pdf. */
export async function apiPdf(app: App, kind: "receipts" | "disbursements", id: string, user: string): Promise<{ ok: boolean; status: number; type: string }> {
  const res = await app.page.request.get(`${app.world.api_origin}/api/v1/treasury/${kind}/${id}/pdf`, {
    headers: { Authorization: `Bearer ${app.world.tokens[user]}`, Accept: "application/pdf", "X-Company-Id": app.world.data.company_id },
  });
  const type = res.headers()["content-type"] ?? "";
  const body = await res.body();
  return { ok: res.ok() && type.startsWith("application/pdf") && body.subarray(0, 5).toString() === "%PDF-", status: res.status(), type };
}
