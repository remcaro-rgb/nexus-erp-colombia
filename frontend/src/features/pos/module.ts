// Module descriptor "pos" (owner P12) — conforms to docs/spec/ui/module-registry.json › modules[id=pos].
// The shell (P08) renders the sidebar item, module bar, submenus, the dashboard (registry KPIs from
// GET /api/v1/kpis/pos and the panel ventas_por_hora) and the quick action; this file contributes the pages, lazily.
import { lazy } from "react";
import type { RegistryModule } from "@lib/api/types";
import type { ModuleDescriptor } from "@shell/registry";

export const posModule: RegistryModule = {
  id: "pos",
  label: "Punto de venta",
  icon: "store",
  group: "Comercial",
  owner_task: "P12",
  route: "/m/pos",
  permission: "pos.read",
  submenus: [
    { id: "resumen", label: "Resumen", route: "/m/pos", permission: "pos.read" },
    { id: "vender", label: "Vender", route: "/m/pos/vender", permission: "pos.sell" },
    { id: "turnos", label: "Turnos y arqueos", route: "/m/pos/turnos", permission: "pos.read" },
    { id: "tiquetes", label: "Tiquetes", route: "/m/pos/tiquetes", permission: "pos.read" },
  ],
  quick_action: { id: "abrir_caja", label: "Abrir caja", permission: "pos.sell", action: "route:/m/pos/vender" },
};

const pages = {
  vender: lazy(() => import("./pages").then((m) => ({ default: m.SellPage }))),
  turnos: lazy(() => import("./pages").then((m) => ({ default: m.SessionsPage }))),
  tiquetes: lazy(() => import("./pages").then((m) => ({ default: m.TicketsPage }))),
};

const descriptor: ModuleDescriptor = {
  id: "pos",
  pages,
  actions: [{ id: "abrir_caja", label: "Abrir caja", route: "/m/pos/vender", permission: "pos.sell" }],
};

export default descriptor;
