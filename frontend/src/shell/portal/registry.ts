// Portal definitions (owner P08): the frozen `portals` section of docs/spec/ui/module-registry.json (G0).
// Only the `portals` key is imported (Vite tree-shakes JSON named imports).
import { portals as rawPortals } from "@spec/ui/module-registry.json";
import type { ComponentType } from "react";
import type { PortalKind, RegistryKpi } from "@lib/api/types";

export interface PortalMenu {
  id: string;
  label: string;
  route: string;
}

export interface PortalDefinition {
  id: string; // route segment: proveedores | clientes | empleados
  label: string;
  kind: PortalKind;
  route: string;
  permission: string;
  owner_task: string;
  menus: PortalMenu[];
  dashboard: { route: string; kpis: RegistryKpi[]; empty_state?: string };
}

export const PORTALS: readonly PortalDefinition[] = rawPortals as unknown as PortalDefinition[];

export function portalById(id: string | undefined): PortalDefinition | null {
  return PORTALS.find((p) => p.id === id) ?? null;
}

export function portalByKind(kind: PortalKind | undefined | null): PortalDefinition | null {
  return PORTALS.find((p) => p.kind === kind) ?? null;
}

/** Pages contributed by the owning module (P10b/P11b/P13) from its own path: features/**\/portal.descriptor.ts */
export interface PortalDescriptor {
  portal: string; // PortalDefinition.id
  pages: Record<string, ComponentType<{ portal: PortalDefinition; menu: PortalMenu }>>;
}

type M = { default?: PortalDescriptor };

export function discoverPortalDescriptors(): PortalDescriptor[] {
  const found = import.meta.glob<M>("../../features/**/portal.descriptor.ts", { eager: true });
  return Object.values(found)
    .map((m) => m.default)
    .filter((d): d is PortalDescriptor => Boolean(d?.portal));
}
