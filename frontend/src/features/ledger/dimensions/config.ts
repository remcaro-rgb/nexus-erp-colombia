// Dimension tabs of Contabilidad › Dimensiones contables (owner P05, UI-CONTRACT §18, module-registry submenu "dimensiones").
export type DimensionKey = "cost_center" | "business_unit" | "aux_abierto" | "item_contable";

export interface DimensionTab {
  key: DimensionKey;
  /** Tab id = last route segment (/m/contabilidad/dimensiones/{tab}). */
  tab: string;
  /** API slug: /api/v1/ledger/dimensions/{slug}. */
  slug: string;
  label: string;
  singular: string;
  lineSlot: string;
}

export const DIMENSION_TABS: readonly DimensionTab[] = [
  { key: "cost_center", tab: "centros-costo", slug: "cost-centers", label: "Centros de costo", singular: "centro de costo", lineSlot: "cost_center_id" },
  { key: "business_unit", tab: "unidades-negocio", slug: "business-units", label: "Unidades de negocio", singular: "unidad de negocio", lineSlot: "business_unit_id" },
  { key: "aux_abierto", tab: "auxiliares-abiertos", slug: "aux-abierto", label: "Auxiliares abiertos", singular: "auxiliar abierto", lineSlot: "aux_abierto_id" },
  { key: "item_contable", tab: "items-contables", slug: "item-contable", label: "Ítems contables", singular: "ítem contable", lineSlot: "item_dimension_id" },
] as const;

export const DIMENSIONS_ROUTE = "/m/contabilidad/dimensiones";

export function tabById(tab: string | undefined): DimensionTab {
  return DIMENSION_TABS.find((t) => t.tab === tab) ?? DIMENSION_TABS[0]!;
}
