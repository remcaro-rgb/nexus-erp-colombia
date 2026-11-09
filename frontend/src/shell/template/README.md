# Module template (owner P08)

Copy this folder to `frontend/src/features/<your-module>/` (your own owned path) and rename. The shell discovers
`features/*/module.ts` automatically — you never edit `frontend/src/shell/**`.

| File | Purpose |
|---|---|
| `module.ts` | `ModuleDescriptor`: `id` = your registry module id; `pages` keyed by submenu id (or `submenu/tab`); `dashboardWidgets`; `contributions` to other modules' menus; palette `actions`. |
| `ExampleListPage.tsx` | List page: `useResource` (context-scoped cache) + `DataGrid` (field masks from `/iam/me`) + `useRegisterExport`. |
| `ExampleDocumentPage.tsx` | Document page: `DocumentLayout` (one primary action), `useEntityForm` (Zod + problem+json), `:simulate` accounting preview. |

Rules (UI-CONTRACT): no module renders its own nav, header or AI widget; dashboards use the registry KPIs and real
API values only; quick actions open real routes; success toasts only after the API confirms; strings via `useT()`
(add your keys to your module's own catalogue through a CCR to P08 or keep labels from the registry/API).
Portal pages: export a `PortalDescriptor` from `features/<x>/**/portal.descriptor.ts` (`portal`: proveedores |
clientes | empleados, `pages` keyed by portal menu id).
