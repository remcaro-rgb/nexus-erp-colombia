// Module descriptor "ventas" (owner P10; P10a core) — mirrors docs/spec/ui/module-registry.json › modules[id=ventas]
// exactly (registry conformance test in __tests__/module.test.ts). The P08 shell renders the sidebar item, module bar,
// submenus, tabs and quick action; this file only plugs the pages in (lazy). The CRM pages live in features/crm and are
// mounted under the "oportunidades" submenu of this module (the registry has no separate CRM module).
import { lazy } from "react";
import type { ModuleDescriptor } from "@shell/registry";

/** Registry entry, verbatim. */
export const ventasRegistry = {
  id: "ventas",
  label: "Ventas y CRM",
  icon: "users",
  group: "Comercial",
  owner_task: "P10",
  descriptor_file: "frontend/src/features/sales/module.ts",
  route: "/m/ventas",
  permission: "sales.read",
  nav_visibility: "hidden when the user lacks the module permission",
  submenus: [
    {
      id: "resumen",
      label: "Resumen",
      route: "/m/ventas",
      permission: "sales.read",
    },
    {
      id: "oportunidades",
      label: "Oportunidades",
      route: "/m/ventas/oportunidades",
      permission: "crm.read",
    },
    {
      id: "clientes",
      label: "Clientes y precios",
      route: "/m/ventas/clientes",
      permission: "party.read",
      tabs: [
        {
          id: "clientes",
          label: "Clientes",
          route: "/m/ventas/clientes/lista",
          permission: "party.read",
          feature: "F-PORTAL-CUS",
        },
        {
          id: "formulas-precio",
          label: "Fórmulas de precio",
          route: "/m/ventas/clientes/formulas-precio",
          permission: "sales.pricing.read",
          feature: "F-SAL-PRICING",
        },
      ],
    },
    {
      id: "pedidos",
      label: "Pedidos",
      route: "/m/ventas/pedidos",
      permission: "sales.read",
    },
    {
      id: "facturas",
      label: "Facturas de venta",
      route: "/m/ventas/facturas",
      permission: "sales.read",
    },
    {
      id: "cartera",
      label: "Cartera",
      route: "/m/ventas/cartera",
      permission: "receivables.read",
    },
  ],
  dashboard: {
    route: "/m/ventas",
    kpis: [
      {
        id: "ventas_netas_v",
        label: "Ventas netas",
        formula: "Σ subtotal de facturas de venta contabilizadas − notas crédito del periodo (sin impuestos)",
        unit: "COP",
        semantics: "flow",
        comparison: "previous_period",
        zero_denominator: "not_applicable",
        permission: "sales.kpi.read",
        provider_task: "P10",
        source: "sales invoices",
        drill: "/m/ventas/facturas?period={period}",
      },
      {
        id: "pedidos_abiertos",
        label: "Pedidos abiertos",
        formula: "Número de pedidos confirmados no facturados totalmente a la fecha de corte",
        unit: "count",
        semantics: "snapshot",
        comparison: "previous_period",
        zero_denominator: "not_applicable",
        permission: "sales.read",
        provider_task: "P10",
        source: "sales orders",
        drill: "/m/ventas/pedidos?estado=abierto",
      },
      {
        id: "pipeline_ponderado",
        label: "Pipeline ponderado",
        formula: "Σ valor × probabilidad registrada de oportunidades abiertas a la fecha de corte",
        unit: "COP",
        semantics: "snapshot",
        comparison: "previous_period",
        zero_denominator: "not_applicable",
        permission: "crm.read",
        provider_task: "P10",
        source: "CRM opportunities",
        drill: "/m/ventas/oportunidades",
      },
      {
        id: "cartera_vencida_v",
        label: "Cartera vencida",
        formula: "Σ saldo de CxC vencido a la fecha de corte",
        unit: "COP",
        semantics: "snapshot",
        comparison: "previous_period",
        zero_denominator: "not_applicable",
        permission: "receivables.kpi.read",
        provider_task: "P10",
        source: "AR open items",
        drill: "/m/ventas/cartera?vencida=1",
      },
      {
        id: "dso",
        label: "Días de cartera (DSO)",
        formula: "Saldo promedio de CxC del periodo ÷ (ventas a crédito del periodo ÷ días del periodo)",
        unit: "days",
        semantics: "flow",
        comparison: "previous_period",
        zero_denominator: "show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN)",
        permission: "receivables.kpi.read",
        provider_task: "P10",
        source: "AR + sales invoices",
        drill: "/m/ventas/cartera",
      },
    ],
    panels: [
      {
        id: "ventas_mensuales",
        title: "Ventas netas por mes",
        type: "line",
        series: [
          {
            id: "ventas",
            style: "chart.revenue",
          },
          {
            id: "notas_credito",
            style: "chart.expenses",
          },
        ],
        provider_task: "P10",
        permission: "sales.kpi.read",
        drill: "/m/ventas/facturas?month={x}",
      },
      {
        id: "embudo",
        title: "Oportunidades por etapa",
        type: "bar",
        provider_task: "P10",
        permission: "crm.read",
        drill: "/m/ventas/oportunidades?etapa={x}",
      },
    ],
    empty_state: "Sin ventas en el periodo. Acciones: 'Nueva oportunidad', 'Crear cliente', 'Importar clientes'.",
  },
  quick_action: {
    id: "nueva_oportunidad",
    label: "Nueva oportunidad",
    permission: "crm.write",
    action: "route:/m/ventas/oportunidades/nueva",
  },
};

/** Pages per "submenu/tab" (P10b adds the customer-portal pages from its own paths). */
export const salesDescriptor: ModuleDescriptor = {
  id: "ventas",
  pages: {
    oportunidades: lazy(() => import("../crm/OpportunitiesPage")),
    clientes: lazy(() => import("./customers/CustomersPage")),
    "clientes/lista": lazy(() => import("./customers/CustomersPage")),
    "clientes/formulas-precio": lazy(() => import("./pricing/PriceFormulasPage")),
    pedidos: lazy(() => import("./orders/OrdersPage")),
    facturas: lazy(() => import("./orders/InvoicesPage")),
    cartera: lazy(() => import("./receivables/ReceivablesPage")),
  },
  actions: [
    { id: "nueva_oportunidad", label: "Nueva oportunidad", route: "/m/ventas/oportunidades/nueva", permission: "crm.write" },
    { id: "nuevo_pedido", label: "Nuevo pedido de venta", route: "/m/ventas/pedidos", permission: "sales.order.create" },
  ],
};

export default salesDescriptor;
