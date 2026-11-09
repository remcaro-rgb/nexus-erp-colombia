/**
 * Module descriptor of "aprobaciones" (Aprobaciones y auditoría), owner P07.
 * Mirrors docs/spec/ui/module-registry.json exactly; the P08 shell registry loader
 * consumes it (no module renders its own nav, header or AI widget).
 */

export interface SubmenuDescriptor {
  id: string;
  label: string;
  route: string;
  permission: string;
}

export interface KpiDescriptor {
  id: string;
  label: string;
  formula: string;
  unit: "count" | "hours" | "COP" | "percent" | "ratio" | "days";
  semantics: "snapshot" | "flow";
  comparison: "none" | "previous_period";
  zero_denominator: string;
  permission: string;
  provider_task: string;
  source: string;
  drill: string;
}

export interface ModuleDescriptor {
  id: string;
  label: string;
  icon: string;
  group: string;
  owner_task: string;
  route: string;
  permission: string;
  submenus: SubmenuDescriptor[];
  dashboard: {
    route: string;
    kpis: KpiDescriptor[];
    panels: { id: string; title: string; type: string; provider_task: string; permission: string; drill: string }[];
    empty_state: string;
    kpi_endpoint: string;
  };
  quick_action: { id: string; label: string; permission: string; action: string };
  ai_prompts: string[];
}

export const aprobacionesModule: ModuleDescriptor = {
  id: "aprobaciones",
  label: "Aprobaciones y auditoría",
  icon: "check-circle-2",
  group: "Sistema",
  owner_task: "P07",
  route: "/m/aprobaciones",
  permission: "workflow.read",
  submenus: [
    { id: "resumen", label: "Resumen", route: "/m/aprobaciones", permission: "workflow.read" },
    { id: "bandeja", label: "Bandeja", route: "/m/aprobaciones/bandeja", permission: "workflow.read" },
    { id: "auditoria", label: "Auditoría", route: "/m/aprobaciones/auditoria", permission: "audit.read" },
    { id: "tareas", label: "Tareas programadas", route: "/m/aprobaciones/tareas", permission: "scheduler.read" },
  ],
  dashboard: {
    route: "/m/aprobaciones",
    kpi_endpoint: "/api/v1/kpis/aprobaciones",
    kpis: [
      {
        id: "pendientes",
        label: "Pendientes de mi aprobación",
        formula: "Número de solicitudes asignadas al usuario o a sus roles en estado pendiente",
        unit: "count",
        semantics: "snapshot",
        comparison: "none",
        zero_denominator: "not_applicable",
        permission: "workflow.read",
        provider_task: "P07",
        source: "workflow instances",
        drill: "/m/aprobaciones/bandeja",
      },
      {
        id: "vencidas_sla",
        label: "Vencidas (SLA)",
        formula: "Número de solicitudes pendientes cuyo plazo definido en el flujo ya pasó",
        unit: "count",
        semantics: "snapshot",
        comparison: "none",
        zero_denominator: "not_applicable",
        permission: "workflow.read",
        provider_task: "P07",
        source: "workflow instances",
        drill: "/m/aprobaciones/bandeja?vencidas=1",
      },
      {
        id: "tiempo_aprobacion",
        label: "Tiempo medio de aprobación",
        formula: "Promedio (aprobada_en − creada_en) de solicitudes resueltas en el periodo",
        unit: "hours",
        semantics: "flow",
        comparison: "previous_period",
        zero_denominator: "show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN)",
        permission: "workflow.read",
        provider_task: "P07",
        source: "workflow instances",
        drill: "/m/aprobaciones/bandeja?resueltas=1",
      },
      {
        id: "eventos_auditoria",
        label: "Eventos de auditoría",
        formula: "Número de eventos de auditoría visibles para el usuario en el periodo",
        unit: "count",
        semantics: "flow",
        comparison: "previous_period",
        zero_denominator: "not_applicable",
        permission: "audit.read",
        provider_task: "P07",
        source: "audit log",
        drill: "/m/aprobaciones/auditoria",
      },
    ],
    panels: [
      {
        id: "actividad",
        title: "Actividad reciente",
        type: "feed",
        provider_task: "P07",
        permission: "audit.read",
        drill: "/m/aprobaciones/auditoria/{event_id}",
      },
    ],
    empty_state: "No tienes solicitudes pendientes.",
  },
  quick_action: {
    id: "ver_bandeja",
    label: "Ver bandeja",
    permission: "workflow.read",
    action: "route:/m/aprobaciones/bandeja",
  },
  ai_prompts: [
    "¿Qué aprobaciones tengo pendientes?",
    "¿Cuáles solicitudes vencieron su plazo?",
    "Muéstrame la actividad de auditoría de hoy",
  ],
};

export default aprobacionesModule;
