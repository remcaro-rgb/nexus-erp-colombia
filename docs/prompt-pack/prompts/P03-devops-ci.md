> **Prompt P03 · Wave W0 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/03 ADR-003, ADR-012, ADR-014; shared/09 EG-9, EG-12; shared/10 §A and §D. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P03 — DevOps, Containers, CI/CD, Observability, Backup

## Mission
Make the system buildable, testable, deployable in containers, observable, and recoverable at minimum maintenance cost (ADR-014).

## Owned paths
`docker-compose*.yml`, `deploy/**`, `.github/**` (or the equivalent CI), `.devcontainer/**`, `docs/runbooks/**`, `scripts/ci/**`.

## Deliverables
1. **Dockerfiles** (multi-stage, non-root, distroless or slim, pinned digests): backend (gunicorn + uvicorn workers), worker (celery), scheduler (P07 loop), outbox-relay, mcp,
   frontend (static build served by Caddy with strict CSP, HSTS, and security headers). Images < 400 MB; SBOM per image (syft); vulnerability scan (grype/trivy) failing on critical.
2. **Compose:** `docker-compose.yml` (postgres:18, valkey, keycloak, minio, mailpit (dev), otel-collector, prism mock), overlays `.dev.yml` (hot reload), `.test.yml` (ephemeral),
   `.prod.yml` (no dev services, secrets via Docker secrets, resource limits, healthchecks, restart policies, read-only root FS where possible).
   PostgreSQL roles: `erp_owner` (migrations), `erp_app` (NOSUPERUSER, NOBYPASSRLS, not owner), `erp_readonly` (replica/reporting), `erp_platform`.
   A one-shot `migrate` job runs as `erp_owner`, and the app runs as `erp_app`.
3. **CI pipeline:** lint (ruff, eslint), type-check (mypy, tsc), unit tests with Postgres service, `makemigrations --check`, contract tests, forbidden-pattern scan
   (`TODO|FIXME|assert True|pytest.mark.skip(?!.*BLK-)|float\(|except:|print\(` in backend), secret scan (gitleaks), SAST (semgrep with a Django ruleset), dependency audit
   (pip-audit, pnpm audit), license check, frontend build + vitest + Playwright smoke + axe, docker build + scan, and from W2 `make scenarios`. Merge queue with required checks.
4. **Static checks** required by ADR-004: every DRF view inherits `SecuredViewSet`/`SecuredAPIView`; no raw SQL outside the allow-listed modules (`iam/rls`, `reporting/compiler`,
   migrations); no `requests`/`httpx` import outside adapters.
5. **Observability:** OpenTelemetry traces/metrics/logs → collector → exporters (OTLP; dev: Jaeger + Prometheus + Grafana optional profile). Structured JSON logs with
   `trace_id`, `tenant_id`, and `company_id`, and **no PII** (log scrubbing filter + test). Dashboards: API latency/errors, outbox lag, Celery queue depth, DIAN gateway status, LLM cost per tenant.
6. **Backup/DR:** WAL archiving with pgBackRest or WAL-G to S3/MinIO, nightly full + PITR; restore script + `make dr-drill` running restore into a fresh container and executing
   the invariant checks (shared/07 §9). Object storage versioning + retention lock for fiscal documents (10-year policy configurable). Document RPO/RTO targets as targets.
7. **Runbooks:** deploy, rollback, upgrade (Django 6.2 LTS migration plan due by Oct 2027; PostgreSQL minor updates), certificate rotation (DIAN signing cert), secret rotation,
   incident response (incl. data-breach notification steps per R-DP-04), DIAN outage → contingency mode, scale-out (more workers, read replica).
8. **Environments:** dev, test (ephemeral per PR), staging, prod. A config matrix in `deploy/README.md`.
9. **Optional `[A]`:** `deploy/k8s/` Helm chart or Kustomize with the same images, HPA for workers, and PodDisruptionBudgets.

## Required tests
T-P03-01 `docker compose up` healthy from a clean clone (CI job). 02 App role cannot bypass RLS (`SELECT rolbypassrls` false; owner ≠ app). 03 DR drill restores and the invariants pass.
04 Log scrubbing removes NIT/cédula/email/bank account patterns. 05 Images run as non-root. 06 Security headers present (CSP, HSTS, X-Content-Type-Options, frame-ancestors).

## Acceptance (G0 for CI and compose; G4 for DR and observability)


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P03.md` when done)
```
# Handoff P03
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
