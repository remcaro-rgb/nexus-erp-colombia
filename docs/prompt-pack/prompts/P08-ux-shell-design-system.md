> **Prompt P08 · Wave W1 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/06 (UX principles); shared/10 §E. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P08 — Frontend Shell, Design System, UX Patterns, Accessibility

## Mission
Deliver an excellent, fast, accessible UI foundation that every module plugs into without editing the shell: the app shell, navigation, a design system, a data grid, forms, document
layout patterns, the command palette with AI, role home dashboards, and i18n es-CO. Requirement UR-20.

## Owned paths
`frontend/` except `frontend/src/features/<module>/**` (owned by modules). `package.json`, `pnpm-lock.yaml`, `frontend/vite.config.ts`, `frontend/src/{app,shell,design-system,lib,i18n}/**`.

## Dependencies
G0 contracts (generate the TypeScript client and Zod schemas from OpenAPI with `openapi-typescript` + `orval` or similar; regenerated in CI; drift fails CI).

## Deliverables
1. **Shell:** responsive layout (sidebar with module groups, top bar with a company switcher showing only permitted companies, global search, notifications, user menu), route registry that
   auto-loads `features/*/routes.tsx` and `features/*/nav.ts` (permission-gated by `/iam/me`), error boundaries, the session timeout warning, and an offline indicator.
2. **Design system** (Radix primitives + tokens; Tailwind or CSS variables): color tokens with AA contrast in light/dark modes, typography, spacing, density modes (comfortable/compact), components:
   Button, IconButton, Input, MoneyInput (es-CO formatting, decimal-safe as string), QuantityInput with UoM, DatePicker (dd/mm/aaaa), PartyPicker (search by NIT/name with DV validation), Select/Combobox,
   Tabs, Dialog, Drawer, Toast (with correlation id), Badge (posting and fiscal status), Stepper, EmptyState, Skeleton, FileDrop, Timeline, KPI card, Chart wrappers (accessible tables as an alternative).
   Storybook with a11y addon and visual regression (Playwright snapshots).
3. **DataGrid:** TanStack Table with virtualization (100k rows client-side, server-side paging/sort/filter for larger), column chooser, saved views (per user, shareable to a role), filter builder that only
   offers permitted fields (from `/iam/me` field masks), inline edit with validation, keyboard navigation (arrows, Enter, Esc, Tab), multi-select bulk actions, export (calls dataio respecting
   policy), and totals rows using Decimal-safe math (`big.js`/`decimal.js`).
4. **Document pattern** (shared by invoice, order, PO, receipt, payroll run, asset, journal entry): header + lines grid + totals + side panel (Related documents graph, Accounting preview from
   `:simulate`, Stock impact preview, Fiscal status with the DIAN event log, Attachments, Audit timeline, Comments/mentions) + a sticky action bar with exactly one primary action. A state machine badge.
5. **Forms:** React Hook Form + Zod from the contracts; server problem+json errors mapped to fields; autosave drafts (non-posting docs only); unsaved-changes guard.
6. **Command palette** (Ctrl/Cmd+K): navigation, actions ("new invoice"), recent records, and "Ask AI" which opens the P19 agent panel with page context (current record ids only, no field
   values the user cannot see).
7. **AI agent panel** container (P19 owns the conversation UI component in `features/ai_agent`, mounted via the slot registry): a slot API for side panels.
8. **Role home dashboards** framework: widgets registered by modules (KPIs, lists, charts), a layout per role, user customization.
9. **i18n:** ICU messages, es-CO default and en-US secondary; lint rule against hard-coded strings; locale formats (COP `$ 1.234.567,89`, thousands `.`, decimals `,`).
10. **Accessibility (WCAG 2.2 AA):** focus visible and not obscured (2.4.11), target size minimum (2.5.8), dragging alternatives (2.5.7), consistent help (3.2.6), redundant entry avoidance (3.3.7),
    accessible authentication (3.3.8: allow password managers and paste; no cognitive puzzles), skip links, landmarks, live regions for async results, reduced motion.
11. **Performance budgets** in CI (Lighthouse CI): LCP < 2.5 s, INP < 200 ms, JS initial < 250 KB gz, route-level code splitting.
12. **POS PWA shell** support: service worker registration, IndexedDB wrapper (Dexie) for a read-only catalog cache and local **unposted draft** storage used by P12, plus a connectivity state hook. There is no write queue for posting commands (C1-12).
13. **UX writing guide** `docs/ux/guide.md` (tone, error messages that say what happened and how to fix it, Colombian accounting terminology glossary: tercero, comprobante, causación, etc.).

## Required tests (T-P08-nn)
01 axe on all Storybook stories: 0 serious/critical. 02 Keyboard-only flows for the grid and document pattern. 03 Generated client drift check. 04 MoneyInput never uses floats (unit tests with
`0.1+0.2` cases, large values 999.999.999.999,9999). 05 Route registry hides modules without permission. 06 Lighthouse budgets. 07 Visual regression baseline. 08 i18n lint.

## Acceptance (G1)
The shell and design system are merged; Storybook is published as a static artifact; module teams have a `features/_template` scaffold with an example list + document page.

## Sources
WCAG 2.2 (T-13); benchmark UX patterns (shared/06).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P08.md` when done)
```
# Handoff P08
Status: DONE | DONE-WITH-BLOCKERS (list BLK ids) | NOT-DONE (reason)
Merged PRs / commits:
Owned paths touched:
Contracts consumed (versions) / CCRs filed (ids, status):
Interfaces provided (+ fakes location):
Tests: counts, coverage %, scenario ids passing, notable property/concurrency/failure-injection tests
Requirements covered (UR ids) and acceptance criteria met (checklist):
Parameters/sources used (ids, statuses) and anything still unverified:
Known limitations, [A] items delivered vs flagged off:
Decisions taken under ambiguity (with rationale):
How to demo (commands, URLs, users):
```
