> **Prompt P07 · Wave W1 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/03 ADR-006; shared/04 R-DP-01, R-DP-02, R-AC-03. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P07 — Workflow/Approvals, Audit Trail, Scheduler, Notifications, Attachments, Webhooks

## Mission
Provide the cross-cutting process services: configurable approval workflows with SoD, a tamper-evident audit trail for every change by any channel, a scheduler for recurring and
AI-scheduled tasks, notifications, attachments, outbound webhooks, and the data-subject-request workflow (Ley 1581).

## Owned paths
`backend/workflow/**` (sub-packages `approvals`, `audit`, `scheduler`, `notifications`, `attachments`, `webhooks`, `dsr`), `frontend/src/features/workflow/**`.

## Dependencies
G0; uses the P04 policy (fake until merged). Implements `WorkflowService`, `AuditService`, `SchedulerService`, `NotificationService`, `FileStore`.

## Components
1. **Approvals:** `ApprovalPolicy`(document type, conditions (amount thresholds in COP, party, cost center, custom expression in a safe DSL — no `eval`), steps (role/user/manager-of, parallel or sequential,
   quorum), escalation after N hours, delegation while absent). Document states integrate via `WorkflowService.request(document_ref, action)`; modules block `:post` until approved
   when a policy matches. SoD checks through P04. The UI: an inbox with bulk approve, mobile-friendly, and a document preview with the posting simulation.
2. **Audit:** `AuditEvent`(tenant, company, at, principal, actor (agent/client), channel, action, resource, resource_id, before/after diff of changed fields (sensitive fields stored as
   hashes or masked per classification), request_id, trace_id, ip, user_agent, reason, prev_hash, hash = SHA-256(prev_hash + canonical event)). Append-only table with an immutability trigger;
   a daily anchor hash written to object storage with retention lock; a verification command `manage.py audit_verify`. Read via API with field masking. Retention ≥ 10 years for financial records.
   Model mixins record audits automatically inside the UoW (same transaction).
3. **Scheduler:** `ScheduledJob`(owner principal, schedule (cron with company timezone, or one-off), job type from the registry (report delivery, recurring entries, payroll reminders, DS weekly
   accumulation, DIAN deadline checks, fiscal retries, AI tasks), params, delegated grant reference for AI-created jobs, next_run, last_result, pause/resume). Implementation: a single-leader
   scheduler loop (Postgres advisory lock) that enqueues Celery tasks; at-least-once with idempotency keys `job_id:scheduled_time`; DST-safe (Bogotá has no DST, but support other zones).
   Jobs run with the owner's *current* permissions (re-evaluated at run time; if revoked → fail + notify).
4. **Notifications:** in-app (WebSocket/SSE), email (SMTP provider adapter), and optional `[A]` WhatsApp/SMS adapters. Templates in es-CO; user preferences; digests. No sensitive data in email
   bodies unless the field policy allows it.
5. **Attachments/FileStore:** S3-compatible storage, tenant-prefixed keys, server-side encryption, antivirus scan hook (ClamAV container `[A]`), signed URLs short TTL, legal hold, retention policies.
6. **Webhooks:** subscriptions per tenant to event types; HMAC-SHA256 signatures with a timestamp; retries with exponential backoff; dead letter + replay UI; payload filtered by the subscriber's
   service-account permissions (field masks).
7. **DSR (Ley 1581 arts. 14–15):** request intake (consulta/reclamo), identity verification step, SLA timers (10+5 / 15+8 business days using the P06 holiday calendar), data discovery across modules
   via a registry each module populates (`dsr_registry.py`: which models hold personal data of employees/customers/suppliers), export package, rectification, and suppression subject to legal retention (explain the denial when retention applies).

## API
`/api/v1/workflow/approval-policies`, `/approvals` (+`:approve`, `:reject`, `:delegate`), `/audit-events` (filter by resource, principal, channel, date), `/audit:verify`,
`/scheduled-jobs` (+`:pause`, `:resume`, `:run-now`), `/notifications`, `/attachments` (+ presigned upload), `/webhooks` (+`:test`, `/deliveries`, `:replay`), `/dsr-requests` (+ actions).

## Required tests (T-P07-nn)
01 Approval threshold routing. 02 SoD block via P04. 03 Escalation timer. 04 Audit hash chain verification detects tampering (a test modifies a row as the owner role and verify fails). 05 Audit writes
roll back with the business transaction. 06 Scheduler single leader under 3 replicas. 07 At-least-once + idempotent job execution. 08 Revoked owner → job fails safely. 09 Webhook signature + retry
+ field masking. 10 DSR SLA business-day computation with holidays. 11 Signed URL expiry and tenant isolation of files. 12 AI-created jobs carry the delegated grant and can be listed and cancelled by the user.

## Acceptance (G1)
All tests green; approvals, audit, and scheduler used by at least one module fake in the cross-module harness; `docs/modules/workflow.md`.

## Sources
Ley 1581/2012 arts. 14–15 (S-24); Ley 962/2005 Art. 28 (S-12); OWASP ASVS logging requirements (T-10).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P07.md` when done)
```
# Handoff P07
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
