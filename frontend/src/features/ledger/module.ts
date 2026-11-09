// Module descriptor "contabilidad" (owner P05) — conforms to docs/spec/ui/module-registry.json › modules[id=contabilidad].
// The shell (P08) renders the sidebar item, module bar, submenus, tabs and quick action from this descriptor; routes are lazy.
import { lazy } from "react";
import type { RegistryModule } from "@lib/api/types";
import { DIMENSION_TABS, DIMENSIONS_ROUTE } from "./dimensions/config";

export const ledgerModule: RegistryModule = {
  id: "contabilidad",
  label: "Contabilidad",
  icon: "landmark",
  group: "Finanzas",
  route: "/m/contabilidad",
  permission: "ledger.read",
  owner_task: "P05",
  submenus: [
    { id: "resumen", label: "Resumen", route: "/m/contabilidad", permission: "ledger.read" },
    { id: "mayor", label: "Libro mayor", route: "/m/contabilidad/mayor", permission: "ledger.read" },
    { id: "comprobantes", label: "Comprobantes", route: "/m/contabilidad/comprobantes", permission: "ledger.read" },
    { id: "cierres", label: "Cierres de periodo", route: "/m/contabilidad/cierres", permission: "ledger.close" },
    {
      id: "dimensiones",
      label: "Dimensiones contables",
      route: DIMENSIONS_ROUTE,
      permission: "ledger.dimension.read",
      owner_task: "P05",
      tabs: DIMENSION_TABS.map((d) => ({
        id: d.tab,
        label: d.label,
        route: `${DIMENSIONS_ROUTE}/${d.tab}`,
        permission: "ledger.dimension.read",
        feature: "F-ACC-DIM",
      })),
    },
  ],
  quick_action: { label: "Nuevo comprobante", route: "/m/contabilidad/comprobantes/nuevo", permission: "ledger.post" },
};

/** Route table contributed to the shell router (paths relative to the app root). */
export const ledgerRoutes = [
  { path: `${DIMENSIONS_ROUTE}`, component: lazy(() => import("./dimensions/DimensionsRoute")) },
  { path: `${DIMENSIONS_ROUTE}/:tab`, component: lazy(() => import("./dimensions/DimensionsRoute")) },
] as const;

export default ledgerModule;
