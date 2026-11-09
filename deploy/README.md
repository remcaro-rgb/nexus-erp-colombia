# deploy/ — containers, compose and environments (owner: P03)

**DEV-only run (CCR-DEV-001).** The only environment that is built and run is `dev`, with synthetic data. `test` is the
ephemeral CI stack. `staging` and `prod` are **reference designs, not deployed**. Nothing here is certified or production-ready.

## Layout
| Path | What |
|---|---|
| `dev/compose.yaml` | DEV runtime (D-P03): proxy, db, valkey, keycloak, minio, mailpit + profiles `app`, `web`, `mcp`, `observability`, `tools` |
| `dev/Caddyfile`, `dev/fallback/` | same-origin proxy: `/healthz`, `/api/`, `/ws/`, `/mcp/`, `/auth/`, `/mailpit/`, `/grafana/`, `/`; security headers |
| `dev/gen-secrets.sh` | writes `dev/secrets/dev.env` + `dev-credentials.env` (umask 077, mode 0600, never overwrites, prints paths only) |
| `dev/db/init/10-roles.sh` | roles `erp_owner` (owns db/schema, migrations), `erp_app` (NOSUPERUSER NOBYPASSRLS, owns nothing), `erp_readonly`, `erp_platform` |
| `dev/backup.sh`, `dev/restore.sh`, `dev/check-health.sh` | pg_dump/pg_restore (restore needs `RESTORE erp` or `--yes`), health helper |
| `dev/dev.env.example` | placeholder values for `docker compose config` validation only |
| `docker/backend.Dockerfile` | targets `api` (gunicorn+uvicorn), `worker` (celery), `scheduler`, `outbox-relay`, `mcp`; uid 10001 |
| `docker/frontend.Dockerfile`, `docker/web.Caddyfile` | Vite build served by Caddy as uid 10001 |
| `observability/` | OTel Collector (PII scrubbing generated from `pii-patterns.json`), Prometheus, Grafana provisioning + 5 dashboards |
| `dr/invariants.d/*.sql` | DR drill invariants (platform); modules add `NN-<module>.sql` via CCR |
| `keycloak/` | realm import directory; `realm-template.json` is owned by P04 |
| `../docker-compose*.yml` | ADR-014 base + `.dev` / `.test` / `.prod` overlays; reuse `dev/compose.yaml` via `extends`, add `build:` |

Commands: `make dev-up | dev-seed | dev-health | dev-down` → `scripts/ci/dev-*.sh`. Runbooks: `docs/runbooks/`.

## Environment matrix
| Setting | dev (run) | test (CI, ephemeral) | staging (reference) | prod (reference) |
|---|---|---|---|---|
| Compose files | `deploy/dev/compose.yaml` | `docker-compose.yml` + `.test.yml` | `docker-compose.yml` + `.prod.yml` + staging env | `docker-compose.yml` + `.prod.yml` |
| `ERP_ENVIRONMENT` | `dev` | `dev` | `staging` | `prod` |
| Data | synthetic (`seed_demo --synthetic`) | synthetic, tmpfs | synthetic/anonymised | real (future project) |
| Bind / origin | `127.0.0.1:${ERP_DEV_PORT:-8080}` (fallback 8081–8090), `http://localhost:<port>` | runner loopback | TLS 1.2+/1.3 behind LB | TLS 1.3 behind LB |
| Secrets | `deploy/dev/secrets/*.env` (0600, generated) | generated per job | Docker secrets / Vault | Vault/OpenBao or cloud KMS (ADR-012) |
| Keycloak | `start-dev`, dev-file DB, realm `erp-dev` | same | `start --optimized`, PostgreSQL | same + MFA enforced |
| DIAN / PILA / banks / payments | `FIXTURE_NOT_CERTIFIED` | fixtures | `SANDBOX` with operator credentials | production (blocked by EG gates) |
| E-mail | Mailpit (`LOCAL_CAPTURE`) | Mailpit | SMTP relay sandbox | SMTP relay |
| LLM | fixture unless a key is in the secrets file | fixture | sandbox key | provider key |
| Backups | `backup.sh` (pg_dump), `dr_drill.py` | DR drill job | WAL-G PITR to MinIO/S3 | WAL-G PITR, nightly full, object lock |
| Observability | optional profile `observability` | off | OTLP → collector | OTLP → collector → managed backend |
| Network | `backend` network internal (no egress) | same | egress allow-list per adapter | same |
| Restart | `unless-stopped` | `unless-stopped` | `unless-stopped` | `unless-stopped` + orchestrator |

## Images (pinned by digest, D-028; registries on the default allow-list only)
| Image | Tag | Use |
|---|---|---|
| `docker.io/library/postgres` | 18.6-alpine | database (C2-1: PostgreSQL 18) |
| `docker.io/valkey/valkey` | 8.1.11-alpine | cache + Celery broker |
| `docker.io/keycloak/keycloak` | 26.8.2 | OIDC (Docker Hub, not quay.io — cloud allow-list) |
| `docker.io/pgsty/minio` | RELEASE.2026-08-04T00-00-00Z | S3-compatible object storage (see decision P03-D3) |
| `docker.io/axllent/mailpit` | v1.33.1 | local mail capture |
| `docker.io/library/caddy` | 2.11.9-alpine | proxy + web image runtime |
| `docker.io/otel/opentelemetry-collector-contrib` | 0.165.0 | observability profile |
| `docker.io/jaegertracing/jaeger`, `prom/prometheus`, `grafana/grafana` | 2.23.0, v3.16.0, 13.2.5 | observability profile |
| `docker.io/stoplight/prism` | 5.15.10 | contract mock (dev overlay, profile `mock`) |
| `docker.io/library/python` | 3.13.18-slim-trixie | backend images |
| `docker.io/library/node` | 22.23.4-trixie-slim | frontend build stage |
| `ghcr.io/astral-sh/uv` | 0.11.32 | uv binary in the backend build stage |
| `zricethezav/gitleaks`, `anchore/syft`, `ghcr.io/anchore/grype` | v8.30.1, v1.57.0, v0.121.2 | CI secret scan, SBOM, vuln scan |

Digests are in the compose files / Dockerfiles; Dependabot (`.github/dependabot.yml`) proposes updates weekly.
Measured (2026-11-09, synthetic Django skeleton): backend images 294 MB (< 400 MB budget), uid 10001.

## Environment variables the application receives (interface for P01 settings — CCR P03-1)
`ERP_ENVIRONMENT`, `ERP_SYNTHETIC_DATA`, `ERP_PUBLIC_ORIGIN`, `ERP_VERSION`, `ERP_SERVICE`, `DJANGO_SETTINGS_MODULE`,
`ERP_SECRET_KEY`, `ERP_FIELD_ENCRYPTION_KEY`, `ERP_DB_HOST/PORT/NAME/USER/PASSWORD` (app = `erp_app`; the `migrate` job
gets `erp_owner`), `ERP_CACHE_URL`, `ERP_CELERY_BROKER_URL`, `ERP_OIDC_ISSUER` (public, `<origin>/auth/realms/erp-dev`),
`ERP_OIDC_INTERNAL_ISSUER`, `ERP_OIDC_API_CLIENT_SECRET`, `ERP_S3_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET`,
`ERP_EMAIL_MODE/HOST/PORT`, `ERP_DIAN_MODE`, `ERP_PILA_MODE`, `ERP_BANK_MODE`, `ERP_PAYMENTS_MODE`, `ERP_LLM_MODE`,
`OTEL_*`, `TZ=America/Bogota`. Seed job: `ERP_DEMO_CREDENTIALS_FILE=/run/nexus/dev-credentials.env`.
Keycloak gets `ERP_PUBLIC_ORIGIN`, `ERP_SMOKE_CLIENT_SECRET`, `ERP_MCP_CLIENT_SECRET`, `ERP_API_OIDC_CLIENT_SECRET` for
`${...}` placeholders in P04's realm template.

## Process contract of the backend images
`api`: `gunicorn erp.asgi:application -k uvicorn_worker.UvicornWorker` (falls back to uvicorn / Django dev server with a
warning if those are not locked) · `worker`: `celery -A ${ERP_CELERY_APP:-erp} worker` · `scheduler`: `manage.py run_scheduler`
(P07) · `outbox-relay`: `manage.py outbox_relay` (P01) · `mcp`: `python -m mcp_server` (P20). `make dev-up` starts only the
services whose code exists (celery locked, management command present, image built) and lists the rest as
`not_available_yet` in `make dev-health`. Containers never migrate on start; `dev-up` runs the `migrate` job explicitly.

## Observability contract (metric names used by the dashboards)
`http_server_request_duration_seconds` (OTel semconv, labels `http_route`, `http_response_status_code`, `tenant_id`),
`erp_outbox_pending_events`, `erp_outbox_oldest_pending_age_seconds`, `erp_outbox_publish_failures_total`,
`erp_celery_queue_length`, `erp_celery_task_failures_total`, `erp_celery_task_runtime_seconds`, `erp_dian_gateway_mode`,
`erp_dian_documents_total`, `erp_dian_contingency_active`, `erp_llm_cost_cop_total`, `erp_llm_tokens_total`,
`erp_ai_tool_denied_total`. Logs: JSON with `trace_id`, `tenant_id`, `company_id`, scrubbed by
`deploy/observability/pii-patterns.json` (backend filter + collector).

## Targets (not measured guarantees)
RPO ≤ 15 min (WAL archiving/PITR, prod reference), RTO ≤ 4 h (restore drill). The DEV drill measures restore time of the
dev database only (`reports/dr/dr-drill.json`). Fiscal documents: object versioning + retention lock with a configurable
10-year policy is a prod-reference requirement (MinIO object lock needs a bucket created with locking; documented in
`docs/runbooks/backup-and-dr.md`).

## `[A]` items
`deploy/k8s/` (Helm/Kustomize, HPA, PDB) — **not delivered, flagged off** (optional, out of scope for the DEV-only run).
