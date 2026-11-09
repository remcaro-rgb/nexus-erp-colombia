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
