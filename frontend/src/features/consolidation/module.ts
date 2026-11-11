/**
 * Contribution descriptor of the intercompany/consolidation scope (owner P16b).
 * The "Intercompañía y consolidación" submenu belongs to the Tesorería module (registry entry "tesoreria", descriptor
 * owned by P16a). Per UI-CONTRACT §14 this task contributes its page from its own path through the shell's
 * contribution API instead of editing the treasury descriptor. `consolidacion` is not a registry module: the shell
 * only uses the contribution (same submenu id, route and permission as the registry), so nothing else is rendered.
 */
import { lazy } from "react";
import type { ModuleDescriptor } from "@shell/registry";

export const intercompanySubmenu = {
  id: "intercompania",
  label: "Intercompañía y consolidación",
  route: "/m/tesoreria/intercompania",
  permission: "consolidation.read",
} as const;

const descriptor: ModuleDescriptor = {
  id: "consolidacion",
  contributions: [{ module: "tesoreria", submenu: { ...intercompanySubmenu }, page: lazy(() => import("./IntercompanyConsolidationPage")) }],
};

export default descriptor;
