> **Prompt P20 · Wave W3 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/03 ADR-011; shared/05 T-01…T-06, T-15…T-19; shared/09 EG-10, EG-11. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P20 — MCP Server (Model Context Protocol) for External Agents

## Mission
Expose the ERP to external AI agents through a standards-compliant MCP server, with OAuth-based delegated access, the same permissions as the user, and no side doors (ADR-011). Requirement UR-23.

## Owned paths
`mcp_server/**` (separate Python service + Dockerfile via a CCR to P03 for compose), `frontend/src/features/mcp/**` (the admin/consent UI).

## Dependencies
G2 public API + P04 OAuth (Keycloak config) + contracts. Calls the REST API over HTTP with the user's access token (audience = the ERP API, obtained by token exchange from the MCP-audience token, or
issued directly with resource indicators: decide in an ADR after testing Keycloak capabilities: EG-11). **No database access.**

## SDK and build-time verification (first task)
* **Use the official Python SDK `mcp` 2.x** (ADR-011). Evidence as of 2026-11-08: v2.0.0 (released 2026-07-28) supports the 2026-07-28 revision and serves 2025-era clients from the same server;
  the latest on PyPI is 2.3.0. Pin the exact version you test with.
* First task: re-open https://modelcontextprotocol.io/specification/versioning and the SDK docs (https://py.sdk.modelcontextprotocol.io/), confirm the pinned version's supported revisions,
  and write `docs/adr/ADR-0xx-mcp-sdk.md` with the version, the lock hash, and the conformance results (EG-10).
* **Fallback (only if the conformance suite fails on 2.x):** pin `mcp>=1.28,<2`, serve **2025-11-25 only**, keep tools/resources/prompts in a protocol-agnostic layer (`mcp_server/core/`) so that
  moving to 2026-07-28 later only touches the transport adapter, and record the limitation in `docs/STATUS.md`. Do not hand-roll the protocol.
* Deployment notes: 2025-era clients use sessions, so use sticky routing or a single replica for them. For 2026 multi-replica traffic, configure the SDK's shared `request_state` key (check the exact API in the pinned docs).

## Protocol requirements (2026-07-28 revision)
* Transport: Streamable HTTP at `/mcp` behind TLS; stateless requests carrying the protocol version in `_meta`; implement the mandatory `server/discover`; no reliance on sessions (`Mcp-Session-Id` removed in
  this revision); `subscriptions/listen` for change notifications (resource updates for watched records) if supported by the SDK; the `resultType` field in results per spec; MRTR `input_required` flows for
  elicitation of missing parameters.
* Backward compatibility: accept 2025-11-25 clients (initialize handshake). SDK v2 serves both from the same app; T-P20-10 proves it.
* Tasks (long-running operations such as imports, payroll calculation, report exports) via the official tasks **extension**, mapped to the API's `/operations/{id}`.
* Deprecated features (roots, sampling, logging) are not implemented.

## Authorization
* The MCP server is an OAuth 2.1 **resource server**: serve Protected Resource Metadata at `/.well-known/oauth-protected-resource` (RFC 9728) naming the authorization server (Keycloak);
  return `401` with `WWW-Authenticate` including `resource_metadata`; validate token audience = this MCP resource (RFC 8707), issuer (RFC 9207 mix-up protection on the client side is documented for integrators),
  expiry, and scopes.
* Client registration: **Client ID Metadata Documents** preferred; pre-registered clients supported; Dynamic Client Registration only if the IdP supports it and an admin enables it (deprecated in the spec).
* Scopes map to permission bundles (`erp.read`, `erp.sales.write`, `erp.purchasing.write`, `erp.payroll.read`, ...); the effective permission = user ∩ scopes ∩ the tenant MCP policy (the admin can disable
  modules/tools for MCP, set amount limits, and require in-app confirmation for posting tools).
* A user consent screen listing the client, the scopes, and the tools; a connected-apps page to revoke; every call is audited with principal_type=mcp_client and the client id.

## Tools, resources, prompts
* **Tools** generated from the same curated catalog as P19 (`ai_agent/tools/catalog.yaml`, read-only shared via a build step; P19 owns the catalog), each with `inputSchema`/`outputSchema`, and annotations
  `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` set honestly (posting tools: destructiveHint=true because ledger and fiscal effects can only be reversed, never undone; idempotentHint=true with an idempotency key param; they **require** the confirmation flow;
  external-effect tools like "send invoice to DIAN/email": openWorldHint=true). Annotations are hints. The server enforces everything itself.
* Posting/destructive tools: a two-phase pattern: `*_prepare` returns a plan + preview + a `confirmation_token` (short TTL, bound to user+args hash); `*_commit` requires the token; if the tenant
  policy requires in-app confirmation, `*_commit` returns `input_required`/pending until the user confirms in the ERP UI.
* **Resources:** `erp://{tenant}/{company}/{module}/{entity}/{id}` (JSON, field-masked), the semantic model description, report results, and document PDFs (by link with short-lived signed URLs).
* **Prompts:** curated prompt templates ("Monthly close checklist", "Explain variance", "Prepare collection plan").
* Pagination, rate limits per client, and size limits on results (with summaries + links).

## Required tests (T-P20-nn)
01 Protocol conformance with the pinned SDK client (and the MCP Inspector or a conformance suite if available) for `server/discover`, tools/list, tools/call, resources/read, prompts/list, and tasks.
02 401 + PRM discovery flow end-to-end with Keycloak in compose. 03 A wrong audience token rejected. 04 Scopes and tenant MCP policy enforced. 05 Field masks and row policies identical to the REST API (leak suite
shared with P04). 06 Posting tool without a confirmation token rejected; the token is bound to the args. 07 Revoking the connected app stops access within 5 s. 08 Audit records principal_type=mcp_client. 09 No DB credentials in
the MCP container (config test). 10 Backward-compatible handshake (if supported). 11 Prompt-injection resilience: tool results are data; the server never executes instructions from content. 12 Load: 50 concurrent clients p95 < 500 ms for read tools.

## Acceptance (G3)
Tests green; `docs/integrations/mcp.md` with connection guides for common MCP clients, scopes, and examples.

## Sources
T-01 (versioning, 2026-07-28 current), T-02 (changelog), T-03 (authorization), T-04/T-05/T-06 (RFC 9728, 8707, 9207), T-12 (OWASP LLM).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P20.md` when done)
```
# Handoff P20
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
