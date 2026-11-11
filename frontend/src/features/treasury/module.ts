// Module descriptor "tesoreria" (owner P16; P16a core) — mirrors docs/spec/ui/module-registry.json › modules[id=tesoreria]
// exactly (registry conformance test in __tests__/module.test.ts). The P08 shell renders the sidebar item, module bar,
// submenus, tabs and quick action; this file only plugs the pages in (lazy) — no module renders its own nav.
import { lazy } from "react";
import type { ModuleDescriptor } from "@shell/registry";

/** Registry entry, verbatim. */
export const tesoreriaRegistry = {
  "id": "tesoreria",
  "label": "Tesorería",
  "icon": "wallet",
  "group": "Finanzas",
  "owner_task": "P16",
  "descriptor_file": "frontend/src/features/treasury/module.ts",
  "route": "/m/tesoreria",
  "permission": "treasury.read",
  "nav_visibility": "hidden when the user lacks the module permission",
  "submenus": [
    {
      "id": "resumen",
      "label": "Resumen",
      "route": "/m/tesoreria",
      "permission": "treasury.read"
    },
    {
      "id": "recibos",
      "label": "Recibos de caja",
      "route": "/m/tesoreria/recibos",
      "permission": "treasury.receipt.read",
      "tabs": [
        {
          "id": "recibos",
          "label": "Recibos",
          "route": "/m/tesoreria/recibos/lista",
          "permission": "treasury.receipt.read",
          "feature": "F-TRE-RC"
        },
        {
          "id": "conceptos",
          "label": "Conceptos de tesorería",
          "route": "/m/tesoreria/recibos/conceptos",
          "permission": "treasury.concept.read",
          "feature": "F-TRE-RC"
        }
      ]
    },
    {
      "id": "egresos",
      "label": "Comprobantes de egreso",
      "route": "/m/tesoreria/egresos",
      "permission": "treasury.disbursement.read",
      "tabs": [
        {
          "id": "comprobantes",
          "label": "Comprobantes",
          "route": "/m/tesoreria/egresos/lista",
          "permission": "treasury.disbursement.read",
          "feature": "F-TRE-CE"
        },
        {
          "id": "lotes",
          "label": "Lotes de pago",
          "route": "/m/tesoreria/egresos/lotes",
          "permission": "treasury.disbursement.read",
          "feature": "F-TRE-CE"
        }
      ]
    },
    {
      "id": "bancos",
      "label": "Bancos y cajas",
      "route": "/m/tesoreria/bancos",
      "permission": "treasury.read"
    },
    {
      "id": "conciliacion",
      "label": "Conciliación bancaria",
      "route": "/m/tesoreria/conciliacion",
      "permission": "treasury.reconcile"
    },
    {
      "id": "intercompania",
      "label": "Intercompañía y consolidación",
      "route": "/m/tesoreria/intercompania",
      "permission": "consolidation.read"
    }
  ],
  "dashboard": {
    "route": "/m/tesoreria",
    "kpis": [
      {
        "id": "saldo_disponible",
        "label": "Saldo disponible",
        "formula": "Σ saldo contable de cuentas bancarias y cajas de la empresa a la fecha de corte",
        "unit": "COP",
        "semantics": "snapshot",
        "comparison": "previous_period",
        "zero_denominator": "not_applicable",
        "permission": "treasury.kpi.read",
        "provider_task": "P16",
        "source": "ledger balances of treasury accounts",
        "drill": "/m/tesoreria/bancos"
      },
      {
        "id": "recaudos_periodo",
        "label": "Recaudos",
        "formula": "Σ recibos de caja contabilizados en el periodo",
        "unit": "COP",
        "semantics": "flow",
        "comparison": "previous_period",
        "zero_denominator": "not_applicable",
        "permission": "treasury.kpi.read",
        "provider_task": "P16",
        "source": "receipts",
        "drill": "/m/tesoreria/recibos/lista"
      },
      {
        "id": "pagos_periodo",
        "label": "Pagos",
        "formula": "Σ egresos contabilizados en el periodo",
        "unit": "COP",
        "semantics": "flow",
        "comparison": "previous_period",
        "zero_denominator": "not_applicable",
        "permission": "treasury.kpi.read",
        "provider_task": "P16",
        "source": "disbursements",
        "drill": "/m/tesoreria/egresos/lista"
      },
      {
        "id": "partidas_sin_conciliar",
        "label": "Partidas sin conciliar",
        "formula": "Número de líneas de extracto importado sin cruce contable a la fecha de corte",
        "unit": "count",
        "semantics": "snapshot",
        "comparison": "previous_period",
        "zero_denominator": "not_applicable",
        "permission": "treasury.reconcile",
        "provider_task": "P16",
        "source": "imported bank statements (CSV, no live bank)",
        "drill": "/m/tesoreria/conciliacion"
      },
      {
        "id": "saldos_intercompania",
        "label": "Diferencias intercompañía",
        "formula": "Σ |saldo por cobrar a vinculada − saldo por pagar espejo| entre empresas del grupo a la fecha de corte",
        "unit": "COP",
        "semantics": "snapshot",
        "comparison": "none",
        "zero_denominator": "not_applicable",
        "permission": "consolidation.read",
        "provider_task": "P16",
        "source": "intercompany balances",
        "drill": "/m/tesoreria/intercompania"
      }
    ],
    "panels": [
      {
        "id": "flujo_caja",
        "title": "Recaudos vs. pagos",
        "type": "line",
        "series": [
          {
            "id": "recaudos",
            "style": "chart.revenue"
          },
          {
            "id": "pagos",
            "style": "chart.expenses"
          }
        ],
        "provider_task": "P16",
        "permission": "treasury.kpi.read",
        "drill": "/m/tesoreria/egresos/lista?month={x}"
      }
    ],
    "empty_state": "No hay cuentas bancarias configuradas. Acción: 'Crear cuenta bancaria' o 'Importar extracto (CSV)'. En dev no existe conexión con bancos reales."
  },
  "quick_action": {
    "id": "nuevo_pago",
    "label": "Nuevo pago",
    "permission": "treasury.pay",
    "action": "route:/m/tesoreria/egresos/nuevo"
  }
} as const;

/** Pages per "submenu/tab" (F-TRE-RC / F-TRE-CE). P16b plugs the intercompany page from its own paths. */
export const treasuryDescriptor: ModuleDescriptor = {
  id: "tesoreria",
  pages: {
    "recibos": lazy(() => import("./vouchers/ReceiptsPage")),
    "recibos/recibos": lazy(() => import("./vouchers/ReceiptsPage")),
    "recibos/conceptos": lazy(() => import("./vouchers/ConceptsPage")),
    "egresos": lazy(() => import("./vouchers/DisbursementsPage")),
    "egresos/comprobantes": lazy(() => import("./vouchers/DisbursementsPage")),
    "egresos/lotes": lazy(() => import("./vouchers/BatchesPage")),
  },
  actions: [
    { id: "nuevo_recibo", label: "Nuevo recibo de caja", route: "/m/tesoreria/recibos/lista", permission: "treasury.receipt.create" },
    { id: "nuevo_egreso", label: "Nuevo comprobante de egreso", route: "/m/tesoreria/egresos/lista", permission: "treasury.disbursement.create" },
  ],
};

export default treasuryDescriptor;
