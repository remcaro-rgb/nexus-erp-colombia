// Module descriptor "compras" (owner P11; P11a core) — mirrors docs/spec/ui/module-registry.json › modules[id=compras]
// exactly (registry conformance test in __tests__/module.test.ts). The P08 shell renders the sidebar item, module bar,
// submenus, tabs and quick action; this file only plugs the pages in (lazy) — no module renders its own nav.
import { lazy } from "react";
import type { ModuleDescriptor } from "@shell/registry";

/** Registry entry, verbatim. */
export const comprasRegistry = {
  id: "compras",
  label: "Compras",
  icon: "shopping-cart",
  group: "Operaciones",
  owner_task: "P11",
  descriptor_file: "frontend/src/features/purchasing/module.ts",
  route: "/m/compras",
  permission: "purchasing.read",
  nav_visibility: "hidden when the user lacks the module permission",
  submenus: [
    {
      id: "resumen",
      label: "Resumen",
      route: "/m/compras",
      permission: "purchasing.read",
    },
    {
      id: "solicitudes",
      label: "Solicitudes internas",
      route: "/m/compras/solicitudes",
      permission: "purchasing.requisition.read",
      tabs: [
        {
          id: "mis-solicitudes",
          label: "Mis solicitudes",
          route: "/m/compras/solicitudes/mias",
          permission: "purchasing.requisition.read",
          feature: "F-INV-REQ",
        },
        {
          id: "por-aprobar",
          label: "Por aprobar",
          route: "/m/compras/solicitudes/por-aprobar",
          permission: "purchasing.requisition.approve",
          feature: "F-INV-REQ",
        },
        {
          id: "consolidacion",
          label: "Consolidación",
          route: "/m/compras/solicitudes/consolidacion",
          permission: "purchasing.requisition.consolidate",
          feature: "F-INV-REQ",
        },
        {
          id: "abastecimiento",
          label: "Abastecimiento",
          route: "/m/compras/solicitudes/abastecimiento",
          permission: "purchasing.requisition.source",
          feature: "F-INV-REQ",
        },
      ],
    },
    {
      id: "cotizaciones",
      label: "Cotizaciones",
      route: "/m/compras/cotizaciones",
      permission: "purchasing.rfq.read",
      tabs: [
        {
          id: "rfq",
          label: "Solicitudes de cotización",
          route: "/m/compras/cotizaciones/rfq",
          permission: "purchasing.rfq.read",
          feature: "F-PUR-CYCLE",
        },
        {
          id: "recibidas",
          label: "Cotizaciones recibidas",
          route: "/m/compras/cotizaciones/recibidas",
          permission: "purchasing.rfq.read",
          feature: "F-PUR-CYCLE",
        },
        {
          id: "comparacion",
          label: "Comparación",
          route: "/m/compras/cotizaciones/comparacion",
          permission: "purchasing.rfq.read",
          feature: "F-PUR-CYCLE",
        },
      ],
    },
    {
      id: "ordenes",
      label: "Órdenes y recepciones",
      route: "/m/compras/ordenes",
      permission: "purchasing.read",
      tabs: [
        {
          id: "ordenes",
          label: "Órdenes de compra",
          route: "/m/compras/ordenes/lista",
          permission: "purchasing.read",
          feature: "F-PUR-CYCLE",
        },
        {
          id: "recepciones",
          label: "Recepciones",
          route: "/m/compras/ordenes/recepciones",
          permission: "purchasing.receive",
          feature: "F-PUR-CYCLE",
        },
      ],
    },
    {
      id: "facturas",
      label: "Facturas de proveedor",
      route: "/m/compras/facturas",
      permission: "payables.read",
    },
    {
      id: "proveedores",
      label: "Proveedores",
      route: "/m/compras/proveedores",
      permission: "party.read",
    },
  ],
  dashboard: {
    route: "/m/compras",
    kpis: [
      {
        id: "compras_periodo",
        label: "Compras del periodo",
        formula: "Σ subtotal de facturas de proveedor contabilizadas en el periodo",
        unit: "COP",
        semantics: "flow",
        comparison: "previous_period",
        zero_denominator: "not_applicable",
        permission: "purchasing.kpi.read",
        provider_task: "P11",
        source: "AP invoices",
        drill: "/m/compras/facturas?period={period}",
      },
      {
        id: "oc_por_aprobar",
        label: "Órdenes por aprobar",
        formula: "Número de órdenes de compra en estado pendiente de aprobación",
        unit: "count",
        semantics: "snapshot",
        comparison: "none",
        zero_denominator: "not_applicable",
        permission: "purchasing.read",
        provider_task: "P11",
        source: "purchase orders",
        drill: "/m/compras/ordenes?estado=por_aprobar",
      },
      {
        id: "cxp",
        label: "Cuentas por pagar",
        formula: "Σ saldo abierto de CxP a la fecha de corte",
        unit: "COP",
        semantics: "snapshot",
        comparison: "previous_period",
        zero_denominator: "not_applicable",
        permission: "payables.kpi.read",
        provider_task: "P11",
        source: "AP open items",
        drill: "/m/compras/facturas?abiertas=1",
      },
      {
        id: "cxp_vencidas",
        label: "CxP vencidas",
        formula: "Σ saldo de CxP con vencimiento < fecha de corte",
        unit: "COP",
        semantics: "snapshot",
        comparison: "previous_period",
        zero_denominator: "not_applicable",
        permission: "payables.kpi.read",
        provider_task: "P11",
        source: "AP open items",
        drill: "/m/compras/facturas?vencidas=1",
      },
      {
        id: "dpo",
        label: "Días de pago (DPO)",
        formula: "Saldo promedio de CxP ÷ (compras a crédito del periodo ÷ días del periodo)",
        unit: "days",
        semantics: "flow",
        comparison: "previous_period",
        zero_denominator: "show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN)",
        permission: "payables.kpi.read",
        provider_task: "P11",
        source: "AP",
        drill: "/m/compras/facturas",
      },
    ],
    panels: [
      {
        id: "compras_mensuales",
        title: "Compras por mes",
        type: "line",
        series: [
          {
            id: "compras",
            style: "chart.revenue",
          },
          {
            id: "pagos",
            style: "chart.expenses",
          },
        ],
        provider_task: "P11",
        permission: "purchasing.kpi.read",
        drill: "/m/compras/facturas?month={x}",
      },
    ],
    empty_state: "Sin compras en el periodo. Acciones: 'Nueva orden de compra', 'Crear proveedor'.",
  },
  quick_action: {
    id: "nueva_oc",
    label: "Nueva orden de compra",
    permission: "purchasing.write",
    action: "route:/m/compras/ordenes/nueva",
  },
} as const;

/** Pages per "submenu/tab" (P11b adds the requisition and supplier-portal pages from its own paths). */
export const purchasingDescriptor: ModuleDescriptor = {
  id: "compras",
  pages: {
    "cotizaciones": lazy(() => import("./rfq/RfqListPage")),
    "cotizaciones/rfq": lazy(() => import("./rfq/RfqListPage")),
    "cotizaciones/recibidas": lazy(() => import("./rfq/QuotesPage")),
    "cotizaciones/comparacion": lazy(() => import("./rfq/ComparisonPage")),
    "ordenes": lazy(() => import("./orders/OrdersPage")),
    "ordenes/ordenes": lazy(() => import("./orders/OrdersPage")),
    "ordenes/recepciones": lazy(() => import("./orders/ReceiptsPage")),
  },
  actions: [
    { id: "nueva_rfq", label: "Nueva solicitud de cotización", route: "/m/compras/cotizaciones/rfq", permission: "purchasing.rfq.create" },
  ],
};

export default purchasingDescriptor;
