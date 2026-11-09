# Architecture Decision Records

Status of all ADRs: **Accepted** (2026-11-08). Changes go through a CCR to P00.

## ADR-001 Modular monolith on one PostgreSQL database
* **Decision:** One deployable Django backend containing bounded-context apps (shared/02), sharing one PostgreSQL 18 database and one transaction per
  business command. Async work goes through the transactional outbox and Celery workers. The MCP server and frontend are separate containers.
* **Why:** The user requires accounting postings and stock updates *in-line*. With one database transaction, invoice + stock move + valuation + journal entry
  + outbox event commit or roll back together. That is a simple atomicity guarantee.
* **Rejected – microservices per module:** These would need sagas/2PC for every invoice. Partial failure would show unbalanced ledgers or phantom stock, and they cost more to run and maintain.
  We can extract services later at the outbox seams (einvoice gateway, AI agent, MCP) without changing the core.
* **Rejected – event-sourced ledger:** It is powerful but expensive to build and operate. We use append-only journal and stock ledgers plus an outbox, which gives
  auditability without full event sourcing.

## ADR-002 Stack: Python 3.13 / Django 5.2 LTS / DRF / PostgreSQL 18 / Celery+Valkey / React+TS
* **Evidence (opened 2026-11-08):** Django download page lists 5.2 LTS with extended support to **April 2028**. 6.1 (current) ends extended support in December 2027.
  6.2 LTS is planned for April 2027 with support to April 2030. Django moves to annual "Django 2028" releases in January 2028 (DEP 20 blog, Aug 10 2026).
  PostgreSQL versioning page: 18 (current minor 18.6) is supported until **November 14, 2030**.
* **Decision:** Start on Django 5.2 LTS for the longest overlap. Schedule the upgrade to 6.2 LTS within 6 months of its release (runbook item for P03).
  PostgreSQL 18.
* **Why this has the best maintenance cost-benefit:** Django gives an ORM with migrations, an admin, auth integration, a mature security track record, and a big hiring pool.
  Decimal is native. DRF plus drf-spectacular gives OpenAPI. PostgreSQL provides RLS, NUMERIC, partial indexes, `FOR UPDATE SKIP LOCKED`, JSONB, and pgvector (optional) in one engine.
  React/TypeScript has the largest ecosystem for dense, accessible data UIs.
* **Rejected:** Java/Spring (heavier for a small team), Node/NestJS (weaker decimal and ORM migration story for accounting), Go (slower CRUD velocity),
  Odoo/ERPNext fork (license and upgrade lock-in, and it does not meet the "own the code" goal).

## ADR-003 Multi-tenancy: shared schema, `tenant_id` + `company_id`, PostgreSQL RLS + app enforcement
* Every tenant-scoped table has `tenant_id NOT NULL`. Company-scoped tables also have `company_id NOT NULL` with a composite FK `(tenant_id, company_id)`.
* RLS: `ALTER TABLE ... ENABLE ROW LEVEL SECURITY; ALTER TABLE ... FORCE ROW LEVEL SECURITY;` Policies use
  `current_setting('app.tenant_id', true)::uuid` and `app.company_ids` (a uuid array of companies the principal may see).
* The application connects as role `erp_app`: **not superuser, not BYPASSRLS, not table owner** (PostgreSQL docs: superusers, BYPASSRLS roles, and normally table
  owners bypass RLS. FORCE makes owners subject to it). Migrations run as a separate owner role in a separate job.
* Connection-pool safety: set context with `SET LOCAL` inside every transaction (`kernel.tenancy.bind()`), so values die at COMMIT/ROLLBACK.
  If PgBouncer is used, use transaction pooling only with `SET LOCAL`. A test asserts that a pooled connection reused across tenants sees zero rows without a binding.
* Cross-tenant jobs (billing, platform metrics) use a separate `erp_platform` role with read-only views on aggregate data only, and every use is audited.

## ADR-004 Authorization: RBAC + ABAC, deny by default, one enforcement path
* `iam.policy.authorize(principal, action, resource|queryset, context)` returns an allow/deny decision. `iam.policy.scope_queryset()` applies row filters
  (company, branch, warehouse, cost center, owner, territory, custom attribute predicates). `iam.policy.field_mask()` returns readable/writable fields.
* Every read path (API serializers, exports, reports/semantic layer, search index, AI tools, MCP tools, notifications, logs) must call these. CI includes
  a static check (P03) that DRF viewsets inherit from `iam.views.SecuredViewSet` and serializers from `iam.serializers.SecuredSerializer`.
* Permission cache: per principal, keyed by `policy_version`. Every policy, role, or assignment change bumps the tenant `policy_version`, so revocation takes effect on the next request
  (target < 5 s). Tokens are short-lived (5–15 min).
* SoD rules (for example, the same user cannot create a vendor and approve its payment) live in policy data, and violations are blocked. Break-glass is a time-boxed elevated role
  that needs a reason and second-person approval, triggers alerts, and produces an immutable audit trail.

## ADR-005 Posting engine and sub-ledgers
* The `ledger.posting.post(document)` service receives a *posting request* built from a module's posting rule set (shared/07), resolves accounts through
  account determination (company + item category + tax code + warehouse + party group + dimension rules), creates the balanced
  entry, rounds, checks period status, and writes. It is called inside the module's unit of work.
* Sub-ledgers (AR open items, AP open items, stock valuation layers, asset book, payroll liabilities) keep a reference to their journal lines. Reconciliation
  queries must prove `GL control account balance = sub-ledger balance` (tested in G2).
* Allocation (for example, payment application to invoices, landed cost to receipts) is a sub-ledger operation. GL posting happens only when value or classification changes.

## ADR-006 Transactional outbox + Celery
* The outbox table is written in the same transaction. The relay process uses `SELECT ... FOR UPDATE SKIP LOCKED` batches and publishes to Celery queues
  (`einvoice`, `notifications`, `reporting`, `ai`, `default`). Consumers dedupe by `event_id`. There is a dead-letter table with a replay UI (P07).
* External calls (DIAN/PT, banks, LLMs, email) happen only in workers, never inside the posting transaction.

## ADR-007 DIAN integration via adapter: technology-provider (PT) first, direct as option
* `einvoice.ports.FiscalGateway` has adapters: `ProviderAdapter` (HTTP API of a contracted PT; the default for cost and time to go-live),
  `DirectDianAdapter` (own software habilitación with DIAN web services; `[A]`, gated), and `SandboxSimulator` (tests only; it cannot run in production, and the settings check fails closed).
* UBL 2.1 XML generation, XAdES-EPES signing, and CUFE/CUDE/CUNE computation are done in-house so documents are reproducible and auditable, even when
  the PT re-signs. The exact algorithms come from the current Anexos Técnicos (shared/04). Tests use DIAN toolbox ("caja de herramientas") examples.

## ADR-008 Inventory valuation
* Supported methods per company and item category: **weighted average (perpetual moving average)** and **FIFO** layers. Standard cost is `[A]`.
  LIFO is not supported. Valuation layers are append-only. Average cost is recomputed in-line under a row lock on `(item, company)`
  (valuation is company-wide by default; per-warehouse valuation is a company option).
* Negative stock: disallowed by default (`allow_negative=false` per company/warehouse). If a company enables it, the issue posts at the last known cost and a
  `negative_stock_correction` adjusts COGS in-line when the receipt arrives (documented, tested, and flagged in reports).

## ADR-009 Reporting/semantic layer instead of raw SQL for AI
* The AI agent and the report builder query a **semantic model** (entities, measures, dimensions, joins) compiled to parameterized ORM/SQL that
  always passes through `iam.policy.scope_queryset` and `field_mask`. Arbitrary SQL, code execution, or shell from AI or users is **not available**.
* Heavy reports run on a read replica when configured, with the same RLS session context.

## ADR-010 AI: provider-agnostic gateway
* `ai_agent.providers.LLMProvider` interface with adapters (OpenAI-compatible HTTP, Anthropic, Google, Azure OpenAI, local vLLM/Ollama). Model routing
  rules are per tenant (cost, latency, data-residency flags). Tenant data is never used for fine-tuning without explicit, recorded consent. Each provider's
  data-retention terms are recorded in the provider registry and shown to tenant admins (an international data transmission/transfer under Ley 1581 Art. 26 → shared/04).

## ADR-011 MCP server
* A separate container implements **MCP protocol revision 2026-07-28** (the current revision per modelcontextprotocol.io versioning page, opened 2026-11-08):
  stateless requests with `_meta` protocol version, the mandatory `server/discover`, Streamable HTTP. Tasks are an extension (`io.modelcontextprotocol/tasks`).
  **SDK decision (evidence 2026-11-08):** use the official **Python `mcp` SDK v2.x**. Its v2.0.0 release (2026-07-28) states that it supports the 2026-07-28 revision and serves every
  earlier revision (2025-era `initialize` clients) from the same server (T-16, search extract). PyPI shows **2.3.0** (2026-10-02) as the latest (T-15, opened). Pin an exact 2.x version in the lock file.
  For 2025-era clients that still use sessions, the deployment needs sticky routing **or** a single replica. Multi-replica 2026 traffic needs the SDK's shared `request_state` key
  configuration (T-18; confirm the exact API in the pinned version's docs).
  **Safe fallback:** if the pinned 2.x fails our conformance suite (EG-10), pin `mcp>=1.28,<2` (maintenance line), serve 2025-11-25 only, keep the tool layer protocol-agnostic,
  and record the limitation in `docs/STATUS.md`. The TypeScript SDK (npm 1.32.2) is **not** chosen, because its revision support was not verified.
* Authorization: the MCP server is an OAuth 2.1 resource server. It publishes Protected Resource Metadata (RFC 9728), validates audience (RFC 8707 resource indicators),
  and supports Client ID Metadata Documents, with Dynamic Client Registration only as a deprecated fallback. Every tool calls the public REST API with the user's delegated token.
  The MCP server has no direct DB access (so there are no side writes).

## ADR-012 Secrets, keys, encryption
* Dev: `.env` (never committed) plus Docker secrets. Prod: Docker secrets or an external secret manager (Vault/OpenBao or cloud KMS/Secrets Manager).
  The DIAN signing certificate (PKCS#12) is stored encrypted, decrypted only in the einvoice worker memory, and access is audited.
* TLS 1.2+ everywhere (TLS 1.3 preferred). The DB uses encrypted storage at the volume level. Column-level encryption (envelope, key per tenant) covers `secret` and
  `sensitive-pii` fields: bank accounts, biometrics templates (if stored at all), health/incapacity details.

## ADR-013 Frontend
* React + TypeScript + Vite, TanStack Router/Query/Table (virtualized), React Hook Form + Zod generated from OpenAPI, Radix primitives
  with an in-house design system (P08), i18n via ICU messages (es-CO default). The POS is a PWA (service worker + IndexedDB), served by the same frontend build.

## ADR-014 Deployment
* Docker images: `backend` (gunicorn/uvicorn), `worker`, `beat-less scheduler` (P07 scheduler loop), `outbox-relay`, `mcp`, `frontend` (static via Caddy/Nginx),
  `keycloak`, `postgres`, `valkey`, `minio` (dev/staging), and an optional `otel-collector`.
* Compose files: `docker-compose.yml` (base), `.dev.yml`, `.staging.yml`, `.prod.yml`. Production on a single node or a small VM pair with managed PostgreSQL is supported.
  Kubernetes manifests are optional (`deploy/k8s/` `[A]`).
* **Proposed** RPO ≤ 15 min (WAL archiving/PITR) and RTO ≤ 4 h (documented restore drill). These are targets to validate in drills, not measured guarantees.

## ADR-015 POS connectivity boundary (online finalization)
* **Default, Tier P:** POS checkout is **online-only**. `POST /pos/tickets:checkout` runs one server UoW that assigns the fiscal number (kernel.sequences),
  computes taxes, issues stock, posts the GL and AR/cash entries, and writes the outbox event for DEE transmission. Nothing is final, numbered, printed as a
  fiscal receipt, or charged until that transaction commits. This keeps C1-1/C1-2 strict for POS exactly as for every other module.
* **When the terminal loses its connection to our server:** the PWA shows a blocking "Sin conexión: no se pueden finalizar ventas" state. Cashiers may still
  browse the cached catalog/prices and build or park carts, which are stored locally as **unposted drafts** (watermarked "BORRADOR – sin valor fiscal", with no number,
  no CUDE, no stock reservation, and no GL). Checkout, tender capture (card/QR/cash acceptance), fiscal numbering, receipt printing, returns, and cash movements are **disabled**.
  On reconnect, the cashier resubmits the draft. The server recomputes prices/taxes/stock from current data, shows any differences, and commits atomically or rejects it.
  If a card was authorised on an external terminal during an outage, that is handled outside the POS by a manager procedure. The POS never records it as a completed sale.
* **DIAN or PT unavailable while our server is up:** not an offline case. Checkout commits in-line as usual; the DEE goes to P15's contingency flow (R-FE-05, 48 h).
* **`[A]` `pos_offline_fiscal_selling` (disabled by default):** true disconnected fiscal selling. It requires pre-allocated numbering blocks per terminal,
  local CUDE, bounded caps (hours/amount/tickets), and GL and stock posted at sync, which means **eventual consistency that the user's in-line requirement does not allow by default**.
  It may be built only behind the flag, must never be used to pass `[P]` tests or gates, and may be enabled for a tenant only after EG-13: a written business and legal acceptance
  of eventual consistency, oversell handling, and bounded number allocation, plus validation of the procedure against DIAN contingency rules (R-FE-05/R-FE-07 with the annex, B-06).

