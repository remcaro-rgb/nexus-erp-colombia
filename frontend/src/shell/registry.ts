// Module registry loader (owner P08; module-registry.json › shell.registry_loader).
// * The SERVER decides visibility: GET /api/v1/ui/modules returns only the modules the user may access
//   (absent, not disabled). Submenus/tabs/quick actions are additionally filtered by permission (deny-by-default).
// * Module teams plug in WITHOUT editing the shell: `frontend/src/features/<x>/module.ts` exports a ModuleDescriptor
//   (pages per submenu/tab, dashboard extras, contributions to other modules' menus) and is auto-loaded here.
import type { ComponentType, LazyExoticComponent } from "react";
import type { RegistryModule, RegistrySubmenu, RegistryTab, UiModules } from "@lib/api/types";
import { can, type PermissionSet } from "@lib/permissions";

export type PageComponent = ComponentType<{ module: RegistryModule; submenu: RegistrySubmenu | null; tab: RegistryTab | null }>;
export type LazyPage = LazyExoticComponent<PageComponent> | PageComponent;

export interface ModuleDescriptor {
  /** Registry module id this descriptor belongs to (e.g. "contabilidad"). */
  id: string;
  /** Pages keyed by submenu id (or "submenu/tab"). The dashboard (submenu "resumen") is rendered by the shell unless overridden. */
  pages?: Record<string, LazyPage>;
  /** Extra dashboard widgets (registered by the module, rendered under the KPI row). */
  dashboardWidgets?: Array<{ id: string; permission: string; component: LazyPage }>;
  /** Contributions to other modules' menus from the contributor's own path (UI-CONTRACT §14). */
  contributions?: Array<{ module: string; submenu: RegistrySubmenu; page?: LazyPage }>;
  /** Command-palette actions ("new invoice"). */
  actions?: Array<{ id: string; label: string; route: string; permission: string }>;
}

type DescriptorModule = { default?: ModuleDescriptor; descriptor?: ModuleDescriptor };

/** Auto-discovered descriptors: features/*\/module.ts + the shell's own modules/*.module.ts. */
export function discoverDescriptors(): ModuleDescriptor[] {
  const found = {
    ...import.meta.glob<DescriptorModule>("../features/*/module.ts", { eager: true }),
    ...import.meta.glob<DescriptorModule>("./modules/*.module.ts", { eager: true }),
  };
  return Object.values(found)
    .map((m) => m.default ?? m.descriptor)
    .filter((d): d is ModuleDescriptor => Boolean(d && d.id));
}

export interface ResolvedModule extends RegistryModule {
  descriptor: ModuleDescriptor | null;
  pages: Record<string, LazyPage>;
}

export interface ResolvedRegistry {
  groups: string[];
  modules: ResolvedModule[];
  byId: Map<string, ResolvedModule>;
  actions: Array<{ id: string; label: string; route: string; module: string }>;
}

function filterTabs(tabs: RegistryTab[] | undefined, perms: PermissionSet): RegistryTab[] | undefined {
  return tabs?.filter((t) => can(perms, t.permission));
}

/** Server modules (already filtered) ∩ client permission filter ⊕ local descriptors and contributions. */
export function resolveRegistry(server: UiModules, perms: PermissionSet, descriptors: ModuleDescriptor[] = discoverDescriptors()): ResolvedRegistry {
  const byDescriptor = new Map(descriptors.map((d) => [d.id, d]));
  const modules: ResolvedModule[] = [];
  for (const m of server.modules) {
    if (!can(perms, m.permission)) continue; // defence in depth; the server already omits it
    const d = byDescriptor.get(m.id) ?? null;
    const submenus = m.submenus
      .filter((s) => can(perms, s.permission))
      .map((s) => ({ ...s, tabs: filterTabs(s.tabs, perms) }));
    const pages: Record<string, LazyPage> = { ...(d?.pages ?? {}) };
    for (const other of descriptors) {
      for (const c of other.contributions ?? []) {
        if (c.module !== m.id || !can(perms, c.submenu.permission)) continue;
        if (!submenus.some((s) => s.id === c.submenu.id)) submenus.push({ ...c.submenu, tabs: filterTabs(c.submenu.tabs, perms) });
        if (c.page) pages[c.submenu.id] = c.page;
      }
    }
    const quick = m.quick_action && can(perms, m.quick_action.permission) ? m.quick_action : null;
    modules.push({ ...m, submenus, quick_action: quick, descriptor: d, pages });
  }
  const order = new Map(server.groups.map((g, i) => [g, i]));
  modules.sort((a, b) => (order.get(a.group) ?? 99) - (order.get(b.group) ?? 99));
  const actions: ResolvedRegistry["actions"] = descriptors.flatMap((d) =>
    (d.actions ?? []).filter((a) => can(perms, a.permission) && modules.some((m) => m.id === d.id)).map((a) => ({ id: a.id, label: a.label, route: a.route, module: d.id })),
  );
  for (const m of modules) {
    if (m.quick_action?.action.startsWith("route:")) actions.push({ id: `${m.id}.${m.quick_action.id}`, label: m.quick_action.label, route: m.quick_action.action.slice(6), module: m.id });
  }
  return { groups: server.groups, modules, byId: new Map(modules.map((m) => [m.id, m])), actions };
}

/** /m/{module}/{submenu}/{tab} -> parts. */
export function matchModuleRoute(reg: ResolvedRegistry, moduleId: string, sub?: string, tab?: string) {
  const module = reg.byId.get(moduleId) ?? null;
  if (!module) return { module: null, submenu: null, tab: null };
  const submenu = sub ? (module.submenus.find((s) => s.id === sub || s.route.endsWith(`/${sub}`)) ?? null) : null;
  const tabDef = submenu && tab ? (submenu.tabs?.find((t) => t.id === tab) ?? null) : submenu?.tabs?.[0] && !tab ? (submenu.tabs[0] ?? null) : null;
  return { module, submenu, tab: tabDef };
}
