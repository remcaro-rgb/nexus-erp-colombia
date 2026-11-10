// Pure view-model helpers for the inventory screens (no I/O). Quantities stay decimal strings end to end; arithmetic
// uses scaled BigInt (6 decimals, NUMERIC(20,6)) so no float ever touches a quantity (D-001).
import type { AvailabilityDto, LocationNodeDto, TransferAction, TransferDto } from "./api";

const SCALE = 6;
const FACTOR = 10n ** BigInt(SCALE);

/** "12.5" → 12500000n (6 decimals). Throws on non-decimal input. */
export function toScaled(value: string): bigint {
  const m = /^(-?)(\d+)(?:\.(\d{1,6}))?$/.exec(value.trim());
  if (!m) throw new Error(`invalid decimal ${value}`);
  const frac = (m[3] ?? "").padEnd(SCALE, "0");
  const n = BigInt(m[2] ?? "0") * FACTOR + BigInt(frac || "0");
  return m[1] === "-" ? -n : n;
}

export function fromScaled(n: bigint): string {
  const neg = n < 0n;
  const abs = neg ? -n : n;
  const int = abs / FACTOR;
  const frac = (abs % FACTOR).toString().padStart(SCALE, "0").replace(/0+$/, "");
  return `${neg ? "-" : ""}${int}${frac ? `.${frac}` : ""}`;
}

/** es-CO display: thousands with ".", decimals with "," (exact, from the string). */
export function formatQty(value: string): string {
  const s = fromScaled(toScaled(value));
  const [int, frac] = s.replace("-", "").split(".");
  const grouped = (int ?? "0").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${s.startsWith("-") ? "-" : ""}${grouped}${frac ? `,${frac}` : ""}`;
}

export interface FlatLocation {
  node: LocationNodeDto;
  depth: number;
  postable: boolean;
}

/** Depth-first rows of the bin tree (table view of the same data as the tree view). */
export function flattenTree(nodes: LocationNodeDto[], depth = 0, out: FlatLocation[] = []): FlatLocation[] {
  for (const n of [...nodes].sort((a, b) => a.code.localeCompare(b.code))) {
    out.push({ node: n, depth, postable: n.is_leaf && n.status === "ACTIVE" && n.location_type !== "VIEW" });
    flattenTree(n.children, depth + 1, out);
  }
  return out;
}

export interface PresentationTotal {
  presentation_id: string;
  code: string;
  factor: string;
  packages: string;
  available_packages: string;
  base: string;
}

/** Stock per presentation over all bins/lots; loose stays separate so nothing is counted twice (D-013). */
export function totalsByPresentation(rows: AvailabilityDto[]): { presentations: PresentationTotal[]; loose: string; canonical: string } {
  const map = new Map<string, { code: string; factor: string; packages: bigint; available: bigint }>();
  let loose = 0n;
  let canonical = 0n;
  for (const r of rows) {
    loose += toScaled(r.loose);
    canonical += toScaled(r.on_hand);
    for (const p of r.packaged) {
      const cur = map.get(p.presentation_id) ?? { code: p.code, factor: p.factor, packages: 0n, available: 0n };
      cur.packages += toScaled(p.packages);
      cur.available += toScaled(p.available_packages);
      map.set(p.presentation_id, cur);
    }
  }
  const presentations = [...map.entries()]
    .map(([id, v]) => ({
      presentation_id: id,
      code: v.code,
      factor: fromScaled(toScaled(v.factor)),
      packages: fromScaled(v.packages),
      available_packages: fromScaled(v.available),
      base: fromScaled((v.packages * toScaled(v.factor)) / FACTOR),
    }))
    .sort((a, b) => a.code.localeCompare(b.code));
  return { presentations, loose: fromScaled(loose), canonical: fromScaled(canonical) };
}

/** Σ packaged × factor + loose = canonical, and packaged ≤ canonical (reconciliation canonical vs packaged). */
export function reconciles(row: AvailabilityDto): boolean {
  const packaged = row.packaged.reduce((acc, p) => acc + (toScaled(p.packages) * toScaled(p.factor)) / FACTOR, 0n);
  const all = toScaled(row.packaged_base);
  return packaged <= all && all + toScaled(row.loose) === toScaled(row.on_hand) && all <= toScaled(row.on_hand);
}

const NEXT: Record<TransferDto["status"], TransferAction[]> = {
  DRAFT: ["submit", "cancel"],
  SUBMITTED: ["approve", "ship", "cancel"],
  APPROVED: ["ship", "cancel"],
  SHIPPED: ["receive"],
  RECEIVED: [],
  CANCELLED: [],
};

export function transferActions(status: TransferDto["status"]): TransferAction[] {
  return NEXT[status];
}

/** Client-side check before posting a bin-to-bin move (the API re-validates everything). */
export function validateMove(input: { item_id: string; from_location_id: string; to_location_id: string; quantity: string }): string | null {
  if (!input.item_id) return "Seleccione un producto";
  if (!input.from_location_id || !input.to_location_id) return "Seleccione las ubicaciones de origen y destino";
  if (input.from_location_id === input.to_location_id) return "El origen y el destino deben ser distintos";
  try {
    if (toScaled(input.quantity) <= 0n) return "La cantidad debe ser mayor que cero";
  } catch {
    return "Cantidad inválida";
  }
  return null;
}
