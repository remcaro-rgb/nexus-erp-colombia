# Observability

* Enable in DEV: `NEXUS_OBSERVABILITY=1 make dev-up` → otel-collector, Jaeger, Prometheus, Grafana (`/grafana/`, user
  `nexus-dev-admin`, password `ERP_GRAFANA_ADMIN_PASSWORD` in `dev.env`). Services export OTLP to
  `http://otel-collector:4318` when `OTEL_SDK_DISABLED=false`.
* Pipeline: OTLP → `memory_limiter` → `attributes/drop_secrets` (password/secret/token/authorization/cookie/api key
  attributes deleted) → `transform/scrub_pii` → batch → Jaeger (traces) / Prometheus remote-write (metrics) / debug (logs).
* PII scrubbing: one pattern file `deploy/observability/pii-patterns.json` (e-mail, NIT, bank account, long numbers,
  cédula). `scripts/ci/render_otel_scrub.py` generates the collector statements; CI fails if they drift
  (`--check`). Only free-text attributes are scrubbed so `trace_id`/`tenant_id`/`company_id` stay intact. The backend
  logging filter must use the same file (CCR P03-1). Test: `T-P03-04`.
* Dashboards (`deploy/observability/dashboards/`): API latency/errors, outbox lag, Celery queue depth, DIAN gateway status
  (SIMULADO in dev), LLM cost per tenant. Metric names: `deploy/README.md` § Observability contract.
