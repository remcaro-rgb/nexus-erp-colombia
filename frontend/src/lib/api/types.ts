// Typed shapes of the UI contracts the shell consumes (owner P08).
// Source: docs/spec/ui/module-registry.json › api (frozen at G0) and contracts/openapi (Me, PortalMe, PortalContext, Problem).
// Endpoints missing from the generated OpenAPI bundle are requested in contracts/change-requests/P08-1.md.

export interface Brand {
  name: string;
  logo_path: string | null;
}

export interface CompanyRef {
  id: string;
  legal_name: string;
  nit?: string | null;
  code?: string | null;
}

export type PeriodStatus = "open" | "closed" | "locked" | string;

export interface PeriodRef {
  id: string;
  start: string;
  end: string;
  status: PeriodStatus;
  label?: string | null;
}

/** GET /api/v1/ui/context */
export interface UiContext {
  tenant_id: string;
  company_id: string | null;
  companies?: CompanyRef[];
  company_ids?: string[];
  period: PeriodRef | null;
  periods?: PeriodRef[];
  roles?: string[];
  permissions?: string[];
  feature_flags?: Record<string, boolean>;
  environment: "dev" | string;
  brand: Brand;
  user?: { display_name: string; email?: string | null } | null;
  locale?: string;
  timezone?: string;
  currency?: string;
  session_idle_minutes?: number;
  logout_url?: string;
}

/** GET /api/v1/iam/me (OpenAPI schema Me) */
export interface Me {
  id: string;
  tenant_id?: string;
  user_id?: string;
  companies?: string[];
  permissions?: string[];
  field_masks?: Record<string, string[]>;
  portal_kind?: string | null;
  display_name?: string | null;
}

export interface RegistryTab {
  id: string;
  label: string;
  route: string;
  permission: string;
  feature?: string;
  owner_task?: string;
}

export interface RegistrySubmenu {
  id: string;
  label: string;
  route: string;
  permission: string;
  owner_task?: string;
  tabs?: RegistryTab[];
}

export interface RegistryKpi {
  id: string;
  label: string;
  formula: string;
  unit: string;
  semantics?: string;
  comparison?: string;
  permission: string;
  provider_task?: string;
  drill?: string;
  sensitive_fields?: string[];
}

export interface RegistryPanel {
  id: string;
  title: string;
  type: "line" | "bar" | "feed" | "list" | "table" | string;
  series?: Array<{ id: string; label?: string; style?: string; formula?: string }>;
  range_selector?: string[];
  permission?: string;
  provider_task?: string;
  drill?: string;
}

export interface RegistryModule {
  id: string;
  label: string;
  icon: string;
  group: string;
  route: string;
  permission: string;
  owner_task?: string;
  submenus: RegistrySubmenu[];
  /** action = "route:/m/…" (a real route/form; shown only with its permission). */
  quick_action?: { id: string; label: string; permission: string; action: string } | null;
  export?: { exportable: boolean; entity?: string } | null;
  ai_suggestions?: string[];
  dashboard?: { route: string; kpis: RegistryKpi[]; panels?: RegistryPanel[]; empty_state?: string };
}

/** GET /api/v1/ui/modules?company_id= (server-filtered: unauthorized modules are absent) */
export interface UiModules {
  groups: string[];
  modules: RegistryModule[];
}

export type KpiState = "ok" | "no_data" | "not_available" | "forbidden_field";

export interface KpiValue {
  id: string;
  value: string | null;
  previous: string | null;
  delta: string | null;
  unit: string;
  state: KpiState;
  formula_ref?: string;
}

/** GET /api/v1/kpis/{module_id}?company_id=&period_id=&compare=previous */
export interface KpiResponse {
  as_of: string;
  kpis: KpiValue[];
  generated_at: string;
}

export interface PanelPoint {
  label: string;
  values: Record<string, string | null>;
}

/** GET /api/v1/kpis/{module_id}/panels/{panel_id} */
export interface PanelResponse {
  state: KpiState;
  series: Array<{ id: string; label: string }>;
  points: PanelPoint[];
  items?: Array<{ id: string; title: string; subtitle?: string; at?: string; href?: string }>;
}

export interface SearchResult {
  id: string;
  type: string;
  title: string;
  subtitle?: string | null;
  href: string;
}

/** GET /api/v1/search */
export interface SearchResponse {
  results: SearchResult[];
}

export interface NotificationItem {
  id: string;
  title: string;
  body?: string | null;
  created_at: string;
  read: boolean;
  href?: string | null;
}

export interface Page<T> {
  results: T[];
  next?: string | null;
}

/** RFC 9457 problem (OpenAPI schema Problem). */
export interface Problem {
  type: string;
  title: string;
  status: number;
  code: string;
  trace_id: string;
  detail?: string;
  errors?: Array<{ pointer?: string; code?: string; detail?: string }>;
}

/** GET /api/portal/v1/context (OpenAPI PortalContext) */
export interface PortalContext {
  id: string;
  tenant_id?: string;
  company_name?: string;
  party?: { id: string; name?: string; nit?: string } | null;
  menu?: Record<string, unknown>;
  session_idle_minutes?: number;
}

export type PortalKind = "supplier" | "customer" | "employee";

/** GET /api/portal/v1/me (OpenAPI PortalMe) */
export interface PortalMe {
  id: string;
  portal_kind?: PortalKind;
  display_name?: string;
  email?: string;
}
