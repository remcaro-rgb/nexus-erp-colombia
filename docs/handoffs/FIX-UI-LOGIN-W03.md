# Handoff FIX-UI-LOGIN-W03 — signed-in UI preview (v1)

Fix session after wave 02 (base `main d601cff`), parallel to wave 03. Paths: kernel views/interfaces (P01), `backend/iam/**`
(P04), `frontend/src/{app,lib,shell,i18n}` (P08), backend Dockerfile (P03). No wave-03 path, no catalog edit, no
`package.json`/lockfile change, no realm/proxy/tools change.

## Root cause and fix per item
| Item | Root cause | Fix |
|---|---|---|
| UL-1 | `catalog.yaml` and `module-registry.json` were excluded from the backend build context; `REPO_DIR` is `/` in the image → `catalog_codes()` empty, `permissions=0`. | `.dockerignore` re-includes both files; runtime stage `COPY --chown=root:root --chmod=0444` them to `/contracts/permissions/catalog.yaml` and `/docs/spec/ui/module-registry.json`. The parent dirs are pre-created `0755` (`install -d`), because BuildKit gives auto-created parents the `--chmod` mode (0444 = not traversable by uid 10001; seen on the first dev-stack run as `PermissionError`). `USER 10001:10001` and the pinned bases unchanged. |
| UL-2 | `UiContextView` read `tenancy.current()`, but DRF authentication runs after `TenancyMiddleware`; nothing activated `request.erp_context` → 500 `tenancy_not_bound` for a valid token. Payload lacked the shell fields. | `kernel.views._ContextView.initial()` activates `request.erp_context` (`tenancy.activate`, closed in `finalize_response`), default DRF auth (IAM bearer) → no token 401 problem; authenticated without context still fails closed. New kernel interface `kernel.interfaces.ui.UiShellProvider` (Protocol: `context_extras`, `modules`), IAM implementation `iam.ui.IamUiShellProvider` registered in `IamConfig.ready()`. The kernel never imports `iam`. `/ui/context` adds `companies[] {id, legal_name, nit, code}` (active memberships only), `roles[]`, `permissions[]`, `user {display_name, email}`, `session_idle_minutes` (30 = realm SSO idle), `feature_flags {}`. `build_ui_context` unchanged. |
| UL-3 | `/api/v1/ui/modules` did not exist; the shell fails closed (BootError). | `kernel.views.UiModulesView` at `api/v1/ui/modules` (`backend/erp/urls.py`) delegates to the provider; no provider → 503 `service_unavailable`; `company_id` must be one of the principal's context companies (`require_company` → 422). The IAM provider loads the shipped registry once and returns `{groups, modules}` in registry order with every unauthorized module/submenu/tab/KPI/panel absent and an unauthorized quick action `null`. Missing registry → 503 (never an empty/fake registry). |
| UL-4 | Registry navigation codes (`shell.access`, `ledger.read`, …) are not catalog codes → nothing visible, Inicio included. | `backend/iam/policy/nav.py`: rules + `NAV_ALIASES`, deny by default (below). `/iam/me` and `/ui/context` return effective catalog codes ∪ resolved navigation codes (`iam.ui.effective_permissions`). CCR `contracts/change-requests/FIX-UI-LOGIN-W03-1.md` to P02/P04. |
| UL-5 | The SPA had no sign-in at all. | `frontend/src/lib/auth/{pkce,oidc,index}.ts` + `frontend/src/app/AuthRoutes.tsx`, wired in `App.tsx` (flow below). |
| UL-6 | The API client sent no `Authorization`; 401 was a generic boot error. | `createApiClient(fetcher?, base?, auth?)`: bearer header when a session exists; 401 → one refresh → one retry (same Idempotency-Key) → else sign-in redirect with the current URL; `BootError` shows "Redirigiendo al inicio de sesión…" for that 401 and stays the trace-id card for every other failure. Abort, idempotency, problem mapping, stale-context handling unchanged. |

## Auth flow (Authorization Code + PKCE S256, public client `erp-web`, realm `erp-dev`)
1. Discovery `GET <origin>/auth/realms/erp-dev/.well-known/openid-configuration` (issuer built from `window.location.origin`;
   the discovered issuer must equal it and every endpoint must be same-origin, else `discovery_failed`).
2. `signIn(returnPath)`: random `code_verifier` (32 bytes), `state`, `nonce` → `sessionStorage["nexus.oidc.pending"]`;
   redirect to `authorization_endpoint` with `response_type=code`, `scope=openid profile email`, `code_challenge`,
   `code_challenge_method=S256`, `redirect_uri=<origin>/oidc/callback`.
3. `/oidc/callback`: "Conectando…" (DEV banner). The pending entry is removed first (one shot), `state` is compared, the code
   is exchanged by `fetch` POST to `token_endpoint` (no secret). The id_token payload (from the token endpoint) is checked:
   `iss`, `aud`/`azp` = `erp-web`, `exp`, `nonce`. Then `navigate(returnPath, {replace: true})` (default `/m/inicio`).
   A state/nonce mismatch or IdP error shows an error card with "Volver a intentar" (user action only: never loops).
4. Storage: access token and id_token **in memory**; only the refresh token in `sessionStorage["nexus.oidc.refresh"]`;
   nothing auth-related in `localStorage` (unit-tested).
5. Full page load of `/m/*`: `RequireAuth` restores silently with the refresh grant (deduplicated: refresh tokens are
   single-use, `revokeRefreshToken=true`), without flashing the entry screen; if that fails it redirects straight to the
   authorize endpoint with the current path (Keycloak's SSO cookie answers without a form). `/` signed in → `/m/inicio`;
   signed out → entry screen (Brand tile, DEV banner, "Bienvenido", primary `Button` "Iniciar sesión").
6. Refresh ~60 s before expiry (timer). Idle timeout: `AppShell`'s `SessionTimeout` uses `session_idle_minutes` → logout.
7. Logout ("Cerrar sesión" in `UserMenu` → `ShellProvider.onLogout`): the shell clears its cache (`store.clear()`), the
   session clears tokens, then RP-initiated logout at `end_session_endpoint` with `post_logout_redirect_uri=<origin>/` and
   `id_token_hint` (or `client_id` when the id_token is gone).
* **Why `/oidc/callback`:** the proxy sends `/auth/*` to Keycloak, and the preview treats paths matching `/(login|auth)\b/`
  as not signed in; `/oidc/callback` is served by the SPA (`try_files … /index.html`) and is covered by the realm's
  `${ERP_PUBLIC_ORIGIN}/*` redirect URIs.
* CSP stays `script-src 'self'; connect-src 'self'` (no inline script; all calls same-origin).

## Kernel interface and IAM implementation
`kernel/interfaces/ui.py::UiShellProvider` (runtime-checkable Protocol, exported from `kernel.interfaces`, not in
`CORE_INTERFACES`). `iam/ui.py::IamUiShellProvider` registered at `IamConfig.ready()` with `kernel.services.register`.
Loaders moved to `iam/policy/catalog.py` (`catalog_codes`, `module_registry`); `iam.api.views.catalog_codes` is re-exported.

## Navigation-code rules (`iam/policy/nav.py`)
1. `shell.access` → INTERNAL principal (or AI/MCP acting for one) with an active company.
2. `NAV_ALIASES` row → any allowed catalog code matching a pattern.
3. `<m>.<a>` (catalog action) → some allowed `<m>.<r>.<a>`.
4. `<m>.<r>.<a>` (catalog action) → the code if the catalog has it, else an allowed `<m>.<r>….<a>` (resource prefix).
5. Else denied. The full `NAV_ALIASES` table, with reasons, is in the code and in CCR FIX-UI-LOGIN-W03-1.

## Modules per role (real catalog at the base commit; `nav.filter_registry`)
| Role | Modules |
|---|---|
| `ui-preview-readonly` (`*.*.read` minus writes/admin) | every module except `administracion` (catalog has read codes for all of them today) |
| `administrador` | all 15 |
| `contador` | inicio, contabilidad, tesoreria, activos, ventas, facturacion, inventario, compras, talento, reportes |
| `auxiliar_contable` | inicio, contabilidad, tesoreria, ventas, compras, reportes |
| `vendedor` | inicio, ventas, inventario · `cajero_pos`: inicio, ventas, pos, inventario |
| `jefe_bodega`, `comprador` | inicio, ventas, inventario, compras |
| `analista_nomina`, `empleado` | inicio, talento |
| `gerente`, `auditor` | all but administracion |
| `agente_ia` | inicio |
The preview only captures modules whose owner task is ACCEPTED (today contabilidad, aprobaciones).

## Compatibility for wave 03
* Unchanged public exports: `createApiClient` (new optional 3rd arg `auth`), `ApiClient`, `ApiError`, `buildUrl` (+ new
  `isAuthRedirect`); `ShellProvider`/`useShell`/`useResource` unchanged (the shell now sends `X-Company-Id` together with
  `company_id` on `/ui/context`, `/iam/me`, `/ui/modules`); `fakeApi`, `renderDs` unchanged. `buildRoutes(api, auth?)` and
  `App({api, auth?})` take an optional session; tests get a synthetic signed-in session from `src/app/test/setup.ts`
  (`setAuth(fakeAuth())`, test helper `src/app/test/auth.ts`). The production bundle has no test mode or bypass.
* A module appears in `/ui/modules` automatically once its catalog codes exist and a role grants them (rules 3/4; tested
  with a synthetic catalog). New non-grammar registry codes need a `NAV_ALIASES` row (the resolvability test fails otherwise).
* Feature code calling `api.get(..., {params: {company_id}})` without `X-Company-Id` gets the principal's default company
  context on the server (unchanged behaviour; noted as residual).

## Step 4 results
All in this session (stack built from this branch; preview code = `c3e55f4`, later commits change only test fixtures and this file).
* `mypy backend`: Success (257 files). Focused pytest (`fix_ui_login_w03 or ui_context or ui_modules or nav`): 16 passed. Whole backend: 864 passed.
* `tsc --noEmit` exit 0; `make frontend-test`: 17 files / 80 tests passed (81 after the NotFound heading); P08 shell e2e (`pnpm --dir frontend run e2e`): 3 passed.
* `make ci`: exit 0 (`make ci: OK`).
* `make dev-up && make dev-seed && make dev-health`: exit 0 (all services PASSED).
* `tools/dev_login.py ensure`: `DEV-LOGIN OK user=ui-preview file=deploy/dev/secrets/ui-preview.env origin=http://localhost:8080 (DEV-UI-PRINCIPAL OK tenant=2b55fda7-6f59-56c8-9fce-10f2e9464e19 company=3d9dd620-bfc8-51a4-a8a4-fefccd75fe50 user=01a1c592-4db2-724d-bdba-0c4e6ceff8da permissio)`
  * The tool cuts its summary at 160 characters, so `permissions=N` is truncated (integrator tooling, not editable here).
    Same formula in the api container (`len(granted_codes(catalog_codes(), ROLE_GRANTS, ROLE_DENIES))`, catalog 795 codes):
    **permissions=256** (was 0 before UL-1).
  * First run on this branch failed with `PermissionError: /contracts/permissions/catalog.yaml` (BuildKit `--chmod` on auto-created
    parent dirs) → fixed with `install -d -m 0755`; second run OK.
* First preview run: `login form not found` — Keycloak publishes `token_endpoint` with its internal host (`http://localhost/…`, no
  port), so discovery was refused; fixed by re-basing realm endpoints onto the page origin (`oidc.ts › rebase`).
  Second run captured Administración as a page because `NotFound` had no heading; `NotFound` now has an `h1`.
* `tools/ui_snapshot.py --wave 3` (real screenshots, `NEXUS_REHEARSAL` unset): `UI-SNAPSHOT: OK 9 images, 15 pages not captured, 0.60 MB, not committed (--commit not given)`
  INDEX rows (captured):

| Page | Route | Theme / variant | Status | Contract notes |
|---|---|---|---|---|
| Login / entry | `/` | light | captured | header not found; sidebar not found; AI panel not found |
| Login / entry | `/` | dark | captured | header not found; sidebar not found; AI panel not found; dark class ✓ |
| Inicio | `/m/inicio` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Inicio | `/m/inicio` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Contabilidad | `/m/contabilidad` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Contabilidad | `/m/contabilidad` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Aprobaciones y auditoría | `/m/aprobaciones` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Aprobaciones y auditoría | `/m/aprobaciones` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Inicio (sidebar + AI panel collapsed) | `/m/inicio` | collapsed | captured | header 64 ✓; sidebar 80 ✓; AI panel closed ✓; collapse control(s) not found (aria-label/aria-controls), captured as rendered |

  `administracion`: `not yet built (HTTP 200, heading: Página no encontrada)`: absent for the read-only role (correct; no IAM admin capability).
  Checked by eye: `inicio-light.png` shows the real shell (header, sidebar, DEV banner, "Vista Previa UI S.A.S." in the company
  selector, user "ui-preview (sintético)"); `contabilidad-light.png` has no "Cierres" submenu and no quick action (denied).
  `docs/ui-progress/**` reverted after every run.
* `tools/parity_gate.py`: first run `PARITY-GATE FAILED 2/4` (secret scanners flagged synthetic token fixtures in tests) → fixture values
  renamed `example-*`; rerun **`PARITY-GATE OK 4/4 (HEAD 925c2b8)`**.
* `make dev-down` (volumes kept).

## GitHub Actions
Observed through the GitHub API: run 157 (`ci`, head `925c2b8`) **success**; jobs `policy`, `make ci (lint, unit, contract)`,
`T-P03-01 docker compose up healthy from a clean clone`, `P03 tests (T-P03-01..06)` all success. (Run 146 on `1d8faab` failed
only on the repo secret scan of the same fixtures; fixed.)

## Residual items
* OpenAPI paths for `ui/context` / `ui/modules` → CCR P08-1 stays with P02 (plus CCR FIX-UI-LOGIN-W03-1).
* KPI / panel / search endpoints still `not_available`; `<m>.kpi.read`-style codes have no catalog code yet (cards absent).
* `integrations.read` has no catalog code yet (submenu absent for everyone).
* Feature modules should send `X-Company-Id` with `company_id` for multi-company users (or the API client could add it).
