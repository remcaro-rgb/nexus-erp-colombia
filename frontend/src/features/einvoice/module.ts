// Module descriptor "facturacion" (owner P15) — conforms to docs/spec/ui/module-registry.json › modules[id=facturacion].
// The shell (P08) renders the sidebar item, module bar, submenus, tabs, dashboard (registry KPIs from
// GET /api/v1/kpis/facturacion) and quick action; this file contributes the pages, lazily.
import { lazy } from "react";
import type { RegistryModule } from "@lib/api/types";
import type { ModuleDescriptor } from "@shell/registry";

export const facturacionModule: RegistryModule = {
  id: "facturacion",
  label: "Facturación electrónica",
  icon: "file-text",
  group: "Comercial",
  owner_task: "P15",
  route: "/m/facturacion",
  permission: "einvoice.read",
  submenus: [
    { id: "resumen", label: "Resumen", route: "/m/facturacion", permission: "einvoice.read" },
    { id: "documentos", label: "Documentos", route: "/m/facturacion/documentos", permission: "einvoice.read" },
    { id: "rechazos", label: "Rechazos y contingencias", route: "/m/facturacion/rechazos", permission: "einvoice.read" },
    {
      id: "numeracion",
      label: "Numeración y proveedores",
      route: "/m/facturacion/numeracion",
      permission: "einvoice.read",
      tabs: [
        {
          id: "proveedores-tecnologicos",
          label: "Proveedores tecnológicos",
          route: "/m/facturacion/numeracion/proveedores",
          permission: "einvoice.provider.read",
          feature: "F-PT-OPEN",
        },
      ],
    },
  ],
  quick_action: { id: "ver_documentos", label: "Ver documentos", permission: "einvoice.read", action: "route:/m/facturacion/documentos" },
};

const pages = {
  documentos: lazy(() => import("./pages").then((m) => ({ default: m.DocumentsPage }))),
  rechazos: lazy(() => import("./pages").then((m) => ({ default: m.RejectionsPage }))),
  numeracion: lazy(() => import("./pages").then((m) => ({ default: m.NumberingPage }))),
  "numeracion/proveedores-tecnologicos": lazy(() => import("./pages").then((m) => ({ default: m.ProvidersPage }))),
};

const descriptor: ModuleDescriptor = {
  id: "facturacion",
  pages,
  actions: [{ id: "ver_documentos", label: "Ver documentos", route: "/m/facturacion/documentos", permission: "einvoice.read" }],
};

export default descriptor;
