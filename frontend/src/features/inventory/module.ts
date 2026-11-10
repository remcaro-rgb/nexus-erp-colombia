// Module descriptor "inventario" (owner P09; docs/spec/ui/module-registry.json › modules[id=inventario]).
// The P08 shell renders nav, submenus and tabs from the registry; this descriptor contributes the pages the inventory core
// (P09a) delivers. Pages of P09b (issues, barcodes) and P09c (BOM, transformations) are registered by those parts from their
// own folders; until then the shell shows its honest "Módulo aún no disponible" state.
import { lazy } from "react";
import type { ModuleDescriptor } from "@shell/registry";

const inventoryModule: ModuleDescriptor = {
  id: "inventario",
  pages: {
    "existencias/por-ubicacion": lazy(() => import("./pages/StockByLocationPage")),
    "existencias/por-presentacion": lazy(() => import("./pages/StockByPresentationPage")),
    "maestros/presentaciones": lazy(() => import("./pages/PresentationsPage")),
    "maestros/bodegas-ubicaciones": lazy(() => import("./pages/WarehousesPage")),
    "movimientos/traslados": lazy(() => import("./pages/TransfersPage")),
    "movimientos/entre-ubicaciones": lazy(() => import("./pages/InternalMovePage")),
  },
  actions: [{ id: "inventario.mover", label: "Mover entre ubicaciones", route: "/m/inventario/movimientos/entre-ubicaciones", permission: "inventory.move" }],
};

export default inventoryModule;
