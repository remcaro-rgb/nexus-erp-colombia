// Pages contributed by P09c (F-INV-BOM) to the "inventario" descriptor (keys = submenu/tab of module-registry.json).
// Wiring into features/inventory/module.ts is requested in contracts/change-requests/P09c-1.md (owned by P09a).
import { lazy } from "react";
import type { LazyPage } from "@shell/registry";

export const bomPages: Record<string, LazyPage> = {
  "produccion/recetas": lazy(() => import("./RecipesPage")),
};
