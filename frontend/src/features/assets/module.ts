/**
 * Module descriptor of "activos" (Activos fijos), owner P14.
 * * `activosModule` mirrors docs/spec/ui/module-registry.json exactly (registry conformance test).
 * * The default export is the P08 shell descriptor (auto-discovered from features/*\/module.ts): pages per submenu
 *   (the dashboard "resumen" is rendered by the shell from GET /api/v1/kpis/activos) and palette actions.
 * No module renders its own nav, header or AI widget.
 */
import { lazy } from "react";
import type { ModuleDescriptor as ShellDescriptor } from "@shell/registry";

export const activosModule = {
  id: "activos",
  label: "Activos fijos",
  icon: "building-2",
  group: "Finanzas",
  owner_task: "P14",
  route: "/m/activos",
  permission: "assets.read",
  submenus: [
    { id: "resumen", label: "Resumen", route: "/m/activos", permission: "assets.read" },
    { id: "registro", label: "Registro de activos", route: "/m/activos/registro", permission: "assets.read" },
    { id: "depreciacion", label: "Depreciación", route: "/m/activos/depreciacion", permission: "assets.depreciate" },
    { id: "bajas", label: "Bajas y traslados", route: "/m/activos/bajas", permission: "assets.write" },
  ],
  dashboard: {
    route: "/m/activos",
    kpi_endpoint: "/api/v1/kpis/activos",
    kpis: [
      {
        id: "costo_historico",
        label: "Costo histórico",
        formula: "Σ costo de adquisición de activos activos a la fecha de corte",
        unit: "COP",
        semantics: "snapshot",
        comparison: "previous_period",
        zero_denominator: "not_applicable",
        permission: "assets.read",
        provider_task: "P14",
        source: "asset register",
        drill: "/m/activos/registro",
      },
      {
        id: "depreciacion_periodo",
        label: "Depreciación del periodo",
        formula: "Σ depreciación contabilizada en el periodo (todas las bases: NIIF y fiscal se muestran por separado)",
        unit: "COP",
        semantics: "flow",
        comparison: "previous_period",
        zero_denominator: "not_applicable",
        permission: "assets.read",
        provider_task: "P14",
        source: "depreciation runs",
        drill: "/m/activos/depreciacion?period={period}",
      },
      {
        id: "valor_neto",
        label: "Valor neto en libros",
        formula: "Costo histórico − depreciación acumulada − deterioro acumulado a la fecha de corte",
        unit: "COP",
        semantics: "snapshot",
        comparison: "previous_period",
        zero_denominator: "not_applicable",
        permission: "assets.read",
        provider_task: "P14",
        source: "asset register",
        drill: "/m/activos/registro?orden=valor_neto",
      },
      {
        id: "sin_depreciar",
        label: "Activos pendientes de depreciación",
        formula: "Número de activos en uso sin corrida de depreciación para el periodo",
        unit: "count",
        semantics: "snapshot",
        comparison: "none",
        zero_denominator: "not_applicable",
        permission: "assets.depreciate",
        provider_task: "P14",
        source: "depreciation runs",
        drill: "/m/activos/depreciacion?pendientes=1",
      },
    ],
    panels: [
      {
        id: "depreciacion_mensual",
        title: "Depreciación mensual",
        type: "bar",
        provider_task: "P14",
        permission: "assets.read",
        drill: "/m/activos/depreciacion?month={x}",
      },
    ],
    empty_state: "No hay activos registrados. Acciones: 'Nuevo activo' o 'Importar activos' (/m/datos/importar?tipo=activos).",
  },
  quick_action: { id: "nuevo_activo", label: "Nuevo activo", permission: "assets.write", action: "route:/m/activos/registro/nuevo" },
} as const;

const descriptor: ShellDescriptor = {
  id: "activos",
  pages: {
    registro: lazy(() => import("./RegisterPage")),
    depreciacion: lazy(() => import("./DepreciationPage")),
    bajas: lazy(() => import("./DisposalsPage")),
  },
  actions: [{ id: "etiquetas_qr", label: "Etiquetas QR de activos", route: "/m/activos/registro", permission: "assets.read" }],
};

export default descriptor;
