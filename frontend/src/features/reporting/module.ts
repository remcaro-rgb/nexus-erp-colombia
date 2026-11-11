// Module descriptor "reportes" (owner P17; P17a core) — mirrors docs/spec/ui/module-registry.json › modules[id=reportes]
// exactly (registry conformance test in __tests__/module.test.ts). The P08 shell renders nav, submenus and tabs; this
// file only plugs pages in (lazy). Extension point (frozen, reporting.interfaces): P17b contributes the standard-report
// tab pages from its own path `./standard/pages.ts` (export `standardPages: Record<"submenu/tab", LazyPage>`), picked
// up here when present; nothing is rendered for those tabs before it exists.
import { lazy } from "react";
import type { LazyPage, ModuleDescriptor } from "@shell/registry";

/** Registry entry, verbatim. */
export const reportesRegistry = {
  "id": "reportes",
  "label": "Reportes",
  "icon": "bar-chart-3",
  "group": "Análisis",
  "owner_task": "P17",
  "descriptor_file": "frontend/src/features/reporting/module.ts",
  "route": "/m/reportes",
  "permission": "reporting.read",
  "nav_visibility": "hidden when the user lacks the module permission",
  "submenus": [
    {
      "id": "resumen",
      "label": "Resumen",
      "route": "/m/reportes",
      "permission": "reporting.read"
    },
    {
      "id": "estados",
      "label": "Estados financieros",
      "route": "/m/reportes/estados",
      "permission": "reporting.financial",
      "tabs": [
        {
          "id": "balance-general",
          "label": "Balance general",
          "route": "/m/reportes/estados/balance-general",
          "permission": "reporting.financial",
          "feature": "F-ACC-REPORTS"
        },
        {
          "id": "estado-resultados",
          "label": "Estado de resultados",
          "route": "/m/reportes/estados/estado-resultados",
          "permission": "reporting.financial",
          "feature": "F-ACC-REPORTS"
        },
        {
          "id": "flujo-efectivo",
          "label": "Flujo de efectivo",
          "route": "/m/reportes/estados/flujo-efectivo",
          "permission": "reporting.financial",
          "feature": "F-ACC-REPORTS"
        },
        {
          "id": "estados-financieros",
          "label": "Estados financieros",
          "route": "/m/reportes/estados/paquete",
          "permission": "reporting.financial",
          "feature": "F-ACC-REPORTS"
        }
      ]
    },
    {
      "id": "auxiliares",
      "label": "Auxiliares",
      "route": "/m/reportes/auxiliares",
      "permission": "reporting.financial",
      "tabs": [
        {
          "id": "por-cuenta",
          "label": "Por cuenta",
          "route": "/m/reportes/auxiliares/por-cuenta",
          "permission": "reporting.financial",
          "feature": "F-ACC-REPORTS"
        },
        {
          "id": "por-centro-costo",
          "label": "Por centro de costo",
          "route": "/m/reportes/auxiliares/por-centro-costo",
          "permission": "reporting.financial",
          "feature": "F-ACC-REPORTS"
        },
        {
          "id": "por-tercero",
          "label": "Por tercero",
          "route": "/m/reportes/auxiliares/por-tercero",
          "permission": "reporting.financial",
          "feature": "F-ACC-REPORTS"
        }
      ]
    },
    {
      "id": "explorador",
      "label": "Explorador",
      "route": "/m/reportes/explorador",
      "permission": "reporting.read"
    },
    {
      "id": "programados",
      "label": "Programados",
      "route": "/m/reportes/programados",
      "permission": "reporting.write"
    },
    {
      "id": "documentos",
      "label": "Documentos",
      "route": "/m/reportes/documentos",
      "permission": "documents.read"
    }
  ],
  "dashboard": {
    "route": "/m/reportes",
    "kpis": [
      {
        "id": "reportes_disponibles",
        "label": "Reportes disponibles",
        "formula": "Número de definiciones de reporte que el usuario puede ejecutar",
        "unit": "count",
        "semantics": "snapshot",
        "comparison": "none",
        "zero_denominator": "not_applicable",
        "permission": "reporting.read",
        "provider_task": "P17",
        "source": "report catalog filtered by policy",
        "drill": "/m/reportes/explorador"
      },
      {
        "id": "ejecuciones",
        "label": "Ejecuciones del periodo",
        "formula": "Número de ejecuciones de reportes en el periodo (propias o compartidas visibles)",
        "unit": "count",
        "semantics": "flow",
        "comparison": "previous_period",
        "zero_denominator": "not_applicable",
        "permission": "reporting.read",
        "provider_task": "P17",
        "source": "report runs",
        "drill": "/m/reportes/programados"
      },
      {
        "id": "ejecuciones_fallidas",
        "label": "Ejecuciones fallidas",
        "formula": "Número de ejecuciones con error en el periodo",
        "unit": "count",
        "semantics": "flow",
        "comparison": "previous_period",
        "zero_denominator": "not_applicable",
        "permission": "reporting.read",
        "provider_task": "P17",
        "source": "report runs",
        "drill": "/m/reportes/programados?estado=error"
      },
      {
        "id": "programados_activos",
        "label": "Programados activos",
        "formula": "Número de reportes programados activos",
        "unit": "count",
        "semantics": "snapshot",
        "comparison": "none",
        "zero_denominator": "not_applicable",
        "permission": "reporting.write",
        "provider_task": "P17",
        "source": "schedules",
        "drill": "/m/reportes/programados"
      }
    ],
    "panels": [
      {
        "id": "ejecuciones_diarias",
        "title": "Ejecuciones por día",
        "type": "bar",
        "provider_task": "P17",
        "permission": "reporting.read",
        "drill": "/m/reportes/programados?dia={x}"
      }
    ],
    "empty_state": "Aún no hay ejecuciones. Acción: 'Generar balance de prueba' (/m/reportes/estados)."
  },
  "quick_action": {
    "id": "nuevo_reporte",
    "label": "Nuevo reporte",
    "permission": "reporting.write",
    "action": "route:/m/reportes/nuevo"
  }
} as const;

type StandardPages = { standardPages?: Record<string, LazyPage> };
const extensions = import.meta.glob<StandardPages>("./standard/pages.ts", { eager: true });
const standardPages: Record<string, LazyPage> = Object.values(extensions).reduce<Record<string, LazyPage>>(
  (acc, m) => ({ ...acc, ...(m.standardPages ?? {}) }),
  {},
);

const ExplorerPage = lazy(() => import("./explorer/ExplorerPage"));
const SavedReportsPage = lazy(() => import("./reports/SavedReportsPage"));
const TemplatesPage = lazy(() => import("../documents/TemplatesPage"));

/** Pages per submenu (P17a): explorer (report builder), saved/scheduled reports, document templates. */
export const reportingDescriptor: ModuleDescriptor = {
  id: "reportes",
  pages: {
    explorador: ExplorerPage,
    programados: SavedReportsPage,
    documentos: TemplatesPage,
    ...standardPages,
  },
  contributions: [
    {
      module: "inicio",
      submenu: { id: "analitica", label: "Analítica", route: "/m/inicio/analitica", permission: "reporting.read", owner_task: "P17" },
      page: ExplorerPage,
    },
    {
      module: "inicio",
      submenu: { id: "reportes", label: "Reportes", route: "/m/inicio/reportes", permission: "reporting.read", owner_task: "P17" },
      page: SavedReportsPage,
    },
  ],
  actions: [
    { id: "nuevo_reporte", label: "Nuevo reporte", route: "/m/reportes/explorador", permission: "reporting.write" },
  ],
};

export default reportingDescriptor;
