# UI-CONTRACT — binding UI/UX overlay (erp-five-machine-dev 2.2.0-dev; overlay introduced in 2.1.0-dev)

**Status: BINDING for every task.** This file, `ui/tokens.json`, `ui/module-registry.json` and `ui/task-matrix.json` are the UI overlay.
It is a specification for the code the workers will write. It does **not** mean any UI has been built yet.

**Precedence.** Original prompt (`docs/prompt-pack/prompts/Pxx-*.md`) → `DEV-ADDENDUM.md` section → **this overlay**. The overlay **overrides visual
layout, navigation, design tokens, dashboards and microcopy only**. It never overrides security (tenant/row/field policy, IAM, AI/MCP access control),
accounting (in-line synchronous ledger posting, stock invariants), Colombian regulatory rules, the DEV-only rules, or the five-machine protocol. If the
overlay seems to conflict with one of those, the other rule wins and the worker reports the conflict in its result `notes`. M5 then decides with a CCR.

Machine-readable files (authoritative over this prose when they differ): `ui/tokens.json` (design tokens), `ui/module-registry.json` (modules,
submenus, dashboards, KPIs, API contracts), `ui/task-matrix.json` (per-task deliverables/acceptance). Readable renderings: `ui/MODULES.md`, `ui/TASK-MATRIX.md`.
Validator: `python3 docs/five-machine/tools/uictl.py validate --root docs/five-machine`.

## 1. Reference assets (user-supplied design reference — not production code)

| File | SHA-256 | Notes |
|---|---|---|
| `ui-reference/reference.html` | `17cdfbf8318451e81145e19d21080c360375486678147b40d1cb02dd4c3de549` | User-supplied HTML mock ("NexusERP"). Read it as text only. **Do not open it in a browser in CI and do not copy code from it.** It loads unpinned CDNs. |
| `ui-reference/reference.png` | `e1133e091559e946e406b072e884a8b2d6fa73f4a8c31714f69c1119d3caca36` | User-supplied screenshot, **1892×856 px** (the inline preview of 1024×463 is only a scaled copy). It shows the AI panel open. |

Both are byte-identical copies of the user's attachments (`ui-reference/REFERENCE.sha256`). The user owns the design and allows the "NexusERP" name
and layers logo as **placeholder** branding. The brand is configurable (`ERP_BRAND_NAME`, `ERP_BRAND_LOGO_PATH`). Make no trademark or legal-entity claims.

## 2. The five mandatory requirements (from the user)

1. **Modules in a collapsible left sidebar**: 256 px expanded, 80 px collapsed (icons + tooltips), grouped, toggle at the top of the sidebar.
2. **AI agent in a collapsible right panel**: 320 px.
3. **Top bar** (64 px): app logo + name, global search, **period selector**, **company selector**, notifications, light/dark toggle, user profile.
4. **Selected module's menu bar** (48 px) directly under the top bar: submenus on the left, the module's quick action and export on the right.
5. **Every module has a main page with a dashboard and KPIs** (3–5 KPI cards + at least one panel). No module overview may be a placeholder.

## 3. Layout anatomy (desktop ≥ 1280 px, light theme)

```
┌ DevBanner 28px (dev only, non-dismissible, full width, amber-50/amber-700 text) ──────────────────────────────────────────┐
├ Header 64px  bg surface, border-b slate-200, shadow-sm, px-24 ────────────────────────────────────────────────────────────┤
│ [■ layers] NexusERP   [🔍 Buscar… (Ctrl+K) 288px]  [Empresa ▾ ≤240px] [Periodo ▾ ≤160px]   [✦ Asistente IA] [🔔•] [☾] [(AU) Nombre ▾] │
├ Sidebar 256/80 ─┬ Module bar 48px  bg surface, border-b, px-24 ───────────────────────────────┬ AI panel 320 ─────────┤
│ MÓDULOS     [⇤] │ [Resumen] [Sub 2] [Sub 3] …  (pill 12px radius, active = slate-100 + indigo) │ header indigo-50       │
│ Inicio          │                                         [+ Acción rápida] [⤓]               │ ✦ Asistente IA   [×]   │
│ FINANZAS        ├─────────────────────────────────────────────────────────────────────────────┤ sugerencias (pills)    │
│  Contabilidad   │ Center: bg slate-50, padding 24 (16 < md), own scroll, section gap 24        │ chat log (own scroll)  │
│  …              │ ┌KPI┐┌KPI┐┌KPI┐┌KPI┐  grid 1/2/4 cols gap 16, card radius 16, p-20            │                        │
│ (own scroll)    │ ┌ Chart panel (2fr) radius16 p24 h288 ┐┌ Feed panel (1fr) ┐                  │ input + enviar         │
│ footer: estado  │ └─────────────────────────────────────┘└──────────────────┘                  │                        │
└─────────────────┴─────────────────────────────────────────────────────────────────────────────┴────────────────────────┘
```

Values come from `ui/tokens.json` (`layout`, `color`, `font`, `chart`). The main ones: Inter (local); primary indigo-600 `#4f46e5`
(dark: indigo-400); slate neutrals; app background slate-50 / dark slate-950; surfaces white / dark slate-900; borders slate-200 / dark slate-800;
control radius 12 px, card radius 16 px; KPI card padding 20 px, panel padding 24 px; gaps 16 (KPI grid) / 24 (sections); compact density
(text 12–14 px, KPI values 24 px bold). Revenue series: indigo-600 solid 3 px with an 8 % area fill. Expense/secondary series: slate-400 dashed [4,4] 2 px, no fill. Charts are 288 px tall with the legend at the bottom.

**Scroll model.** The header, module bar, sidebar and AI panel are fixed regions. Only the center scrolls (`overflow-y:auto`); the sidebar nav and the AI chat log scroll independently.

## 4. Top bar (P08)

Order, left → right: brand (32 px indigo tile, radius 12, `layers` icon 16 px white + "Nexus" slate-900 / "ERP" indigo-600, 16 px bold) ·
global search (288 px; 224 px at 1280–1439) · **company selector** · **period selector** · spacer · **AI toggle** (indigo-50 pill "Asistente IA",
`aria-expanded`, `aria-controls`) · **notifications** (bell, rose dot + number for screen readers, popover 320 px, "Marcar todo como leído") ·
**theme toggle** (sun/moon, `aria-pressed`) · **profile** (avatar 28 px with initials, name ≥ md, menu: "Perfil y preferencias", "Seguridad",
"Cerrar sesión" → real OIDC logout).
* Company selector: lists only the companies the user may access (from `/api/v1/ui/context`). It shows the legal name, truncated with a tooltip,
  and the NIT in the tooltip. Switching re-scopes every query (§11).
* Period selector: fiscal periods with open/closed status, plus "Mes actual", "Trimestre", "Año fiscal". Dashboards read it. Closed periods show a lock icon.
* Global search: `Ctrl+K` / `⌘K` (and `/`) opens a command dialog (modal, focus trapped). It shows results grouped by type (documentos, terceros,
  productos, cuentas, empleados*). Results are policy-filtered by the server (`/api/v1/search`) and come with navigation commands. No "search not implemented" stubs.
* Narrow widths: below 1280 the search field becomes an icon button that opens the same dialog. Below 1024, company + period merge into one
  **context button** ("EMP · nov 2026") that opens a dialog with both selectors. Both stay reachable by keyboard and touch at every width.
  Text is never smaller than 12 px.

## 5. Sidebar (P08 shell + module descriptors)

Header row "MÓDULOS" (12 px uppercase, slate-500) + collapse button (`panel-left-close`/`panel-left-open`, `aria-expanded`, `aria-label`).
Items: icon 20 px + label 14 px/500, padding 10×12, radius 12. Active item: indigo-50 bg, indigo-600 text, shadow-sm. Hover: slate-100.
**Groups** in this order: Inicio · Finanzas · Comercial · Operaciones · Personas · Análisis · Sistema. All 15 registry modules appear for a full-access user:
Inicio, Contabilidad, Tesorería, Activos fijos, Ventas y CRM, Facturación electrónica, Punto de venta, Inventario, Compras, Talento humano y nómina,
Reportes, Aprobaciones y auditoría, Importar y exportar, Puesta en marcha, Administración. The reference shows only six modules. That is not a reason to hide any.
Collapsed mode (80 px): icons only, group headings become separators, every item gets a tooltip and an `aria-label`, and the footer is hidden.
Footer: "Estado del sistema" with a real value from `/health/ready`, shown as text ("Operativo" / "Degradado"), never as a decorative ping.
Modules the user may not access are **absent** (server-filtered), not greyed out. `talento` is visible only with `hr.access`.
Collapse state persists per user. Below 1024 the sidebar is an 80 px rail; expanded, it becomes an overlay drawer. Below 768 it is an off-canvas drawer.

## 6. Module bar (P08 shell; content from the descriptor)

Height 48, surface bg, border-b, px-24. Left: submenu pills from the descriptor (12 px; active = slate-100 bg + indigo-600 600-weight + border
slate-200; `role="tablist"`-style navigation implemented as links with `aria-current="page"`). It scrolls horizontally on overflow, with a visible fade
and a "Más" menu. Right: **quick action** (indigo-600 button, `plus` icon, label from the registry, shown only with its permission, opens a real
route/form) and **export** (`download` icon button, `aria-label="Exportar"`, calls P18 `POST /api/v1/exports` for the current view; disabled with a
tooltip when the view isn't exportable). Routes: `/m/{module}` = dashboard, `/m/{module}/{submenu}` = stable deep links. Reloading a URL restores the same view.

## 7. Module dashboard pattern (module owners, P08 components)

Row 1: KPI cards (grid 1/2/4 columns, gap 16). Card: radius 16, border, p-20, shadow-sm, label 12 px uppercase slate-500, icon tile
(p-8, radius 12, tinted bg), value 24 px bold, delta line (positive emerald-700 / negative rose-700 / neutral slate-500 + text "vs. periodo anterior").
Row 2: chart panel (lg 2fr) + feed/list panel (1fr), gap 24. Further rows are optional tables/lists from the registry.
Each card links to its `drill` route. Each KPI shows `formula` in an info tooltip. Unit formatting follows `module-registry.json › kpi_semantics.units`
(COP: `$ 1.284.500`, es-CO; compact `$ 1,3 M` only with the full value in `aria-label`/tooltip).
States: loading skeleton (same geometry) · `ok` · `no_data` (honest text + the registry's empty-state action) · `not_available` (provider module not
yet accepted: "Módulo aún no disponible") · `forbidden_field` (card omitted) · error (message + "Reintentar"). **Never** use static, sample or random numbers.

## 8. AI panel (P08 frame, P19 content)

Width 320, border-l, header indigo-50 (dark indigo-950/20) with an indigo `sparkles` tile, title "Asistente IA", subtitle "Inteligencia del negocio", and a close button.
Then: suggested prompts (pills, 12 px, per module from the descriptor), chat log (own scroll, 12–14 px), input "Pregúntale al asistente…" + send button (indigo-600).
**Deliberate adaptation (documented):** the reference overlays the panel on top of the content and hides the right column. In this app:
* ≥ 1280 px: the panel **docks**. It pushes the center so the center width becomes viewport − sidebar − 320 and the dashboard grid reflows. Nothing is permanently hidden.
* 768–1279 px: overlay drawer with a scrim. Focus is trapped, Esc and the scrim close it, and focus returns to the toggle.
* < 768 px: full-width sheet.
Open/closed state persists per user. Answers come from the P19 agent API, streamed, with links to the records cited. Text is rendered by a
sanitizing markdown renderer (**no `innerHTML`** of user or model text). Actions appear as proposals that need explicit confirmation and normal
permissions. If no model credentials exist in dev: a deterministic fixture model whose every reply is labelled
"Respuesta simulada (sin proveedor de IA configurado)". The assistant sees only what the user may see.

## 9. Visual acceptance specification (executed later by P22/M5 — no result is claimed here)

Inputs: `ui-reference/reference.png` (1892×856) + the HTML-extracted tokens. Suite location: `tests/e2e/visual/` (P22). Determinism: seeded synthetic
demo tenant, frozen clock (2026-11-15T10:00:00-05:00), fixed company "Demo Andina S.A.S." and period "noviembre 2026", `prefers-reduced-motion`,
transitions disabled, local fonts awaited (`document.fonts.ready`), charts with animation off, Intl `es-CO`/COP, DPR 1.
Viewports: **reference 1892×856**, 1440×900, 1280×800 (desktop) · 1024×768 and 768×1024 (tablet) · 390×844 (mobile). Each in light and dark.
For desktop sizes, all four combinations: sidebar expanded/collapsed × AI open/closed.
Assertions (each test):
1. **Layout boxes** (`getBoundingClientRect`, ±1 px): header height 64; module bar 48; sidebar 256 / 80; AI panel 320 when open; the module bar is directly below the
   header; the center starts after the sidebar and, when docked (≥ 1280), ends at the AI panel's left edge (no overlap); no horizontal page scroll.
2. **Token assertions** (computed style): font-family starts with Inter; primary button bg `rgb(79,70,229)`; app bg `rgb(248,250,252)` light / `rgb(2,6,23)` dark;
   card radius 16 px; control radius 12 px; card border `rgb(226,232,240)` light / `rgb(30,41,59)` dark; revenue series stroke `#4f46e5`, expenses dashed.
3. **Accessibility**: axe-core 0 serious/critical; text contrast ≥ 4.5:1; visible focus ring; keyboard walkthrough (Tab order header → sidebar →
   module bar → center → AI panel); `aria-expanded` on both toggles; Esc closes overlays; Ctrl/Cmd+K opens search.
4. **Screenshot comparison** against the **APPROVED baseline** in `tests/e2e/visual/__baselines__/` (not against reference.png). Proposed thresholds
   (justified but not yet measured): `maxDiffPixelRatio 0.01`, `threshold 0.2`. Masks are allowed only for the clock and the build hash.
   Thresholds may change only through a CCR with the reason recorded. Pixel-perfect identity with the reference is **not** claimed: Spanish copy,
   the company/period selectors, the DEV banner, the docked AI panel and the extra modules are expected deltas.
Baseline approval: the first baselines come from the implemented app at G4. M5 compares them side by side with `reference.png` (same
1892×856 viewport, AI open, sidebar expanded, light) and writes the **visual review** in `docs/gates/G4.md` § "UI visual review". It lists the reference hashes, each expected delta, any
unexpected delta, and the baseline commit. Baselines are never updated automatically (`--update-snapshots` is forbidden in CI). A change needs a CCR
that names the screens involved. If a fundamental design change is needed (layout regions, navigation model, palette), M5 records `HOLD M5: design change needs user confirmation`.
The M5 review is a protocol check, not an independent human design review.

## 10. Gate acceptance (added to the existing gate criteria)

* **G0**: `uictl validate` green; P02 OpenAPI contains every endpoint in `module-registry.json › api`; tokens/registry frozen; `CCR-UI-001` recorded in `docs/gates/G0.md`.
* **G1**: shell renders all browser states with the real `/ui/context` and `/ui/modules`; tokens applied; layout boxes pass; company/period switch
  without a stale render; keyboard/aria for the toggles; no CDN or remote font in the build; Inicio shows `not_available`/`no_data` states honestly.
* **G2**: every registry module whose owner is in W1/W2 has a real dashboard (KPI providers, panels, empty states, drills). Permission matrix and
  field masks are correct (payroll hidden for non-HR). Inicio tiles are populated from the providers. P17 is not required.
* **G3**: AI panel wired to P19 (streamed answers, citations, proposals + confirmation, labelled mock when no model); MCP submenu; onboarding
  dashboard; reporting/data dashboards; leak suite covers KPI/search/notifications/export/AI.
* **G4**: §9 suite green at all viewports and themes; read-only role walkthrough (no write buttons, no quick actions); dark mode; responsive (no overlap);
  `docs/gates/G4-evidence/ui-report.md` lists every screenshot artifact path; M5 visual review recorded.
Dependency rule (checked by `uictl`): a module dashboard's KPI providers are its owner task or that task's ancestors in `plan.json`. The only exception is
Inicio, which composes registered providers at runtime and renders `not_available` until they exist. **P17 is never a required provider outside Reportes**, so it cannot block W2 dashboards.

## 11. Data, security and caching rules

* Every KPI, chart, search result, notification badge, export and AI context is computed **server-side** for the current tenant + company + period + role.
  Row and field policies apply, including counts. The UI is **not** a security boundary: hidden buttons are convenience only, and the API enforces everything.
* Client cache keys = `[tenant, company_id, period_id, permission_hash, endpoint, params]`. Everything is cleared on company switch, period switch,
  logout and permission change. In-flight requests are aborted (AbortController), and responses whose context differs from the current context are dropped.
  The old company's data must never show while the new request is pending. A permission revoked on the server is effective on the next request.
* Dashboards read real, posted data. Ledger posting stays synchronous and in-line with the business document, and the stock invariants are unchanged. Dashboards never post.
* Dev fixtures may simulate **external integrations only** (DIAN validator, banks, PILA operator, e-mail, LLM). They may never simulate ledger figures,
  KPIs or dashboards. Simulated statuses carry the visible label "Simulado".

## 12. Implementation rules — the reference HTML is a mock

Forbidden as "done": placeholder module pages ("Viewing sub-section…"); unimplemented global search; fake toasts ("Action executed successfully",
"Signed out successfully"); canned AI replies; `innerHTML`/`eval`/`new Function` with user or model data; static chart arrays; CDN Tailwind/Chart.js/lucide;
`@latest`; Google Fonts; decorative "Operational" status without a health source; invented KPIs (the reference's "Customer Satisfaction 96.4 % / NPS 72" has no data source and is dropped).
Required: React + TypeScript components in the pack's stack. Pinned npm dependencies (exact versions, lockfile committed); Tailwind built locally
from `tokens.json`; `lucide-react` icons; Inter bundled locally (OFL-1.1, e.g. `@fontsource/inter`, pinned); one chart library chosen by P08 ADR
(Recharts or Chart.js, pinned); a CSP without remote script/font origins; real routes and forms behind every quick action; success messages only after the API confirms.

## 13. Microcopy (es-CO) — reference label → app label

Executive Dashboard → Inicio · Overview/Analytics/Reports/Activity Log → Resumen/Analítica/Reportes/Actividad · Inventory & SCM → Inventario ·
CRM & Sales → Ventas y CRM · Finance & Accounting → Contabilidad · HR & Payroll → Talento humano y nómina · Settings & Admin → Administración ·
Core Modules → Módulos · System Status/Operational → Estado del sistema/Operativo · New Record → (quick action from registry) · Export Data → Exportar ·
Search orders, SKU, clients, or docs (Ctrl+K) → Buscar documentos, productos, terceros… (Ctrl+K) · Nexus AI Agent → Asistente IA ·
Enterprise Intelligence → Inteligencia del negocio · Suggested Prompts → Sugerencias · Ask AI anything... → Pregúntale al asistente… ·
Notifications/Mark all read → Notificaciones/Marcar todo como leído · Profile & Settings → Perfil y preferencias · Security & Roles → Seguridad ·
Logout → Cerrar sesión · Total Revenue → Ventas netas · Revenue vs Operating Expenses → Ingresos vs. gastos operacionales ·
Live Audit Stream → Actividad reciente · vs last month → vs. periodo anterior · Last 6 Months/Full Year → Últimos 6 meses/Año fiscal.
Money: COP, es-CO (`$ 1.284.500`). Dates: `15 nov 2026`, America/Bogota. The reference's user "Jane Doe" is **not** carried over: the profile shows the signed-in user.
Demo users are synthetic (no real people).

## 14. Ownership, contribution and change control

* **P08 (M4)** owns the shell, tokens implementation, shared components, registry loader and Inicio composition (`frontend/src/shell/**`,
  `frontend/src/design-system/**`, …). After G1 the shell API and tokens are **frozen**. Changes go only through an M5 CCR.
* **Module owners** own their descriptor (`frontend/src/features/<x>/module.ts`), dashboard and backend KPI providers. Contributions to another module's
  menu (P06 → Contabilidad › Impuestos, P20 → Administración › MCP, P07/P17 → Inicio submenus) use the shell's contribution API from the contributor's own paths. Nobody edits another task's files.
* **P02** freezes the UI/KPI/search/notification contracts at G0. **P04** supplies permissions and field policies. **P17** adds aggregates later. **P19** owns AI
  panel content, **P20** the MCP submenu, **P21** onboarding. **P22** owns the visual/a11y suites and G4 evidence. **M5** owns `docs/five-machine/**` (this overlay)
  and is the single writer of shared manifests after acceptance (no race on `package.json`: P08 owns it; others ask via CCR).
* **CCR-UI-001** (M5, recorded in the G0 attempt, before any worker codes UI): "Adopt UI overlay 2.1.0-dev: UI-CONTRACT.md + ui/*.json bind every
  task per ui/task-matrix.json; precedence = visual/navigation only." Later changes: `CCR-UI-NNN`, recorded in the next gate file.

## 15. Accessibility and responsiveness (mandatory)

WCAG 2.2 AA. Text ≥ 12 px, contrast ≥ 4.5:1 for text and ≥ 3:1 for UI parts. Full keyboard access. Visible focus. Tooltips on focus and on hover.
`aria-expanded`/`aria-controls` on the sidebar, AI and menu toggles. `aria-current` on the active module and submenu. Focus is managed in dialogs and drawers.
`prefers-reduced-motion` respected. Charts get a text alternative (summary + data table toggle). Numbers are exposed unabbreviated to screen readers.

## 16. Deliberate deviations from the reference (accepted, not bugs)

AI panel docks on desktop (§8) · company and period selectors added (§4) · DEV banner (dev only) · 15 modules in groups instead of 6 ·
slate-400 text → slate-500; emerald-600 small text → emerald-700; 10/11 px labels → 12 px · no pulse/ping animations as status ·
"Customer Satisfaction/NPS" card dropped · Spanish copy and COP formatting.

## 17. Adoption

New runs: start a fresh run with all five 2.2.0-dev bundles (safest). Existing runs: see `MIGRATION.md` (§ "2.1.x-dev → 2.2.0-dev"; older sections for 2.0.0-dev and 2.1.0-dev).

## 18. Accounting dimension screens (2.1.1-dev, `DIMENSIONS-ADDENDUM.md`)

Contabilidad gets one submenu **Dimensiones contables** (`/m/contabilidad/dimensiones`, owner P05, permission `ledger.dimension.read`) with two tabs, **Auxiliares abiertos**
(`/m/contabilidad/dimensiones/auxiliares-abiertos`) and **Ítems contables** (`/m/contabilidad/dimensiones/items-contables`). Each tab has a table view and a tree view of the same data,
toggled in the page header, built on P08's shared `TreeTable` (design-system; no module-local tree). Node type shows as a badge (Agrupador / Detalle). Archived nodes are muted
with a text label (not only colour). Line pickers on documents list only active Detalle nodes of the current company. The Contabilidad dashboard KPIs are unchanged.

## 19. Feature screens, tabs and portals (2.2.0-dev, FEATURES-ADDENDUM)
* **Tabs:** a submenu may have up to 6 tabs (`ui/module-registry.json › submenus[].tabs`), and each tab names its feature (`F-…`).
  * Tabs render as an ARIA tablist under the module bar.
  * The route is deep-linkable.
  * The keyboard works: arrow keys move between tabs, and Home/End jump to the first/last.
  * Permission is checked per tab. Unauthorized tabs are absent, not disabled.
* **Portals:** `/portal/proveedores` (P11), `/portal/clientes` (P10) and `/portal/empleados` (P13) use the **P08 portal shell variant**:
  * the same tokens, light/dark theme, brand and DEV banner;
  * a top bar with no company switcher, no internal search and no AI panel;
  * a left menu from `portals[].menus`.

  Each portal dashboard has the 3–5 KPIs of `portals[].dashboard.kpis` (real, party-scoped, never sample numbers). External users can never see
  internal routes. Portal login screens follow §4 microcopy rules and are covered by Playwright (T-PORTAL-*).
* **Printing:** documents (RC, CE, issues, labels, transformations, statements, payslips) have an HTML preview and a PDF through
  `DocumentRenderer` (D-005). The print stylesheet is part of the visual suite.
* **Labels:** non-certified outputs show the label text defined in FEATURES-ADDENDUM (for example, "Exógena — formato sintético no válido ante la
  DIAN", "IA en modo de prueba (sin proveedor conectado)").
