# Gate defects — wave 01 / G0 (integrator, 2026-11-09)

## D-W01-1: dev stack migrations fail — DB settings contract mismatch between P01 and P03

* Behaviour: `make dev-up` builds the images and starts db/valkey/keycloak/minio/mailpit/proxy,
  then the one-shot migrate job aborts with
  `django.core.exceptions.ImproperlyConfigured: settings.DATABASES is improperly configured. Please supply the ENGINE value.`
  Docker and image pulls work in the cloud VM; this is a product defect, not a platform limit.
* Cause: `deploy/dev/compose.yaml` (P03) passes `ERP_DB_HOST`, `ERP_DB_PORT`, `ERP_DB_NAME`,
  `ERP_DB_USER`, `ERP_DB_PASSWORD`; `backend/erp/settings/base.py` (P01) reads only `DATABASE_URL`
  (default: local unix socket), so inside the container no usable database is configured.
* Modules: `backend/erp/settings/base.py` (P01), `deploy/dev/compose.yaml`, `scripts/ci/dev-up.sh` (P03).
* Impact on G0: none of the G0 gate tests (T-X-QA-06, T-X-QA-07) need the stack; they pass.
  `make dev-up` / `dev-seed` / `dev-health` do not succeed on the integrated head; stack-dependent
  tests of later waves will fail until one side adopts the other's variables (e.g. settings build
  DATABASES from `ERP_DB_*` when `DATABASE_URL` is unset, or compose sets `DATABASE_URL`).
* Decision: operator. Suggested owner: P01 (settings) via change request, or P03 (compose).

### D-W01-1 — RESOLVED (with CI-1…CI-4)
Resolved by FIX-D-W01-1 v2 (branch claude/new-session-7cbu2u, merge 2c16425). Integrator re-ran `make ci`, `scripts/ci/ci-parity.sh` (policy, p03-tests, make-ci, compose-startup: 4/4) and `make dev-up && make dev-seed && make dev-health` (migrations included): PASSED at 2026-11-09T21:06Z UTC. Fixed: D-W01-1 settings x compose; CI-1 forbidden-pattern scan; CI-2 P03 tests dev deps; CI-3 make ci with network PostgreSQL; CI-4 compose start-up incl. ERP_FIELD_ENCRYPTION_KEY.

### CI-W02 — RESOLVED (CI parity on main after wave 02)
Resolved by FIX-CI-W02 v1 (branch claude/new-session-itc56d, merge 6054a22). Integrator re-ran tools/parity_gate.py (scripts/ci/ci-parity.sh: policy, p03-tests, make-ci incl. mypy backend, compose-startup incl. DR drill: 4/4) and the dev stack: PASSED at 2026-11-10T00:53Z UTC. Fixed: CW-1 forbidden pattern in a workflow test payload; CW-2 ADR-004 static checks (secured views, advisory locks via kernel tenancy, webhook transport in an adapter); CW-3 mypy backend 0 errors (apps wired); CW-4 make ci typecheck = CI. UI login: DEV-LOGIN OK user=ui-preview file=deploy/dev/secrets/ui-preview.env origin=http://localhost:8080 .

### IMG-W02 — RESOLVED (critical zlib in erp-web; image scan in local CI parity)
Resolved by FIX-IMG-W02 v1 (branch claude/new-session-u1ozj6, merge b2c00de): web stage upgrades zlib to >= 1.4-r0 (CVE-2026-4738, caddy 2.11.9-alpine base; CCR P08-7), scripts/ci/ci-parity.sh runs image-scan in the default compose-startup run (so tools/parity_gate.py scans the images of each merge), stale erp-web images are neither scanned nor served. Integrator re-ran tools/parity_gate.py: 4/4 PASSED at 2026-11-10T03:17Z UTC.

### UI-LOGIN-W03 — RESOLVED (UI preview could not sign in; CCR P08-6, P08-1)
Resolved by FIX-UI-LOGIN-W03 v1 (branch claude/new-session-aw85gy, merge 5df2568): backend image ships the permission catalog and the module registry; /api/v1/ui/context activates the bearer context and returns companies/roles/permissions; /api/v1/ui/modules filters the registry server-side; registry navigation codes resolved from catalog permissions; SPA OIDC authorization code + PKCE with client erp-web (callback /oidc/callback, RP-initiated logout); API client sends the bearer token. Integrator re-ran tools/parity_gate.py (4/4), tools/dev_login.py and tools/ui_snapshot.py (login OK) at 2026-11-10T19:39Z UTC.

### P07-CPU-W05 — RESOLVED (flaky formula CPU budget; rejected P09b in wave 05)
Resolved by FIX-P07-CPU-W05 v1 (branch claude/beautiful-ptolemy-yx764c, merge e62aebc): the 50 ms per-formula CPU budget (D-008, unchanged) no longer charges cyclic-GC pauses of the evaluating thread; topology/perf tests use deterministic clocks. Integrator re-ran tools/parity_gate.py (4/4), the GC fault injection (3/3) and the formula tests 10/10 at 2026-11-11T16:46Z UTC.
