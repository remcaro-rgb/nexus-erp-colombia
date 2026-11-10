// Pure helpers for the recipes screen (no I/O, no floats): version grouping, activation pre-checks, explosion rows.
import { fromScaled, toScaled } from "../model";
import type { BomAction, BomDto, BomInput, ExplosionNodeDto } from "./api";

/** Versions grouped by recipe code, newest first (the list shows one row per version). */
export function groupVersions(boms: BomDto[]): Array<{ code: string; versions: BomDto[] }> {
  const by = new Map<string, BomDto[]>();
  for (const b of boms) {
    const key = b.code.toLowerCase();
    by.set(key, [...(by.get(key) ?? []), b]);
  }
  return [...by.values()]
    .map((versions) => ({ code: versions[0]!.code, versions: [...versions].sort((a, b) => b.version_no - a.version_no) }))
    .sort((a, b) => a.code.localeCompare(b.code));
}

export function bomActions(status: BomDto["bom_status"]): BomAction[] {
  if (status === "DRAFT") return ["activate", "copy-version", "obsolete"];
  if (status === "ACTIVE") return ["copy-version", "obsolete"];
  return ["copy-version"];
}

/** Client-side validation report before :activate (the server re-checks and owns cycles/overlaps). */
export function validateBom(input: BomInput): string[] {
  const errors: string[] = [];
  const y = toScaled(input.yield_percent || "0");
  if (y <= 0n || y > toScaled("100")) errors.push("El rendimiento debe estar entre 0 y 100 %.");
  if (toScaled(input.output_qty || "0") <= 0n) errors.push("La cantidad de salida debe ser positiva.");
  if (input.components.lines.length === 0) errors.push("La receta necesita al menos un componente.");
  for (const [i, ln] of input.components.lines.entries()) {
    if (ln.item_id === input.item_id) errors.push(`Componente ${i + 1}: el producto no puede contenerse a sí mismo.`);
    if (toScaled(ln.quantity || "0") <= 0n) errors.push(`Componente ${i + 1}: cantidad inválida.`);
  }
  if (input.valid_to && input.valid_to < input.valid_from) errors.push("La vigencia final es anterior a la inicial.");
  return errors;
}

export interface ExplosionRow {
  depth: number;
  code: string;
  quantity: string;
  byproduct: boolean;
}

export function flattenExplosion(node: ExplosionNodeDto, out: ExplosionRow[] = []): ExplosionRow[] {
  for (const c of node.children) {
    out.push({ depth: c.level, code: c.item_code, quantity: fromScaled(toScaled(c.quantity)), byproduct: c.is_byproduct });
    flattenExplosion(c, out);
  }
  return out;
}
