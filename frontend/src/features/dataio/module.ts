/**
 * Module descriptor of "datos" (Importar y exportar), owner P18 (F-X-DATAIO).
 * * `datosModule` mirrors docs/spec/ui/module-registry.json exactly (registry conformance test).
 * * The default export is the P08 shell descriptor (auto-discovered from features/*\/module.ts): one page per
 *   submenu (the dashboard "resumen" is rendered by the shell from GET /api/v1/kpis/datos) and palette actions.
 * No module renders its own nav, header or AI widget.
 */
import { lazy } from "react";
import type { ModuleDescriptor as ShellDescriptor } from "@shell/registry";

export const datosModule = {
  "id": "datos",
  "label": "Importar y exportar",
  "icon": "arrow-left-right",
  "group": "Sistema",
  "owner_task": "P18",
  "route": "/m/datos",
  "permission": "dataio.read",
  "submenus": [
    {
      "id": "resumen",
      "label": "Resumen",
      "route": "/m/datos",
      "permission": "dataio.read"
    },
    {
      "id": "importar",
      "label": "Importar",
      "route": "/m/datos/importar",
      "permission": "dataio.import"
    },
    {
      "id": "exportar",
      "label": "Exportar",
      "route": "/m/datos/exportar",
      "permission": "dataio.export"
    },
    {
      "id": "plantillas",
      "label": "Plantillas",
      "route": "/m/datos/plantillas",
      "permission": "dataio.read"
    }
  ],
  "dashboard": {
    "route": "/m/datos",
    "kpi_endpoint": "/api/v1/kpis/datos",
    "kpis": [
      {
        "id": "lotes_importados",
        "label": "Lotes importados",
        "formula": "Número de lotes de importación confirmados en el periodo",
        "unit": "count",
        "semantics": "flow",
        "comparison": "previous_period",
        "zero_denominator": "not_applicable",
        "permission": "dataio.read",
        "provider_task": "P18",
        "source": "import batches",
        "drill": "/m/datos/importar?estado=confirmado"
      },
      {
        "id": "filas_importadas",
        "label": "Filas importadas",
        "formula": "Σ filas confirmadas en lotes del periodo",
        "unit": "count",
        "semantics": "flow",
        "comparison": "previous_period",
        "zero_denominator": "not_applicable",
        "permission": "dataio.read",
        "provider_task": "P18",
        "source": "import batches",
        "drill": "/m/datos/importar"
      },
      {
        "id": "lotes_rechazados",
        "label": "Lotes rechazados",
        "formula": "Número de lotes rechazados por validación (atómicos: no se aplicó ninguna fila)",
        "unit": "count",
        "semantics": "flow",
        "comparison": "previous_period",
        "zero_denominator": "not_applicable",
        "permission": "dataio.read",
        "provider_task": "P18",
        "source": "import batches",
        "drill": "/m/datos/importar?estado=rechazado"
      },
      {
        "id": "exportaciones",
        "label": "Exportaciones",
        "formula": "Número de exportaciones generadas en el periodo (respetan políticas de fila y campo)",
        "unit": "count",
        "semantics": "flow",
        "comparison": "previous_period",
        "zero_denominator": "not_applicable",
        "permission": "dataio.export",
        "provider_task": "P18",
        "source": "export jobs",
        "drill": "/m/datos/exportar"
      }
    ],
    "panels": [
      {
        "id": "lotes_por_tipo",
        "title": "Lotes por tipo de dato",
        "type": "bar",
        "provider_task": "P18",
        "permission": "dataio.read",
        "drill": "/m/datos/importar?tipo={x}"
      }
    ],
    "empty_state": "No hay importaciones. Acción: 'Descargar plantilla' y 'Nueva importación'."
  },
  "quick_action": {
    "id": "nueva_importacion",
    "label": "Nueva importación",
    "permission": "dataio.import",
    "action": "route:/m/datos/importar/nueva"
  }
} as const;

const descriptor: ShellDescriptor = {
  id: "datos",
  pages: {
    importar: lazy(() => import("./ImportPage")),
    exportar: lazy(() => import("./ExportPage")),
    plantillas: lazy(() => import("./TemplatesPage")),
  },
  actions: [
    { id: "nueva_importacion", label: "Nueva importación", route: "/m/datos/importar/nueva", permission: "dataio.import" },
    { id: "descargar_plantilla", label: "Descargar plantilla de importación", route: "/m/datos/plantillas", permission: "dataio.read" },
  ],
};

export default descriptor;
